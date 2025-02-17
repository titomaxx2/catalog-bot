import os
import logging
import time
import requests
import psycopg2.pool
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

# Конфигурация БД
DB_URL = os.getenv("DATABASE_URL")
OCR_API_KEY = os.getenv("OCR_API_KEY")
MAX_IMAGE_SIZE_MB = 1
user_states = {}

# Пул соединений
connection_pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=1,
    maxconn=10,
    dsn=DB_URL,
    sslmode="require"
)

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
    
    conn = connection_pool.getconn()
    try:
        with conn.cursor() as cursor:
            for command in commands:
                cursor.execute(command)
        conn.commit()
        logger.info("Таблицы БД успешно созданы")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
        conn.rollback()
        raise
    finally:
        connection_pool.putconn(conn)

init_db()

# Веб-сервер для Render
app = Flask(__name__)

@app.route('/')
def home():
    return "Telegram Bot is Running"

class DBCursor:
    def __init__(self):
        self.conn = connection_pool.getconn()
        self.cursor = self.conn.cursor()
        
    def __enter__(self):
        return self.cursor
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        self.cursor.close()
        connection_pool.putconn(self.conn)

def compress_image(image_data: bytes) -> bytes:
    if len(image_data) <= MAX_IMAGE_SIZE_MB * 1024 * 1024:
        return image_data

    try:
        with Image.open(BytesIO(image_data)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            output = BytesIO()
            quality = 85
            
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

def process_barcode_image(image_data: bytes) -> str:
    try:
        processed_image = preprocess_image(image_data)
        compressed_image = compress_image(processed_image)

        for attempt in range(3):
            try:
                response = requests.post(
                    'https://api.ocr.space/parse/image',
                    files={'image': ('barcode.jpg', compressed_image, 'image/jpeg')},
                    data={'apikey': OCR_API_KEY, 'OCREngine': 2, 'isTable': 'true'},
                    timeout=20
                )
                response.raise_for_status()
                
                result = response.json()
                parsed_text = result['ParsedResults'][0]['ParsedText']
                numbers = [word.strip() for word in parsed_text.split() if word.strip().isdigit()]
                valid_barcodes = [num for num in numbers if 8 <= len(num) <= 15]
                
                if valid_barcodes:
                    return max(valid_barcodes, key=len)
                
            except Exception as e:
                logger.warning(f"Попытка {attempt+1} ошибка: {e}")
                time.sleep(2)
        
        return None
    except Exception as e:
        logger.error(f"Ошибка OCR: {e}")
        return None

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("➕ Добавить товар"),
        KeyboardButton("📦 Каталог"),
        KeyboardButton("📷 Сканировать"),
        KeyboardButton("📝 Заявки"),
        KeyboardButton("📤 Экспорт данных")
    )
    return markup

@bot.message_handler(commands=['start'])
def handle_start(message):
    try:
        bot.send_message(message.chat.id, "🏪 Добро пожаловать в Inventory Bot!", reply_markup=main_menu())
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
            raise ValueError("Неверный формат данных")
        
        barcode, name, price = data
        if not barcode.isdigit():
            raise ValueError("Штрихкод должен содержать только цифры")
        
        price = float(price)
        if price <= 0:
            raise ValueError("Цена должна быть больше нуля")

        with DBCursor() as cursor:
            cursor.execute(
                "INSERT INTO products (telegram_id, barcode, name, price) VALUES (%s, %s, %s, %s)",
                (message.chat.id, barcode, name, price)
            )
        
        user_states[message.chat.id] = {
            'step': 'awaiting_product_image',
            'barcode': barcode
        }
        bot.send_message(message.chat.id, "✅ Данные сохранены! Теперь отправьте фото товара")
        
    except psycopg2.errors.UniqueViolation:
        bot.send_message(message.chat.id, "❌ Товар с таким штрихкодом уже существует!")
        del user_states[message.chat.id]
    except ValueError as e:
        bot.send_message(message.chat.id, f"❌ Ошибка: {e}")
        del user_states[message.chat.id]
    except Exception as e:
        logger.error(f"Ошибка добавления товара: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка сохранения данных!")
        del user_states[message.chat.id]

@bot.message_handler(content_types=['photo'], func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_product_image')
def save_product_image(message):
    try:
        image_id = message.photo[-1].file_id
        barcode = user_states[message.chat.id]['barcode']

        with DBCursor() as cursor:
            cursor.execute(
                "UPDATE products SET image_id = %s WHERE barcode = %s AND telegram_id = %s",
                (image_id, barcode, message.chat.id)
            )
            if cursor.rowcount == 0:
                raise ValueError("Товар не найден в базе данных")

        bot.send_message(message.chat.id, "✅ Фото товара успешно сохранено!", reply_markup=main_menu())
    except Exception as e:
        logger.error(f"Ошибка сохранения фото: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка сохранения фото!")
    finally:
        user_states.pop(message.chat.id, None)

@bot.message_handler(func=lambda m: m.text == "📤 Экспорт данных")
def handle_export(message):
    try:
        with DBCursor() as cursor:
            cursor.execute(
                "SELECT barcode, name, price FROM products WHERE telegram_id = %s",
                (message.chat.id,)
            )
            products = cursor.fetchall()

            cursor.execute(
                "SELECT o.id, o.name, COUNT(oi.id) FROM orders o "
                "LEFT JOIN order_items oi ON o.id = oi.order_id "
                "WHERE o.telegram_id = %s GROUP BY o.id",
                (message.chat.id,)
            )
            orders = cursor.fetchall()

        wb = Workbook()
        
        ws_products = wb.active
        ws_products.title = "Товары"
        ws_products.append(["Штрихкод", "Название", "Цена"])
        for product in products:
            ws_products.append(product)
        
        ws_orders = wb.create_sheet("Заявки")
        ws_orders.append(["ID", "Название заявки", "Количество товаров"])
        for order in orders:
            ws_orders.append(order)
        
        filename = f"export_{message.chat.id}.xlsx"
        wb.save(filename)
        
        with open(filename, 'rb') as f:
            bot.send_document(message.chat.id, f, caption="📤 Ваши данные для экспорта")
        
        os.remove(filename)
        
    except Exception as e:
        logger.error(f"Ошибка экспорта: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при экспорте данных!")

# Запуск приложения
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
            time.sleep(15)
