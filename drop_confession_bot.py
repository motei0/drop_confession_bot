import os
import asyncio
import asyncpg
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from fastapi import FastAPI
import uvicorn

TOKEN = "8926289462:AAEzYTMq_DdzNER_4AVMKVn1fC0vI2GKI2U"
ADMIN_ID = 1449427026
DATABASE_URL = os.environ.get("DATABASE_URL")

router = Router()

# Глобальный пул соединений с базой
db_pool = None

async def init_db():
    global db_pool
    # Создаем пул подключений к Supabase
    db_pool = await asyncpg.create_pool(DATABASE_URL)
    async with db_pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS confessions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                text TEXT,
                status TEXT DEFAULT 'pending'
            )
        """)

class ConfessionState(StatesGroup):
    waiting_for_text = State()

@router.message(CommandStart())
async def cmd_start(message: Message):
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🤫 Написать исповедь", callback_data="write")],
            [InlineKeyboardButton(text="📖 Читать ленту", callback_data="read_feed")],
        ]
    )
    await message.answer("Привет! Это анонимная исповедальня.", reply_markup=keyboard)

@router.callback_query(F.data == "write")
async def start_writing(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Напиши свою историю одним сообщением.")
    await state.set_state(ConfessionState.waiting_for_text)
    await callback.answer()

@router.message(ConfessionState.waiting_for_text)
async def process_confession(message: Message, state: FSMContext, bot: Bot):
    text = message.text
    async with db_pool.acquire() as conn:
        conf_id = await conn.fetchval(
            "INSERT INTO confessions (user_id, text) VALUES ($1, $2) RETURNING id",
            message.from_user.id, text
        )

    await state.clear()
    await message.answer("✅ Отправлено на модерацию!")
    
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{conf_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{conf_id}")
    ]])
    await bot.send_message(ADMIN_ID, f"<b>Исповедь #{conf_id}:</b>\n\n{text}", reply_markup=admin_kb, parse_mode="HTML")

@router.callback_query(F.data.startswith("approve_"))
async def approve_confession(callback: CallbackQuery, bot: Bot):
    conf_id = int(callback.data.split("_")[1])
    async with db_pool.acquire() as conn:
        user_id = await conn.fetchval("SELECT user_id FROM confessions WHERE id = $1", conf_id)
        await conn.execute("UPDATE confessions SET status = 'approved' WHERE id = $1", conf_id)

    await callback.message.edit_text(f"{callback.message.text}\n\n<b>[ОДОБРЕНО]</b>", parse_mode="HTML")
    if user_id:
        try: await bot.send_message(user_id, "🎉 Твоя исповедь одобрена!")
        except: pass
    await callback.answer("Опубликовано!")

@router.callback_query(F.data.startswith("reject_"))
async def reject_confession(callback: CallbackQuery, bot: Bot):
    conf_id = int(callback.data.split("_")[1])
    async with db_pool.acquire() as conn:
        await conn.execute("UPDATE confessions SET status = 'rejected' WHERE id = $1", conf_id)
    await callback.message.edit_text(f"{callback.message.text}\n\n<b>[ОТКЛОНЕНО]</b>", parse_mode="HTML")
    await callback.answer("Отклонено.")

@router.callback_query(F.data == "read_feed")
async def read_feed(callback: CallbackQuery):
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT id, text FROM confessions WHERE status = 'approved' ORDER BY RANDOM() LIMIT 1")
    
    if not row:
        await callback.answer("Пока пусто.", show_alert=True)
        return
        
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Следующая", callback_data="read_feed")],
        [InlineKeyboardButton(text="✍️ Написать свою", callback_data="write")]
    ])
    await callback.message.edit_text(f"🤫 **Исповедь #{row['id']}**\n\n{row['text']}", reply_markup=kb, parse_mode="Markdown")

@router.message(Command(commands=["admin"]))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID: return
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, text FROM confessions LIMIT 20")
    
    for row in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{row['id']}")]])
        await message.answer(f"#{row['id']}: {row['text'][:30]}..", reply_markup=kb)

@router.callback_query(F.data.startswith("del_"))
async def delete_from_admin(callback: CallbackQuery):
    conf_id = int(callback.data.split("_")[1])
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM confessions WHERE id = $1", conf_id)
    await callback.message.delete()
    await callback.answer("Удалено!")

app = FastAPI()
@app.get("/")
def index(): return {"status": "Bot is running!"}

async def main():
    await init_db()
    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await bot.delete_webhook(drop_pending_updates=True)
    await asyncio.gather(
        dp.start_polling(bot),
        uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))).serve()
    )

if __name__ == "__main__":
    asyncio.run(main())
