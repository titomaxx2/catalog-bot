import os
import logging
import time
import requests
import psycopg2
import telebot
from flask import Flask
from PIL import Image, ImageEnhance, UnidentifiedImageError
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

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)
DB_URL = os.getenv("DATABASE_URL")
OCR_API_KEY = os.getenv("OCR_API_KEY")
MAX_IMAGE_SIZE_MB = 1
user_states = {}
CACHE_TIMEOUT = 300  # 5 минут

def get_db_connection():
    return psycopg2.connect(DB_URL, sslmode="require")

def init_db():
    commands = (
        """CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            barcode TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            price FLOAT NOT NULL,
            image_id TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            name TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS order_items (
            id SERIAL PRIMARY KEY,
            order_id INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            product_id INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            quantity INT NOT NULL DEFAULT 1,
            price FLOAT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )"""
    )
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            for command in commands:
                cursor.execute(command)
        conn.commit()

init_db()

app = Flask(__name__)

@app.route('/')
def home():
    return "Telegram Bot is Running"

def compress_image(image_data: bytes) -> bytes:
    try:
        if len(image_data) <= MAX_IMAGE_SIZE_MB * 1024 * 1024:
            return image_data

        with Image.open(BytesIO(image_data)) as img:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            output = BytesIO()
            quality = 85
            
            while quality >= 50:
                output.seek(0)
                output.truncate(0)
                img.save(output, format='JPEG', quality=quality, optimize=True)
                if output.getbuffer().nbytes <= MAX_IMAGE_SIZE_MB * 1024 * 1024:
                    break
                quality -= 5
            
            return output.getvalue()
    except Exception as e:
        logger.error(f"Ошибка сжатия: {e}")
        raise

def preprocess_image(image_data: bytes) -> bytes:
    try:
        with Image.open(BytesIO(image_data)) as img:
            enhancer = ImageEnhance.Contrast(img)
            return enhancer.enhance(2.0).convert('L').tobytes()
    except UnidentifiedImageError:
        logger.error("Невозможно определить формат изображения")
        raise
    except Exception as e:
        logger.error(f"Ошибка обработки изображения: {e}")
        raise

def main_menu():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(
        KeyboardButton("➕ Добавить товар"),
        KeyboardButton("📦 Каталог"),
        KeyboardButton("📤 Экспорт"),
        KeyboardButton("📷 Сканировать штрихкод"),
        KeyboardButton("📝 Создать заявку"),
        KeyboardButton("📋 Список заявок")
    )

def catalog_menu(product_id: int):
    return InlineKeyboardMarkup().row(
        InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{product_id}"),
        InlineKeyboardButton("❌ Удалить", callback_data=f"delete_{product_id}")
    )

def order_menu(order_id: int):
    return InlineKeyboardMarkup().row(
        InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_order_{order_id}"),
        InlineKeyboardButton("📤 Выгрузить", callback_data=f"export_order_{order_id}"),
        InlineKeyboardButton("❌ Удалить", callback_data=f"delete_order_{order_id}")
    )

@bot.message_handler(commands=['start'])
def handle_start(message):
    try:
        bot.send_message(message.chat.id, "🏪 Добро пожаловать!", reply_markup=main_menu())
    except Exception as e:
        logger.error(f"Ошибка в /start: {e}")

# Обработчики товаров
@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_'))
def handle_delete_product(call):
    try:
        product_id = call.data.split('_')[1]
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM products WHERE id = %s", (product_id,))
                conn.commit()
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id, "✅ Товар удален")
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка удаления!")

@bot.callback_query_handler(func=lambda call: call.data.startswith('edit_'))
def handle_edit_product(call):
    try:
        product_id = call.data.split('_')[1]
        user_states[call.message.chat.id] = {
            'step': 'edit_product',
            'product_id': product_id,
            'time': time.time()
        }
        bot.send_message(call.message.chat.id, "Введите новые данные:\nНазвание | Цена")
    except Exception as e:
        logger.error(f"Ошибка редактирования: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка!")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'edit_product')
def process_edit_product(message):
    try:
        product_id = user_states[message.chat.id]['product_id']
        name, price = map(str.strip, message.text.split('|', 1))
        
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE products 
                    SET name = %s, price = %s 
                    WHERE id = %s
                """, (name, float(price), product_id))
                conn.commit()
        
        bot.send_message(message.chat.id, "✅ Товар обновлен!", reply_markup=main_menu())
    except Exception as e:
        logger.error(f"Ошибка обновления: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка формата!")
    finally:
        user_states.pop(message.chat.id, None)

# Обработчики заявок
@bot.message_handler(func=lambda m: m.text == "📝 Создать заявку")
def create_order(message):
    try:
        user_states[message.chat.id] = {'step': 'create_order_name', 'time': time.time()}
        bot.send_message(message.chat.id, "📝 Введите название заявки:")
    except Exception as e:
        logger.error(f"Ошибка создания заявки: {e}")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'create_order_name')
def process_order_name(message):
    try:
        order_name = message.text.strip()
        if not order_name:
            raise ValueError("Пустое название")
        
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO orders (telegram_id, name)
                    VALUES (%s, %s) RETURNING id
                """, (message.chat.id, order_name))
                order_id = cur.fetchone()[0]
                conn.commit()
        
        user_states[message.chat.id] = {
            'step': 'order_management',
            'order_id': order_id,
            'time': time.time()
        }
        markup = ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("🔍 Сканировать штрихкод", "⌨️ Ввести 4 цифры", "🔙 Завершить")
        bot.send_message(message.chat.id, "Выберите действие:", reply_markup=markup)
    except Exception as e:
        logger.error(f"Ошибка создания заявки: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка создания!")
        user_states.pop(message.chat.id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_order_'))
def handle_delete_order(call):
    try:
        order_id = call.data.split('_')[2]
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM orders WHERE id = %s", (order_id,))
                conn.commit()
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id, "✅ Заявка удалена")
    except Exception as e:
        logger.error(f"Ошибка удаления заявки: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка удаления!")

@bot.callback_query_handler(func=lambda call: call.data.startswith('export_order_'))
def export_order(call):
    try:
        order_id = call.data.split('_')[2]
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT p.name, oi.quantity, oi.price 
                    FROM order_items oi
                    JOIN products p ON oi.product_id = p.id
                    WHERE oi.order_id = %s
                """, (order_id,))
                items = cur.fetchall()

        wb = Workbook()
        ws = wb.active
        ws.append(["Название", "Количество", "Цена"])
        for item in items:
            ws.append(item)
        
        filename = f"order_{order_id}.xlsx"
        wb.save(filename)
        
        with open(filename, 'rb') as f:
            bot.send_document(call.message.chat.id, f, caption="📤 Экспорт заявки")
        
        os.remove(filename)
    except Exception as e:
        logger.error(f"Ошибка экспорта: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка экспорта!")

# Обработчики сканирования
@bot.message_handler(content_types=['photo'], func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'order_scan')
def process_order_scan(message):
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        try:
            processed_image = preprocess_image(downloaded_file)
            compressed_image = compress_image(processed_image)
        except UnidentifiedImageError:
            raise Exception("Неподдерживаемый формат изображения")

        response = requests.post(
            'https://api.ocr.space/parse/image',
            files={'image': ('barcode.jpg', compressed_image, 'image/jpeg')},
            data={'apikey': OCR_API_KEY, 'OCREngine': 2},
            timeout=20
        )
        
        if response.status_code != 200:
            raise Exception(f"Ошибка API: {response.status_code}")
            
        result = response.json()
        
        if result.get('IsErroredOnProcessing', False):
            errors = result.get('ErrorMessage', ['Unknown error'])
            raise Exception(f"Ошибка OCR: {errors}")
            
        parsed_text = result.get('ParsedResults', [{}])[0].get('ParsedText', '')
        numbers = [word.strip() for word in parsed_text.split() if word.strip().isdigit()]
        valid_barcodes = [num for num in numbers if 8 <= len(num) <= 15]
        
        if not valid_barcodes:
            raise Exception("Штрихкод не найден")
            
        barcode = max(valid_barcodes, key=len)

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, price FROM products 
                    WHERE barcode = %s AND telegram_id = %s
                """, (barcode, message.chat.id))
                product = cur.fetchone()
                
                if product:
                    user_states[message.chat.id].update({
                        'step': 'add_quantity',
                        'product_id': product[0],
                        'default_price': product[1]
                    })
                    bot.send_message(message.chat.id, "Введите количество и цену через пробел (по умолчанию 1 и цена из каталога):")
                else:
                    bot.send_message(message.chat.id, f"❌ Товар с кодом {barcode} не найден")
        
    except Exception as e:
        logger.error(f"Ошибка сканирования: {e}")
        bot.send_message(message.chat.id, f"❌ Ошибка: {str(e)}")
        user_states.pop(message.chat.id, None)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    Thread(target=app.run, kwargs={
        'host': '0.0.0.0',
        'port': port,
        'debug': False,
        'use_reloader': False
    }).start()
    
    logger.info("Бот запущен")
    bot.infinity_polling()
