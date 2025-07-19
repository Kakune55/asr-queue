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
                # 修改为最大堆：优先级数值越大越优先
                # 通过取负实现：用户优先级数值越大 -> 堆中数值越小
                heapq.heappush(self._queue, (-priority, created_at, task_id))
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
            heapq.heappush(self._queue, (-priority, created_at, task_id))
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