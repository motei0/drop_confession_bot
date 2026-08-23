import os
import html
import asyncio
import psycopg2
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardMarkup, KeyboardButton
from fastapi import FastAPI
import uvicorn

TOKEN = "8926289462:AAEzYTMq_DdzNER_4AVMKVn1fC0vI2GKI2U"
ADMIN_ID = 1449427026  # Твой Telegram ID для модерации

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

# Главное нижнее меню (2 кнопки)
def get_main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🤫 Написать исповедь"), KeyboardButton(text="📖 Читать ленту")]
        ],
        resize_keyboard=True
    )

# Нижнее меню во время написания (с кнопкой отмены)
def get_cancel_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="❌ Отменить")]
        ],
        resize_keyboard=True
    )

# Кнопка отмены
@router.message(F.text == "❌ Отменить")
async def cancel_writing(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=get_main_menu())

# Команда /start
@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Это анонимная исповедальня. Здесь можно высказаться или почитать чужие секреты.\n\nНикто и никогда не узнает, кто автор.",
        reply_markup=get_main_menu(),
    )

# Нажатие на кнопку «Написать исповедь» из нижнего меню
@router.message(F.text == "🤫 Написать исповедь")
async def start_writing_text(message: Message, state: FSMContext):
    await state.set_state(ConfessionState.waiting_for_text)
    await message.answer(
        "Напиши свою историю, секрет или факап одним сообщением. Как только модератор ее проверит, она попадет в ленту.",
        reply_markup=get_cancel_menu()
    )

# Получение текста исповеди от пользователя
@router.message(ConfessionState.waiting_for_text)
async def process_confession(message: Message, state: FSMContext, bot: Bot):
    text = message.text

    # Безопасное сохранение в PostgreSQL (Neon) с отловом ошибок
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO confessions (user_id, text) VALUES (%s, %s) RETURNING id",
                    (message.from_user.id, text),
                )
                conf_id = cur.fetchone()[0]
                conn.commit()
    except Exception as e:
        print(f"DB Error: {e}")
        await message.answer("❌ Произошла ошибка при сохранении в базу. Попробуй еще раз /start", reply_markup=get_main_menu())
        await state.clear()
        return

    await state.clear()
    
    # Возвращаем стандартное нижнее меню
    await message.answer(
        "✅ Твоя исповедь отправлена на модерацию! Скоро она появится в ленте.",
        reply_markup=get_main_menu()
    )

    # Экранируем текст для безопасной отправки админу через HTML
    safe_text_for_admin = html.escape(text)

    # Отправка админу на проверку
    admin_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{conf_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{conf_id}"),
            ]
        ]
    )
    try:
        await bot.send_message(
            ADMIN_ID,
            f"<b>Новая исповедь #{conf_id}:</b>\n\n{safe_text_for_admin}",
            reply_markup=admin_kb,
            parse_mode="HTML",
        )
    except Exception as e:
        print(f"Admin send error: {e}")

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

    try:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n<b>[ОДОБРЕНО]</b>", parse_mode="HTML"
        )
    except Exception:
        pass
    
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

    try:
        await callback.message.edit_text(
            f"{callback.message.text}\n\n<b>[ОТКЛОНЕНО]</b>", parse_mode="HTML"
        )
    except Exception:
        pass
    
    if user_id:
        try:
            await bot.send_message(user_id, "ℹ️ Твоя исповедь не прошла модерацию.")
        except:
            pass
            
    await callback.answer("Исповедь отклонена.")

# Функция генерации клавиатуры для ленты с перелистыванием
def get_feed_keyboard(conf_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data=f"feed_prev_{conf_id}"),
                InlineKeyboardButton(text="Вперед ➡️", callback_data=f"feed_next_{conf_id}")
            ]
        ]
    )

# Просмотр ленты (открывает самую последнюю/свежую историю)
@router.message(F.text == "📖 Читать ленту")
async def read_feed_msg(message: Message, state: FSMContext):
    await state.clear()
    
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, text FROM confessions WHERE status = 'approved' ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()

    if not row:
        await message.answer("В ленте пока нет одобренных историй. Стань первым!", reply_markup=get_main_menu())
        return

    conf_id, text = row
    safe_text = html.escape(text)  # Защита от поломки HTML-разметки
    
    await message.answer(
        f"🤫 <b>Исповедь #{conf_id}</b>\n\n{safe_text}",
        reply_markup=get_feed_keyboard(conf_id),
        parse_mode="HTML",
    )

# Перелистывание: Следующая история (более старые по ID)
@router.callback_query(F.data.startswith("feed_next_"))
async def feed_next(callback: CallbackQuery):
    try:
        current_id = int(callback.data.split("_")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка навигации!", show_alert=True)
        return
    
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, text FROM confessions WHERE status = 'approved' AND id < %s ORDER BY id DESC LIMIT 1",
                (current_id,)
            )
            row = cur.fetchone()
            
            # Если дошли до конца, переходим циклично на самую первую (свежую)
            if not row:
                cur.execute(
                    "SELECT id, text FROM confessions WHERE status = 'approved' ORDER BY id DESC LIMIT 1"
                )
                row = cur.fetchone()

    if not row:
        await callback.answer("Больше историй нет!", show_alert=True)
        return

    conf_id, text = row
    safe_text = html.escape(text)  # Экранирование спецсимволов
    
    try:
        await callback.message.edit_text(
            f"🤫 <b>Исповедь #{conf_id}</b>\n\n{safe_text}",
            reply_markup=get_feed_keyboard(conf_id),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()

# Перелистывание: Предыдущая история (более новые по ID)
@router.callback_query(F.data.startswith("feed_prev_"))
async def feed_prev(callback: CallbackQuery):
    try:
        current_id = int(callback.data.split("_")[2])
    except (IndexError, ValueError):
        await callback.answer("Ошибка навигации!", show_alert=True)
        return
    
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, text FROM confessions WHERE status = 'approved' AND id > %s ORDER BY id ASC LIMIT 1",
                (current_id,)
            )
            row = cur.fetchone()
            
            # Если дошли до начала, переходим циклично на самую старую
            if not row:
                cur.execute(
                    "SELECT id, text FROM confessions WHERE status = 'approved' ORDER BY id ASC LIMIT 1"
                )
                row = cur.fetchone()

    if not row:
        await callback.answer("Больше историй нет!", show_alert=True)
        return

    conf_id, text = row
    safe_text = html.escape(text)  # Экранирование спецсимволов
    
    try:
        await callback.message.edit_text(
            f"🤫 <b>Исповедь #{conf_id}</b>\n\n{safe_text}",
            reply_markup=get_feed_keyboard(conf_id),
            parse_mode="HTML",
        )
    except Exception:
        pass
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
        safe_display = html.escape(display_text)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{conf_id}")]
        ])
        await message.answer(f"#{conf_id}: {safe_display}", parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("del_"))
async def delete_from_admin(callback: CallbackQuery):
    conf_id = int(callback.data.split("_")[1])
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM confessions WHERE id = %s", (conf_id,))
            conn.commit()
    try:
        await callback.message.delete()
    except Exception:
        pass
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
