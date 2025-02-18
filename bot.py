import os
import logging
import time
import requests
import psycopg2
import telebot
from flask import Flask
from PIL import Image, ImageEnhance
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
    if len(image_data) <= MAX_IMAGE_SIZE_MB * 1024 * 1024:
        return image_data
    try:
        with Image.open(BytesIO(image_data)) as img:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            output = BytesIO()
            quality = 85
            while quality >= 50:
                output.seek(0)
                img.save(output, format='JPEG', quality=quality, optimize=True)
                if output.getbuffer().nbytes <= MAX_IMAGE_SIZE_MB * 1024 * 1024:
                    break
                quality -= 5
            return output.getvalue()
    except Exception as e:
        logger.error(f"Ошибка сжатия: {e}")
        return image_data

def preprocess_image(image_data: bytes) -> bytes:
    try:
        image = Image.open(BytesIO(image_data))
        enhancer = ImageEnhance.Contrast(image)
        return enhancer.enhance(2.0).convert('L').tobytes()
    except Exception as e:
        logger.error(f"Ошибка обработки: {e}")
        return image_data

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("➕ Добавить товар")
    markup.row("📦 Каталог", "📤 Экспорт")
    markup.row("📷 Сканировать штрихкод")
    markup.row("📝 Создать заявку", "📋 Список заявок")
    return markup

def catalog_menu(product_id: int):
    return InlineKeyboardMarkup().row(
        InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{product_id}"),
        InlineKeyboardButton("❌ Удалить", callback_data=f"delete_{product_id}")
    )

def order_menu(order_id: int):
    return InlineKeyboardMarkup().row(
        InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_order_{order_id}"),
        InlineKeyboardButton("📤 Выгрузить", callback_data=f"export_order_{order_id}")
    )

@bot.message_handler(commands=['start'])
def handle_start(message):
    bot.send_message(message.chat.id, "🏪 Добро пожаловать!", reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "📤 Экспорт")
def export_catalog(message):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT barcode, name, price 
                    FROM products 
                    WHERE telegram_id = %s
                """, (message.chat.id,))
                products = cur.fetchall()

        wb = Workbook()
        ws = wb.active
        ws.append(["Штрихкод", "Название", "Цена"])
        for product in products:
            ws.append(product)
        
        filename = f"catalog_{message.chat.id}.xlsx"
        wb.save(filename)
        
        with open(filename, 'rb') as f:
            bot.send_document(message.chat.id, f, caption="📤 Экспорт каталога")
        
        os.remove(filename)
    except Exception as e:
        logger.error(f"Ошибка экспорта: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка экспорта!")

@bot.message_handler(func=lambda m: m.text == "📷 Сканировать штрихкод")
def handle_scan(message):
    user_states[message.chat.id] = {'step': 'main_scan'}
    bot.send_message(message.chat.id, "📷 Отправьте фото штрихкода")

@bot.message_handler(content_types=['photo'], func=lambda m: user_states.get(m.chat.id, {}).get('step') in ['main_scan', 'order_scan'])
def process_barcode_scan(message):
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        processed_image = preprocess_image(downloaded_file)
        compressed_image = compress_image(processed_image)

        response = requests.post(
            'https://api.ocr.space/parse/image',
            files={'image': ('barcode.jpg', compressed_image, 'image/jpeg')},
            data={'apikey': OCR_API_KEY, 'OCREngine': 2},
            timeout=15
        )
        
        result = response.json()
        parsed_text = result.get('ParsedResults', [{}])[0].get('ParsedText', '')
        numbers = [word.strip() for word in parsed_text.split() if word.strip().isdigit()]
        valid_barcodes = [num for num in numbers if 8 <= len(num) <= 15]
        barcode = max(valid_barcodes, key=len) if valid_barcodes else None

        if barcode:
            if user_states[message.chat.id]['step'] == 'main_scan':
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            SELECT name, price FROM products 
                            WHERE barcode = %s AND telegram_id = %s
                        """, (barcode, message.chat.id))
                        product = cur.fetchone()
                        if product:
                            response_text = f"✅ Штрихкод: {barcode}\n📦 {product[0]}\n💰 {product[1]} руб."
                        else:
                            response_text = f"❌ Товар не найден. Распознанный штрихкод: {barcode}"
                bot.send_message(message.chat.id, response_text)
                del user_states[message.chat.id]
            
            elif user_states[message.chat.id]['step'] == 'order_scan':
                user_states[message.chat.id].update({
                    'step': 'add_to_order',
                    'barcode': barcode
                })
                bot.send_message(message.chat.id, "Введите количество и цену через пробел (по умолчанию 1 и цена из каталога):")
        
        else:
            bot.send_message(message.chat.id, "❌ Штрихкод не распознан")
            del user_states[message.chat.id]

    except Exception as e:
        logger.error(f"Ошибка сканирования: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка обработки изображения")
        del user_states[message.chat.id]

@bot.message_handler(func=lambda m: m.text == "📝 Создать заявку")
def create_order(message):
    user_states[message.chat.id] = {'step': 'create_order_name'}
    bot.send_message(message.chat.id, "📝 Введите название заявки:")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'create_order_name')
def process_order_name(message):
    try:
        order_name = message.text.strip()
        if not order_name:
            raise ValueError()
        
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO orders (telegram_id, name)
                    VALUES (%s, %s) RETURNING id
                """, (message.chat.id, order_name))
                order_id = cur.fetchone()[0]
        
        user_states[message.chat.id] = {
            'step': 'order_management',
            'order_id': order_id
        }
        markup = ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("🔍 Сканировать штрихкод", "⌨️ Ввести 4 цифры", "🔙 Завершить")
        bot.send_message(message.chat.id, "Выберите действие:", reply_markup=markup)
    except:
        bot.send_message(message.chat.id, "❌ Ошибка создания!")
        del user_states[message.chat.id]

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'order_management')
def handle_order_action(message):
    if message.text == "🔍 Сканировать штрихкод":
        user_states[message.chat.id]['step'] = 'order_scan'
        bot.send_message(message.chat.id, "📷 Отправьте фото штрихкода")
    elif message.text == "⌨️ Ввести 4 цифры":
        user_states[message.chat.id]['step'] = 'order_input'
        bot.send_message(message.chat.id, "Введите последние 4 цифры штрихкода:")
    elif message.text == "🔙 Завершить":
        del user_states[message.chat.id]
        bot.send_message(message.chat.id, "✅ Заявка сохранена", reply_markup=main_menu())

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'order_input')
def process_order_input(message):
    try:
        last_digits = message.text.strip()
        if len(last_digits) != 4 or not last_digits.isdigit():
            raise ValueError()
        
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, price FROM products 
                    WHERE RIGHT(barcode, 4) = %s AND telegram_id = %s
                """, (last_digits, message.chat.id))
                product = cur.fetchone()
                
                if product:
                    user_states[message.chat.id].update({
                        'step': 'add_to_order',
                        'product_id': product[0],
                        'default_price': product[1]
                    })
                    bot.send_message(message.chat.id, "Введите количество и цену через пробел (по умолчанию 1 и цена из каталога):")
                else:
                    bot.send_message(message.chat.id, "❌ Товар не найден")
    except:
        bot.send_message(message.chat.id, "❌ Неверный формат!")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'add_to_order')
def process_add_to_order(message):
    try:
        parts = message.text.split()
        quantity = int(parts[0]) if len(parts) > 0 else 1
        price = float(parts[1]) if len(parts) > 1 else user_states[message.chat.id].get('default_price')
        
        order_id = user_states[message.chat.id]['order_id']
        product_id = user_states[message.chat.id].get('product_id')
        barcode = user_states[message.chat.id].get('barcode')

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if barcode:
                    cur.execute("""
                        SELECT id, price FROM products 
                        WHERE barcode = %s AND telegram_id = %s
                    """, (barcode, message.chat.id))
                    product = cur.fetchone()
                    if not product:
                        raise ValueError()
                    product_id = product[0]
                    price = price or product[1]

                cur.execute("""
                    INSERT INTO order_items (order_id, product_id, quantity, price)
                    VALUES (%s, %s, %s, %s)
                """, (order_id, product_id, quantity, price))
        
        bot.send_message(message.chat.id, "✅ Товар добавлен в заявку!")
        user_states[message.chat.id]['step'] = 'order_management'
    except Exception as e:
        logger.error(f"Ошибка добавления: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка добавления товара")

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
