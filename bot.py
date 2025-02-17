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

# Инициализация бота
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

# Конфигурация
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
            price FLOAT NOT NULL,
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

def compress_image(image_data: bytes) -> bytes:
    """Сжимает изображение только если размер превышает 1 МБ"""
    if len(image_data) <= MAX_IMAGE_SIZE_MB * 1024 * 1024:
        logger.info("Изображение не требует сжатия")
        return image_data

    try:
        with Image.open(BytesIO(image_data)) as img:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
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
                    break
            
            logger.info(f"Изображение сжато до {len(output.getvalue())//1024} KB")
            return output.getvalue()
    except Exception as e:
        logger.error(f"Ошибка сжатия: {e}")
        raise

def preprocess_image(image_data: bytes) -> bytes:
    """Предварительная обработка изображения"""
    try:
        image = Image.open(BytesIO(image_data))
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(2.0)
        image = image.convert('L')
        
        output = BytesIO()
        image.save(output, format='JPEG', quality=85)
        output.seek(0)
        
        return output.getvalue()
    except Exception as e:
        logger.error(f"Ошибка обработки изображения: {e}")
        raise

# Клавиатуры
def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("➕ Добавить товар"))
    markup.add(KeyboardButton("📦 Каталог"), KeyboardButton("📤 Экспорт"))
    markup.add(KeyboardButton("📷 Сканировать штрихкод"))
    markup.add(KeyboardButton("📝 Создать заявку"), KeyboardButton("📋 Список заявок"))
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
        InlineKeyboardButton("📤 Выгрузить", callback_data=f"export_order:{order_id}")
    )
    return markup

# Обработчики сообщений
@bot.message_handler(commands=['start'])
def handle_start(message):
    try:
        logger.info(f"Новый пользователь: {message.chat.id}")
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
        user_states[message.chat.id] = {
            'step': 'awaiting_product_image',
            'product_data': (barcode, name, float(price))
        }
        bot.send_message(message.chat.id, "📷 Отправьте фото товара")
        
    except Exception as e:
        logger.error(f"Ошибка обработки: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка формата!")
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
        
    except psycopg2.errors.UniqueViolation:
        bot.send_message(message.chat.id, "❌ Штрихкод уже существует!")
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка сохранения!")
    finally:
        user_states.pop(message.chat.id, None)

@bot.message_handler(func=lambda m: m.text == "📦 Каталог")
def show_catalog(message):
    try:
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
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

@bot.message_handler(func=lambda m: m.text == "📷 Сканировать штрихкод")
def handle_scan(message):
    user_states[message.chat.id] = {'step': 'awaiting_barcode_scan'}
    bot.send_message(message.chat.id, "📷 Отправьте фото штрихкода", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add("Отмена"))

@bot.message_handler(content_types=['photo'], func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_barcode_scan')
def process_barcode_scan(message):
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        processed_image = preprocess_image(downloaded_file)
        compressed_image = compress_image(processed_image)

        max_retries = 3
        barcode = None
        
        for attempt in range(max_retries):
            try:
                response = requests.post(
                    'https://api.ocr.space/parse/image',
                    files={'image': ('barcode.jpg', compressed_image, 'image/jpeg')},
                    data={'apikey': OCR_API_KEY, 'OCREngine': 2},
                    timeout=30
                )
                
                result = response.json()
                parsed_text = result['ParsedResults'][0]['ParsedText']
                cleaned_text = parsed_text.replace("\n", "").replace(" ", "")
                numbers = [word.strip() for word in cleaned_text.split() if word.strip().isdigit()]
                valid_barcodes = [num for num in numbers if 8 <= len(num) <= 15]
                
                if valid_barcodes:
                    barcode = max(valid_barcodes, key=len)
                    break

            except Exception as e:
                logger.error(f"Попытка {attempt+1} ошибка: {e}")
                if attempt < max_retries - 1:
                    time.sleep(5)

        if barcode:
            if 'order_id' in user_states.get(message.chat.id, {}):
                # Добавление в заявку
                order_id = user_states[message.chat.id]['order_id']
                with psycopg2.connect(DB_URL, sslmode="require") as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            "SELECT id FROM products WHERE barcode = %s AND telegram_id = %s",
                            (barcode, message.chat.id)
                        )
                        product_id = cursor.fetchone()
                        if product_id:
                            cursor.execute(
                                "INSERT INTO order_items (order_id, product_id) VALUES (%s, %s)",
                                (order_id, product_id[0])
                            )
                            conn.commit()
                            bot.send_message(message.chat.id, "✅ Товар добавлен в заявку!", reply_markup=main_menu())
                        else:
                            bot.send_message(message.chat.id, "❌ Товар не найден")
            else:
                # Обычный поиск
                with psycopg2.connect(DB_URL, sslmode="require") as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            "SELECT name, price FROM products WHERE barcode = %s AND telegram_id = %s",
                            (barcode, message.chat.id)
                        )
                        product = cursor.fetchone()
                
                if product:
                    response_text = f"✅ Штрихкод: {barcode}\n📦 {product[0]}\n💰 {product[1]} руб."
                else:
                    response_text = f"❌ Товар не найден. Распознанный штрих-код: {barcode}"  # Изменено здесь
        else:
            response_text = "❌ Штрихкод не распознан"

        bot.send_message(message.chat.id, response_text, reply_markup=main_menu())

    except Exception as e:
        logger.error(f"Ошибка сканирования: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка сканирования")
    finally:
        user_states.pop(message.chat.id, None)

# ... (остальные функции без изменений)

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
            bot.polling(none_stop=True)
        except Exception as e:
            logger.error(f"Ошибка подключения: {e}")
            time.sleep(10)
