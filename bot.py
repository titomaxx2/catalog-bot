import os
import logging
import psycopg2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackContext, MessageHandler, Filters, CallbackQueryHandler, ConversationHandler

# Настройки
ADMIN_PASSWORD = "admin1234"  # Пароль администратора
SUPERVISOR_PASSWORD = "supervisor5678"  # Пароль супервайзеров
DB_URL = os.getenv("DATABASE_URL")

# Логирование
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Состояния диалога
LOGIN, MENU, ADD_PRODUCT, REMOVE_PRODUCT, ORDER, ADMIN_MENU = range(6)

# Подключение к БД
def get_db_connection():
    return psycopg2.connect(DB_URL, sslmode="require")

# Стартовое сообщение
def start(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("Привет! Введите пароль для входа.")
    return LOGIN

# Обработка пароля
def login(update: Update, context: CallbackContext) -> int:
    password = update.message.text
    user_id = update.message.chat_id
    
    if password == ADMIN_PASSWORD:
        context.user_data['role'] = 'admin'
        return admin_menu(update, context)
    elif password == SUPERVISOR_PASSWORD:
        context.user_data['role'] = 'supervisor'
        return menu(update, context)
    else:
        update.message.reply_text("Неверный пароль! Попробуйте снова.")
        return LOGIN

# Главное меню для супервайзеров
def menu(update: Update, context: CallbackContext) -> int:
    keyboard = [[InlineKeyboardButton("Добавить товар", callback_data='add_product')],
                [InlineKeyboardButton("Удалить товар", callback_data='remove_product')],
                [InlineKeyboardButton("Оформить заказ", callback_data='order')]]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Выберите действие:", reply_markup=reply_markup)
    return MENU

# Административное меню
def admin_menu(update: Update, context: CallbackContext) -> int:
    keyboard = [[InlineKeyboardButton("Добавить супервайзера", callback_data='add_supervisor')],
                [InlineKeyboardButton("Удалить супервайзера", callback_data='remove_supervisor')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Админ-меню:", reply_markup=reply_markup)
    return ADMIN_MENU

# Добавление товара
def add_product(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("Введите название товара:")
    return ADD_PRODUCT

def save_product(update: Update, context: CallbackContext) -> int:
    product_name = update.message.text
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO products (name) VALUES (%s)", (product_name,))
            conn.commit()
    update.message.reply_text("Товар добавлен!")
    return MENU

# Удаление товара
def remove_product(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("Введите название товара для удаления:")
    return REMOVE_PRODUCT

def delete_product(update: Update, context: CallbackContext) -> int:
    product_name = update.message.text
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM products WHERE name = %s", (product_name,))
            conn.commit()
    update.message.reply_text("Товар удален!")
    return MENU

# Обработка кнопок
def button(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    query.answer()
    
    if query.data == "add_product":
        query.message.reply_text("Введите название товара:")
        return ADD_PRODUCT
    elif query.data == "remove_product":
        query.message.reply_text("Введите название товара для удаления:")
        return REMOVE_PRODUCT
    elif query.data == "order":
        query.message.reply_text("Оформление заказа пока недоступно.")
        return MENU
    elif query.data == "add_supervisor":
        query.message.reply_text("Введите имя нового супервайзера:")
        return ADMIN_MENU
    elif query.data == "remove_supervisor":
        query.message.reply_text("Введите имя супервайзера для удаления:")
        return ADMIN_MENU

# Основная функция запуска бота
def main():
    updater = Updater(os.getenv("TELEGRAM_BOT_TOKEN"), use_context=True)
    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            LOGIN: [MessageHandler(Filters.text & ~Filters.command, login)],
            MENU: [CallbackQueryHandler(button)],
            ADD_PRODUCT: [MessageHandler(Filters.text & ~Filters.command, save_product)],
            REMOVE_PRODUCT: [MessageHandler(Filters.text & ~Filters.command, delete_product)],
            ADMIN_MENU: [CallbackQueryHandler(button)],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    dp.add_handler(conv_handler)
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
