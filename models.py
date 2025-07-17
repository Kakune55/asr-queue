import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

class TaskStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class Task:
    id: str
    audio_filepath: Optional[str] # 存储音频文件路径
    priority: int
    status: TaskStatus
    created_at: datetime
    updated_at: Optional[datetime] = None
    result: Optional[str] = None
    waiting_time: Optional[float] = None # 等待时间 (从创建到开始处理)
    processing_time: Optional[float] = None # 处理时间 (从开始处理到完成/失败)

def init_db():
    conn = sqlite3.connect('asr_queue.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            audio_filepath TEXT,
            priority INTEGER DEFAULT 0,
            status TEXT CHECK(status IN ('pending', 'processing', 'completed', 'failed')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP,
            result TEXT,
            waiting_time REAL,
            processing_time REAL
        )
    ''')
    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()