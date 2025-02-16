import os
import logging
import json
import telebot
import psycopg2
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Получение переменных окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")

if not TOKEN or not DB_URL:
    raise ValueError("Не заданы необходимые переменные окружения")

bot = telebot.TeleBot(TOKEN)

# Функция для подключения к БД
def get_db_connection():
    return psycopg2.connect(DB_URL, sslmode="require")

# Инициализация БД
def init_db():
    commands = (
        """
        CREATE TABLE IF NOT EXISTS supervisors (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            is_authorized BOOLEAN DEFAULT FALSE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            supervisor_id INT NOT NULL REFERENCES supervisors(id) ON DELETE CASCADE,
            barcode TEXT NOT NULL,
            name TEXT NOT NULL,
            price FLOAT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            supervisor_id INT NOT NULL REFERENCES supervisors(id) ON DELETE CASCADE,
            shop_name TEXT NOT NULL,
            products JSONB NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
        """
    )
    
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            for command in commands:
                cursor.execute(command)
        conn.commit()

init_db()

# Функции работы с БД
def authorize_user(telegram_id: int, username: str, password: str) -> bool:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "UPDATE supervisors SET telegram_id = %s, is_authorized = TRUE "
                "WHERE username = %s AND password = %s RETURNING id",
                (telegram_id, username, password)
            )
            if cursor.fetchone():
                conn.commit()
                return True
            return False

def is_user_authorized(telegram_id: int) -> bool:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT id FROM supervisors WHERE telegram_id = %s AND is_authorized = TRUE",
                (telegram_id,)
            )
            return cursor.fetchone() is not None

# Обработчики команд
@bot.message_handler(commands=['start'])
def handle_start(message):
    bot.send_message(
        message.chat.id,
        "🔑 Введите ваш логин и пароль через пробел (формат: логин пароль)"
    )

@bot.message_handler(func=lambda m: m.text.count(' ') == 1)
def handle_login(message):
    try:
        username, password = message.text.split()
        if authorize_user(message.chat.id, username, password):
            bot.send_message(
                message.chat.id,
                "✅ Успешная авторизация!",
                reply_markup=main_menu()
            )
        else:
            bot.send_message(message.chat.id, "❌ Неверные учетные данные")
    except Exception as e:
        logger.error(f"Ошибка авторизации: {e}")
        bot.send_message(message.chat.id, "⚠️ Ошибка авторизации")

def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("➕ Добавить товар"))
    markup.add(KeyboardButton("📦 Создать заказ"))
    return markup

# Обработчики товаров
@bot.message_handler(func=lambda m: m.text == "➕ Добавить товар")
def handle_add_product(message):
    if not is_user_authorized(message.chat.id):
        bot.send_message(message.chat.id, "🔒 Требуется авторизация!")
        return
    
    bot.send_message(
        message.chat.id,
        "📝 Введите данные товара в формате:\n"
        "<штрихкод>, <название>, <цена>\n"
        "Пример: 46207657112, Молоко 3.2%, 89.99"
    )
    bot.register_next_step_handler(message, process_product)

def process_product(message):
    try:
        barcode, name, price = map(str.strip, message.text.split(',', 2))
        price = float(price)
        
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO products (supervisor_id, barcode, name, price) "
                    "SELECT id, %s, %s, %s FROM supervisors WHERE telegram_id = %s",
                    (barcode, name, price, message.chat.id)
                )
                conn.commit()
        
        bot.send_message(
            message.chat.id,
            "✅ Товар успешно добавлен!",
            reply_markup=main_menu()
        )
    except Exception as e:
        logger.error(f"Ошибка добавления товара: {e}")
        bot.send_message(
            message.chat.id,
            "❌ Ошибка формата данных. Попробуйте еще раз."
        )

# Обработчики заказов
@bot.message_handler(func=lambda m: m.text == "📦 Создать заказ")
def handle_create_order(message):
    if not is_user_authorized(message.chat.id):
        bot.send_message(message.chat.id, "🔒 Требуется авторизация!")
        return
    
    bot.send_message(message.chat.id, "🏪 Введите название магазина:")
    bot.register_next_step_handler(message, process_shop_name)

def process_shop_name(message):
    shop_name = message.text.strip()
    bot.send_message(
        message.chat.id,
        "📦 Введите товары в формате:\n"
        "<штрихкод>:<количество>, ...\n"
        "Пример: 46207657112:2, 46207657113:5"
    )
    bot.register_next_step_handler(
        message, 
        lambda m: process_order_items(m, shop_name)
    )

def process_order_items(message, shop_name):
    try:
        items = []
        for pair in message.text.split(','):
            barcode, qty = map(str.strip, pair.split(':'))
            items.append({
                "barcode": barcode,
                "quantity": int(qty)
            })
        
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO orders (supervisor_id, shop_name, products) "
                    "SELECT id, %s, %s FROM supervisors WHERE telegram_id = %s",
                    (shop_name, json.dumps(items), message.chat.id)
                )
                conn.commit()
        
        bot.send_message(
            message.chat.id,
            "✅ Заказ успешно сохранен!",
            reply_markup=main_menu()
        )
    except Exception as e:
        logger.error(f"Ошибка создания заказа: {e}")
        bot.send_message(
            message.chat.id,
            "❌ Ошибка формата данных. Попробуйте еще раз."
        )

if __name__ == "__main__":
    logger.info("Бот запущен")
    bot.infinity_polling()
