import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
from fastapi import APIRouter, File, UploadFile, BackgroundTasks, HTTPException, Depends
from pydantic import BaseModel, Field
from task_queue.priority_queue import PriorityQueue, AUDIO_STORAGE_DIR
from models import TaskStatus
from uvicorn.server import logger
# 导入配置模块
from config import config, SystemConfig
import time
import uuid

# 为任务和配置创建不同的路由
router = APIRouter()
config_router = APIRouter()

class TaskRequest(BaseModel):
    """定义任务请求中可配置的参数"""
    priority: int = Field(default=10, ge=1, le=100, description="任务优先级，数值越小，优先级越高 (1-100)")

class AsyncResponse(BaseModel):
    """异步任务响应模型"""
    task_id: str
    status_url: str

# --- 依赖注入 ---
def get_queue():
    """
    依赖注入占位符。
    在main.py中，这个函数会被一个返回单例队列实例的函数覆盖。
    """
    raise NotImplementedError("This should be overridden in main.py")

# --- 辅助函数：保存音频文件 ---
async def save_audio_file(audio_file: UploadFile) -> str:
    """将上传的音频文件保存到文件系统，并返回文件路径。"""
    # 确保文件名不为空，并获取文件扩展名
    if audio_file.filename:
        file_extension = audio_file.filename.split(".")[-1] if "." in audio_file.filename else "wav"
    else:
        file_extension = "wav" # 默认扩展名
    
    filename = f"{uuid.uuid4()}.{file_extension}"
    filepath = os.path.join(AUDIO_STORAGE_DIR, filename)
    
    # 确保目录存在
    os.makedirs(AUDIO_STORAGE_DIR, exist_ok=True)

    with open(filepath, "wb") as f:
        while contents := await audio_file.read(1024 * 1024): # 分块读取，避免大文件一次性读入内存
            f.write(contents)
    return filepath

# --- API端点 ---
@router.post("/asr/async", response_model=AsyncResponse, summary="提交异步ASR任务")
async def create_asr_task(
    priority: int,
    audio_file: UploadFile = File(..., description="需要转写的音频文件"),
    asr_queue: PriorityQueue = Depends(get_queue)
):
    """
    处理异步语音转文字任务。
    - 接收音频文件和优先级。
    - 将任务添加到队列后立即返回任务ID。
    - 客户端需要通过状态查询接口轮询结果。
    """
    # 检查队列是否已满
    if asr_queue.size >= config.max_queue_size:
        raise HTTPException(status_code=429, detail="服务器繁忙，请稍后再试 (队列已满)")
        
    # 保存音频文件到文件系统
    audio_filepath = await save_audio_file(audio_file)
    # 将任务推入队列，只存储文件路径
    task_id = asr_queue.push(audio_filepath, priority) # 这里的push方法现在接受的是filepath
    # 返回任务ID和状态查询URL
    return {"task_id": task_id, "status_url": f"/api/asr/status/{task_id}"}


@router.post("/asr/sync", summary="提交同步ASR任务")
async def create_asr_task_sync(
    priority: int,
    audio_file: UploadFile = File(..., description="需要转写的音频文件"),
    asr_queue: PriorityQueue = Depends(get_queue)
):
    """
    处理同步语音转文字任务。
    - 接收音频文件和优先级。
    - 将任务添加到队列，并阻塞等待直到任务完成。
    - 直接返回转写结果。
    """
    # 检查队列是否已满
    if asr_queue.size >= config.max_queue_size:
        raise HTTPException(status_code=429, detail="服务器繁忙，请稍后再试 (队列已满)")

    # 保存音频文件到文件系统
    audio_filepath = await save_audio_file(audio_file)
    # 将任务推入队列，只存储文件路径
    task_id = asr_queue.push(audio_filepath, priority) # 这里的push方法现在接受的是filepath

    # 循环等待任务完成
    while True:
        task = asr_queue.get_task(task_id)
        if task and task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED]:
            # 任务完成或失败，返回结果
            return {"task_id": task_id, "status": task.status.value, "result": task.result}
        # 短暂休眠，避免CPU空转
        await asyncio.sleep(0.5)

@router.get("/asr/status/{task_id}", summary="查询任务状态")
async def get_asr_status(
    task_id: str,
    asr_queue: PriorityQueue = Depends(get_queue)
):
    """根据任务ID查询任务的当前状态。"""
    task = asr_queue.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")
    return {"task_id": task_id, "status": task.status.value}


@router.get("/task/{task_id}", summary="获取任务详情")
async def get_task_details(
    task_id: str,
    asr_queue: PriorityQueue = Depends(get_queue)
):
    """根据任务ID获取单个任务的详细信息，包括完整的识别结果。"""
    task = asr_queue.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务未找到")
    return task # 直接返回Task对象，FastAPI会自动序列化为JSON

@router.get("/statistics", summary="获取系统统计信息")
async def get_statistics(asr_queue: PriorityQueue = Depends(get_queue)):
    """获取最近5/15/45分钟的平均等待时间和负载统计"""
    stats = {}
    intervals = [5, 15, 45]
    # 当前系统工作线程数量（根据main.py中只启动了一个worker）
    worker_count = 1
    for interval in intervals:
        avg_waiting, avg_load = asr_queue.calculate_statistics(interval, worker_count)
        stats[f"last_{interval}_min"] = {
            "avg_waiting_time": avg_waiting,
            "avg_load_percent": avg_load
        }
    return stats


# --- 配置 API 端点 ---
@config_router.get("/config", response_model=SystemConfig, summary="获取当前系统配置")
async def get_current_config():
    """返回当前的系统配置。"""
    return config

@config_router.put("/config", response_model=SystemConfig, summary="更新系统配置")
async def update_config(new_config: SystemConfig):
    """
    在线更新系统配置。
    例如，可以用来调整队列的最大容量。
    """
    config.max_queue_size = new_config.max_queue_size
    logger.info(f"队列最大容量已更新为: {config.max_queue_size}")
    return config
