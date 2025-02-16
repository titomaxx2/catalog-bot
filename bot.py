import os
import asyncio
import logging
import psycopg2
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Токен бота
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Подключение к базе данных Supabase (PostgreSQL)
DB_URL = os.getenv("DATABASE_URL")
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# Глобальные переменные
active_users = {}
admin_id = int(os.getenv("ADMIN_ID"))  # ID админа

# === ФУНКЦИИ РАБОТЫ С БД ===

# Проверка, является ли пользователь супервайзером
def is_supervisor(user_id):
    cur.execute("SELECT COUNT(*) FROM supervisors WHERE user_id = %s", (user_id,))
    return cur.fetchone()[0] > 0

# Добавление супервайзера
def add_supervisor(user_id):
    cur.execute("INSERT INTO supervisors (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    conn.commit()

# Удаление супервайзера
def remove_supervisor(user_id):
    cur.execute("DELETE FROM supervisors WHERE user_id = %s", (user_id,))
    conn.commit()

# Получение каталога товаров
def get_catalog():
    cur.execute("SELECT id, name, price FROM catalog")
    products = cur.fetchall()
    return products

# Добавление заказа
def add_order(user_id, items):
    cur.execute("INSERT INTO orders (user_id, items, created_at) VALUES (%s, %s, %s)", (user_id, str(items), datetime.now()))
    conn.commit()

# Получение всех заказов
def get_orders():
    cur.execute("SELECT id, user_id, items FROM orders ORDER BY created_at DESC")
    return cur.fetchall()

# === ОБРАБОТЧИКИ ===

# Главное меню
def get_main_menu():
    keyboard = [
        [InlineKeyboardButton("📦 Каталог", callback_data="catalog")],
        [InlineKeyboardButton("🛒 Корзина", callback_data="cart")],
        [InlineKeyboardButton("📜 Оформить заказ", callback_data="order")],
    ]
    return InlineKeyboardMarkup(keyboard)

# Команда /start
async def start(update: Update, context):
    user_id = update.message.chat_id
    active_users[user_id] = datetime.now() + timedelta(minutes=5)  # Таймер активности
    text = "Привет! Я бот для заказов. Выбери действие:"
    if user_id == admin_id:
        text += "\n🔹 Ты админ, доступно: /add_supervisor, /del_supervisor, /orders"
    elif is_supervisor(user_id):
        text += "\n🔹 Ты супервайзер, можешь управлять заказами."
    await update.message.reply_text(text, reply_markup=get_main_menu())

# Обработчик кнопок
async def button_handler(update: Update, context):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == "catalog":
        products = get_catalog()
        text = "📦 Каталог товаров:\n" + "\n".join([f"{p[0]}. {p[1]} - {p[2]}₸" for p in products])
        await query.edit_message_text(text, reply_markup=get_main_menu())

    elif query.data == "cart":
        await query.edit_message_text("🛒 Ваша корзина пуста.", reply_markup=get_main_menu())

    elif query.data == "order":
        await query.edit_message_text("📜 Для оформления заказа напишите ваш адрес.", reply_markup=get_main_menu())

# === АДМИН-КОМАНДЫ ===

# Добавить супервайзера
async def add_supervisor_cmd(update: Update, context):
    if update.message.chat_id != admin_id:
        return
    if len(context.args) == 0:
        await update.message.reply_text("Используй: /add_supervisor [user_id]")
        return
    user_id = int(context.args[0])
    add_supervisor(user_id)
    await update.message.reply_text(f"✅ Супервайзер {user_id} добавлен.")

# Удалить супервайзера
async def del_supervisor_cmd(update: Update, context):
    if update.message.chat_id != admin_id:
        return
    if len(context.args) == 0:
        await update.message.reply_text("Используй: /del_supervisor [user_id]")
        return
    user_id = int(context.args[0])
    remove_supervisor(user_id)
    await update.message.reply_text(f"✅ Супервайзер {user_id} удалён.")

# Показать заказы
async def show_orders(update: Update, context):
    if update.message.chat_id != admin_id and not is_supervisor(update.message.chat_id):
        return
    orders = get_orders()
    if not orders:
        await update.message.reply_text("❌ Заказов нет.")
        return
    text = "\n".join([f"📦 Заказ {o[0]} от {o[1]}: {o[2]}" for o in orders])
    await update.message.reply_text(f"📜 Все заказы:\n{text}")

# Проверка активности пользователей
async def check_inactive_users():
    while True:
        now = datetime.now()
        to_remove = [user for user, timeout in active_users.items() if now > timeout]
        for user in to_remove:
            del active_users[user]
            logger.info(f"Пользователь {user} отключен из-за неактивности.")
        await asyncio.sleep(60)

# === ГЛАВНАЯ ФУНКЦИЯ ===
async def main():
    app = Application.builder().token(TOKEN).build()

    # Обработчики команд и кнопок
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(CommandHandler("add_supervisor", add_supervisor_cmd))
    app.add_handler(CommandHandler("del_supervisor", del_supervisor_cmd))
    app.add_handler(CommandHandler("orders", show_orders))

    # Запуск проверки неактивных пользователей
    asyncio.create_task(check_inactive_users())

    # Запуск бота
    await app.run_polling()

# Запуск
if __name__ == "__main__":
    asyncio.run(main())
