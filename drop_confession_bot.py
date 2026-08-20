from aiogram.filters import CommandStart, Command
import asyncio
import logging
import sqlite3
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

TOKEN = "8926289462:AAEzYTMq_DdzNER_4AVMKVn1fC0vI2GKI2U"
ADMIN_ID = 1449427026  # Твой Telegram ID для модерации

# Инициализация БД
db = sqlite3.connect("confessions.db", check_same_thread=False)
cursor = db.cursor()
cursor.execute(
    """
CREATE TABLE IF NOT EXISTS confessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    text TEXT,
    status TEXT DEFAULT 'pending'
)
"""
)
db.commit()


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
          [
              InlineKeyboardButton(
                  text="📖 Читать ленту", callback_data="read_feed"
              )
          ],
      ]
  )
  await message.answer(
      "Привет! Это анонимная исповедальня. Здесь можно высказаться или почитать"
      " чужие секреты.\n\nНикто и никогда не узнает, кто автор.",
      reply_markup=keyboard,
  )


# Нажатие на «Написать исповедь»
@router.callback_query(F.data == "write")
async def start_writing(callback: CallbackQuery, state: FSMContext):
  await callback.message.answer(
      "Напиши свою историю, секрет или факап одним сообщением. Как только"
      " модератор ее проверит, она попадет в ленту."
  )
  await state.set_state(ConfessionState.waiting_for_text)
  await callback.answer()


# Получение текста исповеди от пользователя
@router.message(ConfessionState.waiting_for_text)
async def process_confession(message: Message, state: FSMContext, bot: Bot):
  text = message.text

  # Сохраняем в БД
  cursor.execute(
      "INSERT INTO confessions (user_id, text) VALUES (?, ?)",
      (message.from_user.id, text),
  )
  db.commit()
  conf_id = cursor.lastrowid

  await state.clear()
  await message.answer(
      "✅ Твоя исповедь отправлена на модерацию! Скоро она появится в ленте."
  )

# Отправка админу на проверку
  admin_kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="✅ Одобрить", callback_data=f"approve_{conf_id}"
              ),
              InlineKeyboardButton(
                  text="❌ Отклонить", callback_data=f"reject_{conf_id}"
              ),
          ]
      ]
  )
  await bot.send_message(
      ADMIN_ID,
      f"<b>Новая исповедь #{conf_id}:</b>\n\n{text}",
      reply_markup=admin_kb,
      parse_mode="HTML",  # <--- Исправили на parse_mode="HTML"
  )
  
# Модерация: Одобрить
@router.callback_query(F.data.startswith("approve_"))
async def approve_confession(callback: CallbackQuery, bot: Bot):
    conf_id = int(callback.data.split("_")[1])
    
    # Получаем user_id автора
    cursor.execute("SELECT user_id FROM confessions WHERE id = ?", (conf_id,))
    row = cursor.fetchone()
    user_id = row[0] if row else None

    cursor.execute("UPDATE confessions SET status = 'approved' WHERE id = ?", (conf_id,))
    db.commit()

    await callback.message.edit_text(
        f"{callback.message.text}\n\n<b>[ОДОБРЕНО]</b>", parse_mode="HTML"
    )
    
    # Пишем автору
    if user_id:
        try:
            await bot.send_message(user_id, "🎉 Поздравляем! Твоя исповедь была одобрена и теперь доступна в ленте.")
        except:
            pass # Если юзер заблокировал бота, ничего не делаем
            
    await callback.answer("Исповедь опубликована!")

# Модерация: Отклонить
@router.callback_query(F.data.startswith("reject_"))
async def reject_confession(callback: CallbackQuery, bot: Bot):
    conf_id = int(callback.data.split("_")[1])
    
    cursor.execute("SELECT user_id FROM confessions WHERE id = ?", (conf_id,))
    row = cursor.fetchone()
    user_id = row[0] if row else None

    cursor.execute("UPDATE confessions SET status = 'rejected' WHERE id = ?", (conf_id,))
    db.commit()

    await callback.message.edit_text(
        f"{callback.message.text}\n\n<b>[ОТКЛОНЕНО]</b>", parse_mode="HTML"
    )
    
    if user_id:
        try:
            await bot.send_message(user_id, "ℹ️ Твоя исповедь не прошла модерацию.")
        except:
            pass
            
    await callback.answer("Исповедь отклонена.")


# Просмотр ленты (вывод случайной одобренной истории)
@router.callback_query(F.data == "read_feed")
async def read_feed(callback: CallbackQuery):
  cursor.execute(
      "SELECT id, text FROM confessions WHERE status = 'approved' ORDER BY"
      " RANDOM() LIMIT 1"
  )
  row = cursor.fetchone()

  if not row:
    await callback.answer(
        "В ленте пока нет историй. Стань первым!", show_alert=True
    )
    return

  conf_id, text = row
  kb = InlineKeyboardMarkup(
      inline_keyboard=[
          [
              InlineKeyboardButton(
                  text="🔄 Следующая история", callback_data="read_feed"
              )
          ],
          [InlineKeyboardButton(text="✍️ Написать свою", callback_data="write")],
      ]
  )

  # Проверяем, вызвано ли из ленты или из главного меню, чтобы не было ошибок редактирования
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

# Админ-панель: список исповедей для управления
@router.message(Command(commands=["admin"]))
async def admin_panel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return # Игнорируем всех, кроме админа

    cursor.execute("SELECT id, text FROM confessions LIMIT 20")
    rows = cursor.fetchall()

    if not rows:
        await message.answer("База данных пуста.")
        return

    for conf_id, text in rows:
        # Обрезаем текст, чтобы не засорять чат
        display_text = (text[:30] + '..') if len(text) > 30 else text
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del_{conf_id}")]
        ])
        await message.answer(f"#{conf_id}: {display_text}", reply_markup=kb)

# Обработчик удаления из админ-панели
@router.callback_query(F.data.startswith("del_"))
async def delete_from_admin(callback: CallbackQuery):
    conf_id = int(callback.data.split("_")[1])
    cursor.execute("DELETE FROM confessions WHERE id = ?", (conf_id,))
    db.commit()
    await callback.message.delete() # Удаляет сообщение с кнопкой из чата
    await callback.answer("Исповедь удалена из базы!")

async def main():
  bot = Bot(token=TOKEN)
  dp = Dispatcher()
  dp.include_router(router)
  await bot.delete_webhook(drop_pending_updates=True)
  await dp.start_polling(bot)


if __name__ == "__main__":
  asyncio.run(main())
  