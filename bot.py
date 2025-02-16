import os
import logging
import telebot
import psycopg2
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

# Настраиваем логирование
logging.basicConfig(level=logging.INFO)

# Получаем токен бота и данные для подключения к БД из переменных окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")

if not TOKEN or not DB_URL:
    raise ValueError("Отсутствуют переменные окружения TELEGRAM_BOT_TOKEN или DATABASE_URL")

bot = telebot.TeleBot(TOKEN)
conn = psycopg2.connect(DB_URL, sslmode="require")

# Создаём таблицы, если их нет
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
            price FLOAT,
            image_url TEXT
        );
        """)

# Функция проверки авторизации
def is_authorized(user_id):
    with conn.cursor() as cursor:
        cursor.execute("SELECT id FROM supervisors WHERE telegram_id = %s", (user_id,))
        return cursor.fetchone() is not None

# Обработчик команды /start
@bot.message_handler(commands=['start'])
def start_message(message):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("🔑 Войти"))
    bot.send_message(message.chat.id, "Привет! Это бот супервайзера. Войдите, чтобы продолжить.", reply_markup=markup)

# Обработчик входа (запрос логина)
@bot.message_handler(func=lambda message: message.text == "🔑 Войти")
def login_request(message):
    bot.send_message(message.chat.id, "Введите ваш логин:")
    bot.register_next_step_handler(message, login_process)

# Обработчик ввода логина
def login_process(message):
    username = message.text
    bot.send_message(message.chat.id, "Введите пароль:")
    bot.register_next_step_handler(message, lambda msg: check_credentials(msg, username))

# Проверка логина и пароля
def check_credentials(message, username):
    password = message.text
    with conn.cursor() as cursor:
        cursor.execute("SELECT id FROM supervisors WHERE username = %s AND password = %s", (username, password))
        supervisor = cursor.fetchone()
        if supervisor:
            cursor.execute("UPDATE supervisors SET telegram_id = %s WHERE id = %s", (message.chat.id, supervisor[0]))
            conn.commit()
            bot.send_message(message.chat.id, "✅ Успешный вход! Вы можете управлять каталогом товаров.")
        else:
            bot.send_message(message.chat.id, "❌ Неверный логин или пароль. Попробуйте снова.")

# Обработчик команды /add_product
@bot.message_handler(commands=['add_product'])
def add_product(message):
    if not is_authorized(message.chat.id):
        bot.send_message(message.chat.id, "⛔ Вы не авторизованы!")
        return
    bot.send_message(message.chat.id, "Введите штрихкод товара:")
    bot.register_next_step_handler(message, process_barcode)

# Ввод штрихкода
def process_barcode(message):
    barcode = message.text
    bot.send_message(message.chat.id, "Введите название товара:")
    bot.register_next_step_handler(message, lambda msg: process_name(msg, barcode))

# Ввод названия товара
def process_name(message, barcode):
    name = message.text
    bot.send_message(message.chat.id, "Введите цену товара:")
    bot.register_next_step_handler(message, lambda msg: process_price(msg, barcode, name))

# Ввод цены
def process_price(message, barcode, name):
    try:
        price = float(message.text)
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM supervisors WHERE telegram_id = %s", (message.chat.id,))
            supervisor = cursor.fetchone()
            if supervisor:
                cursor.execute("""
                INSERT INTO products (supervisor_id, barcode, name, price)
                VALUES (%s, %s, %s, %s)
                """, (supervisor[0], barcode, name, price))
                conn.commit()
                bot.send_message(message.chat.id, "✅ Товар успешно добавлен!")
            else:
                bot.send_message(message.chat.id, "⛔ Ошибка авторизации!")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Ошибка! Введите корректную цену.")

# Запуск бота
try:
    bot.polling(none_stop=True, skip_pending=True)
finally:
    conn.close()
