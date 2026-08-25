# Нажатие на кнопку «Написать исповедь»
@router.message(F.text == "🤫 Написать исповедь")
async def start_writing_text(message: Message, state: FSMContext):
    user_id = message.from_user.id
    now = time.time()
    
    # Проверка кулдауна (30 секунд) до перевода в состояние
    if user_id in cooldowns and now - cooldowns[user_id] < 30:
        left = int(30 - (now - cooldowns[user_id]))
        await message.answer(f"⏳ Подожди еще {left} сек., прежде чем отправлять новую исповедь.")
        return

    await state.set_state(ConfessionState.waiting_for_text)
    await message.answer(
        "Напиши свою историю, секрет или факап одним сообщением. Как только модератор ее проверит, она попадет в ленту.",
        reply_markup=get_cancel_menu()
    )

# Получение текста исповеди от пользователя (срабатывает ТОЛЬКО в состоянии waiting_for_text)
@router.message(ConfessionState.waiting_for_text, F.text != "❌ Отменить")
async def process_confession(message: Message, state: FSMContext, bot: Bot):
    user_id = message.from_user.id
    now = time.time()

    if user_id in cooldowns and now - cooldowns[user_id] < 30:
        left = int(30 - (now - cooldowns[user_id]))
        await message.answer(f"⏳ Подожди еще {left} сек., прежде чем отправлять новую исповедь.")
        await state.clear()
        return

    text = message.text
    username = f"@{message.from_user.username}" if message.from_user.username else "Отсутствует"

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
