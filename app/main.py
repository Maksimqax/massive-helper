import os
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update
from aiogram.filters import Command

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN env var is not set")

bot = Bot(TOKEN)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

@app.get("/ping")
async def ping():
    return {"status": "ok"}

# Главное меню
def main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="Аудио 🔊", callback_data="menu_audio")
    kb.button(text="Видео / Кружок 🎦", callback_data="menu_video")
    kb.adjust(2)
    return kb.as_markup()

# Аудио услуги
def audio_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="Видео → аудио", callback_data="audio_from_video")
    kb.button(text="Кружок → аудио", callback_data="audio_from_circle")
    kb.button(text="Голосов. → аудио", callback_data="audio_from_voice")
    kb.button(text="Аудио → голос.", callback_data="audio_to_voice")
    kb.button(text="Вид/Круж → голос.", callback_data="video_to_voice")
    kb.button(text="↩️ Назад", callback_data="back_main")
    kb.adjust(2, 2, 2)
    return kb.as_markup()

# Видео/Кружок услуги
def video_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="Видео → кружок", callback_data="video_to_circle")
    kb.button(text="Кружок → видео", callback_data="circle_to_video")
    kb.button(text="↩️ Назад", callback_data="back_main")
    kb.adjust(2, 1)
    return kb.as_markup()

@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    await message.answer("👋 Привет! Выберите категорию:", reply_markup=main_menu())

@dp.callback_query()
async def callbacks(cb: types.CallbackQuery):
    if cb.data == "menu_audio":
        await cb.message.edit_text(
            "🔊 **Аудио — выберите услугу:**\n"
            "• Видео → аудио (извлечь звук)\n"
            "• Кружок → аудио (звук из video note)\n"
            "• Голосовое → аудио (сохранить файл)\n"
            "• Аудио → голосовое (создать voice)\n"
            "• Видео/кружок → голосовое (в voice)",
            reply_markup=audio_menu(),
            parse_mode="Markdown"
        )
    elif cb.data == "menu_video":
        await cb.message.edit_text(
            "🎦 **Видео / Кружок — выберите услугу:**\n"
            "• Видео → кружок (video note)\n"
            "• Кружок → видео (mp4 файл)",
            reply_markup=video_menu(),
            parse_mode="Markdown"
        )
    elif cb.data == "back_main":
        await cb.message.edit_text("↩️ Возврат в главное меню:", reply_markup=main_menu())
    elif cb.data == "audio_from_video":
        await cb.message.answer("Отправьте видео (mp4/mov, до 50 МБ), я извлеку из него звук 🎶")
    elif cb.data == "audio_from_circle":
        await cb.message.answer("Отправьте кружок (video note), я извлеку из него звук 🔄")
    elif cb.data == "audio_from_voice":
        await cb.message.answer("Отправьте голосовое, я преобразую его в аудиофайл 🎤")
    elif cb.data == "audio_to_voice":
        await cb.message.answer("Отправьте аудиофайл (mp3/m4a/ogg), я превращу его в voice 🎧")
    elif cb.data == "video_to_voice":
        await cb.message.answer("Отправьте видео/кружок, я сделаю из него voice 🎥")
    elif cb.data == "video_to_circle":
        await cb.message.answer("Отправьте видео (до 60 c, формат 1:1), я сделаю из него кружок 📼")
    elif cb.data == "circle_to_video":
        await cb.message.answer("Отправьте кружок, я сделаю из него обычное видео 🔄")

@app.post("/")
async def webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}
