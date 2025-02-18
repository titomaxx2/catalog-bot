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
            created_at TIMESTAMP DEFAULT NOW()
        )"""
    )
    try:
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                for command in commands:
                    cursor.execute(command)
        logger.info("Таблицы БД успешно созданы")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
        raise

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
            if img.mode != 'RGB': img = img.convert('RGB')
            output = BytesIO()
            quality = 85
            while True:
                img.save(output, format='JPEG', quality=quality, optimize=True)
                if output.getbuffer().nbytes <= MAX_IMAGE_SIZE_MB * 1024 * 1024:
                    break
                quality -= 5
                if quality < 50:
                    break
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

@bot.message_handler(func=lambda m: m.text == "➕ Добавить товар")
def start_add_product(message):
    user_states[message.chat.id] = {'step': 'await_product_data'}
    bot.send_message(message.chat.id, "📝 Введите данные в формате:\nШтрихкод | Название | Цена")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'await_product_data')
def process_product_data(message):
    try:
        barcode, name, price = map(str.strip, message.text.split('|', 2))
        user_states[message.chat.id] = {
            'step': 'await_product_image',
            'product_data': (barcode, name, float(price))
        }
        bot.send_message(message.chat.id, "📷 Отправьте фото товара")
    except:
        bot.send_message(message.chat.id, "❌ Неверный формат!")
        user_states.pop(message.chat.id, None)

@bot.message_handler(content_types=['photo'], func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'await_product_image')
def process_product_image(message):
    try:
        barcode, name, price = user_states[message.chat.id]['product_data']
        image_id = message.photo[-1].file_id
        
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO products (telegram_id, barcode, name, price, image_id)
                    VALUES (%s, %s, %s, %s, %s)
                """, (message.chat.id, barcode, name, price, image_id))
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
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, barcode, name, price, image_id 
                    FROM products WHERE telegram_id = %s
                """, (message.chat.id,))
                products = cur.fetchall()

        for product in products:
            product_id, barcode, name, price, image_id = product
            caption = f"📦 {name}\n🔖 {barcode}\n💰 {price} руб."
            if image_id:
                bot.send_photo(message.chat.id, image_id, caption, reply_markup=catalog_menu(product_id))
            else:
                bot.send_message(message.chat.id, caption, reply_markup=catalog_menu(product_id))
    except Exception as e:
        logger.error(f"Ошибка каталога: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки")

@bot.callback_query_handler(func=lambda call: call.data.startswith('edit_'))
def handle_edit_product(call):
    try:
        product_id = call.data.split('_')[1]
        user_states[call.message.chat.id] = {'step': 'edit_product', 'product_id': product_id}
        bot.send_message(call.message.chat.id, "Введите новые данные:\nНазвание | Цена")
    except Exception as e:
        logger.error(f"Ошибка редактирования: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка!")

@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_'))
def handle_delete_product(call):
    try:
        product_id = call.data.split('_')[1]
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM products WHERE id = %s", (product_id,))
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id, "✅ Товар удален")
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка удаления!")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'edit_product')
def process_edit_product(message):
    try:
        product_id = user_states[message.chat.id]['product_id']
        name, price = map(str.strip, message.text.split('|', 1))
        
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE products 
                    SET name = %s, price = %s 
                    WHERE id = %s
                """, (name, float(price), product_id))
        
        bot.send_message(message.chat.id, "✅ Товар обновлен!", reply_markup=main_menu())
        user_states.pop(message.chat.id, None)
    except:
        bot.send_message(message.chat.id, "❌ Неверный формат!")

@bot.message_handler(func=lambda m: m.text == "📝 Создать заявку")
def create_order(message):
    user_states[message.chat.id] = {'step': 'await_order_name'}
    bot.send_message(message.chat.id, "📝 Введите название заявки:")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'await_order_name')
def process_order_name(message):
    try:
        order_name = message.text.strip()
        if not order_name:
            raise ValueError()
        
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO orders (telegram_id, name)
                    VALUES (%s, %s) RETURNING id
                """, (message.chat.id, order_name))
                order_id = cur.fetchone()[0]
        
        user_states[message.chat.id] = {'step': 'order_manage', 'order_id': order_id}
        markup = ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("🔍 Сканировать штрихкод", "⌨️ Ввести 4 цифры", "🔙 Назад")
        bot.send_message(message.chat.id, "Выберите действие:", reply_markup=markup)
    except:
        bot.send_message(message.chat.id, "❌ Ошибка создания!")
        user_states.pop(message.chat.id, None)

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'order_manage')
def handle_order_action(message):
    if message.text == "🔍 Сканировать штрихкод":
        user_states[message.chat.id]['step'] = 'await_barcode_scan'
        bot.send_message(message.chat.id, "📷 Отправьте фото штрихкода")
    elif message.text == "⌨️ Ввести 4 цифры":
        user_states[message.chat.id]['step'] = 'await_barcode_input'
        bot.send_message(message.chat.id, "Введите последние 4 цифры штрихкода:")
    elif message.text == "🔙 Назад":
        user_states.pop(message.chat.id, None)
        bot.send_message(message.chat.id, "🔙 Главное меню", reply_markup=main_menu())

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'await_barcode_input')
def process_barcode_input(message):
    try:
        last_digits = message.text.strip()
        if len(last_digits) != 4 or not last_digits.isdigit():
            raise ValueError()
        
        order_id = user_states[message.chat.id]['order_id']
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id FROM products 
                    WHERE RIGHT(barcode, 4) = %s AND telegram_id = %s
                """, (last_digits, message.chat.id))
                product = cur.fetchone()
                
                if product:
                    cur.execute("""
                        INSERT INTO order_items (order_id, product_id)
                        VALUES (%s, %s)
                    """, (order_id, product[0]))
                    bot.send_message(message.chat.id, "✅ Товар добавлен в заявку!")
                else:
                    bot.send_message(message.chat.id, "❌ Товар не найден")
    except:
        bot.send_message(message.chat.id, "❌ Неверный формат!")
    finally:
        user_states[message.chat.id]['step'] = 'order_manage'

@bot.message_handler(func=lambda m: m.text == "📋 Список заявок")
def list_orders(message):
    try:
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, name, created_at 
                    FROM orders 
                    WHERE telegram_id = %s
                """, (message.chat.id,))
                orders = cur.fetchall()

        for order in orders:
            order_id, name, created_at = order
            bot.send_message(
                message.chat.id,
                f"📋 Заявка: {name}\n🕒 {created_at.strftime('%d.%m.%Y %H:%M')}",
                reply_markup=order_menu(order_id)
            )
    except Exception as e:
        logger.error(f"Ошибка списка заявок: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки")

@bot.callback_query_handler(func=lambda call: call.data.startswith('edit_order_'))
def edit_order(call):
    try:
        order_id = call.data.split('_')[2]
        user_states[call.message.chat.id] = {'step': 'edit_order', 'order_id': order_id}
        markup = ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add("📦 Список товаров", "➕ Добавить товар", "❌ Удалить товар")
        markup.add("🔙 Назад")
        bot.send_message(call.message.chat.id, "✏️ Редактирование заявки:", reply_markup=markup)
    except Exception as e:
        logger.error(f"Ошибка редактирования заявки: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка!")

@bot.callback_query_handler(func=lambda call: call.data.startswith('export_order_'))
def export_order(call):
    try:
        order_id = call.data.split('_')[2]
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT p.name, p.price, oi.quantity 
                    FROM order_items oi
                    JOIN products p ON oi.product_id = p.id
                    WHERE oi.order_id = %s
                """, (order_id,))
                items = cur.fetchall()

        wb = Workbook()
        ws = wb.active
        ws.append(["Название", "Цена", "Количество"])
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

@bot.message_handler(content_types=['photo'], func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'await_barcode_scan')
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
            timeout=10
        )
        
        result = response.json()
        parsed_text = result['ParsedResults'][0]['ParsedText']
        numbers = [word.strip() for word in parsed_text.split() if word.strip().isdigit()]
        valid_barcodes = [num for num in numbers if 8 <= len(num) <= 15]
        barcode = max(valid_barcodes, key=len) if valid_barcodes else None

        if barcode:
            order_id = user_states[message.chat.id]['order_id']
            with psycopg2.connect(DB_URL, sslmode="require") as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id FROM products 
                        WHERE barcode = %s AND telegram_id = %s
                    """, (barcode, message.chat.id))
                    product = cur.fetchone()
                    
                    if product:
                        cur.execute("""
                            INSERT INTO order_items (order_id, product_id)
                            VALUES (%s, %s)
                        """, (order_id, product[0]))  # Исправлено здесь
                        bot.send_message(message.chat.id, "✅ Товар добавлен в заявку!")
                    else:
                        bot.send_message(message.chat.id, f"❌ Товар с кодом {barcode} не найден")
        else:
            bot.send_message(message.chat.id, "❌ Штрих-код не распознан")
    except Exception as e:
        logger.error(f"Ошибка сканирования: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка обработки изображения")
    finally:
        user_states[message.chat.id]['step'] = 'order_manage'

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
