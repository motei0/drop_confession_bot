import os
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

# Инициализация таблиц в PostgreSQL на старте
def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS confessions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                category TEXT DEFAULT 'Факап',
                text TEXT,
                status TEXT DEFAULT 'pending',
                likes INTEGER DEFAULT 0,
                cringe INTEGER DEFAULT 0
            )
            """)
            conn.commit()

init_db()

# Состояния FSM
class ConfessionState(StatesGroup):
    waiting_for_category = State()
    waiting_for_text = State()

router = Router()

# Главное нижнее меню
def get_main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🤫 Написать исповедь"), KeyboardButton(text="📖 Читать ленту")]
        ],
        resize_keyboard=True
    )

# Нижнее меню с кнопкой отмены
def get_cancel_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="❌ Отменить")]
        ],
        resize_keyboard=True
    )

# Клавиатура выбора категорий
def get_categories_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📚 Учеба", callback_data="cat_Учеба")],
            [InlineKeyboardButton(text="❤️ Личное", callback_data="cat_Личное")],
            [InlineKeyboardButton(text="🔥 Факап", callback_data="cat_Факап")],
            [InlineKeyboardButton(text="❌ Отменить", callback_data="cat_cancel")]
        ]
    )

# Универсальная кнопка отмены (работает всегда)
@router.message(F.text == "❌ Отменить")
async def cancel_anytime(message: Message, state: FSMContext):
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

# Нажатие на «Написать исповедь»
@router.message(F.text == "🤫 Написать исповедь")
async def start_writing_text(message: Message, state: FSMContext):
    await message.answer(
        "Выбери категорию своей истории:",
        reply_markup=get_categories_kb()
    )
    await state.set_state(ConfessionState.waiting_for_category)
    await message.answer("Или нажми кнопку отмены ниже:", reply_markup=get_cancel_menu())

# Выбор категории
@router.callback_query(F.data.startswith("cat_"))
async def process_category(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split("_", 1)[1]
    
    if action == "cancel":
        await state.clear()
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.message.answer("Отменено.", reply_markup=get_main_menu())
        await callback.answer()
        return

    # Безопасно обновляем данные стейта, не затирая другие поля
    await state.update_data(category=action)
    await state.set_state(ConfessionState.waiting_for_text)
    
    try:
        await callback.message.edit_text(
            f"Категория выбрана: <b>{action}</b>\n\nТеперь напиши свою историю одним сообщением (от 10 до 1000 символов):",
            parse_mode="HTML"
        )
    except Exception:
        await callback.message.answer(
            f"Категория выбрана: <b>{action}</b>\n\nТеперь напиши свою историю одним сообщением (от 10 до 1000 символов):",
            parse_mode="HTML"
        )
    await callback.answer()

# Получение и валидация текста исповеди
@router.message(ConfessionState.waiting_for_text)
async def process_confession(message: Message, state: FSMContext, bot: Bot):
    text = message.text
    
    if len(text) < 10:
        await message.answer("⚠️ Текст слишком короткий! Напиши чуть подробнее (минимум 10 символов):")
        return  
        
    if len(text) > 1000:
        await message.answer("⚠️ Текст слишком длинный! Сократи его до 1000 символов:")
        return

    data = await state.get_data()
    category = data.get("category", "Факап")

    # Сохраняем в PostgreSQL
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO confessions (user_id, category, text) VALUES (%s, %s, %s) RETURNING id",
                    (message.from_user.id, category, text),
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

    # Админ-панель проверки
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
            f"<b>Новая исповедь #{conf_id} [{category}]:</b>\n\n{text}",
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

# Просмотр ленты
@router.message(F.text == "📖 Читать ленту")
async def read_feed_msg(message: Message):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, category, text, likes, cringe FROM confessions WHERE status = 'approved' ORDER BY RANDOM() LIMIT 1"
            )
            row = cur.fetchone()

    if not row:
        await message.answer("В ленте пока нет историй. Стань первым!", reply_markup=get_main_menu())
        return

    conf_id, category, text, likes, cringe = row
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"🔥 {likes}", callback_data=f"like_{conf_id}"),
                InlineKeyboardButton(text=f"🤡 {cringe}", callback_data=f"cringe_{conf_id}")
            ],
            [InlineKeyboardButton(text="🔄 Следующая история", callback_data="read_feed_next")],
        ]
    )

    await message.answer(
        f"🤫 <b>Исповедь #{conf_id}</b> | 📂 <i>{category}</i>\n\n{text}",
        reply_markup=kb,
        parse_mode="HTML",
    )

# Обработка реакций (Лайк / Кринж)
@router.callback_query(F.data.startswith(("like_", "cringe_")))
async def process_reaction(callback: CallbackQuery):
    action, conf_id_str = callback.data.split("_")
    conf_id = int(conf_id_str)
    
    col = "likes" if action == "like" else "cringe"
    
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(f"UPDATE confessions SET {col} = {col} + 1 WHERE id = %s RETURNING likes, cringe, category, text", (conf_id,))
            row = cur.fetchone()
            conn.commit()

    if not row:
        await callback.answer("История не найдена!", show_alert=True)
        return

    likes, cringe, category, text = row
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"🔥 {likes}", callback_data=f"like_{conf_id}"),
                InlineKeyboardButton(text=f"🤡 {cringe}", callback_data=f"cringe_{conf_id}")
            ],
            [InlineKeyboardButton(text="🔄 Следующая история", callback_data="read_feed_next")],
        ]
    )

    try:
        await callback.message.edit_text(
            f"🤫 <b>Исповедь #{conf_id}</b> | 📂 <i>{category}</i>\n\n{text}",
            reply_markup=kb,
            parse_mode="HTML",
        )
    except Exception:
        pass
    
    await callback.answer("Принято!")

# Следующая история по кнопке
@router.callback_query(F.data == "read_feed_next")
async def read_feed_next(callback: CallbackQuery):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, category, text, likes, cringe FROM confessions WHERE status = 'approved' ORDER BY RANDOM() LIMIT 1"
            )
            row = cur.fetchone()

    if not row:
        await callback.answer("Больше историй пока нет!", show_alert=True)
        return

    conf_id, category, text, likes, cringe = row
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=f"🔥 {likes}", callback_data=f"like_{conf_id}"),
                InlineKeyboardButton(text=f"🤡 {cringe}", callback_data=f"cringe_{conf_id}")
            ],
            [InlineKeyboardButton(text="🔄 Следующая история", callback_data="read_feed_next")],
        ]
    )

    try:
        await callback.message.edit_text(
            f"🤫 <b>Исповедь #{conf_id}</b> | 📂 <i>{category}</i>\n\n{text}",
            reply_markup=kb,
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
