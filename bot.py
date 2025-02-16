import os
import psycopg2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Updater, CommandHandler, CallbackContext, MessageHandler,
                          filters, CallbackQueryHandler, ConversationHandler)

# Подключение к БД
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:9379992Aasd$@db.loghmyfndcfffvcmkqbz.supabase.co:5432/postgres")
conn = psycopg2.connect(DB_URL, sslmode="require")
cursor = conn.cursor()

# Константы состояний
ADMIN_PASS, SUPERVISOR_PASS, MAIN_MENU = range(3)

# Пароли
ADMIN_PASSWORD = "admin123"
SUPERVISOR_PASSWORD = "super123"

# Словарь для хранения данных сессий
sessions = {}

# Команда /start
def start(update: Update, context: CallbackContext):
    keyboard = [[InlineKeyboardButton("Вход как администратор", callback_data='admin_login')],
                [InlineKeyboardButton("Вход как супервайзер", callback_data='supervisor_login')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Выберите роль для входа:", reply_markup=reply_markup)

# Обработчик кнопок входа
def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()

    if query.data == 'admin_login':
        query.message.reply_text("Введите пароль администратора:")
        return ADMIN_PASS
    elif query.data == 'supervisor_login':
        query.message.reply_text("Введите пароль супервайзера:")
        return SUPERVISOR_PASS

# Проверка пароля администратора
def admin_pass(update: Update, context: CallbackContext):
    if update.message.text == ADMIN_PASSWORD:
        update.message.reply_text("Добро пожаловать, администратор! Вы можете управлять супервайзерами и каталогами.")
        return MAIN_MENU
    else:
        update.message.reply_text("Неверный пароль! Попробуйте снова.")
        return ADMIN_PASS

# Проверка пароля супервайзера
def supervisor_pass(update: Update, context: CallbackContext):
    if update.message.text == SUPERVISOR_PASSWORD:
        update.message.reply_text("Добро пожаловать, супервайзер! Вы можете управлять своим каталогом товаров.")
        return MAIN_MENU
    else:
        update.message.reply_text("Неверный пароль! Попробуйте снова.")
        return SUPERVISOR_PASS

# Главное меню
def main_menu(update: Update, context: CallbackContext):
    keyboard = [[InlineKeyboardButton("Каталог товаров", callback_data='catalog')],
                [InlineKeyboardButton("Выход", callback_data='exit')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Выберите действие:", reply_markup=reply_markup)
    return MAIN_MENU

# Обработчик выхода
def exit_handler(update: Update, context: CallbackContext):
    update.callback_query.message.reply_text("Вы вышли из системы.")
    return ConversationHandler.END

# Создание бота
def main():
    updater = Updater(token=os.getenv("TELEGRAM_BOT_TOKEN"), use_context=True)
    dispatcher = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ADMIN_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_pass)],
            SUPERVISOR_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, supervisor_pass)],
            MAIN_MENU: [CallbackQueryHandler(exit_handler, pattern='exit')]
        },
        fallbacks=[CommandHandler("start", start)]
    )

    dispatcher.add_handler(conv_handler)
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
