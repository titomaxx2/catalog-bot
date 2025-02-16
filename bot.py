# bot.py
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
    level=logging.INFO,
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

# Инициализация БД с обработкой ошибок
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

# Главное меню
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

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_product_data')
def process_product_data(message):
    try:
        data = [x.strip() for x in message.text.split('|')]
        if len(data) != 3:
            raise ValueError("Неверный формат данных")
        
        barcode, name, price = data
        price = float(price)
        
        user_states[message.chat.id] = {
            'step': 'awaiting_product_image',
            'product_data': (barcode, name, price)
        }
        bot.send_message(message.chat.id, "📷 Теперь отправьте фото товара")
        
    except Exception as e:
        logger.error(f"Ошибка обработки данных: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка формата. Попробуйте снова.")
        del user_states[message.chat.id]

@bot.message_handler(content_types=['photo'], func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_product_image')
def process_product_image(message):
    try:
        product_data = user_states[message.chat.id]['product_data']
        image_id = message.photo[-1].file_id
        
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO products (telegram_id, barcode, name, price, image_id) VALUES (%s, %s, %s, %s, %s)",
                    (message.chat.id, *product_data, image_id)
                )
                conn.commit()
        
        bot.send_message(message.chat.id, "✅ Товар добавлен!", reply_markup=main_menu())
        del user_states[message.chat.id]
        
    except Exception as e:
        logger.error(f"Ошибка сохранения товара: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка сохранения. Попробуйте снова.")
        del user_states[message.chat.id]

@bot.message_handler(func=lambda m: m.text == "📦 Каталог")
def show_catalog(message):
    try:
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT barcode, name, price, image_id FROM products WHERE telegram_id = %s ORDER BY created_at DESC LIMIT 10",
                    (message.chat.id,)
                )
                products = cursor.fetchall()
        
        if not products:
            bot.send_message(message.chat.id, "Каталог пуст")
            return
        
        for product in products:
            barcode, name, price, image_id = product
            caption = f"📦 {name}\n🔖 {barcode}\n💵 {price} руб."
            if image_id:
                bot.send_photo(message.chat.id, image_id, caption, reply_markup=catalog_menu())
            else:
                bot.send_message(message.chat.id, caption, reply_markup=catalog_menu())
                
    except Exception as e:
        logger.error(f"Ошибка показа каталога: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки каталога")

def catalog_menu():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Редактировать", callback_data="edit"),
               InlineKeyboardButton("Удалить", callback_data="delete"))
    return markup

# Запуск приложения
if __name__ == "__main__":
    # Запуск Flask
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
