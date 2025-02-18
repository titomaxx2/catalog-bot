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

# Инициализация бота
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(TOKEN, num_threads=5)

# Конфигурация
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
    
    for attempt in range(3):
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    for command in commands:
                        cursor.execute(command)
                conn.commit()
            logger.info("Таблицы БД успешно созданы")
            return
        except Exception as e:
            logger.error(f"Ошибка инициализации БД (попытка {attempt+1}): {e}")
            time.sleep(2)
    raise Exception("Не удалось подключиться к БД")

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
                try:
                    img.save(output, format='JPEG', quality=quality, optimize=True)
                except OSError:
                    img = img.convert('RGB')
                    img.save(output, format='JPEG', quality=quality, optimize=True)
                
                if output.getbuffer().nbytes <= MAX_IMAGE_SIZE_MB * 1024 * 1024:
                    break
                quality -= 5
            
            return output.getvalue()
    except UnidentifiedImageError:
        logger.error("Невозможно определить формат изображения")
        raise
    except Exception as e:
        logger.error(f"Ошибка сжатия: {e}")
        raise

def preprocess_image(image_data: bytes) -> bytes:
    try:
        with Image.open(BytesIO(image_data)) as img:
            enhancer = ImageEnhance.Contrast(img)
            return enhancer.enhance(2.0).convert('L').tobytes()
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
        InlineKeyboardButton("📤 Выгрузить", callback_data=f"export_order_{order_id}")
    )

@bot.message_handler(commands=['start'])
def handle_start(message):
    try:
        bot.send_message(message.chat.id, "🏪 Добро пожаловать!", reply_markup=main_menu())
    except Exception as e:
        logger.error(f"Ошибка в /start: {e}")

@bot.message_handler(func=lambda m: m.text == "➕ Добавить товар")
def start_add_product(message):
    try:
        user_states[message.chat.id] = {'step': 'await_product_data', 'time': time.time()}
        bot.send_message(message.chat.id, "📝 Введите данные в формате:\nШтрихкод | Название | Цена")
    except Exception as e:
        logger.error(f"Ошибка добавления товара: {e}")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'await_product_data')
def process_product_data(message):
    try:
        data = [x.strip() for x in message.text.split('|')]
        if len(data) != 3:
            raise ValueError("Неверный формат данных")
        
        barcode, name, price = data
        user_states[message.chat.id] = {
            'step': 'await_product_image',
            'product_data': (barcode, name, float(price)),
            'time': time.time()
        }
        bot.send_message(message.chat.id, "📷 Отправьте фото товара")
    except Exception as e:
        logger.error(f"Ошибка обработки данных: {e}")
        bot.send_message(message.chat.id, "❌ Неверный формат! Используйте: Штрихкод | Название | Цена")
        user_states.pop(message.chat.id, None)

@bot.message_handler(content_types=['photo'], func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'await_product_image')
def process_product_image(message):
    try:
        product_data = user_states[message.chat.id]['product_data']
        image_id = message.photo[-1].file_id
        
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO products (telegram_id, barcode, name, price, image_id)
                    VALUES (%s, %s, %s, %s, %s)
                """, (message.chat.id, *product_data, image_id))
                conn.commit()
        
        bot.send_message(message.chat.id, "✅ Товар добавлен!", reply_markup=main_menu())
    except psycopg2.errors.UniqueViolation:
        bot.send_message(message.chat.id, "❌ Штрихкод уже существует!")
    except Exception as e:
        logger.error(f"Ошибка сохранения товара: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка сохранения товара!")
    finally:
        user_states.pop(message.chat.id, None)

@bot.message_handler(func=lambda m: m.text == "📦 Каталог")
def show_catalog(message):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, barcode, name, price, image_id 
                    FROM products 
                    WHERE telegram_id = %s
                """, (message.chat.id,))
                products = cur.fetchall()

        if not products:
            bot.send_message(message.chat.id, "🛒 Каталог пуст")
            return

        for product in products:
            product_id, barcode, name, price, image_id = product
            caption = f"📦 {name}\n🔖 {barcode}\n💰 {price} руб."
            if image_id:
                bot.send_photo(message.chat.id, image_id, caption, reply_markup=catalog_menu(product_id))
            else:
                bot.send_message(message.chat.id, caption, reply_markup=catalog_menu(product_id))
    except Exception as e:
        logger.error(f"Ошибка загрузки каталога: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки каталога")

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
    try:
        user_states[message.chat.id] = {'step': 'main_scan', 'time': time.time()}
        bot.send_message(message.chat.id, "📷 Отправьте фото штрихкода")
    except Exception as e:
        logger.error(f"Ошибка сканирования: {e}")

@bot.message_handler(content_types=['photo'], func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'main_scan')
def process_main_scan(message):
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
        
        if response.status_code != 200:
            raise Exception(f"Ошибка OCR API: {response.status_code}")
            
        result = response.json()
        
        if result.get('IsErroredOnProcessing', False):
            raise Exception(f"Ошибка OCR: {result.get('ErrorMessage', 'Неизвестная ошибка')}")
            
        parsed_results = result.get('ParsedResults', [])
        if not parsed_results:
            raise Exception("Нет результатов распознавания")
            
        parsed_text = parsed_results[0].get('ParsedText', '')
        numbers = [word.strip() for word in parsed_text.split() if word.strip().isdigit()]
        valid_barcodes = [num for num in numbers if 8 <= len(num) <= 15]
        
        if not valid_barcodes:
            raise Exception("Не найдено подходящих штрихкодов")
            
        barcode = max(valid_barcodes, key=len)

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT name, price FROM products 
                    WHERE barcode = %s AND telegram_id = %s
                """, (barcode, message.chat.id))
                product = cur.fetchone()

        if product:
            response_text = f"✅ Найден товар:\n📦 {product[0]}\n💰 {product[1]} руб."
        else:
            response_text = f"❌ Товар не найден\nРаспознанный штрихкод: {barcode}"

        bot.send_message(message.chat.id, response_text)
        
    except Exception as e:
        logger.error(f"Ошибка сканирования: {e}")
        bot.send_message(message.chat.id, f"❌ Ошибка сканирования: {str(e)}")
    finally:
        user_states.pop(message.chat.id, None)

@bot.message_handler(func=lambda m: m.text == "📋 Список заявок")
def list_orders(message):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, name, created_at 
                    FROM orders 
                    WHERE telegram_id = %s
                    ORDER BY created_at DESC
                """, (message.chat.id,))
                orders = cur.fetchall()

        if not orders:
            bot.send_message(message.chat.id, "📋 Нет активных заявок")
            return

        for order in orders:
            order_id, name, created_at = order
            bot.send_message(
                message.chat.id,
                f"📋 Заявка: {name}\n🕒 {created_at.strftime('%d.%m.%Y %H:%M')}",
                reply_markup=order_menu(order_id)
            )
    except Exception as e:
        logger.error(f"Ошибка списка заявок: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки заявок")

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
