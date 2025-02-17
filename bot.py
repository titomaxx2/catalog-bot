import logging
import time
import requests
import psycopg2
import telebot
import json
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
        InlineKeyboardButton("📤 Выгрузить", callback_data=f"export_order:{order_id}"),
        InlineKeyboardButton("❌ Удалить", callback_data=f"delete_order:{order_id}")
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
                            bot.send_message(message.chat.id, f"❌ Товар с штрихкодом {barcode} не найден")
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
                    response_text = f"❌ Товар с штрихкодом {barcode} не найден"
        else:
            response_text = "❌ Штрихкод не распознан"

        bot.send_message(message.chat.id, response_text, reply_markup=main_menu())

    except Exception as e:
        logger.error(f"Ошибка сканирования: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка сканирования")
    finally:
        user_states.pop(message.chat.id, None)

@bot.message_handler(func=lambda m: m.text == "📝 Создать заявку")
def create_order(message):
    user_states[message.chat.id] = {'step': 'awaiting_order_name'}
    bot.send_message(message.chat.id, "📝 Введите название заявки:")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_order_name')
def process_order_name(message):
    try:
        order_name = message.text.strip()
        if not order_name:
            raise ValueError("Пустое название")
        
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO orders (telegram_id, name) VALUES (%s, %s) RETURNING id",
                    (message.chat.id, order_name)
                )
                order_id = cursor.fetchone()[0]
                conn.commit()
        
        user_states[message.chat.id] = {
            'step': 'awaiting_order_action',
            'order_id': order_id
        }
        
        markup = ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add(KeyboardButton("🔍 Сканировать штрихкод"))
        markup.add(KeyboardButton("⌨️ Ввести 4 цифры"))
        markup.add(KeyboardButton("🔙 Назад"))
        
        bot.send_message(message.chat.id, "Выберите способ добавления товара:", reply_markup=markup)

    except Exception as e:
        logger.error(f"Ошибка создания заявки: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка создания")
        del user_states[message.chat.id]

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_order_action')
def handle_order_action(message):
    order_id = user_states[message.chat.id]['order_id']
    
    if message.text == "🔍 Сканировать штрихкод":
        user_states[message.chat.id] = {'step': 'awaiting_barcode_scan', 'order_id': order_id}
        bot.send_message(message.chat.id, "📷 Отправьте фото штрихкода")
        
    elif message.text == "⌨️ Ввести 4 цифры":
        user_states[message.chat.id] = {'step': 'awaiting_order_barcode', 'order_id': order_id}
        bot.send_message(message.chat.id, "Введите последние 4 цифры штрихкода:")
        
    elif message.text == "🔙 Назад":
        user_states.pop(message.chat.id, None)
        bot.send_message(message.chat.id, "🔙 Отменено", reply_markup=main_menu())

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_order_barcode')
def process_order_barcode(message):
    try:
        last_four = message.text.strip()
        if not last_four.isdigit() or len(last_four) != 4:
            raise ValueError("Некорректный ввод")
        
        order_id = user_states[message.chat.id]['order_id']
        
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM products WHERE barcode LIKE %s AND telegram_id = %s",
                    (f"%{last_four}", message.chat.id)
                )
                product_id = cursor.fetchone()
                
                if product_id:
                    cursor.execute(
                        "INSERT INTO order_items (order_id, product_id) VALUES (%s, %s)",
                        (order_id, product_id[0])
                    )
                    conn.commit()
                    bot.send_message(message.chat.id, "✅ Товар добавлен!", reply_markup=main_menu())
                else:
                    bot.send_message(message.chat.id, "❌ Товар не найден")
        
    except Exception as e:
        logger.error(f"Ошибка добавления: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка ввода")
    finally:
        user_states.pop(message.chat.id, None)

@bot.message_handler(func=lambda m: m.text == "📋 Список заявок")
def list_orders(message):
    try:
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, name, created_at FROM orders WHERE telegram_id = %s",
                    (message.chat.id,)
                )
                orders = cursor.fetchall()
        
        if not orders:
            bot.send_message(message.chat.id, "📋 Нет заявок")
            return
        
        for order in orders:
            order_id, name, created_at = order
            bot.send_message(
                message.chat.id,
                f"📋 {name}\n🕒 {created_at.strftime('%Y-%m-%d %H:%M')}",
                reply_markup=order_menu(order_id)
            )
                
    except Exception as e:
        logger.error(f"Ошибка списка заявок: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки")

@bot.callback_query_handler(func=lambda call: call.data.startswith(('edit_order:', 'export_order:', 'delete_order:')))
def handle_order_callback(call):
    try:
        action, order_id = call.data.split(':')
        order_id = int(order_id)
        
        if action == 'edit_order':
            user_states[call.message.chat.id] = {
                'step': 'edit_order',
                'order_id': order_id
            }
            
            markup = ReplyKeyboardMarkup(resize_keyboard=True)
            markup.add(KeyboardButton("📦 Список товаров"))
            markup.add(KeyboardButton("➕ Добавить товар"))
            markup.add(KeyboardButton("❌ Удалить товар"))
            markup.add(KeyboardButton("🔙 Назад"))
            
            bot.send_message(call.message.chat.id, "✏️ Редактирование заявки:", reply_markup=markup)
            
        elif action == 'export_order':
            with psycopg2.connect(DB_URL, sslmode="require") as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT p.name, p.price, oi.quantity FROM order_items oi "
                        "JOIN products p ON oi.product_id = p.id "
                        "WHERE oi.order_id = %s",
                        (order_id,)
                    )
                    items = cursor.fetchall()
            
            wb = Workbook()
            ws = wb.active
            ws.append(["Название", "Цена", "Количество"])
            for item in items:
                ws.append(item)
            
            filename = f"order_{order_id}.xlsx"
            wb.save(filename)
            
            with open(filename, 'rb') as f:
                bot.send_document(call.message.chat.id, f, caption="📤 Ваша заявка")
            
            os.remove(filename)
            
        elif action == 'delete_order':
            with psycopg2.connect(DB_URL, sslmode="require") as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM orders WHERE id = %s",
                        (order_id,)
                    )
                    conn.commit()
            
            bot.send_message(call.message.chat.id, "✅ Заявка удалена", reply_markup=main_menu())
            
    except Exception as e:
        logger.error(f"Ошибка callback: {e}")
        bot.send_message(call.message.chat.id, "❌ Ошибка обработки")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'edit_order')
def handle_edit_order(message):
    order_id = user_states[message.chat.id]['order_id']
    
    if message.text == "📦 Список товаров":
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT p.name, p.price, oi.quantity FROM order_items oi "
                    "JOIN products p ON oi.product_id = p.id "
                    "WHERE oi.order_id = %s",
                    (order_id,)
                )
                items = cursor.fetchall()
        
        if not items:
            bot.send_message(message.chat.id, "🛒 Заявка пуста")
            return
        
        response = "📦 Товары в заявке:\n"
        for item in items:
            response += f"{item[0]} - {item[1]} руб. x {item[2]}\n"
        
        bot.send_message(message.chat.id, response)
        
    elif message.text == "➕ Добавить товар":
        markup = ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add(KeyboardButton("🔍 Сканировать штрихкод"))
        markup.add(KeyboardButton("⌨️ Ввести 4 цифры"))
        markup.add(KeyboardButton("🔙 Назад"))
        
        user_states[message.chat.id]['step'] = 'edit_order_add'
        bot.send_message(message.chat.id, "Выберите способ добавления:", reply_markup=markup)
        
    elif message.text == "❌ Удалить товар":
        user_states[message.chat.id]['step'] = 'edit_order_remove'
        bot.send_message(message.chat.id, "Введите последние 4 цифры штрихкода:")
        
    elif message.text == "🔙 Назад":
        user_states.pop(message.chat.id, None)
        bot.send_message(message.chat.id, "🔙 Отменено", reply_markup=main_menu())

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'edit_order_add')
def handle_edit_order_add(message):
    order_id = user_states[message.chat.id]['order_id']
    
    if message.text == "🔍 Сканировать штрихкод":
        user_states[message.chat.id] = {'step': 'awaiting_barcode_scan', 'order_id': order_id}
        bot.send_message(message.chat.id, "📷 Отправьте фото штрихкода")
        
    elif message.text == "⌨️ Ввести 4 цифры":
        user_states[message.chat.id] = {'step': 'awaiting_order_barcode', 'order_id': order_id}
        bot.send_message(message.chat.id, "Введите последние 4 цифры:")
        
    elif message.text == "🔙 Назад":
        user_states[message.chat.id]['step'] = 'edit_order'
        bot.send_message(message.chat.id, "🔙 Возврат", reply_markup=main_menu())

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'edit_order_remove')
def handle_edit_order_remove(message):
    try:
        last_four = message.text.strip()
        if not last_four.isdigit() or len(last_four) != 4:
            raise ValueError("Некорректный ввод")
        
        order_id = user_states[message.chat.id]['order_id']
        
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM order_items WHERE order_id = %s AND product_id IN "
                    "(SELECT id FROM products WHERE barcode LIKE %s)",
                    (order_id, f"%{last_four}")
                )
                conn.commit()
                
        bot.send_message(message.chat.id, "✅ Товар удален", reply_markup=main_menu())
        
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка удаления")
    finally:
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
    while True:
        try:
            bot.polling(none_stop=True)
        except Exception as e:
            logger.error(f"Ошибка подключения: {e}")
            time.sleep(10)
