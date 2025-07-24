from pydantic import BaseModel, Field

class SystemConfig(BaseModel):
    max_queue_size: int = Field(default=10, ge=1, description="任务队列的最大容量")

# 全局配置实例
config = SystemConfig()