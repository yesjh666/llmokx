FROM python:3.11-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY app/ ./app/
COPY config/ ./config/
COPY run.py .

# 创建数据和日志目录
RUN mkdir -p data logs

EXPOSE 8080

CMD ["python", "run.py"]
