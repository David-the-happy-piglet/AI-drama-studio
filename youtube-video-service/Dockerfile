FROM python:3.11-slim

WORKDIR /app

# 拷贝代码和依赖列表
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再复制你的 Python 代码
COPY . .
COPY ../Utils/Chinese.py .

CMD ["python", "main.py"]
