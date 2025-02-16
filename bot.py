import os
import logging
import telebot
import psycopg2
from telebot.types import ReplyKeyboardMarkup, KeyboardButton

# Настраиваем логирование
logging.basicConfig(level=logging.INFO)

# Получаем токен бота и данные для подключения к БД из переменных окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")

bot = telebot.TeleBot(TOKEN)

# Подключение к БД
conn = psycopg2.connect(DB_URL, sslmode="require")
cursor = conn.cursor()

# Создаём таблицы, если их нет
cursor.execute("""
CREATE TABLE IF NOT EXISTS supervisors (
    id SERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE,
    username TEXT,
    password TEXT
);
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    supervisor_id INT REFERENCES supervisors(id),
    barcode TEXT,
    name TEXT,
    price FLOAT,
    image_url TEXT
);
""")
conn.commit()

# Команда /start
@bot.message_handler(commands=['start'])
def start_message(message):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("🔑 Войти"))
    bot.send_message(message.chat.id, "Привет! Это бот супервайзера. Войдите, чтобы продолжить.", reply_markup=markup)

# Запуск бота
bot.polling(none_stop=True)
