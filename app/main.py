import os
from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update

TOKEN = os.getenv("BOT_TOKEN")
bot = Bot(TOKEN)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

# Главное меню
def main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="Аудио 🔊", callback_data="menu_audio")
    kb.button(text="Видео / Кружок 🎦", callback_data="menu_video")
    return kb.as_markup()

# Подменю аудио
def audio_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="Извлечь звук из видео", callback_data="audio_from_video")
    kb.button(text="Извлечь звук из кружка", callback_data="audio_from_circle")
    kb.button(text="Извлечь звук из голосового", callback_data="audio_from_voice")
    kb.button(text="Аудио → голосовое", callback_data="audio_to_voice")
    kb.button(text="Видео/кружок → голосовое", callback_data="video_to_voice")
    kb.button(text="↩️ Назад", callback_data="back_main")
    return kb.as_markup()

# Подменю видео
def video_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="Видео → кружок", callback_data="video_to_circle")
    kb.button(text="Кружок → видео", callback_data="circle_to_video")
    kb.button(text="↩️ Назад", callback_data="back_main")
    return kb.as_markup()

@dp.message(commands=["start"])
async def start_cmd(message: types.Message):
    await message.answer("Выберите категорию:", reply_markup=main_menu())

# Обработка кнопок
@dp.callback_query()
async def callbacks(cb: types.CallbackQuery):
    if cb.data == "menu_audio":
        await cb.message.edit_text("Выберите аудио-услугу:", reply_markup=audio_menu())
    elif cb.data == "menu_video":
        await cb.message.edit_text("Выберите видео/кружок-услугу:", reply_markup=video_menu())
    elif cb.data == "back_main":
        await cb.message.edit_text("Возврат в главное меню:", reply_markup=main_menu())
    elif cb.data == "audio_from_video":
        await cb.message.answer("Отправьте видео, я извлеку из него звук 🎶")
    elif cb.data == "audio_from_circle":
        await cb.message.answer("Отправьте кружок, я извлеку из него звук 🔄")
    elif cb.data == "audio_from_voice":
        await cb.message.answer("Отправьте голосовое, я преобразую его 🎤")
    elif cb.data == "audio_to_voice":
        await cb.message.answer("Отправьте аудиофайл, я превращу его в голосовое 🎧")
    elif cb.data == "video_to_voice":
        await cb.message.answer("Отправьте видео/кружок, я сделаю из него голосовое 🎥")
    elif cb.data == "video_to_circle":
        await cb.message.answer("Отправьте видео, я сделаю из него кружок 📼")
    elif cb.data == "circle_to_video":
        await cb.message.answer("Отправьте кружок, я сделаю из него обычное видео 🔄")

@app.post("/")
async def webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}
