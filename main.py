import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import asyncio
import torch  # 用于检测GPU数量
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles # 导入StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from router.job import router as job_router, config_router, get_queue
from router.device import router as device_router
from task_queue.priority_queue import PriorityQueue
from uvicorn.server import logger
from worker.asr_worker import ASRWorker
from models import TaskStatus
import json

# 初始化FastAPI应用
app = FastAPI(title="ASR Service", description="一个带优先级队列的ASR语音转文字服务")

# 允许跨域请求
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 静态文件服务 ---
# 将'static'目录挂载到'/static'路径，用于提供前端文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- 单例模式管理核心组件 ---
# 创建一个全局唯一的任务队列实例
asr_queue = PriorityQueue()
# 导入配置
from config import config
# 检测可用的GPU数量，如果没有GPU则使用CPU
if config.force_cpu:
    gpu_count = 0
    logger.info("强制使用CPU模式，忽略可用的GPU")
else:
    gpu_count = torch.cuda.device_count() if torch.cuda.is_available() else 0
    logger.info(f"检测到 {gpu_count} 个GPU")

# 创建多个ASR工作者实例以支持多卡调度
asr_workers = []
if gpu_count > 0:
    # 为每个GPU创建一个工作者实例
    for i in range(gpu_count):
        worker = ASRWorker(asr_queue, device=f"cuda:{i}")
        asr_workers.append(worker)
else:
    # 如果没有GPU，创建一个使用CPU的工作者实例
    worker = ASRWorker(asr_queue, device="cpu")
    asr_workers.append(worker)

# 为了保持向后兼容性，仍然提供单个worker的引用
asr_worker = asr_workers[0]

# --- 依赖注入 ---
# 定义一个函数，用于在请求处理时获取队列实例
def get_singleton_queue():
    """获取全局唯一的任务队列实例"""
    return asr_queue

# 定义一个函数，用于获取ASRWorker实例
def get_asr_worker():
    """获取全局唯一的ASRWorker实例"""
    return asr_worker

# 使用FastAPI的依赖覆盖功能，将所有对get_queue的依赖请求都导向get_singleton_queue
# 这样可以确保整个应用（包括不同的路由）共享同一个asr_queue实例
app.dependency_overrides[get_queue] = get_singleton_queue
# 注册任务路由
app.include_router(job_router, prefix="/api", tags=["ASR Tasks"])
app.include_router(config_router, prefix="/api", tags=["Configuration"])
# 注册设备信息路由，并提供ASRWorker依赖
from router.device import get_asr_worker as get_worker_dep
app.dependency_overrides[get_worker_dep] = get_asr_worker
# 为job router提供ASRWorker依赖
from router.job import get_asr_worker as get_job_worker_dep
app.dependency_overrides[get_job_worker_dep] = get_asr_worker
app.include_router(device_router, prefix="/api/device", tags=["Device Info"])


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
                    result_display = "详情"
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
        logger.info("开始执行定期的数据库清理任务...")
        try:
            # 清理30分钟前的旧任务音频数据
            asr_queue.cleanup_old_audio_data(minutes=30)
        except Exception as e:
            logger.error(f"执行数据库清理任务时出错: {e}")

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
        logger.info("A client disconnected from dashboard.")

# --- 应用生命周期事件 ---
@app.on_event("startup")
async def startup_event():
    """应用启动时执行"""
    # 启动所有ASR工作者线程
    for i, worker in enumerate(asr_workers):
        worker.start()
        logger.info(f"ASR Worker {i} (设备: {worker.device}) 已启动")
    app.state.workers = asr_workers
    # 在后台启动一个任务，用于定期广播队列状态
    asyncio.create_task(broadcast_queue_status())
    # 在后台启动数据库清理调度器
    asyncio.create_task(run_cleanup_scheduler())
    logger.info(f"已启动 {len(asr_workers)} 个ASR Worker和清理调度器。")

@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭时执行"""
    # 停止所有ASR工作者线程
    for i, worker in enumerate(app.state.workers):
        worker.stop()
        worker.join()
        logger.info(f"ASR Worker {i} 已停止")
    logger.info("所有ASR Worker均已停止。")

# --- 管理后台HTML页面 ---
@app.get("/", response_class=HTMLResponse, tags=["Dashboard"])
async def get_admin_dashboard():
    """提供一个简单的HTML页面作为管理后台"""
    # 直接返回静态文件
    return HTMLResponse(content=open("static/index.html", "r").read())

@app.get("/history.html", response_class=HTMLResponse, tags=["Dashboard"])
async def get_history_page():
    """提供历史任务查看页面"""
    return HTMLResponse(content=open("static/history.html", "r").read())
