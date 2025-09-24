
# app/main.py
# --- Telegram bot with FastAPI webhook (aiogram v3) ---
# Fixes:
# 1) Accept Telegram POSTs without 403 by making secret header optional (checks only if SECRET_TOKEN is set).
# 2) Correct media download for Video/VideoNote using bot.get_file + direct file URL (no .download() on model).
# 3) Safer file-size guard to avoid "file is too big" errors from Telegram.
# 4) Clearer keyboard labels with emojis and full captions (no truncation).

import os
import asyncio
import aiohttp
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from aiogram import Bot, Dispatcher, F
from aiogram.types import Update, Message, KeyboardButton, ReplyKeyboardMarkup, Video, VideoNote, Audio, Voice
from aiogram.filters import CommandStart
from aiogram.utils.markdown import hbold

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")  # e.g. https://massive-helper.onrender.com
SECRET_TOKEN = os.getenv("SECRET_TOKEN", "")  # optional. If set, Telegram must send this header
MAX_FILE_MB = float(os.getenv("MAX_FILE_MB", "18"))  # default 18MB due to Telegram file constraints on some types
INBOX_DIR = Path(os.getenv("INBOX_DIR", "/tmp/inbox"))
INBOX_DIR.mkdir(parents=True, exist_ok=True)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN env is required")

bot = Bot(BOT_TOKEN, parse_mode="HTML")
dp = Dispatcher()

# --- UI keyboard with full captions ---
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎙️ Голос → MP3"), KeyboardButton(text="🗣️ MP3 → Голосовое")],
        [KeyboardButton(text="🎥 Видео/Кружок → Голосовое")],
        [KeyboardButton(text="ℹ️ Помощь / Меню")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Выберите режим ↓",
)

@dp.message(CommandStart())
async def on_start_cmd(msg: Message):
    await msg.answer(
        "Привет! Я помогу с конвертацией аудио и видео.\n"
        f"{hbold('Доступные режимы:')}\n"
        "• 🎙️ Голос → MP3\n"
        "• 🗣️ MP3 → Голосовое\n"
        "• 🎥 Видео/Кружок → Голосовое\n\n"
        "Просто выбери кнопку ниже 👇 или пришли файл.",
        reply_markup=main_kb
    )

# --- Helpers ---

TG_FILE_BASE = "https://api.telegram.org/file"

async def _download_by_file_id(file_id: str, suffix: str) -> Path:
    """
    Download any Telegram file_id to a local path using get_file + https fetch.
    Works for Video, VideoNote, Voice, Audio, etc.
    """
    # 1) ask api about file path & size
    tg_file = await bot.get_file(file_id)
    file_path = tg_file.file_path  # like "videos/file_123.mp4"
    file_size = tg_file.file_size or 0
    if file_size > int(MAX_FILE_MB * 1024 * 1024):
        raise HTTPException(status_code=413, detail=f"Файл больше {MAX_FILE_MB} МБ. Пришлите поменьше.")

    # 2) build direct url and fetch
    url = f"{TG_FILE_BASE}/bot{BOT_TOKEN}/{file_path}"
    out = (INBOX_DIR / f"{file_id}{suffix}")
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise HTTPException(status_code=502, detail=f"Не получилось скачать файл (HTTP {resp.status})")
            with out.open("wb") as f:
                while True:
                    chunk = await resp.content.read(1024 * 64)
                    if not chunk:
                        break
                    f.write(chunk)
    return out

# --- Handlers ---

@dp.message(F.text == "ℹ️ Помощь / Меню")
async def help_menu(msg: Message):
    await on_start_cmd(msg)

@dp.message(F.text == "🎙️ Голос → MP3")
async def expect_voice_to_mp3(msg: Message):
    await msg.answer("Ок! Пришлите голосовое сообщение (Voice), я верну MP3.")

@dp.message(F.text == "🗣️ MP3 → Голосовое")
async def expect_mp3_to_voice(msg: Message):
    await msg.answer("Пришлите MP3 / аудио-файл, я верну голосовое сообщение.")

@dp.message(F.text == "🎥 Видео/Кружок → Голосовое")
async def expect_video_to_voice(msg: Message):
    await msg.answer("Пришлите видео или кружок (Video / VideoNote), я достану из него звук как голосовое.")

# Voice -> MP3 (demo: просто отвечает фактом скачивания)
@dp.message(F.voice)
async def handle_voice(msg: Message):
    try:
        in_path = await _download_by_file_id(msg.voice.file_id, ".ogg")
        await msg.answer(f"✅ Голосовое скачано ({round((in_path.stat().st_size/1024/1024),2)} МБ). Тут должна быть конвертация → MP3.")
    except HTTPException as e:
        await msg.answer(f"❌ {e.detail}")
    except Exception as e:
        await msg.answer("❌ Ошибка при обработке голосового.")
        raise

# Audio (mp3) -> Voice (stub)
@dp.message(F.audio)
async def handle_audio_to_voice(msg: Message):
    try:
        in_path = await _download_by_file_id(msg.audio.file_id, ".mp3")
        await msg.answer("✅ MP3 скачан. Тут должна быть конвертация → голосовое сообщение.")
    except HTTPException as e:
        await msg.answer(f"❌ {e.detail}")
    except Exception:
        await msg.answer("❌ Ошибка при обработке аудио.")
        raise

# Video/VideoNote -> Voice
@dp.message(F.video | F.video_note)
async def handle_video_or_circle_to_voice(msg: Message):
    try:
        file_id: Optional[str] = None
        suffix = ".mp4"
        if isinstance(msg.video, Video):
            file_id = msg.video.file_id
            suffix = ".mp4"
        elif isinstance(msg.video_note, VideoNote):
            file_id = msg.video_note.file_id
            suffix = ".mp4"
        if not file_id:
            await msg.answer("Не вижу видео/кружок в сообщении 🤔")
            return

        in_path = await _download_by_file_id(file_id, suffix)
        await msg.answer("✅ Видео скачано. Тут должна быть конвертация → голосовое сообщение.")
    except HTTPException as e:
        await msg.answer(f"❌ {e.detail}")
    except Exception:
        await msg.answer("❌ Ошибка при обработке видео/кружка.")
        raise

# Fallback
@dp.message()
async def unhandled(msg: Message):
    await msg.answer("Я понимаю голосовые, аудио MP3, видео и кружки. Нажмите «ℹ️ Помощь / Меню» для подсказок.", reply_markup=main_kb)


# --- FastAPI part ---
app = FastAPI()

@app.get("/health", response_class=PlainTextResponse)
async def health():
    return "ok"

# Webhook endpoint (root '/')
@app.post("/")
async def webhook(request: Request, x_telegram_bot_api_secret_token: Optional[str] = Header(default=None)):
    # If SECRET_TOKEN is configured, check header. If not set — accept all (avoid 403 confusion).
    if SECRET_TOKEN and x_telegram_bot_api_secret_token != SECRET_TOKEN:
        # 403 only if we explicitly enforce secret
        raise HTTPException(status_code=403, detail="Forbidden (bad secret token)")

    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}

# On startup configure webhook (idempotent)
@app.on_event("startup")
async def on_startup():
    if WEBHOOK_URL:
        params = {}
        if SECRET_TOKEN:
            params["secret_token"] = SECRET_TOKEN
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.set_webhook(url=WEBHOOK_URL, **params)
    # else: polling not used on Render; we rely on external setWebhook you might have done earlier.

@app.on_event("shutdown")
async def on_shutdown():
    await bot.session.close()
