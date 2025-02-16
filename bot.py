import os
import logging
import json
import time
import psycopg2
import telebot
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

bot = telebot.TeleBot(TOKEN)

# Состояния пользователей
user_states = {}

# Инициализация БД
def init_db():
    commands = (
        """
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            supervisor_id INT NOT NULL,
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
                try:
                    cursor.execute(command)
                except Exception as e:
                    logger.error(f"Ошибка создания таблиц: {e}")
        conn.commit()

init_db()

# Клавиатуры
def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("➕ Добавить товар"))
    markup.add(KeyboardButton("📦 Каталог"))
    return markup

def catalog_menu():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Редактировать товар", callback_data="edit"))
    markup.add(InlineKeyboardButton("Удалить товар", callback_data="delete"))
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
        "Введите данные товара в формате:\n"
        "Штрихкод | Название | Цена\n"
        "Пример: 46207657112 | Молоко 3.2% | 89.99"
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
        bot.send_message(message.chat.id, "❌ Ошибка формата данных. Попробуйте снова.")
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
                    "INSERT INTO products (supervisor_id, barcode, name, price, image_id) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (message.chat.id, *product_data, image_id)
                )
                conn.commit()
        
        bot.send_message(
            message.chat.id,
            "✅ Товар успешно добавлен!",
            reply_markup=main_menu()
        )
        del user_states[message.chat.id]
        
    except Exception as e:
        logger.error(f"Ошибка сохранения товара: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка сохранения. Попробуйте снова.")
        del user_states[message.chat.id]

@bot.message_handler(func=lambda m: m.text == "📦 Каталог")
def show_catalog(message):
    with psycopg2.connect(DB_URL, sslmode="require") as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id, name, price, image_id FROM products "
                "WHERE supervisor_id = %s ORDER BY created_at DESC LIMIT 10",
                (message.chat.id,)
            )
            products = cursor.fetchall()
    
    if not products:
        bot.send_message(message.chat.id, "Каталог пуст")
        return
    
    for product in products:
        product_id, name, price, image_id = product
        caption = f"🆔 {product_id}\n📛 {name}\n💵 {price} руб."
        
        if image_id:
            bot.send_photo(
                message.chat.id,
                image_id,
                caption=caption,
                reply_markup=catalog_menu()
            )
        else:
            bot.send_message(
                message.chat.id,
                caption,
                reply_markup=catalog_menu()
            )

@bot.callback_query_handler(func=lambda call: call.data in ['edit', 'delete'])
def handle_catalog_actions(call):
    if call.data == 'edit':
        bot.send_message(call.message.chat.id, "Введите ID товара и новые данные в формате:\nID | Поле | Значение\nПример: 15 | price | 99.99")
        user_states[call.message.chat.id] = {'step': 'awaiting_edit_data'}
        
    elif call.data == 'delete':
        bot.send_message(call.message.chat.id, "Введите ID товара для удаления:")
        user_states[call.message.chat.id] = {'step': 'awaiting_delete_id'}

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_edit_data')
def process_edit(message):
    try:
        data = [x.strip() for x in message.text.split('|')]
        if len(data) != 3:
            raise ValueError
        
        product_id, field, value = data
        field = field.lower()
        
        if field not in ['name', 'price', 'barcode']:
            raise ValueError
        
        if field == 'price':
            value = float(value)
            
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"UPDATE products SET {field} = %s "
                    "WHERE id = %s AND supervisor_id = %s",
                    (value, product_id, message.chat.id)
                )
                conn.commit()
        
        bot.send_message(
            message.chat.id,
            "✅ Товар успешно обновлен!",
            reply_markup=main_menu()
        )
        del user_states[message.chat.id]
        
    except Exception as e:
        logger.error(f"Ошибка редактирования: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка формата данных. Попробуйте снова.")
        del user_states[message.chat.id]

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get('step') == 'awaiting_delete_id')
def process_delete(message):
    try:
        product_id = int(message.text)
        
        with psycopg2.connect(DB_URL, sslmode="require") as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM products "
                    "WHERE id = %s AND supervisor_id = %s",
                    (product_id, message.chat.id)
                )
                conn.commit()
        
        bot.send_message(
            message.chat.id,
            "✅ Товар успешно удален!",
            reply_markup=main_menu()
        )
        del user_states[message.chat.id]
        
    except Exception as e:
        logger.error(f"Ошибка удаления: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка удаления. Проверьте ID товара.")
        del user_states[message.chat.id]

if __name__ == "__main__":
    logger.info("Бот запущен")
    while True:
        try:
            bot.polling(none_stop=True, interval=2, timeout=60)
        except Exception as e:
            logger.error(f"Ошибка подключения: {e}")
            time.sleep(10)
