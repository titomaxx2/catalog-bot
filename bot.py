import telebot
import psycopg2
import logging
from telebot import types

# Логирование
logging.basicConfig(level=logging.INFO)

# Телеграм токен
TOKEN = "ВАШ_ТОКЕН"
bot = telebot.TeleBot(TOKEN)

# Подключение к базе данных
DB_CONFIG = {
    'dbname': 'postgres',
    'user': 'postgres',
    'password': 'ВАШ_ПАРОЛЬ',
    'host': 'db.ваш_supabase_host.com',
    'port': '5432'
}

conn = psycopg2.connect(**DB_CONFIG)

# Хранилище авторизованных пользователей
authorized_users = {}

# Команда /start
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(message.chat.id, "Введите логин и пароль через пробел (пример: user pass)")

# Авторизация пользователя
@bot.message_handler(func=lambda message: ' ' in message.text)
def authorize(message):
    user_id = message.chat.id
    try:
        username, password = message.text.split(" ", 1)
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM supervisors WHERE username = %s AND password = %s", (username, password))
            supervisor = cursor.fetchone()

            if supervisor:
                authorized_users[user_id] = username
                bot.send_message(user_id, "✅ Вход выполнен!")
            else:
                bot.send_message(user_id, "❌ Неверный логин или пароль")
    except Exception as e:
        logging.error(f"Ошибка авторизации: {e}")
        bot.send_message(user_id, "❌ Ошибка входа. Попробуйте снова.")

# Добавить товар
@bot.message_handler(func=lambda message: message.text.lower() == "добавить товар")
def handle_add_product(message):
    user_id = message.chat.id
    if user_id in authorized_users:
        bot.send_message(user_id, "Введите название товара:")
        bot.register_next_step_handler(message, save_product_name)
    else:
        bot.send_message(user_id, "❌ Неверный логин или пароль")

# Сохранение товара
def save_product_name(message):
    user_id = message.chat.id
    if user_id in authorized_users:
        product_name = message.text
        with conn.cursor() as cursor:
            cursor.execute("INSERT INTO products (name, supervisor) VALUES (%s, %s)", (product_name, authorized_users[user_id]))
            conn.commit()
        bot.send_message(user_id, f"✅ Товар '{product_name}' добавлен!")
    else:
        bot.send_message(user_id, "❌ Ошибка: не авторизован.")

# Отладка
@bot.message_handler(commands=['debug'])
def debug(message):
    bot.send_message(message.chat.id, f"Auth users: {authorized_users}")

# Запуск бота
bot.polling(none_stop=True, skip_pending=True)
