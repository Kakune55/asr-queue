from pydantic import BaseModel, Field
import os

class SystemConfig(BaseModel):
    max_queue_size: int = Field(default=10, ge=1, description="任务队列的最大容量")
    force_cpu: bool = Field(default=False, description="是否强制使用CPU模式，即使有可用的GPU")

# 从环境变量中读取配置，如果没有设置则使用默认值
force_cpu_env = os.environ.get('FORCE_CPU', 'false').lower() == 'true'

# 全局配置实例
config = SystemConfig(force_cpu=force_cpu_env)