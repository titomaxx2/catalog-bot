import os
import psycopg2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Updater, CommandHandler, CallbackContext, MessageHandler, filters, CallbackQueryHandler, ConversationHandler

# Подключение к базе данных
def get_db_connection():
    return psycopg2.connect(os.getenv("DATABASE_URL"))

# Состояния для авторизации
AUTH_ROLE, AUTH_USERNAME, AUTH_PASSWORD = range(3)

# Хранение временных данных пользователя
user_data = {}

def start(update: Update, context: CallbackContext):
    keyboard = [[InlineKeyboardButton("Администратор", callback_data='admin'),
                 InlineKeyboardButton("Супервайзер", callback_data='supervisor')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("Выберите роль:", reply_markup=reply_markup)
    return AUTH_ROLE

def auth_role(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    context.user_data['role'] = query.data
    query.message.reply_text("Введите ваше имя:")
    return AUTH_USERNAME

def auth_username(update: Update, context: CallbackContext):
    context.user_data['username'] = update.message.text
    update.message.reply_text("Введите пароль:")
    return AUTH_PASSWORD

def auth_password(update: Update, context: CallbackContext):
    username = context.user_data['username']
    password = update.message.text
    role = context.user_data['role']
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    if role == "admin":
        cur.execute("SELECT * FROM admins WHERE username=%s AND password=%s", (username, password))
    else:
        cur.execute("SELECT * FROM supervisors WHERE username=%s AND password=%s", (username, password))
    
    user = cur.fetchone()
    cur.close()
    conn.close()
    
    if user:
        update.message.reply_text(f"Успешный вход как {role} {username}!")
        return ConversationHandler.END
    else:
        update.message.reply_text("Неверное имя или пароль! Попробуйте снова.")
        return AUTH_USERNAME

def cancel(update: Update, context: CallbackContext):
    update.message.reply_text("Авторизация отменена.")
    return ConversationHandler.END

def main():
    updater = Updater(os.getenv("TELEGRAM_BOT_TOKEN"))
    dp = updater.dispatcher

    auth_conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            AUTH_ROLE: [CallbackQueryHandler(auth_role)],
            AUTH_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, auth_username)],
            AUTH_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, auth_password)],
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )
    
    dp.add_handler(auth_conv_handler)
    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
