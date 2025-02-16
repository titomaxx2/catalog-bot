import os
import psycopg2
import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from urllib.parse import urlparse

# Настройки
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")

bot = telebot.TeleBot(TOKEN)

# Подключение к БД
url = urlparse(DB_URL)
conn = psycopg2.connect(
    dbname=url.path[1:], user=url.username, password=url.password,
    host=url.hostname, port=url.port, sslmode="require"
)
cursor = conn.cursor()

# Создание таблиц
cursor.execute("""
CREATE TABLE IF NOT EXISTS supervisors (
    id SERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog (
    id SERIAL PRIMARY KEY,
    supervisor_id INTEGER REFERENCES supervisors(id),
    barcode TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    image TEXT,
    price NUMERIC(10,2) NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    supervisor_id INTEGER REFERENCES supervisors(id),
    customer TEXT NOT NULL,
    total_price NUMERIC(10,2) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS order_items (
    id SERIAL PRIMARY KEY,
    order_id INTEGER REFERENCES orders(id),
    product_id INTEGER REFERENCES catalog(id),
    quantity INTEGER NOT NULL
);
""")
conn.commit()

# Авторизация
sessions = {}

def get_supervisor(user_id):
    return sessions.get(user_id)

@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "Привет! Введите логин супервайзера:")
    bot.register_next_step_handler(message, login_step)

def login_step(message):
    username = message.text
    bot.send_message(message.chat.id, "Введите пароль:")
    bot.register_next_step_handler(message, lambda msg: check_login(msg, username))

def check_login(message, username):
    password = message.text
    cursor.execute("SELECT id FROM supervisors WHERE username=%s AND password=%s", (username, password))
    result = cursor.fetchone()
    if result:
        sessions[message.chat.id] = result[0]
        main_menu(message)
    else:
        bot.send_message(message.chat.id, "Ошибка авторизации. Попробуйте снова /start")

def main_menu(message):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("Каталог", "Оформить заказ", "Администрирование")
    bot.send_message(message.chat.id, "Главное меню:", reply_markup=markup)

# Каталог
@bot.message_handler(func=lambda msg: msg.text == "Каталог")
def catalog(message):
    supervisor_id = get_supervisor(message.chat.id)
    if not supervisor_id:
        return
    cursor.execute("SELECT barcode, name, price FROM catalog WHERE supervisor_id=%s", (supervisor_id,))
    products = cursor.fetchall()
    text = "Каталог товаров:\n" + '\n'.join(f"{p[1]} ({p[0]}) - {p[2]} KZT" for p in products)
    bot.send_message(message.chat.id, text)

# Оформление заказа
@bot.message_handler(func=lambda msg: msg.text == "Оформить заказ")
def order(message):
    bot.send_message(message.chat.id, "Введите имя заказчика:")
    bot.register_next_step_handler(message, lambda msg: create_order(msg, []))

def create_order(message, items):
    customer = message.text
    bot.send_message(message.chat.id, "Введите штрихкод товара:")
    bot.register_next_step_handler(message, lambda msg: add_order_item(msg, customer, items))

def add_order_item(message, customer, items):
    barcode = message.text
    cursor.execute("SELECT id, name, price FROM catalog WHERE barcode=%s", (barcode,))
    product = cursor.fetchone()
    if not product:
        bot.send_message(message.chat.id, "Товар не найден. Попробуйте снова:")
        return bot.register_next_step_handler(message, lambda msg: add_order_item(msg, customer, items))
    bot.send_message(message.chat.id, "Введите количество:")
    bot.register_next_step_handler(message, lambda msg: finalize_order(msg, customer, items, product))

def finalize_order(message, customer, items, product):
    try:
        quantity = int(message.text)
        items.append((product[0], quantity))
        bot.send_message(message.chat.id, "Добавить еще товар? (да/нет)")
        bot.register_next_step_handler(message, lambda msg: add_more_items(msg, customer, items))
    except ValueError:
        bot.send_message(message.chat.id, "Некорректное количество. Введите число:")
        bot.register_next_step_handler(message, lambda msg: finalize_order(msg, customer, items, product))

def add_more_items(message, customer, items):
    if message.text.lower() == "да":
        bot.send_message(message.chat.id, "Введите штрихкод товара:")
        return bot.register_next_step_handler(message, lambda msg: add_order_item(msg, customer, items))
    supervisor_id = get_supervisor(message.chat.id)
    total_price = sum(q * cursor.execute("SELECT price FROM catalog WHERE id=%s", (p,)).fetchone()[0] for p, q in items)
    cursor.execute("INSERT INTO orders (supervisor_id, customer, total_price) VALUES (%s, %s, %s) RETURNING id", (supervisor_id, customer, total_price))
    order_id = cursor.fetchone()[0]
    for product_id, quantity in items:
        cursor.execute("INSERT INTO order_items (order_id, product_id, quantity) VALUES (%s, %s, %s)", (order_id, product_id, quantity))
    conn.commit()
    bot.send_message(message.chat.id, "Заказ оформлен!")

# Запуск
bot.polling(none_stop=True)
