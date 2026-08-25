import os
import html
import time
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

# Словари для кулдаунов
cooldowns = {}          # Кулдаун на отправку исповедей (user_id: timestamp)
click_cooldowns = {}    # Защита от спама кнопками ленты (user_id: timestamp)

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

# Состояния FSM
class ConfessionState(StatesGroup):
    waiting_for_text = State()

router = Router()

def get_main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🤫 Написать исповедь"), KeyboardButton(text="📖 Читать ленту")]
        ],
        resize_keyboard=True
    )

def get_cancel_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="❌ Отменить")]
        ],
        resize_keyboard=True
    )

@router.message(F.text == "❌ Отменить")
async def cancel_writing(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.", reply_markup=get_main_menu())

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Привет! Это анонимная исповедальня. Здесь можно высказаться или почитать чужие секреты.\n\nНикто и никогда не узнает, кто автор.",
        reply_markup=get_main_menu(),
    )

@router.message(F.text == "🤫 Написать исповедь")
async def start_writing_text(message: Message, state: FSMContext):
    user_id = message.from_user.id
    now = time.time()
    
    # Проверка кулдауна отправки (30 секунд)
    if user_id in cooldowns and now - cooldowns[user_id] < 30:
        left = int(30 - (now - cooldowns[user_id]))
        await message.answer(f"⏳ Подожди еще {left} сек., прежде чем отправлять новую исповедь.")
        return

    # Если пользователь уже в процессе написания
    current_state = await state.get_state()
    if current_state == ConfessionState.waiting_for_text.state:
        await message.answer("Ты уже в режиме создания истории. Просто отправь её следующим сообщением или нажми «❌ Отменить».")
        return

    await state.set_state(ConfessionState.waiting_for_text)
    await message.answer(
        "Напиши свою историю, секрет или факап одним сообщением (от 50 до 2500 символов). Как только модератор ее проверит, она попадет в ленту.",
        reply_markup=get_cancel_menu()
    )

@router.message(ConfessionState.waiting_for_text)
async def process_confession(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    now = time.time()

    # Жёсткая проверка кулдауна первым делом
    if user_id in cooldowns and now - cooldowns[user_id] < 30:
        left = int(30 - (now - cooldowns[user_id]))
        await message.answer(f"⏳ Подожди еще {left} сек., прежде чем отправлять новую исповедь.")
        return

    # Обработка кнопок меню во время ввода
    if message.text in ["🤫 Написать исповедь", "📖 Читать ленту"]:
        return
    if message.text == "❌ Отменить":
        await state.clear()
        await message.answer("Действие отменено.", reply_markup=get_main_menu())
        return

    text = message.text

    # Проверка лимита символов (от 50 до 2500)
    if len(text) < 50:
        await message.answer(
            f"❌ Твоя исповедь слишком короткая ({len(text)} из мин. 50 символов).\n"
            "Пожалуйста, расскажи историю подробнее.",
            reply_markup=get_cancel_menu()
        )
        return

    if len(text) > 2500:
        await message.answer(
            f"❌ Твоя исповедь слишком длинная ({len(text)} из макс. 2500 символов).\n"
            "Пожалуйста, немного сократи текст.",
            reply_markup=get_cancel_menu()
        )
        return

    username = f"@{message.from_user.username}" if message.from_user.username else "Отсутствует"

    # Устанавливаем кулдаун сразу при успешном принятии текста
    cooldowns[user_id] = now

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO confessions (user_id, text) VALUES (%s, %s) RETURNING id",
                    (user_id, text),
                )
                conf_id = cur.fetchone()[0]
                conn.commit()
    except Exception as e:
        print(f"DB Error: {e}")
        await message.answer("❌ Произошла ошибка при сохранении в базу. Попробуй еще раз /start", reply_markup=get_main_menu())
        await state.clear()
        return

    await state.clear()
    await message.answer(
        "✅ Твоя исповедь отправлена на модерацию! Скоро она появится в ленте.",
        reply_markup=get_main_menu()
    )

    safe_text_for_admin = html.escape(text)
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
            f"<b>Новая исповедь</b>\n"
            f"👤 <b>Автор:</b> {username}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n\n"
            f"{safe_text_for_admin}",
            reply_markup=admin_kb,
            parse_mode="HTML",
        )
    except Exception as e:
        print(f"Admin send error: {e}")

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
        await callback.message.edit_text(f"{callback.message.text}\n\n<b>[ОДОБРЕНО]</b>", parse_mode="HTML")
    except Exception:
        pass
    
    if user_id:
        try:
            await bot.send_message(user_id, "🎉 Поздравляем! Твоя исповедь была одобрена и теперь доступна в ленте.")
        except:
            pass
            
    await callback.answer("Исповедь опубликована!")

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
        await callback.message.edit_text(f"{callback.message.text}\n\n<b>[ОТКЛОНЕНО]</b>", parse_mode="HTML")
    except Exception:
        pass
    
    if user_id:
        try:
            await bot.send_message(user_id, "ℹ️ Твоя исповедь не прошла модерацию.")
        except:
            pass
            
    await callback.answer("Исповедь отклонена.")

def get_feed_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ Назад", callback_data="feed_prev"),
                InlineKeyboardButton(text="Вперед ➡️", callback_data="feed_next")
            ]
        ]
    )

@router.message(F.text == "📖 Читать ленту")
async def read_feed_msg(message: Message, state: FSMContext):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, text FROM confessions WHERE status = 'approved' ORDER BY RANDOM() LIMIT 1")
            row = cur.fetchone()

    if not row:
        await message.answer("В ленте пока нет одобренных историй. Стань первым!", reply_markup=get_main_menu())
        return

    conf_id, text = row
    
    await state.set_data({
        "viewed_history": [conf_id],
        "current_index": 0
    })

    safe_text = html.escape(text)
    await message.answer(
        f"🤫 <b>Исповедь</b>\n\n{safe_text}",
        reply_markup=get_feed_keyboard(),
        parse_mode="HTML",
    )

@router.callback_query(F.data == "feed_next")
async def feed_next(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    now = time.time()

    # Защита от спама кнопками ленты (интервал 0.6 секунды)
    if user_id in click_cooldowns and now - click_cooldowns[user_id] < 0.6:
        await callback.answer("⚠️ Не так быстро!", show_alert=False)
        return
    click_cooldowns[user_id] = now

    data = await state.get_data()
    viewed = data.get("viewed_history", [])
    index = data.get("current_index", 0)

    text = None
    conf_id = None

    if index >= len(viewed) - 1:
        if not viewed:
            viewed = []
            index = -1

        with get_db() as conn:
            with conn.cursor() as cur:
                if viewed:
                    format_strings = ','.join(['%s'] * len(viewed))
                    query = f"SELECT id, text FROM confessions WHERE status = 'approved' AND id NOT IN ({format_strings}) ORDER BY RANDOM() LIMIT 1"
                    cur.execute(query, tuple(viewed))
                else:
                    cur.execute("SELECT id, text FROM confessions WHERE status = 'approved' ORDER BY RANDOM() LIMIT 1")
                
                row = cur.fetchone()

        if not row:
            await callback.answer("🎉 Ты просмотрел все доступные истории в этой сессии!", show_alert=True)
            return

        conf_id, text = row
        viewed.append(conf_id)
        index = len(viewed) - 1
    else:
        while index < len(viewed) - 1:
            index += 1
            conf_id = viewed[index]
            
            with get_db() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT text FROM confessions WHERE id = %s AND status = 'approved'", (conf_id,))
                    row = cur.fetchone()
            
            if row:
                text = row[0]
                break
        
        if not text:
            with get_db() as conn:
                with conn.cursor() as cur:
                    format_strings = ','.join(['%s'] * len(viewed))
                    query = f"SELECT id, text FROM confessions WHERE status = 'approved' AND id NOT IN ({format_strings}) ORDER BY RANDOM() LIMIT 1"
                    cur.execute(query, tuple(viewed))
                    row = cur.fetchone()
            
            if not row:
                await callback.answer("🎉 Больше нет новых историй!", show_alert=True)
                return
            conf_id, text = row
            viewed.append(conf_id)
            index = len(viewed) - 1

    await state.update_data(viewed_history=viewed, current_index=index)
    safe_text = html.escape(text)

    try:
        await callback.message.edit_text(
            f"🤫 <b>Исповедь</b>\n\n{safe_text}",
            reply_markup=get_feed_keyboard(),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()

@router.callback_query(F.data == "feed_prev")
async def feed_prev(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    now = time.time()

    # Защита от спама кнопками ленты (интервал 0.6 секунды)
    if user_id in click_cooldowns and now - click_cooldowns[user_id] < 0.6:
        await callback.answer("⚠️ Не так быстро!", show_alert=False)
        return
    click_cooldowns[user_id] = now

    data = await state.get_data()
    viewed = data.get("viewed_history", [])
    index = data.get("current_index", 0)

    if index <= 0 or not viewed:
        await callback.answer("⚠️ Это первая история в текущем просмотре, дальше назад нельзя.", show_alert=True)
        return

    text = None
    while index > 0:
        index -= 1
        conf_id = viewed[index]

        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT text FROM confessions WHERE id = %s AND status = 'approved'", (conf_id,))
                row = cur.fetchone()

        if row:
            text = row[0]
            break

    if not text:
        await callback.answer("⚠️ Предыдущая история была удалена модератором.", show_alert=True)
        return

    await state.update_data(current_index=index)
    safe_text = html.escape(text)

    try:
        await callback.message.edit_text(
            f"🤫 <b>Исповедь</b>\n\n{safe_text}",
            reply_markup=get_feed_keyboard(),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()

# --- ПАНЕЛЬ АДМИНИСТРАТОРА С ПАГИНАЦИЕЙ ---

@router.message(Command(commands=["admin"]))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    await show_admin_page(message, page=0)

async def show_admin_page(message_or_callback, page: int, is_callback: bool = False):
    limit = 5  # Количество историй на одной странице
    offset = page * limit

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM confessions")
            total_count = cur.fetchone()[0]

            cur.execute("SELECT id, text, status FROM confessions ORDER BY id DESC LIMIT %s OFFSET %s", (limit, offset))
            rows = cur.fetchall()

    if not rows:
        text_msg = "📭 База данных пуста."
        if is_callback:
            await message_or_callback.message.edit_text(text_msg)
        else:
            await message_or_callback.answer(text_msg)
        return

    keyboard = []
    
    for conf_id, text, status in rows:
        display_text = (text[:25] + '..') if len(text) > 25 else text
        safe_display = html.escape(display_text)
        status_icon = "⏳" if status == 'pending' else ("✅" if status == 'approved' else "❌")
        
        keyboard.append([
            InlineKeyboardButton(text=f"{status_icon} ID {conf_id}: {safe_display}", callback_data=f"admin_view_{conf_id}")
        ])

    max_page = max(0, (total_count - 1) // limit)
    pagination_buttons = []
    if page > 0:
        pagination_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_page_{page - 1}"))
    if page < max_page:
        pagination_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"admin_page_{page + 1}"))
    
    if pagination_buttons:
        keyboard.append(pagination_buttons)

    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    text_msg = f"🛠 <b>Панель администратора</b> (Страница {page + 1} из {max_page + 1}):"

    if is_callback:
        try:
            await message_or_callback.message.edit_text(text_msg, reply_markup=reply_markup, parse_mode="HTML")
        except Exception:
            pass
    else:
        await message_or_callback.answer(text_msg, reply_markup=reply_markup, parse_mode="HTML")

@router.callback_query(F.data.startswith("admin_page_"))
async def admin_turn_page(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    page = int(callback.data.split("_")[2])
    await show_admin_page(callback, page=page, is_callback=True)
    await callback.answer()

@router.callback_query(F.data.startswith("admin_view_"))
async def admin_view_single(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
    conf_id = int(callback.data.split("_")[2])

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, text, status FROM confessions WHERE id = %s", (conf_id,))
            row = cur.fetchone()

    if not row:
        await callback.answer("⚠️ Эта исповедь уже удалена или не существует.", show_alert=True)
        return

    user_id, text, status = row
    safe_text = html.escape(text)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить эту исповедь", callback_data=f"del_{conf_id}")],
            [InlineKeyboardButton(text="🔙 Назад к списку", callback_data="admin_page_0")]
        ]
    )

    try:
        await callback.message.edit_text(
            f"🔍 <b>Исповедь ID:</b> <code>{conf_id}</code>\n"
            f"👤 <b>Автор ID:</b> <code>{user_id}</code>\n"
            f"📌 <b>Статус:</b> <code>{status}</code>\n\n"
            f"{safe_text}",
            reply_markup=kb,
            parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer()

@router.callback_query(F.data.startswith("del_"))
async def delete_from_admin(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        return
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
