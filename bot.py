import os
import logging
import time
import requests
import psycopg2
import telebot
from flask import Flask
from PIL import Image, ImageEnhance
from io import BytesIO
from threading import Thread, Lock
from openpyxl import Workbook
from telebot.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")
OCR_API_KEY = os.getenv("OCR_API_KEY")
MAX_IMAGE_SIZE_MB = 2
PORT = int(os.getenv("PORT", 5000))

bot = telebot.TeleBot(TOKEN, num_threads=5)
user_states = {}
state_lock = Lock()

class Database:
    def __init__(self):
        self.conn = psycopg2.connect(DB_URL, sslmode="require")
        self.init_db()
        
    def init_db(self):
        with self.conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT NOT NULL,
                    barcode TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    price FLOAT NOT NULL,
                    image_id TEXT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT NOT NULL,
                    name TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS order_items (
                    id SERIAL PRIMARY KEY,
                    order_id INT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                    product_id INT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                    quantity INT NOT NULL DEFAULT 1,
                    price FLOAT NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)
            self.conn.commit()
    
    def execute(self, query, params=None):
        with self.conn.cursor() as cur:
            cur.execute(query, params or ())
            self.conn.commit()
            return cur
    
    def fetch(self, query, params=None):
        with self.conn.cursor() as cur:
            cur.execute(query, params or ())
            return cur.fetchall()

db = Database()

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running"

def compress_image(image_data):
    try:
        if len(image_data) > MAX_IMAGE_SIZE_MB * 1024 * 1024:
            with Image.open(BytesIO(image_data)) as img:
                img = img.convert('RGB')
                output = BytesIO()
                img.save(output, format='JPEG', quality=85, optimize=True)
                return output.getvalue()
        return image_data
    except Exception as e:
        logger.error(f"Compression error: {e}")
        raise

def process_barcode(image_data):
    try:
        processed = compress_image(image_data)
        response = requests.post(
            'https://api.ocr.space/parse/image',
            files={'image': ('barcode.jpg', processed, 'image/jpeg')},
            data={'apikey': OCR_API_KEY, 'OCREngine': 2},
            timeout=20
        )
        
        if response.status_code != 200:
            raise Exception(f"API Error: {response.status_code}")
            
        data = response.json()
        if data.get('IsErroredOnProcessing', False):
            raise Exception(data.get('ErrorMessage', 'OCR Error'))
            
        text = data.get('ParsedResults', [{}])[0].get('ParsedText', '')
        numbers = [w.strip() for w in text.split() if w.isdigit()]
        barcodes = [n for n in numbers if 8 <= len(n) <= 15]
        
        return max(barcodes, key=len) if barcodes else None
        
    except Exception as e:
        logger.error(f"Barcode processing failed: {e}")
        raise

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("➕ Добавить товар", "📦 Каталог")
    markup.row("📷 Сканировать", "📝 Заявки")
    markup.row("📤 Экспорт данных")
    return markup

def catalog_markup(product_id):
    return InlineKeyboardMarkup().row(
        InlineKeyboardButton("✏️ Редактировать", callback_data=f"edit_{product_id}"),
        InlineKeyboardButton("❌ Удалить", callback_data=f"del_{product_id}")
    )

def order_markup(order_id):
    return InlineKeyboardMarkup().row(
        InlineKeyboardButton("📤 Экспорт", callback_data=f"export_{order_id}"),
        InlineKeyboardButton("❌ Удалить", callback_data=f"delord_{order_id}"),
        InlineKeyboardButton("✏️ Редактировать", callback_data=f"editord_{order_id}")
    )

def cleanup_states():
    while True:
        time.sleep(300)
        with state_lock:
            now = time.time()
            to_delete = [uid for uid, state in user_states.items() if now - state['time'] > 300]
            for uid in to_delete:
                del user_states[uid]

Thread(target=cleanup_states, daemon=True).start()

# Обработчики товаров
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "🛒 Добро пожаловать в систему управления товарами!", reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "➕ Добавить товар")
def add_product(message):
    with state_lock:
        user_states[message.chat.id] = {'step': 'await_product', 'time': time.time()}
    bot.send_message(message.chat.id, "Введите данные в формате: Штрихкод | Название | Цена")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'await_product')
def handle_product_data(message):
    try:
        barcode, name, price = map(str.strip, message.text.split('|', 2))
        with state_lock:
            user_states[message.chat.id] = {
                'step': 'await_image',
                'data': (barcode, name, float(price)),
                'time': time.time()
            }
        bot.send_message(message.chat.id, "Отправьте изображение товара")
    except:
        bot.send_message(message.chat.id, "❌ Неверный формат данных!")
        with state_lock:
            del user_states[message.chat.id]

@bot.message_handler(content_types=['photo'], func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'await_image')
def handle_product_image(message):
    try:
        barcode, name, price = user_states[message.chat.id]['data']
        image_id = message.photo[-1].file_id
        
        db.execute(
            "INSERT INTO products (telegram_id, barcode, name, price, image_id) VALUES (%s, %s, %s, %s, %s)",
            (message.chat.id, barcode, name, price, image_id)
        )
        bot.send_message(message.chat.id, "✅ Товар успешно добавлен!", reply_markup=main_menu())
    except psycopg2.IntegrityError:
        bot.send_message(message.chat.id, "❌ Такой штрихкод уже существует!")
    except Exception as e:
        logger.error(f"Product add error: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка сохранения товара!")
    finally:
        with state_lock:
            del user_states[message.chat.id]

@bot.message_handler(func=lambda m: m.text == "📦 Каталог")
def show_catalog(message):
    products = db.fetch("SELECT id, barcode, name, price, image_id FROM products WHERE telegram_id = %s", 
                       (message.chat.id,))
    if not products:
        bot.send_message(message.chat.id, "📭 Каталог пуст")
        return
    
    for prod in products:
        pid, barcode, name, price, img = prod
        caption = f"📦 {name}\n🔖 {barcode}\n💰 {price} руб."
        if img:
            bot.send_photo(message.chat.id, img, caption, reply_markup=catalog_markup(pid))
        else:
            bot.send_message(message.chat.id, caption, reply_markup=catalog_markup(pid))

@bot.callback_query_handler(func=lambda c: c.data.startswith('del_'))
def delete_product(call):
    try:
        pid = call.data.split('_')[1]
        db.execute("DELETE FROM products WHERE id = %s", (pid,))
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id, "✅ Товар удален")
    except Exception as e:
        logger.error(f"Delete product error: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка удаления!")

# Обработчики заявок
@bot.message_handler(func=lambda m: m.text == "📝 Заявки")
def handle_orders(message):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("📝 Создать заявку", "📋 Мои заявки")
    markup.row("🔙 Назад")
    bot.send_message(message.chat.id, "Управление заявками:", reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "📋 Мои заявки")
def list_orders(message):
    orders = db.fetch("SELECT id, name, created_at FROM orders WHERE telegram_id = %s", (message.chat.id,))
    if not orders:
        bot.send_message(message.chat.id, "📭 У вас нет активных заявок")
        return
    
    for order in orders:
        oid, name, date = order
        bot.send_message(
            message.chat.id,
            f"📋 Заявка: {name}\n🕒 {date.strftime('%d.%m.%Y %H:%M')}",
            reply_markup=order_markup(oid)
        )

@bot.message_handler(func=lambda m: m.text == "📝 Создать заявку")
def create_order(message):
    with state_lock:
        user_states[message.chat.id] = {'step': 'create_order', 'time': time.time()}
    bot.send_message(message.chat.id, "Введите название заявки:")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'create_order')
def process_order_name(message):
    try:
        name = message.text.strip()
        if not name:
            raise ValueError("Пустое название")
        
        order_id = db.fetch(
            "INSERT INTO orders (telegram_id, name) VALUES (%s, %s) RETURNING id",
            (message.chat.id, name)
        )[0][0]
        
        with state_lock:
            user_states[message.chat.id] = {
                'step': 'order_management',
                'order_id': order_id,
                'time': time.time()
            }
        
        markup = ReplyKeyboardMarkup(resize_keyboard=True)
        markup.row("📷 Сканировать штрихкод", "⌨ Ввести 4 цифры")
        markup.row("🔙 Завершить")
        bot.send_message(message.chat.id, "Выберите способ добавления товаров:", reply_markup=markup)
        
    except Exception as e:
        logger.error(f"Order creation error: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка создания заявки!")
        with state_lock:
            del user_states[message.chat.id]

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'order_management')
def handle_order_management(message):
    if message.text == "📷 Сканировать штрихкод":
        with state_lock:
            user_states[message.chat.id]['step'] = 'order_scan'
        bot.send_message(message.chat.id, "Отправьте фото штрихкода товара")
        
    elif message.text == "⌨ Ввести 4 цифры":
        with state_lock:
            user_states[message.chat.id]['step'] = 'order_input'
        bot.send_message(message.chat.id, "Введите последние 4 цифры штрихкода:")
        
    elif message.text == "🔙 Завершить":
        with state_lock:
            del user_states[message.chat.id]
        bot.send_message(message.chat.id, "✅ Заявка сохранена", reply_markup=main_menu())

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'order_input')
def handle_order_input(message):
    try:
        last_digits = message.text.strip()
        if len(last_digits) != 4 or not last_digits.isdigit():
            raise ValueError("Некорректный ввод")
        
        product = db.fetch(
            "SELECT id, price FROM products WHERE RIGHT(barcode, 4) = %s AND telegram_id = %s",
            (last_digits, message.chat.id)
        )
        
        if not product:
            bot.send_message(message.chat.id, "❌ Товар не найден")
            return
            
        with state_lock:
            user_states[message.chat.id].update({
                'step': 'add_quantity',
                'product_id': product[0][0],
                'default_price': product[0][1]
            })
            
        bot.send_message(message.chat.id, "Введите количество и цену через пробел (по умолчанию 1 и цена из каталога):")
        
    except Exception as e:
        logger.error(f"Order input error: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка ввода данных!")

@bot.message_handler(content_types=['photo'], func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'order_scan')
def handle_order_scan(message):
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        image_data = bot.download_file(file_info.file_path)
        
        barcode = process_barcode(image_data)
        if not barcode:
            raise Exception("Штрихкод не найден")
            
        product = db.fetch(
            "SELECT id, price FROM products WHERE barcode = %s AND telegram_id = %s",
            (barcode, message.chat.id)
        )
        
        if not product:
            bot.send_message(message.chat.id, f"❌ Товар с кодом {barcode} не найден")
            return
            
        with state_lock:
            user_states[message.chat.id].update({
                'step': 'add_quantity',
                'product_id': product[0][0],
                'default_price': product[0][1]
            })
            
        bot.send_message(message.chat.id, "Введите количество и цену через пробел (по умолчанию 1 и цена из каталога):")
        
    except Exception as e:
        logger.error(f"Order scan error: {e}")
        bot.send_message(message.chat.id, f"❌ Ошибка: {str(e)}")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'add_quantity')
def handle_add_quantity(message):
    try:
        parts = message.text.split()
        quantity = int(parts[0]) if len(parts) > 0 else 1
        price = float(parts[1]) if len(parts) > 1 else user_states[message.chat.id]['default_price']
        
        order_id = user_states[message.chat.id]['order_id']
        product_id = user_states[message.chat.id]['product_id']
        
        db.execute(
            "INSERT INTO order_items (order_id, product_id, quantity, price) VALUES (%s, %s, %s, %s)",
            (order_id, product_id, quantity, price)
        )
        
        bot.send_message(message.chat.id, "✅ Товар добавлен в заявку!")
        with state_lock:
            user_states[message.chat.id]['step'] = 'order_management'
            
    except Exception as e:
        logger.error(f"Add quantity error: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка добавления товара!")

@bot.callback_query_handler(func=lambda c: c.data.startswith('delord_'))
def delete_order(call):
    try:
        order_id = call.data.split('_')[1]
        db.execute("DELETE FROM orders WHERE id = %s", (order_id,))
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.answer_callback_query(call.id, "✅ Заявка удалена")
    except Exception as e:
        logger.error(f"Delete order error: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка удаления!")

@bot.callback_query_handler(func=lambda c: c.data.startswith('export_'))
def export_order(call):
    try:
        order_id = call.data.split('_')[1]
        items = db.fetch("""
            SELECT p.name, oi.quantity, oi.price 
            FROM order_items oi
            JOIN products p ON oi.product_id = p.id
            WHERE oi.order_id = %s
        """, (order_id,))
        
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
        logger.error(f"Export order error: {e}")
        bot.answer_callback_query(call.id, "❌ Ошибка экспорта!")

@bot.message_handler(func=lambda m: m.text == "📤 Экспорт данных")
def export_data(message):
    try:
        products = db.fetch("SELECT barcode, name, price FROM products WHERE telegram_id = %s", 
                          (message.chat.id,))
        
        wb = Workbook()
        ws = wb.active
        ws.append(["Штрихкод", "Название", "Цена"])
        for p in products:
            ws.append(p)
            
        filename = f"catalog_{message.chat.id}.xlsx"
        wb.save(filename)
        
        with open(filename, 'rb') as f:
            bot.send_document(message.chat.id, f, caption="📤 Экспорт каталога")
            
        os.remove(filename)
    except Exception as e:
        logger.error(f"Export error: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка экспорта данных!")

if __name__ == "__main__":
    Thread(target=app.run, kwargs={'host':'0.0.0.0','port':PORT}).start()
    bot.infinity_polling()
