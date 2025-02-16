import os
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes)

# Настройки
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
IDLE_TIMEOUT = 300  # 5 минут

# Глобальные переменные для хранения состояний
user_sessions = {}

# Логирование
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("Я администратор", callback_data='admin')],
                [InlineKeyboardButton("Я супервайзер", callback_data='supervisor')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите свою роль:", reply_markup=reply_markup)

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if query.data == "admin":
        user_sessions[user_id] = asyncio.create_task(auto_logout(user_id, context))
        await query.message.reply_text("Введите пароль администратора:")
    elif query.data == "supervisor":
        user_sessions[user_id] = asyncio.create_task(auto_logout(user_id, context))
        await query.message.reply_text("Введите пароль супервайзера:")

async def check_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    password = update.message.text.strip()
    
    if password == "admin123":
        await update.message.reply_text("Добро пожаловать, администратор!")
    elif password == "super123":
        await update.message.reply_text("Добро пожаловать, супервайзер!")
    else:
        await update.message.reply_text("Неверный пароль. Попробуйте снова.")

async def auto_logout(user_id, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(IDLE_TIMEOUT)
    context.bot_data.pop(user_id, None)
    logger.info(f"Пользователь {user_id} был отключен из-за бездействия")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Бот завершается через 5 секунд...")
    await asyncio.sleep(5)
    os._exit(0)

# Основная функция
async def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, check_password))
    app.add_handler(CommandHandler("stop", stop))
    
    logger.info("Бот запущен!")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
