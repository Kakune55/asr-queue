from fastapi import APIRouter, Depends
from uvicorn.server import logger

router = APIRouter()

# 依赖函数，用于获取ASRWorker实例
def get_asr_worker():
    """获取ASRWorker实例的依赖函数"""
    # 这个函数会被main.py中的依赖覆盖
    return None

@router.get("/info", summary="获取设备信息")
def get_device_info(worker=Depends(get_asr_worker)):
    """
    获取当前设备的基本信息。
    - 根据ASRWorker的实际配置返回设备信息。
    """
    import os
    import re
    import torch
    
    try:
        # 获取ASRWorker配置的设备类型
        if worker is None:
            return {"error": "ASRWorker未初始化"}
        
        configured_device = worker.device
        
        if configured_device == "auto":
            # 如果是auto，则根据硬件检测结果
            actual_device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            # 如果明确指定了设备，使用指定的设备
            actual_device = configured_device
        
        if actual_device == "cuda":
            # GPU信息
            try:
                gpu_name = torch.cuda.get_device_name(0)
                gpu_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)  # 转换为GB
                device_info = f"{gpu_name} ({gpu_memory:.1f}GB)"
                device_type_str = "GPU"
            except:
                # 如果获取GPU信息失败，可能是配置错误
                device_info = "GPU配置错误"
                device_type_str = "GPU"
        else:
            # CPU信息
            # 读取CPU信息
            with open('/proc/cpuinfo', 'r') as f:
                cpuinfo = f.read()
            
            # 获取CPU型号
            cpu_model_match = re.search(r'model name\s*:\s*(.+)', cpuinfo)
            cpu_model = cpu_model_match.group(1).strip() if cpu_model_match else "Unknown CPU"
            
            # 获取逻辑核心数
            logical_cores = os.cpu_count()
            
            # 获取物理CPU插槽数
            physical_ids = set(re.findall(r'physical id\s*:\s*(\d+)', cpuinfo))
            socket_count = len(physical_ids) if physical_ids else 1
            
            device_info = f"{logical_cores} x {cpu_model} ({socket_count} 插槽)"
            
            # 检测CPU厂商
            if "AMD" in cpu_model.upper():
                device_type_str = "AMD_CPU"
            elif "INTEL" in cpu_model.upper():
                device_type_str = "INTEL_CPU"
            else:
                device_type_str = "CPU"
        
        # 添加配置信息到返回结果
        config_info = f"(配置: {configured_device})"
        if configured_device != "auto":
            device_info += f" {config_info}"
        
        return {
            "device_type": device_type_str,
            "device_info": device_info,
            "configured_device": configured_device,
            "actual_device": actual_device
        }
        
    except Exception as e:
        error_msg = f"无法获取设备信息: {str(e)}"
        logger.error(error_msg)
        return {"error": error_msg}


if __name__ == "__main__":
    get_device_info()