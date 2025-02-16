import os
import logging
import time
import requests
import psycopg2
import telebot
from openpyxl import Workbook
from PIL import Image
from io import BytesIO
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

# Конфигурация
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")
OCR_API_KEY = os.getenv("OCR_API_KEY")  # Получить на https://ocr.space/

bot = telebot.TeleBot(TOKEN)
user_states = {}

# Инициализация БД
def init_db():
    commands = (
        """
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            barcode TEXT NOT NULL,
            name TEXT NOT NULL,
            price FLOAT NOT NULL,
            image_id TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """,
    )
    
    with psycopg2.connect(DB_URL, sslmode="require") as conn:
        with conn.cursor() as cursor:
            for command in commands:
                cursor.execute(command)
        conn.commit()

init_db()

# Клавиатуры
def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("➕ Добавить товар"))
    markup.add(KeyboardButton("📦 Каталог"),
               KeyboardButton("📤 Экспорт"))
    markup.add(KeyboardButton("📷 Сканировать штрихкод"))
    return markup

def scan_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("Отмена"))
    return markup

def catalog_menu():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Редактировать", callback_data="edit"),
               InlineKeyboardButton("Удалить", callback_data="delete"))
    return markup

# Обработчики команд
@bot.message_handler(commands=['start'])
def handle_start(message):
    bot.send_message(message.chat.id, "Добро пожаловать!", reply_markup=main_menu())

@bot.message_handler(func=lambda m: m.text == "➕ Добавить товар")
def start_add_product(message):
    user_states[message.chat.id] = {'step': 'awaiting_product_data'}
    bot.send_message(
        message.chat.id,
        "Введите данные товара в формате:\nШтрихкод | Название | Цена\nПример: 46207657112 | Молоко 3.2% | 89.99"
    )

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_product_data')
def process_product_data(message):
    try:
        data = [x.strip() for x in message.text.split('|')]
        if len(data) != 3:
            raise ValueError
        
        barcode, name, price = data
        price = float(price)
        
        user_states[message.chat.id] = {
            'step': 'awaiting_product_image',
            'product_data': (barcode, name, price)
        }
        
        bot.send_message(message.chat.id, "📷 Теперь отправьте фото товара")
        
    except Exception as e:
        logger.error(f"Ошибка обработки данных: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка формата. Попробуйте снова.")
        del user_states[message.chat.id]

@bot.message_handler(content_types=['photo'], 
                   func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_product_image')
def process_product_image(message):
    try:
        product_data = user_states[message.chat.id]['product_data']
        image_id = message.photo[-1].file_id
        
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO products (telegram_id, barcode, name, price, image_id) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (message.chat.id, *product_data, image_id)
                )
                conn.commit()
        
        bot.send_message(message.chat.id, "✅ Товар добавлен!", reply_markup=main_menu())
        del user_states[message.chat.id]
        
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка сохранения. Попробуйте снова.")
        del user_states[message.chat.id]

@bot.message_handler(func=lambda m: m.text == "📦 Каталог")
def show_catalog(message):
    with psycopg2.connect(DB_URL, sslmode="require") as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT barcode, name, price, image_id FROM products "
                "WHERE telegram_id = %s ORDER BY created_at DESC LIMIT 10",
                (message.chat.id,)
            )
            products = cursor.fetchall()
    
    if not products:
        bot.send_message(message.chat.id, "Каталог пуст")
        return
    
    for product in products:
        barcode, name, price, image_id = product
        caption = f"📦 {name}\n🔖 {barcode}\n💵 {price} руб."
        
        if image_id:
            bot.send_photo(message.chat.id, image_id, caption, reply_markup=catalog_menu())
        else:
            bot.send_message(message.chat.id, caption, reply_markup=catalog_menu())

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    if call.data == 'edit':
        bot.send_message(call.message.chat.id, "Введите новый ценник в формате:\nБаркод | Новая цена\nПример: 46207657112 | 99.99")
        user_states[call.message.chat.id] = {'step': 'edit_price'}
    
    elif call.data == 'delete':
        bot.send_message(call.message.chat.id, "Введите баркод товара для удаления:")
        user_states[call.message.chat.id] = {'step': 'delete_product'}

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'edit_price')
def edit_price(message):
    try:
        barcode, new_price = message.text.split('|')
        barcode = barcode.strip()
        new_price = float(new_price.strip())
        
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE products SET price = %s "
                    "WHERE barcode = %s AND telegram_id = %s",
                    (new_price, barcode, message.chat.id)
                )
                conn.commit()
        
        bot.send_message(message.chat.id, "✅ Цена обновлена!", reply_markup=main_menu())
        del user_states[message.chat.id]
    
    except Exception as e:
        logger.error(f"Ошибка обновления: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка формата. Попробуйте снова.")
        del user_states[message.chat.id]

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'delete_product')
def delete_product(message):
    try:
        barcode = message.text.strip()
        
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM products "
                    "WHERE barcode = %s AND telegram_id = %s",
                    (barcode, message.chat.id)
                )
                conn.commit()
        
        bot.send_message(message.chat.id, "✅ Товар удален!", reply_markup=main_menu())
        del user_states[message.chat.id]
    
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка удаления. Проверьте баркод.")
        del user_states[message.chat.id]

@bot.message_handler(func=lambda m: m.text == "📤 Экспорт")
def handle_export(message):
    try:
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT barcode, name, price, created_at 
                    FROM products 
                    WHERE telegram_id = %s
                    ORDER BY created_at DESC
                """, (message.chat.id,))
                data = cursor.fetchall()

        if not data:
            bot.send_message(message.chat.id, "❌ Нет данных для экспорта")
            return

        wb = Workbook()
        ws = wb.active
        ws.title = "Каталог товаров"
        ws.append(["Штрихкод", "Название", "Цена", "Дата добавления"])
        
        for item in data:
            ws.append([item[0], item[1], item[2], item[3].strftime("%Y-%m-%d %H:%M")])
        
        filename = f"catalog_{message.chat.id}.xlsx"
        wb.save(filename)
        
        with open(filename, "rb") as f:
            bot.send_document(message.chat.id, f)
        
        os.remove(filename)
    
    except Exception as e:
        logger.error(f"Ошибка экспорта: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при экспорте данных")

def is_valid_barcode(text: str) -> bool:
    """Проверяет валидность штрихкода"""
    return any([
        text.isdigit() and len(text) in {8, 12, 13, 14},
        len(text) == 15 and text.startswith('2'),
        text.startswith('01') and len(text) >= 14,
    ])

@bot.message_handler(func=lambda m: m.text == "📷 Сканировать штрихкод")
def handle_scan(message):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("Отмена"))
    msg = bot.send_message(
        message.chat.id,
        "📷 Сфотографируйте штрихкод или отправьте изображение",
        reply_markup=markup
    )
    user_states[message.chat.id] = {'step': 'awaiting_barcode_scan'}

@bot.message_handler(content_types=['photo'], 
                   func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_barcode_scan')
def process_barcode_scan(message):
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        response = requests.post(
            'https://api.ocr.space/parse/image',
            files={'image': downloaded_file},
            data={
                'apikey': OCR_API_KEY,
                'language': 'eng',
                'OCREngine': 2,
                'isTable': True,
            }
        )
        
        result = response.json()
        barcode = None
        
        if result.get('IsErroredOnProcessing', False):
            raise Exception(result.get('ErrorMessage', 'Unknown error'))

        for parsed_result in result.get('ParsedResults', []):
            for line in parsed_result.get('TextOverlay', {}).get('Lines', []):
                for word in line.get('Words', []):
                    text = word.get('WordText', '')
                    if is_valid_barcode(text):
                        barcode = text
                        break
                if barcode:
                    break
            if barcode:
                break

        if barcode:
            response_text = f"✅ Распознан штрихкод: `{barcode}`\n"
            
            with psycopg2.connect(DB_URL, sslmode="require") as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT name, price FROM products 
                        WHERE barcode = %s AND telegram_id = %s
                    """, (barcode, message.chat.id))
                    product = cursor.fetchone()
                    
            if product:
                response_text += f"📦 Товар в базе:\nНазвание: {product[0]}\nЦена: {product[1]} руб."
            else:
                response_text += "❌ Товар не найден в базе"
        else:
            response_text = "❌ Не удалось распознать штрихкод"

        bot.send_message(
            message.chat.id,
            response_text,
            parse_mode='Markdown',
            reply_markup=main_menu()
        )
        del user_states[message.chat.id]
        
    except Exception as e:
        logger.error(f"Ошибка сканирования: {e}")
        bot.send_message(
            message.chat.id,
            "⚠️ Ошибка обработки изображения",
            reply_markup=main_menu()
        )
        del user_states[message.chat.id]

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_barcode_scan' 
                   and m.text == "Отмена")
def cancel_scan(message):
    del user_states[message.chat.id]
    bot.send_message(
        message.chat.id,
        "❌ Сканирование отменено",
        reply_markup=main_menu()
    )

if __name__ == "__main__":
    logger.info("Бот запущен")
    while True:
        try:
            bot.polling(none_stop=True, interval=2, timeout=60)
        except Exception as e:
            logger.error(f"Ошибка подключения: {e}")
            time.sleep(10)
