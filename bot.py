import os
import logging
import psycopg2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, CallbackContext
)

# Настройки бота
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Подключение к базе данных Supabase (PostgreSQL)
DB_URL = os.getenv("DATABASE_URL")
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# Логирование
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)

# Состояния для авторизации
ADMIN_AUTH, SUPERVISOR_AUTH = range(2)

# Команда /start
async def start(update: Update, context: CallbackContext):
    buttons = [
        [InlineKeyboardButton("Я администратор", callback_data="admin")],
        [InlineKeyboardButton("Я супервайзер", callback_data="supervisor")]
    ]
    keyboard = InlineKeyboardMarkup(buttons)
    await update.message.reply_text("Выберите вашу роль:", reply_markup=keyboard)

# Обработка нажатия кнопок
async def role_selection(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()

    if query.data == "admin":
        await query.message.reply_text("Введите пароль администратора:")
        return ADMIN_AUTH
    elif query.data == "supervisor":
        await query.message.reply_text("Введите пароль супервайзера:")
        return SUPERVISOR_AUTH

# Проверка пароля администратора
async def admin_auth(update: Update, context: CallbackContext):
    password = update.message.text
    cur.execute("SELECT * FROM admins WHERE password = %s", (password,))
    admin = cur.fetchone()

    if admin:
        await update.message.reply_text("Авторизация успешна! Добро пожаловать, администратор.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Неверный пароль. Попробуйте снова.")
        return ADMIN_AUTH

# Проверка пароля супервайзера
async def supervisor_auth(update: Update, context: CallbackContext):
    password = update.message.text
    cur.execute("SELECT * FROM supervisors WHERE password = %s", (password,))
    supervisor = cur.fetchone()

    if supervisor:
        await update.message.reply_text("Авторизация успешна! Добро пожаловать, супервайзер.")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Неверный пароль. Попробуйте снова.")
        return SUPERVISOR_AUTH

# Функция для добавления товаров в каталог
async def add_product(update: Update, context: CallbackContext):
    await update.message.reply_text("Введите название товара:")

# Основная функция запуска бота
def main():
    app = Application.builder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ADMIN_AUTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_auth)],
            SUPERVISOR_AUTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, supervisor_auth)]
        },
        fallbacks=[],
    )

    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(role_selection))  # Обработчик кнопок
    app.add_handler(CommandHandler("add_product", add_product))

    app.run_polling()

if __name__ == "__main__":
    main()
