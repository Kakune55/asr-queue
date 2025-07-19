import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles # 导入StaticFiles
from router.job import router as job_router, get_queue
from task_queue.priority_queue import PriorityQueue
from worker.asr_worker import ASRWorker
from models import TaskStatus
import json

# 初始化FastAPI应用
app = FastAPI(title="ASR Service", description="一个带优先级队列的ASR语音转文字服务")

# --- 静态文件服务 ---
# 将'static'目录挂载到'/static'路径，用于提供前端文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- 单例模式管理核心组件 ---
# 创建一个全局唯一的任务队列实例
asr_queue = PriorityQueue()
# 创建一个全局唯一的ASR工作者实例
asr_worker = ASRWorker(asr_queue)

# --- 依赖注入 ---
# 定义一个函数，用于在请求处理时获取队列实例
def get_singleton_queue():
    """获取全局唯一的任务队列实例"""
    return asr_queue

# 使用FastAPI的依赖覆盖功能，将所有对get_queue的依赖请求都导向get_singleton_queue
# 这样可以确保整个应用（包括不同的路由）共享同一个asr_queue实例
app.dependency_overrides[get_queue] = get_singleton_queue
# 注册任务路由
app.include_router(job_router, prefix="/api", tags=["ASR Tasks"])


# --- WebSocket后台管理 ---
class ConnectionManager:
    """管理WebSocket连接"""
    def __init__(self):
        # 存放活跃的WebSocket连接
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """接受新的WebSocket连接"""
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        """断开WebSocket连接"""
        self.active_connections.remove(websocket)

    async def broadcast(self, message: str):
        """向所有连接的客户端广播消息"""
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

async def broadcast_queue_status():
    """定期向所有后台前端广播队列状态"""
    while True:
        # 获取队列中任务的快照信息，用于前端展示
        tasks_snapshot = [{"id": t[2], "priority": -t[0]} for t in asr_queue._queue] # 负号反转优先级
        # 获取最近处理的任务列表
        recent_tasks_data = []
        for task in asr_queue.get_recent_tasks(limit=10): # 获取最近10个任务
            # 为了减少数据传输量，对结果进行截断处理
            result_display = "N/A"
            if task.result:
                if task.status == TaskStatus.COMPLETED:
                    result_display = "点击查看详情"
                elif task.status == TaskStatus.FAILED:
                     result_display = "失败，点击查看详情"

            recent_tasks_data.append({
                "id": task.id,
                "priority": task.priority,
                "status": task.status.value,
                "waiting_time": task.waiting_time,
                "processing_time": task.processing_time,
                "result_display": result_display # 使用截断后的结果
            })

        # 获取正在处理中的任务列表
        processing_tasks_data = []
        for task in asr_queue.get_processing_tasks():
            # 正在处理中的任务，result_display可以显示为"处理中..."或者不显示
            processing_tasks_data.append({
                "id": task.id,
                "priority": task.priority,
                "status": task.status.value,
                "waiting_time": task.waiting_time,
                "processing_time": task.processing_time,
                "result_display": "处理中..." # 正在处理中的任务，结果显示为“处理中...”
            })

        status = {
            "queue_size": asr_queue.size,
            "pending_tasks": tasks_snapshot,
            "processing_tasks": processing_tasks_data, # 新增
            "recent_tasks": recent_tasks_data
        }
        # 广播JSON格式的状态信息
        await manager.broadcast(json.dumps(status))
        # 每1秒广播一次
        await asyncio.sleep(1)

async def run_cleanup_scheduler():
    """定期运行数据库清理任务"""
    while True:
        # 每小时运行一次清理
        await asyncio.sleep(3600) 
        print("开始执行定期的数据库清理任务...")
        try:
            # 清理30分钟前的旧任务音频数据
            asr_queue.cleanup_old_audio_data(minutes=30)
        except Exception as e:
            print(f"执行数据库清理任务时出错: {e}")

@app.websocket("/ws/status")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket通信端点，用于后台实时监控"""
    await manager.connect(websocket)
    try:
        while True:
            # 等待客户端消息，保持连接
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        print("A client disconnected from dashboard.")

# --- 应用生命周期事件 ---
@app.on_event("startup")
async def startup_event():
    """应用启动时执行"""
    # 启动ASR工作者线程
    asr_worker.start()
    app.state.worker = asr_worker
    # 在后台启动一个任务，用于定期广播队列状态
    asyncio.create_task(broadcast_queue_status())
    # 在后台启动数据库清理调度器
    asyncio.create_task(run_cleanup_scheduler())
    print("ASR Worker and cleanup scheduler have been started.")

@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时执行"""
    # 停止ASR工作者线程
    app.state.worker.stop()
    app.state.worker.join()
    print("ASR Worker has been stopped.")

# --- 管理后台HTML页面 ---
@app.get("/", response_class=HTMLResponse, tags=["Dashboard"])
async def get_admin_dashboard():
    """提供一个简单的HTML页面作为管理后台"""
    # 直接返回静态文件
    return HTMLResponse(content=open("static/index.html", "r").read())
