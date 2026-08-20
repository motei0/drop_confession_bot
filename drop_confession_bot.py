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
ADMIN_ID = 1449427026
# Добавляем ?sslmode=require для обхода проблем с сетью
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL and "?" not in DATABASE_URL:
    DATABASE_URL += "?sslmode=require"

router = Router()

def get_db():
    return psycopg2.connect(DATABASE_URL)

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

class ConfessionState(StatesGroup):
    waiting_for_text = State()

@router.message(CommandStart())
async def cmd_start(message: Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🤫 Написать исповедь", callback_data="write")],
        [InlineKeyboardButton(text="📖 Читать ленту", callback_data="read_feed")]
    ])
    await message.answer("Привет! Это анонимная исповедальня.", reply_markup=keyboard)

@router.callback_query(F.data == "write")
async def start_writing(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Напиши свою историю одним сообщением.")
    await state.set_state(ConfessionState.waiting_for_text)
    await callback.answer()

@router.message(ConfessionState.waiting_for_text)
async def process_confession(message: Message, state: FSMContext, bot: Bot):
    text = message.text
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO confessions (user_id, text) VALUES (%s, %s) RETURNING id", (message.from_user.id, text))
            conf_id = cur.fetchone()[0]
            conn.commit()
    await state.clear()
    await message.answer("✅ Отправлено!")
    await bot.send_message(ADMIN_ID, f"Исповедь #{conf_id}:\n{text}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅", callback_data=f"approve_{conf_id}"),
        InlineKeyboardButton(text="❌", callback_data=f"reject_{conf_id}")
    ]]))

@router.callback_query(F.data.startswith("approve_"))
async def approve(callback: CallbackQuery):
    conf_id = callback.data.split("_")[1]
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE confessions SET status = 'approved' WHERE id = %s", (conf_id,))
            conn.commit()
    await callback.message.edit_text(callback.message.text + "\n\n[ОДОБРЕНО]")

@router.callback_query(F.data == "read_feed")
async def read_feed(callback: CallbackQuery):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, text FROM confessions WHERE status = 'approved' ORDER BY RANDOM() LIMIT 1")
            row = cur.fetchone()
    if row:
        await callback.message.edit_text(f"#{row[0]}: {row[1]}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔄", callback_data="read_feed")]]))

app = FastAPI()
@app.get("/")
def index(): return {"status": "ok"}

async def main():
    init_db()
    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await asyncio.gather(
        dp.start_polling(bot),
        uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))).serve()
    )

if __name__ == "__main__":
    asyncio.run(main())
