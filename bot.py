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
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# Настройка логов
logging.basicConfig(
    level=logging.DEBUG,  # Включен режим отладки
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")
OCR_API_KEY = os.getenv("OCR_API_KEY")
MAX_IMAGE_SIZE_MB = 1

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
        logger.info("Таблицы БД успешно созданы")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
        raise

init_db()

# Веб-сервер для Render
app = Flask(__name__)

@app.route('/')
def home():
    return "Telegram Bot is Running"

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
        logger.debug(f"/start от {message.chat.id}")
        bot.send_message(message.chat.id, "Добро пожаловать!", reply_markup=main_menu())
    except Exception as e:
        logger.error(f"Ошибка в /start: {e}")

# Обработчики добавления товара (рабочие, оставить без изменений)

@bot.message_handler(func=lambda m: m.text == "📷 Сканировать штрихкод")
def handle_scan(message):
    try:
        logger.debug(f"Начало сканирования для {message.chat.id}")
        user_states[message.chat.id] = {'step': 'awaiting_barcode_scan'}
        bot.send_message(message.chat.id, "Отправьте фото штрихкода...")
    except Exception as e:
        logger.error(f"Ошибка handle_scan: {e}", exc_info=True)

@bot.message_handler(content_types=['photo'], func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_barcode_scan')
def process_scan(message):
    try:
        logger.debug(f"Обработка фото от {message.chat.id}")
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Логика обработки изображения
        # ...
        
        bot.send_message(message.chat.id, "Штрихкод обработан!")
    except Exception as e:
        logger.error(f"Ошибка process_scan: {e}", exc_info=True)
    finally:
        user_states.pop(message.chat.id, None)

@bot.message_handler(func=lambda m: m.text == "📤 Экспорт")
def handle_export(message):
    try:
        logger.debug(f"Экспорт для {message.chat.id}")
        # Логика экспорта
        bot.send_document(message.chat.id, open('export.csv', 'rb'))
    except Exception as e:
        logger.error(f"Ошибка экспорта: {e}", exc_info=True)

@bot.callback_query_handler(func=lambda call: call.data in ['edit', 'delete'])
def handle_callback(call):
    try:
        if call.data == 'edit':
            logger.debug(f"Редактирование товара {call.message.chat.id}")
            # Логика редактирования
        elif call.data == 'delete':
            logger.debug(f"Удаление товара {call.message.chat.id}")
            # Логика удаления
    except Exception as e:
        logger.error(f"Ошибка callback: {e}", exc_info=True)

# Запуск приложения
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    Thread(target=app.run, kwargs={
        'host': '0.0.0.0', 
        'port': port,
        'debug': False
    }).start()
    
    logger.info("Бот запущен")
    while True:
        try:
            bot.polling(none_stop=True, interval=3, timeout=30)
        except Exception as e:
            logger.error(f"Ошибка polling: {e}")
            time.sleep(10)
