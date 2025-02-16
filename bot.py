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
OCR_API_KEY = os.getenv("OCR_API_KEY")  # Получите ключ на https://ocr.space/

bot = telebot.TeleBot(TOKEN)
user_states = {}

# ... (остальная часть кода остается без изменений до обработчика сканирования) ...

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
        
        # Используем API для распознавания
        response = requests.post(
            'https://api.ocr.space/parse/image',
            files={'image': downloaded_file},
            data={
                'apikey': OCR_API_KEY,
                'language': 'eng',
                'isOverlayRequired': False,
                'detectOrientation': True,
                'scale': True
            }
        )
        
        result = response.json()
        barcode = None
        
        if result['IsErroredOnProcessing']:
            raise Exception(result['ErrorMessage'])
        
        for item in result['ParsedResults'][0]['TextOverlay']['Lines']:
            for word in item['Words']:
                text = word['WordText']
                if text.isdigit() and len(text) in [8, 12, 13, 14]:  # Проверка на стандартные форматы штрихкодов
                    barcode = text
                    break
            if barcode:
                break

        if barcode:
            response_text = f"✅ Распознан штрихкод: `{barcode}`\n"
            
            # Проверка в базе данных
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

# ... (остальная часть кода остается без изменений) ...
