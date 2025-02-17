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
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                for command in commands:
                    cursor.execute(command)
            conn.commit()
        logger.info("Таблицы БД созданы")
    except Exception as e:
        logger.error(f"Ошибка БД: {e}")
        raise

init_db()

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running"

#region Image Processing
def compress_image(image_data: bytes) -> bytes:
    if len(image_data) <= MAX_IMAGE_SIZE_MB * 1024 * 1024:
        return image_data
    try:
        with Image.open(BytesIO(image_data)) as img:
            if img.mode != 'RGB': img = img.convert('RGB')
            output = BytesIO()
            quality = 85
            while True:
                output.seek(0)
                output.truncate()
                img.save(output, format='JPEG', quality=quality, optimize=True)
                if len(output.getvalue()) <= MAX_IMAGE_SIZE_MB * 1024 * 1024: break
                quality -= 5
                if quality < 50: break
            return output.getvalue()
    except Exception as e:
        logger.error(f"Ошибка сжатия: {e}")
        raise

def preprocess_image(image_data: bytes) -> bytes:
    try:
        image = Image.open(BytesIO(image_data))
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(2.0).convert('L')
        output = BytesIO()
        image.save(output, format='JPEG', quality=85)
        return output.getvalue()
    except Exception as e:
        logger.error(f"Ошибка обработки: {e}")
        raise
#endregion

#region Keyboards
def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("➕ Добавить товар", "📦 Каталог")
    markup.add("📷 Сканировать", "📝 Создать заявку")
    markup.add("📋 Мои заявки", "📤 Экспорт данных")
    return markup

def catalog_menu(product_id: int):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{product_id}"),
        InlineKeyboardButton("❌ Удалить", callback_data=f"delete_{product_id}")
    )
    return markup

def order_actions_menu(order_id: int):
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("🔄 Изменить название", callback_data=f"rename_order_{order_id}"),
        InlineKeyboardButton("📦 Добавить товары", callback_data=f"add_to_order_{order_id}")
    )
    markup.row(
        InlineKeyboardButton("🗑️ Удалить заявку", callback_data=f"delete_order_{order_id}"),
        InlineKeyboardButton("📥 Экспорт в Excel", callback_data=f"export_order_{order_id}")
    )
    return markup
#endregion

#region Product Handlers
@bot.message_handler(func=lambda m: m.text == "➕ Добавить товар")
def add_product_start(message):
    user_states[message.chat.id] = {'step': 'awaiting_product_data'}
    bot.send_message(message.chat.id, "Введите данные в формате:\nШтрихкод | Название | Цена")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_product_data')
def process_product_data(message):
    try:
        data = [x.strip() for x in message.text.split('|')]
        if len(data) != 3: raise ValueError
        barcode, name, price = data
        user_states[message.chat.id] = {
            'step': 'awaiting_product_image',
            'product_data': (barcode, name, float(price))
        }
        bot.send_message(message.chat.id, "Отправьте фото товара 📸")
    except:
        bot.send_message(message.chat.id, "❌ Неверный формат!")
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
        bot.send_message(message.chat.id, "✅ Товар успешно добавлен!", reply_markup=main_menu())
    except psycopg2.errors.UniqueViolation:
        bot.send_message(message.chat.id, "❌ Такой штрихкод уже существует!")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка сохранения!")
    finally:
        user_states.pop(message.chat.id, None)

@bot.callback_query_handler(func=lambda call: call.data.startswith('edit_'))
def edit_product_callback(call):
    try:
        product_id = call.data.split('_')[1]
        user_states[call.message.chat.id] = {
            'step': 'editing_product',
            'product_id': product_id
        }
        bot.send_message(call.message.chat.id, "Введите новые данные в формате:\nНазвание | Цена")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        bot.send_message(call.message.chat.id, "❌ Ошибка редактирования")

@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_'))
def delete_product_callback(call):
    try:
        product_id = call.data.split('_')[1]
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute("DELETE FROM products WHERE id = %s", (product_id,))
                conn.commit()
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, "✅ Товар удален")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        bot.send_message(call.message.chat.id, "❌ Ошибка удаления")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'editing_product')
def process_product_edit(message):
    try:
        product_id = user_states[message.chat.id]['product_id']
        new_name, new_price = [x.strip() for x in message.text.split('|')]
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE products SET name = %s, price = %s WHERE id = %s",
                    (new_name, float(new_price), product_id)
                conn.commit()
        bot.send_message(message.chat.id, "✅ Товар обновлен!", reply_markup=main_menu())
    except:
        bot.send_message(message.chat.id, "❌ Неверный формат данных!")
    finally:
        user_states.pop(message.chat.id, None)
#endregion

#region Catalog
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
            bot.send_message(message.chat.id, "🛍️ Ваш каталог пуст")
            return

        for product in products:
            product_id, barcode, name, price, image_id = product
            caption = f"📦 {name}\n🔖 {barcode}\n💰 {price} руб."
            if image_id:
                bot.send_photo(message.chat.id, image_id, caption=caption, reply_markup=catalog_menu(product_id))
            else:
                bot.send_message(message.chat.id, caption, reply_markup=catalog_menu(product_id))
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки каталога")
#endregion

#region Orders
@bot.message_handler(func=lambda m: m.text == "📝 Создать заявку")
def create_order_start(message):
    user_states[message.chat.id] = {'step': 'creating_order'}
    bot.send_message(message.chat.id, "Введите название для новой заявки:")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'creating_order')
def process_order_creation(message):
    try:
        order_name = message.text.strip()
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO orders (telegram_id, name) VALUES (%s, %s) RETURNING id",
                    (message.chat.id, order_name)
                )
                order_id = cursor.fetchone()[0]
                conn.commit()
        user_states[message.chat.id] = {'step': 'adding_to_order', 'order_id': order_id}
        bot.send_message(
            message.chat.id,
            "✅ Заявка создана! Выберите действие:",
            reply_markup=order_actions_menu(order_id)
        )
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка создания заявки")

@bot.callback_query_handler(func=lambda call: call.data.startswith('add_to_order_'))
def add_to_order_callback(call):
    order_id = call.data.split('_')[-1]
    user_states[call.message.chat.id] = {
        'step': 'awaiting_order_barcode',
        'order_id': order_id
    }
    bot.send_message(call.message.chat.id, "📷 Отправьте фото штрихкода или введите последние 4 цифры:")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_order_barcode')
def process_order_barcode(message):
    # Реализация обработки штрихкода для заявки
    pass  # Аналогично предыдущей реализации с привязкой к order_id

# Остальные обработчики заявок аналогично
#endregion

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    Thread(target=app.run, kwargs={'host':'0.0.0.0','port':port}).start()
    logger.info("Бот запущен")
    while True:
        try: bot.polling(none_stop=True)
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            time.sleep(10)
