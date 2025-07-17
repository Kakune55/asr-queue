# ASR 服务 (ASR Service)

一个基于 FastAPI 构建的语音转文字 (ASR) 服务，具备优先级队列、任务持久化和实时监控功能。

## ✨ 特性

*   **异步/同步任务处理**: 支持灵活的 ASR 任务提交方式，满足不同场景需求。
*   **优先级队列**: 确保高优先级任务优先处理，优化资源分配。
*   **任务持久化**: 所有任务信息存储在 SQLite 数据库中，服务重启后任务状态可恢复，保证数据不丢失。
*   **后台工作者**: 独立的 ASR 工作者线程高效处理语音识别任务，集成 `funasr` 模型。
*   **实时监控**: 通过 WebSocket 提供队列状态和任务进度的实时更新，方便管理和调试。
*   **音频文件管理**: 自动保存上传的音频文件，并定期清理旧文件以节省存储空间。
*   **Web 管理界面**: 提供一个简洁的 HTML 页面，用于直观地查看任务队列和最近任务列表。

## 🚀 技术栈

*   **后端**: FastAPI (Python) - 高性能 Web 框架。
*   **任务队列**: 自定义实现 (基于 `heapq` + SQLite) - 兼顾内存效率和数据持久性。
*   **语音识别**: FunASR - 强大的 ASR 模型库。
*   **数据库**: SQLite - 轻量级嵌入式数据库。
*   **实时通信**: WebSocket - 实现客户端与服务器的双向通信。

## ⚙️ 安装与运行

1.  **克隆仓库**:
    ```bash
    git clone [仓库地址]
    cd asr-queue
    ```
2.  **安装依赖**:
    ```bash
    pip install -r requirements.txt
    ```
3.  **运行服务**:
    ```bash
    uvicorn main:app --reload
    ```
    服务将在 `http://127.0.0.1:8000` 启动。

## 💡 API 接口

*   **`POST /api/asr/async`**: 提交异步 ASR 任务。
    *   **参数**: `audio_file` (文件), `priority` (整数, 1-100)。
    *   **返回**: `task_id`, `status_url`。
*   **`POST /api/asr/sync`**: 提交同步 ASR 任务。
    *   **参数**: `audio_file` (文件), `priority` (整数, 1-100)。
    *   **返回**: `task_id`, `status`, `result` (阻塞直到任务完成)。
*   **`GET /api/asr/status/{task_id}`**: 查询任务状态。
*   **`GET /api/task/{task_id}`**: 获取任务详情及完整识别结果。

## 📝 使用示例

### 提交异步任务

```bash
curl -X POST "http://127.0.0.1:8000/api/asr/async?priority=10" \
     -H "accept: application/json" \
     -H "Content-Type: multipart/form-data" \
     -F "audio_file=@/path/to/your/audio.wav"
```

### 提交同步任务

```bash
curl -X POST "http://127.0.0.1:8000/api/asr/sync?priority=5" \
     -H "accept: application/json" \
     -H "Content-Type: multipart/form-data" \
     -F "audio_file=@/path/to/your/audio.wav"
```

### 查询任务状态

```bash
curl -X GET "http://127.0.0.1:8000/api/asr/status/{your_task_id}" \
     -H "accept: application/json"
```

### 获取任务详情

```bash
curl -X GET "http://127.0.0.1:8000/api/task/{your_task_id}" \
     -H "accept: application/json"
```

## 🌐 管理后台

访问 `http://127.0.0.1:8000` 即可查看实时任务队列和最近任务列表。