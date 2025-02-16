import os
import logging
import time
import requests
import psycopg2
import telebot
from flask import Flask
from PIL import Image
from io import BytesIO
from threading import Thread
from telebot.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

# Настройка логов
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")
OCR_API_KEY = os.getenv("OCR_API_KEY")
MAX_IMAGE_SIZE_MB = 1  # Максимальный размер изображения для OCR API

bot = telebot.TeleBot(TOKEN)
user_states = {}

# Инициализация БД
def init_db():
    commands = (
        """
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            barcode TEXT NOT NULL,
            name TEXT NOT NULL,
            price FLOAT NOT NULL,
            image_id TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """,
    )
    
    try:
        conn = psycopg2.connect(DB_URL, sslmode="require")
        with conn.cursor() as cursor:
            for command in commands:
                cursor.execute(command)
        conn.commit()
        logger.info("Таблицы БД успешно инициализированы")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
        raise

init_db()

# Веб-сервер для Render
app = Flask(__name__)

@app.route('/')
def home():
    return "Telegram Bot is Running"

# Сжатие изображения
def compress_image(image_data: bytes) -> bytes:
    """Сжимает изображение до 1 МБ"""
    try:
        with Image.open(BytesIO(image_data)) as img:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            img.thumbnail((800, 800))
            output = BytesIO()
            quality = 85
            
            while True:
                output.seek(0)
                output.truncate()
                img.save(output, format='JPEG', quality=quality, optimize=True)
                if len(output.getvalue()) <= MAX_IMAGE_SIZE_MB * 1024 * 1024:
                    break
                quality -= 5
                if quality < 50:
                    img = img.resize((img.width//2, img.height//2))
                    quality = 75
            
            logger.info(f"Изображение сжато: {len(output.getvalue())//1024} KB")
            return output.getvalue()
    except Exception as e:
        logger.error(f"Ошибка сжатия: {e}")
        raise

# Клавиатуры
def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("➕ Добавить товар"))
    markup.add(KeyboardButton("📦 Каталог"), KeyboardButton("📤 Экспорт"))
    markup.add(KeyboardButton("📷 Сканировать штрихкод"))
    return markup

@bot.message_handler(commands=['start'])
def handle_start(message):
    try:
        logger.info(f"Новый пользователь: {message.chat.id}")
        bot.send_message(
            message.chat.id,
            "Добро пожаловать! Выберите действие:",
            reply_markup=main_menu()
        )
    except Exception as e:
        logger.error(f"Ошибка в /start: {e}")

@bot.message_handler(func=lambda m: m.text == "➕ Добавить товар")
def start_add_product(message):
    try:
        user_states[message.chat.id] = {'step': 'awaiting_product_data'}
        bot.send_message(
            message.chat.id,
            "Введите данные в формате:\nШтрихкод | Название | Цена\nПример: 123456 | Молоко | 100"
        )
    except Exception as e:
        logger.error(f"Ошибка добавления товара: {e}")

# Остальные обработчики...

if __name__ == "__main__":
    # Запуск Flask в отдельном потоке
    port = int(os.environ.get("PORT", 10000))
    Thread(target=app.run, kwargs={
        'host': '0.0.0.0',
        'port': port,
        'debug': False,
        'use_reloader': False
    }).start()
    
    # Запуск бота
    logger.info("Бот запущен")
    while True:
        try:
            bot.polling(none_stop=True, interval=3, timeout=30)
        except Exception as e:
            logger.error(f"Ошибка polling: {e}")
            time.sleep(10)
