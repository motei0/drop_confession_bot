import os
import asyncio
import psycopg2
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from fastapi import FastAPI
import uvicorn

TOKEN = "8926289462:AAEzYTMq_DdzNER_4AVMKVn1fC0vI2GKI2U"
ADMIN_ID = 1449427026  # Твой Telegram ID для модерации.

# Подключение к Neon.tech через переменную окружения DATABASE_URL в Render
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and "?" not in DATABASE_URL:
    DATABASE_URL += "?sslmode=require"

def get_db():
    return psycopg2.connect(DATABASE_URL)

# Инициализация таблицы в PostgreSQL на старте
def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS confessions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                text TEXT,
                status TEXT DEFAULT 'pending'
            )
            """)
            conn.commit()

init_db()

# Состояния FSM для отправки исповеди
class ConfessionState(StatesGroup):
    waiting_for_text = State()

router = Router()

# Стартовое меню
@router.message(CommandStart())
async def cmd_start(message: Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🤫 Написать исповедь", callback_data="write")],
            [InlineKeyboardButton(text="📖 Читать ленту", callback_data="read_feed")],
        ]
    )
    await message.answer(
        "Привет! Это анонимная исповедальня. Здесь можно высказаться или почитать чужие секреты.\n\nНикто и никогда не узнает, кто автор.",
        reply_markup=keyboard,
    )

# Нажатие на «Написать исповедь»
@router.callback_query(F.data == "write")
async def start_writing(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "Напиши свою историю, секрет или факап одним сообщением. Как только модератор ее проверит, она попадет в ленту."
    )
    await state.set_state(ConfessionState.waiting_for_text)
    await callback.answer()

# Получение текста исповеди от пользователя
@router.message(ConfessionState.waiting_for_text)
async def process_confession(message: Message, state: FSMContext, bot: Bot):
    text = message.text

    # Сохраняем в PostgreSQL (Neon)
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO confessions (user_id, text) VALUES (%s, %s) RETURNING id",
                (message.from_user.id, text),
            )
            conf_id = cur.fetchone()[0]
            conn.commit()

    await state.clear()
    await message.answer(
        "✅ Твоя исповедь отправлена на модерацию! Скоро она появится в ленте."
    )

    # Отправка админу на проверку
    admin_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{conf_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{conf_id}"),
            ]
        ]
    )
    await bot.send_message(
        ADMIN_ID,
        f"<b>Новая исповедь #{conf_id}:</b>\n\n{text}",
        reply_markup=admin_kb,
        parse_mode="HTML",
    )

# Модерация: Одобрить
@router.callback_query(F.data.startswith("approve_"))
async def approve_confession(callback: CallbackQuery, bot: Bot):
    conf_id = int(callback.data.split("_")[1])
    
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM confessions WHERE id = %s", (conf_id,))
            row = cur.fetchone()
            user_id = row[0] if row else None

            cur.execute("UPDATE confessions SET status = 'approved' WHERE id = %s", (conf_id,))
            conn.commit()

    await callback.message.edit_text(
        f"{callback.message.text}\n\n<b>[ОДОБРЕНО]</b>", parse_mode="HTML"
    )
    
    if user_id:
        try:
            await bot.send_message(user_id, "🎉 Поздравляем! Твоя исповедь была одобрена и теперь доступна в ленте.")
        except:
            pass
            
    await callback.answer("Исповедь опубликована!")

# Модерация: Отклонить
@router.callback_query(F.data.startswith("reject_"))
async def reject_confession(callback: CallbackQuery, bot: Bot):
    conf_id = int(callback.data.split("_")[1])
    
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM confessions WHERE id = %s", (conf_id,))
            row = cur.fetchone()
            user_id = row[0] if row else None

            cur.execute("UPDATE confessions SET status = 'rejected' WHERE id = %s", (conf_id,))
            conn.commit()

    await callback.message.edit_text(
        f"{callback.message.text}\n\n<b>[ОТКЛОНЕНО]</b>", parse_mode="HTML"
    )
    
    if user_id:
        try:
            await bot.send_message(user_id, "ℹ️ Твоя исповедь не прошла модерацию.")
        except:
            pass
            
    await callback.answer("Исповедь отклонена.")

# Просмотр ленты
@router.callback_query(F.data == "read_feed")
async def read_feed(callback: CallbackQuery):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, text FROM confessions WHERE status = 'approved' ORDER BY RANDOM() LIMIT 1"
            )
            row = cur.fetchone()

    if not row:
        await callback.answer(
            "В ленте пока нет историй. Стань первым!", show_alert=True
        )
        return

    conf_id, text = row
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Следующая история", callback_data="read_feed")],
            [InlineKeyboardButton(text="✍️ Написать свою", callback_data="write")],
        ]
    )

    try:
        await callback.message.edit_text(
            f"🤫 **Исповедь #{conf_id}**\n\n{text}",
            reply_markup=kb,
            parse_mode="Markdown",
        )
    except Exception:
        await callback.message.answer(
            f"🤫 **Исповедь #{conf_id}**\n\n{text}",
            reply_markup=kb,
            parse_mode="Markdown",
        )
    await callback.answer()

# Админ-панель
@router.message(Command(commands=["admin"]))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, text FROM confessions LIMIT 20")
            rows = cur.fetchall()

    if not rows:
        await message.answer("База данных пуста.")
        return

    for conf_id, text in rows:
        display_text = (text[:30] + '..') if len(text) > 30 else text
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{conf_id}")]
        ])
        await message.answer(f"#{conf_id}: {display_text}", reply_markup=kb)

@router.callback_query(F.data.startswith("del_"))
async def delete_from_admin(callback: CallbackQuery):
    conf_id = int(callback.data.split("_")[1])
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM confessions WHERE id = %s", (conf_id,))
            conn.commit()
    await callback.message.delete()
    await callback.answer("Исповедь удалена из базы!")

app = FastAPI()
@app.get("/")
def index():
    return {"status": "Bot is running!"}

async def main():
    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    
    port = int(os.environ.get("PORT", 8080))
    await asyncio.gather(
        dp.start_polling(bot),
        uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port)).serve()
    )

if __name__ == "__main__":
    asyncio.run(main())
