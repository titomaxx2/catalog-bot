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
OCR_API_KEY = os.getenv("OCR_API_KEY")
MAX_IMAGE_SIZE_MB = 1  # Максимальный размер изображения для OCR API

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

def compress_image(image_data: bytes, quality: int = 85, max_size: int = 1024) -> bytes:
    """Сжимает изображение с сохранением читаемости штрихкода"""
    with Image.open(BytesIO(image_data)) as img:
        # Конвертируем в RGB для JPEG
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Уменьшаем размер изображения
        img.thumbnail((max_size, max_size))
        
        # Постепенное сжатие до достижения нужного размера
        output = BytesIO()
        quality = min(quality, 95)
        while True:
            output.seek(0)
            output.truncate()
            img.save(output, format='JPEG', quality=quality, optimize=True)
            if len(output.getvalue()) <= MAX_IMAGE_SIZE_MB * 1024 * 1024:
                break
            quality -= 5
            if quality < 50:
                img = img.resize((img.width//2, img.height//2))
                quality = 75
        
        logger.info(f"Сжато до: {len(output.getvalue())//1024} KB, качество: {quality}%")
        return output.getvalue()

# ... (остальные функции: main_menu, scan_menu, catalog_menu остаются без изменений) ...

@bot.message_handler(content_types=['photo'], 
                   func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_barcode_scan')
def process_barcode_scan(message):
    try:
        # Скачиваем изображение
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Сжимаем изображение
        compressed_image = compress_image(downloaded_file)
        logger.info(f"Размер после сжатия: {len(compressed_image)} bytes")

        # Отправка в OCR.Space
        response = requests.post(
            'https://api.ocr.space/parse/image',
            files={'image': ('barcode.jpg', compressed_image, 'image/jpeg')},
            data={
                'apikey': OCR_API_KEY,
                'language': 'eng',
                'OCREngine': 2,
                'isTable': True,
                'scale': True,
                'detectOrientation': True
            },
            timeout=15
        )

        # Обработка ответа (остается без изменений)
        # ... (код обработки OCR ответа из предыдущего решения) ...

    except Exception as e:
        logger.error(f"Ошибка сканирования: {str(e)}", exc_info=True)
        error_msg = "⚠️ Ошибка обработки изображения\nПопробуйте:"
        error_msg += "\n- Сфотографировать при хорошем освещении"
        error_msg += "\n- Убедиться, что штрихкод в фокусе"
        error_msg += "\n- Расположить камеру параллельно штрихкоду"
        
        bot.send_message(
            message.chat.id,
            error_msg,
            reply_markup=main_menu()
        )
    finally:
        user_states.pop(message.chat.id, None)

# ... (остальные обработчики: handle_start, start_add_product, process_product_data,
# process_product_image, show_catalog, handle_callback, edit_price, delete_product,
# handle_export, handle_scan, cancel_scan остаются без изменений) ...

if __name__ == "__main__":
    logger.info("Бот запущен")
    while True:
        try:
            bot.polling(none_stop=True, interval=2, timeout=60)
        except Exception as e:
            logger.error(f"Ошибка подключения: {e}")
            time.sleep(10)
