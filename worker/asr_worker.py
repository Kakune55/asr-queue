import sys
import os
# 将项目根目录添加到Python路径中，以解决模块导入问题
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
import threading
import tempfile
from funasr import AutoModel
from task_queue.priority_queue import PriorityQueue, AUDIO_STORAGE_DIR
from models import TaskStatus, Task # 导入Task模型
from uvicorn.server import logger
import uuid

class ASRWorker(threading.Thread):
    """
    ASR处理工作者线程。
    - 在后台持续运行，从队列中获取任务并处理。
    - 在初始化时加载一次模型，避免重复加载的开销。
    """
    def __init__(self, queue: PriorityQueue, model_path="iic/SenseVoiceSmall"):
        super().__init__()
        self.queue = queue
        self.model_path = model_path
        self.model = None
        self.stop_event = threading.Event()  # 用于优雅地停止线程
        self.daemon = True  # 设置为守护线程，主程序退出时线程也会退出

    def run(self):
        """线程的主执行逻辑"""
        logger.info("正在初始化ASR模型...")
        # 加载FunASR模型
        self.model = AutoModel(
            model=self.model_path,
            vad_model="fsmn-vad",
            vad_kwargs={"max_single_segment_time": 30000},
            device="cpu",  # 使用GPU进行推理
            runtime="onnx", # 使用ONNX Runtime以获得更好的性能
        )
        logger.info("ASR模型初始化完成。")

        # 循环，直到stop_event被设置
        while not self.stop_event.is_set():
            task_id = self.queue.pop()
            if task_id:
                task = self.queue.get_task(task_id)
                # 确保task存在且audio_filepath不为空
                if task and task.audio_filepath:
                    logger.info(f"正在处理任务 {task.id}...")
                    try:
                        # FunASR模型需要文件路径作为输入
                        # 确保文件存在
                        if not os.path.exists(task.audio_filepath):
                            raise FileNotFoundError(f"音频文件未找到: {task.audio_filepath}")

                        # 调用模型进行推理
                        res = self.model.generate(
                            input=task.audio_filepath, # 直接使用文件路径
                            cache={},
                            language="zh",
                            disable_pbar=True,
                            batch_size_s=60,
                            use_itn=True,
                            merge_vad=True,
                            merge_length_s=15,
                        )
                        # 推理成功，更新任务状态和结果
                        self.queue.update_task_status(task.id, TaskStatus.COMPLETED, res[0]["text"])
                        logger.info(f"任务 {task.id} 已完成。")
                    except Exception as e:
                        # 推理失败，记录错误信息
                        logger.error(f"处理任务 {task.id} 时出错: {e}")
                        self.queue.update_task_status(task.id, TaskStatus.FAILED, str(e))
                else:
                    logger.warning(f"任务 {task_id} 未找到或无音频文件路径。")
            else:
                # 如果队列为空，短暂休眠，避免CPU空转
                time.sleep(1)

    def stop(self):
        """设置事件，通知线程停止"""
        self.stop_event.set()