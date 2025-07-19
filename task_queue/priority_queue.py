import sys
import os
# 将项目根目录添加到Python路径中，以解决模块导入问题
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import heapq
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional, List

from models import Task, TaskStatus, init_db

# 定义存储音频文件的目录
AUDIO_STORAGE_DIR = "audio_files"
if not os.path.exists(AUDIO_STORAGE_DIR):
    os.makedirs(AUDIO_STORAGE_DIR)

class PriorityQueue:
    """
    一个持久化的、线程安全的优先级队列。
    - 使用heapq实现内存中的优先级队列。
    - 使用SQLite进行任务的持久化存储。
    - 在启动时会从数据库加载未完成的任务。
    """
    def __init__(self, db_path='asr_queue.db'):
        self.db_path = db_path
        self._queue = []  # 内存中的优先队列 (priority, created_at, task_id)
        self._lock = threading.Lock()  # 线程锁，确保多线程操作安全
        init_db()  # 初始化数据库和表结构
        self._load_pending_tasks()

    def _load_pending_tasks(self):
        """从数据库加载所有'pending'或'processing'状态的任务到内存队列中，以实现服务重启后的任务恢复。"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            # 选取需要恢复的任务
            cursor.execute("SELECT id, priority, created_at FROM tasks WHERE status IN ('pending', 'processing')")
            for row in cursor.fetchall():
                task_id, priority, created_at_str = row
                created_at = datetime.fromisoformat(created_at_str)
                # heapq默认是最小堆，所以优先级数值越小，越先被处理
                # created_at作为次要排序键，确保相同优先级的任务按FIFO顺序处理
                heapq.heappush(self._queue, (priority, created_at, task_id))
            conn.close()
            print(f"从数据库加载了 {len(self._queue)} 个待处理任务。")

    def push(self, audio_filepath: str, priority: int) -> str: # 更改为audio_filepath
        """
        向队列中添加一个新任务，并将其持久化到数据库。
        返回任务的唯一ID。
        """
        with self._lock:
            task_id = str(uuid.uuid4())
            created_at = datetime.now()
            
            # 1. 持久化到数据库
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO tasks (id, audio_filepath, priority, status, created_at) VALUES (?, ?, ?, ?, ?)",
                (task_id, audio_filepath, priority, TaskStatus.PENDING.value, created_at)
            )
            conn.commit()
            conn.close()

            # 2. 推入内存队列
            heapq.heappush(self._queue, (priority, created_at, task_id))
            return task_id

    def pop(self) -> Optional[str]:
        """
        从队列中弹出一个优先级最高的任务ID，并将其状态更新为'processing'。
        如果队列为空，返回None。
        """
        with self._lock:
            if not self._queue:
                return None
            
            priority, created_at, task_id = heapq.heappop(self._queue)
            
            # 更新数据库中的任务状态
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = conn.cursor()
            # 计算等待时间
            waiting_time = (datetime.now() - created_at).total_seconds()
            cursor.execute("UPDATE tasks SET status = ?, waiting_time = ? WHERE id = ?", (TaskStatus.PROCESSING.value, waiting_time, task_id))
            conn.commit()
            conn.close()
            
            return task_id

    def get_task(self, task_id: str) -> Optional[Task]:
        """根据任务ID从数据库获取任务详情。"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row  # 允许通过列名访问数据
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            # 将数据库行数据转换为Task数据类实例
            return Task(
                id=row['id'],
                audio_filepath=row['audio_filepath'],
                priority=row['priority'],
                status=TaskStatus(row['status']),
                created_at=datetime.fromisoformat(row['created_at']),
                updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None,
                result=row['result'],
                waiting_time=row['waiting_time'],
                processing_time=row['processing_time']
            )
        return None

    def update_task_status(self, task_id: str, status: TaskStatus, result: Optional[str] = None):
        """更新指定任务的状态和结果。"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = conn.cursor()
        
        # 获取任务的创建时间或更新时间，用于计算处理时间
        cursor.execute("SELECT created_at, updated_at FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        processing_time = None
        if row:
            created_at = datetime.fromisoformat(row[0])
            # 如果是第一次更新为processing，则updated_at是None，此时处理时间从created_at算起
            # 否则从上一次updated_at算起
            start_time = datetime.fromisoformat(row[1]) if row[1] else created_at
            processing_time = (datetime.now() - start_time).total_seconds()

        cursor.execute(
            "UPDATE tasks SET status = ?, result = ?, updated_at = ?, processing_time = ? WHERE id = ?",
            (status.value, result, datetime.now(), processing_time, task_id)
        )
        conn.commit()
        conn.close()

    def cleanup_old_audio_data(self, minutes: int = 30):
        """清理指定分钟数之前的已完成或失败任务的音频数据，以节省空间。"""
        cleanup_time = datetime.now() - timedelta(minutes=minutes)
        try:
            conn = sqlite3.connect(self.db_path, check_same_thread=False)
            cursor = conn.cursor()
            # 获取需要清理的旧任务的文件路径
            cursor.execute(
                "SELECT audio_filepath FROM tasks WHERE status IN (?, ?) AND created_at < ? AND audio_filepath IS NOT NULL",
                (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, cleanup_time)
            )
            filepaths_to_delete = [row[0] for row in cursor.fetchall()]

            # 删除文件系统中的音频文件
            for filepath in filepaths_to_delete:
                if os.path.exists(filepath):
                    os.remove(filepath)
                    print(f"文件清理：删除了旧音频文件 {filepath}")
            
            # 将数据库中的audio_filepath字段设置为空
            cursor.execute(
                "UPDATE tasks SET audio_filepath = NULL WHERE status IN (?, ?) AND created_at < ? AND audio_filepath IS NOT NULL",
                (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, cleanup_time)
            )
            count = cursor.rowcount
            conn.commit()
            conn.close()
            if count > 0:
                print(f"数据库清理：清除了 {count} 个旧任务的音频文件路径。")
        except Exception as e:
            print(f"数据库清理时出错: {e}")


    @property
    def size(self):
        """返回当前队列中的任务数量。"""
        with self._lock:
            return len(self._queue)

    def get_recent_tasks(self, limit: int = 10) -> List[Task]:
        """获取最近完成或失败的任务列表。"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM tasks WHERE status IN (?, ?) ORDER BY updated_at DESC LIMIT ?",
            (TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, limit)
        )
        rows = cursor.fetchall()
        conn.close()
        
        recent_tasks = []
        for row in rows:
            recent_tasks.append(Task(
                id=row['id'],
                audio_filepath=row['audio_filepath'],
                priority=row['priority'],
                status=TaskStatus(row['status']),
                created_at=datetime.fromisoformat(row['created_at']),
                updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None,
                result=row['result'],
                waiting_time=row['waiting_time'],
                processing_time=row['processing_time']-row['waiting_time'],
            ))
        return recent_tasks

    def get_processing_tasks(self) -> List[Task]:
        """获取所有'processing'状态的任务列表。"""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM tasks WHERE status = ?",
            (TaskStatus.PROCESSING.value,)
        )
        rows = cursor.fetchall()
        conn.close()
        
        processing_tasks = []
        for row in rows:
            processing_tasks.append(Task(
                id=row['id'],
                audio_filepath=row['audio_filepath'],
                priority=row['priority'],
                status=TaskStatus(row['status']),
                created_at=datetime.fromisoformat(row['created_at']),
                updated_at=datetime.fromisoformat(row['updated_at']) if row['updated_at'] else None,
                result=row['result'],
                waiting_time=row['waiting_time'],
                processing_time=row['processing_time']
            ))
        return processing_tasks

# 示例用法
if __name__ == '__main__':
    print("--- PriorityQueue Test ---")
    # 创建一个新的数据库文件进行测试，避免影响主数据库
    test_db = "test_queue.db"
    if os.path.exists(test_db):
        os.remove(test_db)
    if not os.path.exists(AUDIO_STORAGE_DIR):
        os.makedirs(AUDIO_STORAGE_DIR)

    pq = PriorityQueue(db_path=test_db)
    
    # 添加入队
    print("添加两个任务 P10, P1...")
    task_id_1 = pq.push(os.path.join(AUDIO_STORAGE_DIR, "test_audio_1.wav"), priority=10) # 传入文件路径
    time.sleep(1) # 确保时间戳不同
    task_id_2 = pq.push(os.path.join(AUDIO_STORAGE_DIR, "test_audio_2.wav"), priority=1) # 传入文件路径
    
    # 创建对应的测试音频文件
    with open(os.path.join(AUDIO_STORAGE_DIR, "test_audio_1.wav"), 'wb') as f:
        f.write(b"some_audio_data_1")
    with open(os.path.join(AUDIO_STORAGE_DIR, "test_audio_2.wav"), 'wb') as f:
        f.write(b"some_audio_data_2")

    print(f"当前队列大小: {pq.size}")
    
    # 取出任务 (应取出P1，即task_id_2)
    next_task_id = pq.pop()
    if next_task_id:
        print(f"取出的任务ID: {next_task_id} (预期为 {task_id_2})")
        assert next_task_id == task_id_2
        
        task_details = pq.get_task(next_task_id)
        assert task_details is not None
        print(f"任务详情: {task_details}")
        assert task_details.status == TaskStatus.PROCESSING
        
        # 更新任务
        print("更新任务状态为 'completed'...")
        pq.update_task_status(next_task_id, TaskStatus.COMPLETED, "This is a test result.")
        completed_task = pq.get_task(next_task_id)
        assert completed_task is not None
        print(f"完成的任务: {completed_task}")
        assert completed_task.status == TaskStatus.COMPLETED
    else:
        print("测试失败：未能从队列中取出任务。")

    print(f"弹出后队列大小: {pq.size}")
    assert pq.size == 1
    
    # 测试从数据库重新加载
    print("\n--- 测试从数据库重新加载 ---")
    pq2 = PriorityQueue(db_path=test_db)
    print(f"新队列大小: {pq2.size} (预期为 1)")
    assert pq2.size == 1
    reloaded_task_id = pq2.pop()
    print(f"重新加载后弹出的任务ID: {reloaded_task_id} (预期为 {task_id_1})")
    assert reloaded_task_id == task_id_1

    # 测试清理任务
    print("\n--- 测试清理任务 ---")
    print("将剩余任务标记为失败...")
    remaining_task_id = pq.pop()
    if remaining_task_id:
        pq.update_task_status(remaining_task_id, TaskStatus.FAILED, "Failed for cleanup test.")
    
    # 手动插入一个更早的任务
    conn = sqlite3.connect(test_db, check_same_thread=False)
    cursor = conn.cursor()
    old_time = datetime.now() - timedelta(minutes=40)
    # 注意：这里需要提供audio_filepath，即使是旧任务
    cursor.execute("INSERT INTO tasks (id, audio_filepath, priority, status, created_at) VALUES (?, ?, ?, ?, ?)",
                   ('old_task', os.path.join(AUDIO_STORAGE_DIR, 'old_task.wav'), 1, 'completed', old_time))
    conn.commit()
    conn.close()
    # 创建一个对应的旧音频文件
    with open(os.path.join(AUDIO_STORAGE_DIR, 'old_task.wav'), 'wb') as f:
        f.write(b'old_audio_data')

    print("执行清理...")
    pq.cleanup_old_audio_data(minutes=30)
    
    # 验证旧任务的音频文件是否被删除，且数据库记录中的路径是否为空
    assert not os.path.exists(os.path.join(AUDIO_STORAGE_DIR, 'old_task.wav'))
    conn = sqlite3.connect(test_db, check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute("SELECT audio_filepath FROM tasks WHERE id = 'old_task'")
    assert cursor.fetchone()[0] is None
    print("旧任务的音频文件已被成功删除，数据库路径已清空。")
    # 验证新任务是否保留
    cursor.execute("SELECT audio_filepath FROM tasks WHERE id = ?", (remaining_task_id,))
    assert cursor.fetchone()[0] is not None
    print("近期任务的音频文件路径被正确保留。")
    conn.close()

    # 测试获取最近任务
    print("\n--- 测试获取最近任务 ---")
    recent_tasks = pq.get_recent_tasks(limit=5)
    print(f"最近任务数量: {len(recent_tasks)}")
    for task in recent_tasks:
        print(f"  ID: {task.id}, Status: {task.status.value}, Waiting: {task.waiting_time:.2f}s, Processing: {task.processing_time:.2f}s")


    # 清理测试数据库和音频文件目录
    os.remove(test_db)
    for f in os.listdir(AUDIO_STORAGE_DIR):
        os.remove(os.path.join(AUDIO_STORAGE_DIR, f))
    os.rmdir(AUDIO_STORAGE_DIR)
    print("\n测试完成。")
