import os
import logging
import psycopg2
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Загружаем переменные окружения
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_URL = os.getenv("DATABASE_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # ID админа

# Подключение к базе данных Supabase
conn = psycopg2.connect(DB_URL)
cur = conn.cursor()

# Глобальный список пользователей (супервайзеров)
supervisors = {}

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

    # Обработка логина
    if " " in text:
        login, password = text.split(" ", 1)
        cur.execute("SELECT id FROM supervisors WHERE login = %s AND password = %s", (login, password))
        result = cur.fetchone()
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

    if query.data == "add_supervisor":
        await query.message.reply_text("Введите данные супервайзера: логин пароль")
    elif query.data == "remove_supervisor":
        cur.execute("SELECT login FROM supervisors")
        supervisors_list = cur.fetchall()
        if not supervisors_list:
            await query.message.reply_text("Нет зарегистрированных супервайзеров.")
            return

        keyboard = [[InlineKeyboardButton(sup[0], callback_data=f"del_{sup[0]}")] for sup in supervisors_list]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Выберите супервайзера для удаления:", reply_markup=reply_markup)

    elif query.data.startswith("del_"):
        login_to_delete = query.data[4:]
        cur.execute("DELETE FROM supervisors WHERE login = %s", (login_to_delete,))
        conn.commit()
        await query.message.reply_text(f"Супервайзер {login_to_delete} удален.")

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

    # Убедимся, что цикл событий не запущен
    try:
        asyncio.run(main())
    except RuntimeError as e:
        if str(e) == "Event loop is closed":
            # Если цикл событий уже закрыт, запускаем его вручную
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(main())
        else:
            raise e
