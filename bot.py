import os
import psycopg2
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Загрузка переменных окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")
ADMIN_ID = os.getenv("ADMIN_ID")

if not ADMIN_ID:
    raise ValueError("Переменная окружения ADMIN_ID не задана!")

ADMIN_ID = int(ADMIN_ID)

# Подключение к базе данных Supabase (PostgreSQL)
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# Функция старта
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("Добавить супервайзера", callback_data="add_supervisor")],
            [InlineKeyboardButton("Удалить супервайзера", callback_data="remove_supervisor")],
            [InlineKeyboardButton("Показать супервайзеров", callback_data="list_supervisors")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Вы в режиме админа.", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Привет! Используйте /catalog для работы.")

# Добавление супервайзера
async def add_supervisor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.message.reply_text("Введите Telegram ID супервайзера:")
    context.user_data["adding_supervisor"] = True

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    message_text = update.message.text

    if context.user_data.get("adding_supervisor"):
        try:
            supervisor_id = int(message_text)
            cur.execute("INSERT INTO supervisors (id) VALUES (%s) ON CONFLICT DO NOTHING;", (supervisor_id,))
            conn.commit()
            await update.message.reply_text(f"Супервайзер {supervisor_id} добавлен.")
        except ValueError:
            await update.message.reply_text("Ошибка: введите числовой ID.")
        context.user_data["adding_supervisor"] = False

# Удаление супервайзера
async def remove_supervisor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.message.reply_text("Введите Telegram ID супервайзера для удаления:")
    context.user_data["removing_supervisor"] = True

async def handle_remove_supervisor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    message_text = update.message.text

    if context.user_data.get("removing_supervisor"):
        try:
            supervisor_id = int(message_text)
            cur.execute("DELETE FROM supervisors WHERE id = %s;", (supervisor_id,))
            conn.commit()
            await update.message.reply_text(f"Супервайзер {supervisor_id} удалён.")
        except ValueError:
            await update.message.reply_text("Ошибка: введите числовой ID.")
        context.user_data["removing_supervisor"] = False

# Показ супервайзеров
async def list_supervisors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur.execute("SELECT id FROM supervisors;")
    supervisors = cur.fetchall()
    if supervisors:
        supervisors_list = "\n".join([str(s[0]) for s in supervisors])
        await update.callback_query.message.reply_text(f"Супервайзеры:\n{supervisors_list}")
    else:
        await update.callback_query.message.reply_text("Супервайзеров пока нет.")

# Обработчик команд супервайзера
async def catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    cur.execute("SELECT id FROM supervisors WHERE id = %s;", (user_id,))
    if cur.fetchone():
        await update.message.reply_text("Каталог: (будет реализован)")
    else:
        await update.message.reply_text("Вы не являетесь супервайзером!")

# Callback обработчик
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data == "add_supervisor":
        await add_supervisor(update, context)
    elif query.data == "remove_supervisor":
        await remove_supervisor(update, context)
    elif query.data == "list_supervisors":
        await list_supervisors(update, context)
    await query.answer()

# Главная функция
async def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("catalog", catalog))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
