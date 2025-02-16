FROM python:3.9-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    libzbar0 \
    libzbar-dev \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "bot.py"]
