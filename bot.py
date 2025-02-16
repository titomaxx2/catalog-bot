# Добавить в импорты
from openpyxl import Workbook
import json

# Обновленная функция сжатия изображений
def compress_image(image_data: bytes) -> bytes:
    """Сжимает изображение только если размер превышает 1 МБ"""
    if len(image_data) <= MAX_IMAGE_SIZE_MB * 1024 * 1024:
        logger.info("Изображение не требует сжатия")
        return image_data

    try:
        with Image.open(BytesIO(image_data)) as img:
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            img.thumbnail((800, 800))
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
                    img = img.resize((img.width//2, img.height//2))
                    quality = 75
            
            logger.info(f"Изображение сжато до {len(output.getvalue())//1024} KB")
            return output.getvalue()
    except Exception as e:
        logger.error(f"Ошибка сжатия: {e}")
        raise

# Обновленный обработчик сканирования
@bot.message_handler(content_types=['photo'], func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_barcode_scan')
def process_barcode_scan(message):
    try:
        # Скачивание изображения
        file_info = bot.get_file(message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # Сжатие при необходимости
        compressed_image = compress_image(downloaded_file)
        logger.debug(f"Размер изображения для OCR: {len(compressed_image)} байт")

        # Отправка в OCR API
        response = requests.post(
            'https://api.ocr.space/parse/image',
            files={'image': ('barcode.jpg', compressed_image, 'image/jpeg')},
            data={'apikey': OCR_API_KEY, 'OCREngine': 2},
            timeout=20
        )
        
        # Обработка ответа
        if response.status_code != 200:
            raise Exception(f"HTTP Error {response.status_code}")
        
        result = response.json()
        logger.debug(f"Ответ OCR: {json.dumps(result, indent=2)}")

        # Проверка структуры ответа
        if not result.get('ParsedResults'):
            raise Exception("Некорректный ответ от OCR API")
        
        parsed_text = result['ParsedResults'][0].get('ParsedText', '')
        logger.debug(f"Распознанный текст: {parsed_text}")

        # Поиск штрихкода
        barcode = None
        numbers = [word.strip() for word in parsed_text.split() if word.strip().isdigit()]
        valid_barcodes = [num for num in numbers if 8 <= len(num) <= 15]
        
        if valid_barcodes:
            barcode = max(valid_barcodes, key=len)
            logger.info(f"Найден штрихкод: {barcode}")

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
