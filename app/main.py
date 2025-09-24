
import os
import asyncio
import tempfile
import ffmpeg
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode, ChatAction
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.markdown import hbold
from aiogram.fsm.storage.memory import MemoryStorage

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
if not BOT_TOKEN or not WEBHOOK_URL:
    raise RuntimeError("BOT_TOKEN и WEBHOOK_URL должны быть заданы")

bot = Bot(token=BOT_TOKEN, default=ParseMode.HTML)
dp = Dispatcher(storage=MemoryStorage())

app = FastAPI()

# Главное меню
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎧 Аудио"), KeyboardButton(text="🎥 Видео / Кружок")]
    ],
    resize_keyboard=True
)

# Подменю аудио
audio_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎵 Извлечь звук из видео")],
        [KeyboardButton(text="🔊 Извлечь звук из кружка")],
        [KeyboardButton(text="🎤 Извлечь звук из голосового")],
        [KeyboardButton(text="🎧 Аудио → Голосовое")],
        [KeyboardButton(text="🎬 Видео/Кружок → Голосовое")],
        [KeyboardButton(text="⬅️ Назад")]
    ],
    resize_keyboard=True
)

# Подменю видео/кружок
video_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎦 Видео → Кружок")],
        [KeyboardButton(text="📹 Кружок → Видео")],
        [KeyboardButton(text="⬅️ Назад")]
    ],
    resize_keyboard=True
)

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет!\n\n"
        "Я умею:\n"
        "🎧 Работать с аудио (извлечение, конвертация).\n"
        "🎥 Работать с видео и кружками (конвертация туда-обратно).\n\n"
        "Выбирай нужный раздел кнопками ниже ⬇️",
        reply_markup=main_kb
    )

@dp.message(F.text == "🎧 Аудио")
async def audio_menu(message: Message):
    await message.answer("Выберите аудио-функцию:", reply_markup=audio_kb)

@dp.message(F.text == "🎥 Видео / Кружок")
async def video_menu(message: Message):
    await message.answer("Выберите видео-функцию:", reply_markup=video_kb)

@dp.message(F.text == "⬅️ Назад")
async def back_menu(message: Message):
    await message.answer("Главное меню:", reply_markup=main_kb)

# Пример: обработка видео → кружок
@dp.message(F.text == "🎦 Видео → Кружок")
async def video_to_circle(message: Message):
    await message.answer("🤖 Отправляю видео-кружок…")
    # тут логика ffmpeg, упрощена
    await message.answer("Готово ✅")

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = dp._parse_update(data)
    await dp.feed_update(bot, update)
    return {"ok": True}
