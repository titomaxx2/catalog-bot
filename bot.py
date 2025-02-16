import os
import logging
import asyncpg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загружаем переменные окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # ID админа

# Глобальный список пользователей (супервайзеров)
supervisors = {}

# Подключение к базе данных
async def connect_db():
    return await asyncpg.connect(DB_URL)

# Команда /start
async def start(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    if user_id == ADMIN_ID:
        keyboard = [
            [InlineKeyboardButton("Добавить супервайзера", callback_data="add_supervisor")],
            [InlineKeyboardButton("Удалить супервайзера", callback_data="remove_supervisor")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("Вы вошли как админ. Выберите действие:", reply_markup=reply_markup)
    else:
        await update.message.reply_text("Добро пожаловать! Войдите в систему с помощью /login")

# Авторизация супервайзера
async def login(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("Введите логин и пароль в формате: логин пароль")

async def handle_message(update: Update, context: CallbackContext) -> None:
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if " " in text:
        login, password = text.split(" ", 1)
        conn = await connect_db()
        result = await conn.fetchrow("SELECT id FROM supervisors WHERE login = $1 AND password = $2", login, password)
        await conn.close()

        if result:
            supervisors[user_id] = login
            await update.message.reply_text("Вы успешно вошли! Используйте /catalog для работы с каталогом.")
        else:
            await update.message.reply_text("Ошибка: неверный логин или пароль.")

# Обработка нажатий админ-кнопок
async def button_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    user_id = query.from_user.id

    if user_id != ADMIN_ID:
        await query.answer("У вас нет прав!", show_alert=True)
        return

    conn = await connect_db()

    if query.data == "add_supervisor":
        await query.message.reply_text("Введите данные супервайзера: логин пароль")
    elif query.data == "remove_supervisor":
        supervisors_list = await conn.fetch("SELECT login FROM supervisors")
        if not supervisors_list:
            await query.message.reply_text("Нет зарегистрированных супервайзеров.")
            await conn.close()
            return

        keyboard = [[InlineKeyboardButton(sup["login"], callback_data=f"del_{sup['login']}")] for sup in supervisors_list]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Выберите супервайзера для удаления:", reply_markup=reply_markup)
    elif query.data.startswith("del_"):
        login_to_delete = query.data[4:]
        await conn.execute("DELETE FROM supervisors WHERE login = $1", login_to_delete)
        await query.message.reply_text(f"Супервайзер {login_to_delete} удален.")

    await conn.close()
    await query.answer()

# Функция запуска бота
async def main() -> None:
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("login", login))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_callback))

    await app.run_polling()

# Запуск бота
if __name__ == "__main__":
    import asyncio
    
    try:
        asyncio.run(main())
    except RuntimeError as e:
        if "Event loop is closed" in str(e):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(main())
        else:
            raise
