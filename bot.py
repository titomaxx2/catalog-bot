import os
import logging
import json
import telebot
import psycopg2
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

# Логирование
logging.basicConfig(level=logging.INFO)

# Получаем переменные окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")

if not TOKEN or not DB_URL:
    raise ValueError("Отсутствуют необходимые переменные окружения")

bot = telebot.TeleBot(TOKEN)
conn = psycopg2.connect(DB_URL, sslmode="require")

# Инициализация БД
with conn:
    with conn.cursor() as cursor:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS supervisors (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE,
            username TEXT UNIQUE,
            password TEXT
        );
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            supervisor_id INT REFERENCES supervisors(id) ON DELETE CASCADE,
            barcode TEXT,
            name TEXT,
            price FLOAT
        );
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id SERIAL PRIMARY KEY,
            supervisor_id INT REFERENCES supervisors(id) ON DELETE CASCADE,
            shop_name TEXT,
            products JSONB
        );
        """)
        conn.commit()

# Авторизованные пользователи
authorized_users = {}

def is_authorized(user_id):
    return user_id in authorized_users

def authorize(user_id, username, password):
    with conn.cursor() as cursor:
        cursor.execute("SELECT id FROM supervisors WHERE username = %s AND password = %s", (username, password))
        supervisor = cursor.fetchone()
        if supervisor:
            authorized_users[user_id] = supervisor[0]
            return True
    return False

@bot.message_handler(commands=['start'])
def start_message(message):
    bot.send_message(message.chat.id, "Введите логин и пароль через пробел (пример: user pass)")

@bot.message_handler(func=lambda message: " " in message.text)
def login(message):
    username, password = message.text.split(" ", 1)
    if authorize(message.chat.id, username, password):
        bot.send_message(message.chat.id, "✅ Вход выполнен!", reply_markup=main_menu())
    else:
        bot.send_message(message.chat.id, "❌ Неверный логин или пароль")

# Главное меню
def main_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("Добавить товар"), KeyboardButton("Создать заказ"))
    return markup

# Добавление товаров
@bot.message_handler(func=lambda message: message.text == "Добавить товар")
def add_product(message):
    if not is_authorized(message.chat.id):
        bot.send_message(message.chat.id, "⛔ Вы не авторизованы!")
        return
    bot.send_message(message.chat.id, "Введите штрихкод, название и цену через запятую (пример: 123456, Молоко, 200)")
    bot.register_next_step_handler(message, process_product)

def process_product(message):
    try:
        barcode, name, price = message.text.split(",")
        price = float(price)
        with conn.cursor() as cursor:
            cursor.execute("""
            INSERT INTO products (supervisor_id, barcode, name, price) 
            VALUES (%s, %s, %s, %s)
            """, (authorized_users[message.chat.id], barcode.strip(), name.strip(), price))
            conn.commit()
        bot.send_message(message.chat.id, "✅ Товар добавлен!", reply_markup=main_menu())
    except Exception as e:
        logging.error(f"Ошибка при добавлении товара: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка ввода!")

# Создание заказа
@bot.message_handler(func=lambda message: message.text == "Создать заказ")
def order(message):
    if not is_authorized(message.chat.id):
        bot.send_message(message.chat.id, "⛔ Вы не авторизованы!")
        return
    bot.send_message(message.chat.id, "Введите название магазина:")
    bot.register_next_step_handler(message, process_order)

def process_order(message):
    shop_name = message.text
    bot.send_message(message.chat.id, "Введите штрихкоды и количество через запятую (пример: 123456:2, 654321:5)")
    bot.register_next_step_handler(message, lambda msg: save_order(msg, shop_name))

def save_order(message, shop_name):
    order_data = []
    try:
        for item in message.text.split(","):
            barcode, quantity = item.split(":")
            order_data.append({"barcode": barcode.strip(), "quantity": int(quantity)})
        with conn.cursor() as cursor:
            cursor.execute("""
            INSERT INTO orders (supervisor_id, shop_name, products) VALUES (%s, %s, %s)
            """, (authorized_users[message.chat.id], shop_name, json.dumps(order_data)))
            conn.commit()
        bot.send_message(message.chat.id, "✅ Заказ сохранен!", reply_markup=main_menu())
    except Exception as e:
        logging.error(f"Ошибка при создании заказа: {e}")
        bot.send_message(message.chat.id, "❌ Ошибка ввода!")

# Запуск polling
try:
    bot.polling(none_stop=True, skip_pending=True)
finally:
    conn.close()
