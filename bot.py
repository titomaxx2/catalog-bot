import os
import logging
import time
import requests
import psycopg2
import telebot
from flask import Flask
from PIL import Image, ImageEnhance, ImageOps
from io import BytesIO
from threading import Thread
from openpyxl import Workbook
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

# Инициализация бота
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(TOKEN, num_threads=5)
DB_URL = os.getenv("DATABASE_URL")
OCR_API_KEY = os.getenv("OCR_API_KEY")
MAX_IMAGE_SIZE_MB = 1
user_states = {}

# Инициализация БД
def init_db():
    commands = (
        """
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            barcode TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            price FLOAT NOT NULL CHECK (price > 0),
            image_id TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS order_items (
            id SERIAL PRIMARY KEY,
            order_id INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            product_id INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            quantity INT NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """
    )
    conn = None
    try:
        conn = psycopg2.connect(DB_URL, sslmode="require")
        with conn.cursor() as cursor:
            for command in commands:
                cursor.execute(command)
        conn.commit()
        logger.info("Таблицы БД успешно созданы")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

init_db()

# Веб-сервер для Render
app = Flask(__name__)

@app.route('/')
def home():
    return "Telegram Bot is Running"

class DBConnection:
    def __init__(self):
        self.conn = psycopg2.connect(DB_URL, sslmode="require")
        
    def __enter__(self):
        return self.conn.cursor()
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            if exc_type is None:
                self.conn.commit()
            else:
                self.conn.rollback()
            self.conn.close()

def compress_image(image_data: bytes) -> bytes:
    if len(image_data) <= MAX_IMAGE_SIZE_MB * 1024 * 1024:
        return image_data

    try:
        with Image.open(BytesIO(image_data)) as img:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            output = BytesIO()
            quality = 85
            img = ImageOps.exif_transpose(img)
            
            while quality >= 20:
                output.seek(0)
                output.truncate()
                img.save(output, format='JPEG', quality=quality, optimize=True)
                if len(output.getvalue()) <= MAX_IMAGE_SIZE_MB * 1024 * 1024:
                    break
                quality -= 5
                if quality < 50:
                    img = img.resize((int(img.width*0.9), int(img.height*0.9)))
            
            return output.getvalue()
    except Exception as e:
        logger.error(f"Ошибка сжатия: {e}")
        raise

def preprocess_image(image_data: bytes) -> bytes:
    try:
        image = Image.open(BytesIO(image_data))
        image = ImageEnhance.Contrast(image).enhance(2.0)
        image = image.convert('L')
        image = ImageOps.exif_transpose(image)
        
        output = BytesIO()
        image.save(output, format='JPEG', quality=85)
        return output.getvalue()
    except Exception as e:
        logger.error(f"Ошибка обработки изображения: {e}")
        raise

# Клавиатуры
def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("➕ Добавить товар", "📦 Каталог")
    markup.add("📷 Сканировать штрихкод", "📤 Экспорт")
    markup.add("📝 Создать заявку", "📋 Список заявок")
    return markup

def catalog_menu(product_id: int):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{product_id}"),
        InlineKeyboardButton("❌ Удалить", callback_data=f"delete_{product_id}")
    )
    return markup

def order_menu(order_id: int):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_order:{order_id}"),
        InlineKeyboardButton("📤 Выгрузить", callback_data=f"export_order:{order_id}"),
        InlineKeyboardButton("❌ Удалить", callback_data=f"delete_order:{order_id}")
    )
    return markup

@bot.message_handler(commands=['start'])
def handle_start(message):
    try:
        bot.send_message(message.chat.id, "🏪 Добро пожаловать!", reply_markup=main_menu())
    except Exception as e:
        logger.error(f"Ошибка в /start: {e}")

@bot.message_handler(func=lambda m: m.text == "➕ Добавить товар")
def start_add_product(message):
    user_states[message.chat.id] = {'step': 'awaiting_product_data'}
    bot.send_message(message.chat.id, "📝 Введите данные в формате:\nШтрихкод | Название | Цена")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_product_data')
def process_product_data(message):
    try:
        data = [x.strip() for x in message.text.split('|')]
        if len(data) != 3:
            raise ValueError("Неверный формат")
        
        barcode, name, price = data
        if not barcode.isdigit():
            raise ValueError("Штрихкод должен содержать только цифры")
        
        price = float(price)
        if price <= 0:
            raise ValueError("Цена должна быть больше нуля")

        with DBConnection() as cursor:
            cursor.execute(
                "INSERT INTO products (telegram_id, barcode, name, price) VALUES (%s, %s, %s, %s)",
                (message.chat.id, barcode, name, price)
            )
        
        user_states[message.chat.id] = {
            'step': 'awaiting_product_image',
            'barcode': barcode
        }
        bot.send_message(message.chat.id, "📷 Отправьте фото товара")
        
    except psycopg2.errors.UniqueViolation:
        bot.send_message(message.chat.id, "❌ Штрихкод уже существует!")
        del user_states[message.chat.id]
    except ValueError as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")
        del user_states[message.chat.id]
    except Exception as e:
        logger.error(f"Ошибка добавления товара: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка сохранения!")
        del user_states[message.chat.id]

@bot.message_handler(content_types=['photo'], func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_product_image')
def process_product_image(message):
    try:
        image_id = message.photo[-1].file_id
        barcode = user_states[message.chat.id]['barcode']

        with DBConnection() as cursor:
            cursor.execute(
                "UPDATE products SET image_id = %s WHERE barcode = %s AND telegram_id = %s",
                (image_id, barcode, message.chat.id)
            )
            if cursor.rowcount == 0:
                raise ValueError("Товар не найден")

        bot.send_message(message.chat.id, "✅ Товар добавлен!", reply_markup=main_menu())
    except Exception as e:
        logger.error(f"Ошибка сохранения фото: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка сохранения фото!")
    finally:
        user_states.pop(message.chat.id, None)

@bot.message_handler(func=lambda m: m.text == "📦 Каталог")
def show_catalog(message):
    try:
        with DBConnection() as cursor:
            cursor.execute(
                "SELECT id, barcode, name, price, image_id FROM products WHERE telegram_id = %s",
                (message.chat.id,)
            )
            products = cursor.fetchall()
        
        if not products:
            bot.send_message(message.chat.id, "🛒 Каталог пуст")
            return
        
        for product in products:
            product_id, barcode, name, price, image_id = product
            caption = f"📦 {name}\n🔖 {barcode}\n💰 {price} руб."
            reply_markup = catalog_menu(product_id)
            
            if image_id:
                bot.send_photo(message.chat.id, image_id, caption=caption, reply_markup=reply_markup)
            else:
                bot.send_message(message.chat.id, caption, reply_markup=reply_markup)
                
    except Exception as e:
        logger.error(f"Ошибка каталога: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки")

@bot.callback_query_handler(func=lambda call: call.data.startswith(('edit_', 'delete_')))
def handle_product_callback(call):
    try:
        action, product_id = call.data.split('_')
        product_id = int(product_id)
        
        if action == 'edit':
            msg = bot.send_message(call.message.chat.id, "Введите новое название и цену в формате:\nНазвание | Цена")
            bot.register_next_step_handler(msg, process_edit_product, product_id)
            
        elif action == 'delete':
            with DBConnection() as cursor:
                cursor.execute(
                    "DELETE FROM products WHERE id = %s AND telegram_id = %s",
                    (product_id, call.message.chat.id)
                )
                if cursor.rowcount > 0:
                    bot.answer_callback_query(call.id, "✅ Товар удален")
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                else:
                    bot.answer_callback_query(call.id, "❌ Товар не найден")
                    
    except Exception as e:
        logger.error(f"Ошибка обработки товара: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка обработки")

def process_edit_product(message, product_id):
    try:
        data = [x.strip() for x in message.text.split('|')]
        if len(data) != 2:
            raise ValueError("Неверный формат")
        
        name, price = data
        price = float(price)
        if price <= 0:
            raise ValueError("Цена должна быть больше нуля")

        with DBConnection() as cursor:
            cursor.execute(
                "UPDATE products SET name = %s, price = %s WHERE id = %s AND telegram_id = %s",
                (name, price, product_id, message.chat.id)
            )
            if cursor.rowcount == 0:
                raise ValueError("Товар не найден")
            
        bot.send_message(message.chat.id, "✅ Товар обновлен!", reply_markup=main_menu())
    except Exception as e:
        logger.error(f"Ошибка обновления товара: {e}")
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")

# Остальные обработчики для заявок и экспорта остаются аналогичными, но с использованием DBConnection

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    Thread(target=app.run, kwargs={
        'host': '0.0.0.0',
        'port': port,
        'debug': False,
        'use_reloader': False
    }).start()
    
    logger.info("Бот запущен")
    while True:
        try:
            bot.infinity_polling()
        except Exception as e:
            logger.error(f"Ошибка подключения: {e}")
            time.sleep(10)
