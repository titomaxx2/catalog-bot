import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackContext, 
                          MessageHandler, filters, CallbackQueryHandler, ConversationHandler)

# Логирование
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# Состояния
ADMIN_AUTH, SUPERVISOR_AUTH, CATALOG_MANAGE, ORDER_PROCESS = range(4)

# Пароли
ADMIN_PASSWORD = "admin123"
SUPERVISOR_PASSWORD = "super123"

# Хранилище пользователей
admins = set()
supervisors = {}

async def start(update: Update, context: CallbackContext) -> int:
    keyboard = [[InlineKeyboardButton("Войти как Админ", callback_data='admin')],
                [InlineKeyboardButton("Войти как Супервайзер", callback_data='supervisor')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите роль для входа:", reply_markup=reply_markup)
    return ADMIN_AUTH

async def login(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "admin":
        await query.message.reply_text("Введите пароль администратора:")
        return ADMIN_AUTH
    elif query.data == "supervisor":
        await query.message.reply_text("Введите пароль супервайзера:")
        return SUPERVISOR_AUTH

async def auth_admin(update: Update, context: CallbackContext) -> int:
    if update.message.text == ADMIN_PASSWORD:
        admins.add(update.message.chat_id)
        await update.message.reply_text("Вы вошли как администратор!")
        return ConversationHandler.END
    else:
        await update.message.reply_text("Неверный пароль!")
        return ADMIN_AUTH

async def auth_supervisor(update: Update, context: CallbackContext) -> int:
    if update.message.text == SUPERVISOR_PASSWORD:
        supervisors[update.message.chat_id] = {}
        await update.message.reply_text("Вы вошли как супервайзер! Теперь можете управлять каталогом.")
        return CATALOG_MANAGE
    else:
        await update.message.reply_text("Неверный пароль!")
        return SUPERVISOR_AUTH

async def add_product(update: Update, context: CallbackContext) -> int:
    chat_id = update.message.chat_id
    if chat_id not in supervisors:
        await update.message.reply_text("Вы не авторизованы!")
        return ConversationHandler.END
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Используйте: /add Товар Цена")
        return CATALOG_MANAGE
    product, price = " ".join(args[:-1]), args[-1]
    supervisors[chat_id][product] = price
    await update.message.reply_text(f"Товар {product} добавлен по цене {price}.")
    return CATALOG_MANAGE

async def show_catalog(update: Update, context: CallbackContext) -> None:
    chat_id = update.message.chat_id
    if chat_id not in supervisors or not supervisors[chat_id]:
        await update.message.reply_text("Каталог пуст.")
        return
    text = "Каталог товаров:\n" + "\n".join([f"{item}: {price}" for item, price in supervisors[chat_id].items()])
    await update.message.reply_text(text)

async def cancel(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text("Операция отменена.")
    return ConversationHandler.END

# Основная функция
def main():
    app = Application.builder().token(os.getenv("TELEGRAM_BOT_TOKEN")).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ADMIN_AUTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, auth_admin)],
            SUPERVISOR_AUTH: [MessageHandler(filters.TEXT & ~filters.COMMAND, auth_supervisor)],
            CATALOG_MANAGE: [CommandHandler("add", add_product), CommandHandler("catalog", show_catalog)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(login))
    app.run_polling()

if __name__ == "__main__":
    main()
