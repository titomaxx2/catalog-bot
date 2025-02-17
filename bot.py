import os
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
            
            # Сохраняем изображение с уменьшенным качеством
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
    """
    Предварительная обработка изображения для улучшения распознавания.
    """
    try:
        # Открываем изображение с помощью Pillow
        image = Image.open(BytesIO(image_data))
        
        # Увеличение контрастности
        enhancer = ImageEnhance.Contrast(image)
        image = enhancer.enhance(2.0)  # Увеличиваем контрастность в 2 раза
        
        # Преобразуем изображение в grayscale
        image = image.convert('L')
        
        # Сохраняем изображение в BytesIO
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
        InlineKeyboardButton("📦 Просмотреть", callback_data=f"view_order_{order_id}"),
        InlineKeyboardButton("📤 Выгрузить в Excel", callback_data=f"export_order_{order_id}")
    )
    return markup

@bot.message_handler(commands=['start'])
def handle_start(message):
    try:
        logger.info(f"Новый пользователь: {message.chat.id}")
        bot.send_message(
            message.chat.id,
            "🏪 Добро пожаловать в систему управления товарами!",
            reply_markup=main_menu()
        )
    except Exception as e:
        logger.error(f"Ошибка в /start: {e}")

@bot.message_handler(func=lambda m: m.text == "➕ Добавить товар")
def start_add_product(message):
    try:
        user_states[message.chat.id] = {'step': 'awaiting_product_data'}
        bot.send_message(
            message.chat.id,
            "📝 Введите данные в формате:\nШтрихкод | Название | Цена\nПример: 46207657112 | Молоко 3.2% | 89.99"
        )
    except Exception as e:
        logger.error(f"Ошибка начала добавления товара: {e}")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_product_data')
def process_product_data(message):
    try:
        data = [x.strip() for x in message.text.split('|')]
        if len(data) != 3:
            raise ValueError("Неверный формат данных")
        
        barcode, name, price = data
        price = float(price)
        
        user_states[message.chat.id] = {
            'step': 'awaiting_product_image',
            'product_data': (barcode, name, price)
        }
        bot.send_message(message.chat.id, "📷 Теперь отправьте фото товара")
        
    except Exception as e:
        logger.error(f"Ошибка обработки данных: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка формата. Используйте правильный формат!")
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
        logger.error("Попытка добавить дубликат штрихкода")
        bot.send_message(message.chat.id, "❌ Этот штрихкод уже существует!")
    except Exception as e:
        logger.error(f"Ошибка сохранения товара: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка при сохранении товара!")
    finally:
        user_states.pop(message.chat.id, None)

@bot.message_handler(func=lambda m: m.text == "📦 Каталог")
def show_catalog(message):
    try:
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, barcode, name, price, image_id FROM products WHERE telegram_id = %s ORDER BY created_at DESC",
                    (message.chat.id,)
                )
                products = cursor.fetchall()
        
        if not products:
            bot.send_message(message.chat.id, "🛒 Ваш каталог товаров пуст")
            return
        
        for product in products:
            product_id, barcode, name, price, image_id = product
            caption = f"📦 {name}\n🔖 Штрихкод: {barcode}\n💰 Цена: {price} руб."
            
            if image_id:
                bot.send_photo(
                    message.chat.id,
                    image_id,
                    caption=caption,
                    reply_markup=catalog_menu(product_id)
                )
            else:
                bot.send_message(
                    message.chat.id,
                    caption,
                    reply_markup=catalog_menu(product_id)
                )
                
    except Exception as e:
        logger.error(f"Ошибка показа каталога: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки каталога")

@bot.message_handler(func=lambda m: m.text == "📷 Сканировать штрихкод")
def handle_scan(message):
    try:
        user_states[message.chat.id] = {'step': 'awaiting_barcode_scan'}
        bot.send_message(
            message.chat.id,
            "📷 Сфотографируйте штрихкод или отправьте изображение:",
            reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("Отмена"))
        )
    except Exception as e:
        logger.error(f"Ошибка начала сканирования: {e}")

@bot.message_handler(content_types=['photo'], func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_barcode_scan')
def process_barcode_scan(message):
    try:
        # Скачивание изображения
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Предварительная обработка изображения
        processed_image = preprocess_image(downloaded_file)
        
        # Сжатие изображения (если необходимо)
        compressed_image = compress_image(processed_image)
        logger.debug(f"Размер изображения для OCR: {len(compressed_image)} байт")

        # Отправка в OCR API
        max_retries = 3
        retry_delay = 5  # Задержка между попытками в секундах
        barcode = None

        for attempt in range(max_retries):
            try:
                response = requests.post(
                    'https://api.ocr.space/parse/image',
                    files={'image': ('barcode.jpg', compressed_image, 'image/jpeg')},
                    data={'apikey': OCR_API_KEY, 'OCREngine': 2},
                    timeout=30
                )
                
                # Обработка ответа
                if response.status_code != 200:
                    logger.error(f"HTTP Error {response.status_code}: {response.text}")
                    raise Exception(f"HTTP Error {response.status_code}")
                
                result = response.json()
                logger.info(f"Ответ OCR: {json.dumps(result, indent=2)}")  # Логируем полный ответ

                # Проверка структуры ответа
                if not result.get('ParsedResults'):
                    logger.error("Некорректный ответ от OCR API: отсутствует ParsedResults")
                    raise Exception("Некорректный ответ от OCR API")
                
                parsed_text = result['ParsedResults'][0].get('ParsedText', '')
                logger.info(f"Распознанный текст: {parsed_text}")

                # Удаляем символы новой строки и пробелы
                cleaned_text = parsed_text.replace("\n", "").replace(" ", "")
                logger.info(f"Очищенный текст: {cleaned_text}")

                # Поиск штрихкода
                numbers = [word.strip() for word in cleaned_text.split() if word.strip().isdigit()]
                valid_barcodes = [num for num in numbers if 8 <= len(num) <= 15]
                
                if valid_barcodes:
                    barcode = max(valid_barcodes, key=len)
                    logger.info(f"Найден штрихкод: {barcode}")
                    break  # Успешное распознавание, выходим из цикла
                else:
                    logger.warning(f"Попытка {attempt + 1}: Штрихкод не распознан")
                    if attempt < max_retries - 1:
                        time.sleep(retry_delay)
            except Exception as e:
                logger.error(f"Попытка {attempt + 1} не удалась: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    raise  # Если все попытки исчерпаны, выбрасываем исключение

        # Отправка результата
        if barcode:
            # Поиск в базе данных
            with psycopg2.connect(DB_URL, sslmode="require") as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        "SELECT name, price FROM products WHERE barcode = %s AND telegram_id = %s",
                        (barcode, message.chat.id)
                    )
                    product = cursor.fetchone()
            
            response_text = f"✅ Штрихкод: `{barcode}`"
            if product:
                response_text += f"\n📦 Товар: {product[0]}\n💰 Цена: {product[1]} руб."
            else:
                response_text += "\n❌ Товар не найден в базе"
        else:
            response_text = "❌ Штрихкод не распознан\nПопробуйте сделать более четкое фото"

        bot.send_message(
            message.chat.id,
            response_text,
            parse_mode='Markdown',
            reply_markup=main_menu()
        )

    except Exception as e:
        logger.error(f"Ошибка сканирования: {str(e)}", exc_info=True)
        bot.send_message(
            message.chat.id,
            f"⚠️ Ошибка: {str(e)}",
            reply_markup=main_menu()
        )
    finally:
        user_states.pop(message.chat.id, None)

@bot.message_handler(func=lambda m: m.text == "📝 Создать заявку")
def create_order(message):
    try:
        user_states[message.chat.id] = {'step': 'awaiting_order_name'}
        bot.send_message(
            message.chat.id,
            "📝 Введите название заявки:"
        )
    except Exception as e:
        logger.error(f"Ошибка создания заявки: {e}")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_order_name')
def process_order_name(message):
    try:
        order_name = message.text.strip()
        if not order_name:
            raise ValueError("Название заявки не может быть пустым")
        
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO orders (telegram_id, name) VALUES (%s, %s) RETURNING id",
                    (message.chat.id, order_name)
                )
                order_id = cursor.fetchone()[0]
                conn.commit()
        
        user_states[message.chat.id] = {
            'step': 'awaiting_order_barcode',
            'order_id': order_id
        }
        bot.send_message(
            message.chat.id,
            "📦 Введите последние 4 цифры штрихкода товара для добавления в заявку:"
        )
        
    except Exception as e:
        logger.error(f"Ошибка обработки названия заявки: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка: название заявки не может быть пустым")
        del user_states[message.chat.id]

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_order_barcode')
def process_order_barcode(message):
    try:
        last_four_digits = message.text.strip()
        if not last_four_digits.isdigit() or len(last_four_digits) != 4:
            raise ValueError("Введите ровно 4 цифры")
        
        order_id = user_states[message.chat.id]['order_id']
        
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, name, price FROM products WHERE telegram_id = %s AND barcode LIKE %s",
                    (message.chat.id, f"%{last_four_digits}")
                )
                product = cursor.fetchone()
                
                if product:
                    product_id, name, price = product
                    cursor.execute(
                        "INSERT INTO order_items (order_id, product_id) VALUES (%s, %s)",
                        (order_id, product_id)
                    )
                    conn.commit()
                    bot.send_message(
                        message.chat.id,
                        f"✅ Товар '{name}' добавлен в заявку!",
                        reply_markup=main_menu()
                    )
                else:
                    bot.send_message(
                        message.chat.id,
                        "❌ Товар не найден. Попробуйте еще раз.",
                        reply_markup=main_menu()
                    )
        
    except Exception as e:
        logger.error(f"Ошибка обработки штрихкода: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка: введите ровно 4 цифры")
    finally:
        user_states.pop(message.chat.id, None)

@bot.message_handler(func=lambda m: m.text == "📋 Список заявок")
def list_orders(message):
    try:
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, name, created_at FROM orders WHERE telegram_id = %s ORDER BY created_at DESC",
                    (message.chat.id,)
                )
                orders = cursor.fetchall()
        
        if not orders:
            bot.send_message(message.chat.id, "📋 У вас нет заявок.")
            return
        
        for order in orders:
            order_id, name, created_at = order
            bot.send_message(
                message.chat.id,
                f"📋 Заявка: {name}\n🕒 Дата создания: {created_at.strftime('%Y-%m-%d %H:%M')}",
                reply_markup=order_menu(order_id)
            )
                
    except Exception as e:
        logger.error(f"Ошибка показа списка заявок: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка загрузки списка заявок")

@bot.callback_query_handler(func=lambda call: call.data.startswith(('view_order_', 'export_order_')))
def handle_order_callback(call):
    try:
        action, order_id = call.data.split('_')
        order_id = int(order_id)
        
        if action == 'view':
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
                bot.send_message(call.message.chat.id, "🛒 Заявка пуста.")
                return
            
            response_text = "📦 Товары в заявке:\n"
            for item in items:
                name, price, quantity = item
                response_text += f"📦 {name} - {price} руб. x {quantity}\n"
            
            bot.send_message(call.message.chat.id, response_text)
            
        elif action == 'export':
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
                bot.send_message(call.message.chat.id, "🛒 Заявка пуста.")
                return
            
            wb = Workbook()
            ws = wb.active
            ws.title = "Заявка"
            ws.append(["Название", "Цена", "Количество"])
            
            for item in items:
                name, price, quantity = item
                ws.append([name, price, quantity])
            
            filename = f"order_{order_id}.xlsx"
            wb.save(filename)
            
            with open(filename, "rb") as f:
                bot.send_document(
                    call.message.chat.id,
                    f,
                    caption="📤 Ваша заявка в формате Excel"
                )
            
            os.remove(filename)
            
    except Exception as e:
        logger.error(f"Ошибка обработки callback: {e}")
        bot.send_message(call.message.chat.id, "❌ Произошла ошибка при обработке запроса")

if __name__ == "__main__":
    # Запуск Flask сервера
    port = int(os.environ.get("PORT", 10000))
    Thread(target=app.run, kwargs={
        'host': '0.0.0.0',
        'port': port,
        'debug': False,
        'use_reloader': False
    }).start()
    
    # Запуск бота
    logger.info("Бот запущен")
    while True:
        try:
            bot.polling(none_stop=True, interval=3, timeout=30)
        except Exception as e:
            logger.error(f"Ошибка подключения: {e}")
            time.sleep(10)
