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

if not TOKEN or not DB_URL:
    raise ValueError("Отсутствуют переменные окружения TELEGRAM_BOT_TOKEN или DATABASE_URL")

bot = telebot.TeleBot(TOKEN)
conn = psycopg2.connect(DB_URL, sslmode="require")

# Функция проверки логина и пароля
def check_credentials(username, password):
    with conn.cursor() as cursor:
        cursor.execute("SELECT id FROM supervisors WHERE username = %s AND password = %s", (username, password))
        return cursor.fetchone() is not None

# Обработчик команды /start
@bot.message_handler(commands=['start'])
def start_message(message):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("🔑 Войти"))
    bot.send_message(message.chat.id, "Привет! Это бот супервайзера. Войдите, чтобы продолжить.", reply_markup=markup)

# Вход в систему
@bot.message_handler(func=lambda message: message.text == "🔑 Войти")
def login_request(message):
    bot.send_message(message.chat.id, "Введите ваш логин:")
    bot.register_next_step_handler(message, login_process)

# Ввод логина
def login_process(message):
    username = message.text
    bot.send_message(message.chat.id, "Введите пароль:")
    bot.register_next_step_handler(message, lambda msg: authenticate_user(msg, username))

# Проверка пароля
def authenticate_user(message, username):
    password = message.text
    if check_credentials(username, password):
        bot.send_message(message.chat.id, f"✅ Успешный вход, {username}! Теперь вы можете управлять каталогом.")
    else:
        bot.send_message(message.chat.id, "❌ Неверный логин или пароль. Попробуйте снова.")

# Запуск бота
try:
    bot.polling(none_stop=True, skip_pending=True)
finally:
    conn.close()
