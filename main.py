import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_gigachat.chat_models import GigaChat

# Загружаем .env
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GIGACHAT_CREDENTIALS = os.getenv("GIGACHAT_CREDENTIALS")

if not TELEGRAM_TOKEN or not GIGACHAT_CREDENTIALS:
    raise RuntimeError("Укажи TELEGRAM_BOT_TOKEN и GIGACHAT_CREDENTIALS в .env!")

# Инициализация GigaChat
giga = GigaChat(
    credentials=GIGACHAT_CREDENTIALS,
    verify_ssl_certs=False,  # отключаем проверку SSL (если нужно)
)

# System prompt
SYSTEM_PROMPT = (
    "Вы — бот технической поддержки. "
    "Ваша задача: помогать пользователям решать их технические проблемы, "
    "давать инструкции и советы, а если информации недостаточно — "
    "просить уточнения."
)

# Telegram setup
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# Память для сессий
sessions: dict[int, dict] = {}

def init_session(user_id: int):
    if user_id not in sessions:
        sessions[user_id] = {
            "messages": [SystemMessage(content=SYSTEM_PROMPT)],
            "history": [
                {"role": "system", "text": SYSTEM_PROMPT, "ts": datetime.utcnow().isoformat()}
            ],
        }

def action_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="Решено ✅", callback_data="solved"),
            InlineKeyboardButton(text="Не решено ❌", callback_data="unsolved"),
        ]
    ])

def get_recent_messages(messages, max_pairs=8):
    """Оставляем system + последние max_pairs диалогов"""
    if not messages:
        return [SystemMessage(content=SYSTEM_PROMPT)]
    system = messages[0]
    others = messages[1:]
    return [system] + others[-(max_pairs * 2):]

async def call_gigachat(messages):
    try:
        payload = get_recent_messages(messages)
        res = await asyncio.to_thread(giga.invoke, payload)
        return getattr(res, "content", str(res))
    except Exception as e:
        return f"Ошибка при обращении к GigaChat: {e}"

def save_dialog_to_file_and_clear(user_id: int, status: str) -> str:
    sess = sessions.get(user_id)
    if not sess or not sess.get("history"):
        return ""
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"dialog_{user_id}_{status}_{ts}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"User ID: {user_id}\nSaved at: {datetime.utcnow().isoformat()} UTC\nStatus: {status}\n\n")
        for item in sess["history"]:
            f.write(f"{item['ts']}  {item['role'].upper()}: {item['text']}\n\n")
    sessions.pop(user_id, None)
    return filename

# -------------------- Handlers --------------------

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    init_session(message.from_user.id)
    await message.answer(
        "👋 Привет! Я бот техподдержки.\n\n"
        "Напиши свою проблему, а я постараюсь помочь.\n"
        "После ответа ты сможешь отметить: «Решено» ✅ или «Не решено» ❌."
    )

@dp.message()
async def handle_user_message(message: types.Message):
    user_id = message.from_user.id
    text = message.text or ""
    if not text.strip():
        await message.reply("Пожалуйста, опиши проблему текстом.")
        return

    init_session(user_id)

    # Сохраняем сообщение пользователя
    human_msg = HumanMessage(content=text)
    sessions[user_id]["messages"].append(human_msg)
    sessions[user_id]["history"].append({
        "role": "user", "text": text, "ts": datetime.utcnow().isoformat()
    })

    # Отправляем запрос в GigaChat
    await message.chat.do("typing")
    bot_reply = await call_gigachat(sessions[user_id]["messages"])

    # Сохраняем ответ бота
    sessions[user_id]["messages"].append(SystemMessage(content=bot_reply))
    sessions[user_id]["history"].append({
        "role": "bot", "text": bot_reply, "ts": datetime.utcnow().isoformat()
    })

    # Отправляем пользователю
    await message.answer(bot_reply, reply_markup=action_keyboard())

@dp.callback_query(F.data == "solved")
async def cb_solved(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    filename = save_dialog_to_file_and_clear(user_id, "solved")
    if filename:
        await callback.message.answer(f"✅ Диалог сохранён: `{filename}`", parse_mode="Markdown")
    else:
        await callback.message.answer("✅ Диалог пуст или уже сохранён.")
    await callback.answer()

@dp.callback_query(F.data == "unsolved")
async def cb_unsolved(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    filename = save_dialog_to_file_and_clear(user_id, "unsolved")
    if filename:
        await callback.message.answer(
            f"❌ Диалог сохранён: `{filename}`. Отправим в ServiceDesk.",
            parse_mode="Markdown"
        )
    else:
        await callback.message.answer("❌ Диалог пуст или уже сохранён.")
    await callback.answer()

# -------------------- Запуск --------------------

async def main():
    print("🚀 Бот запущен")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Бот остановлен")
