# 使用官方Python运行时作为父镜像
FROM python:3.12-slim-buster

# 设置工作目录
WORKDIR /app

# 复制requirements.txt并安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制所有项目文件到工作目录
COPY . .

# 暴露应用程序运行的端口
EXPOSE 8000

# 定义启动命令
# 使用uvicorn启动FastAPI应用，监听所有网络接口的8000端口
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]