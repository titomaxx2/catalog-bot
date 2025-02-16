FROM python:3.9-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    libzbar0 \
    libcairo2 \
    libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# Установка Python-зависимостей
RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "bot.py"]
