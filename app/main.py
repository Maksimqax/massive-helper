
import os
import tempfile
import asyncio
import subprocess
from typing import Optional

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import PlainTextResponse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, FSInputFile, Update
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
SECRET_TOKEN = os.getenv("SECRET_TOKEN", "")
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "18"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

# aiogram 3.7+ way to set parse_mode
from aiogram.client.default import DefaultBotProperties

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
router = Router()
dp.include_router(router)

app = FastAPI()

# ---- Keyboards ----

def main_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🎧 Аудио", callback_data="menu:audio")
    kb.button(text="🎦 Видео / Кружок", callback_data="menu:video")
    kb.adjust(1)
    return kb.as_markup()

def audio_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🎬→🔊 Из видео", callback_data="audio:from_video")
    kb.button(text="⭕→🔊 Из кружка", callback_data="audio:from_circle")
    kb.button(text="🗣️→🔊 Из голосового", callback_data="audio:from_voice")
    kb.button(text="🎵→🗣️ Аудио→Голос", callback_data="audio:audio_to_voice")
    kb.button(text="🎬/⭕→🗣️ Видео/Круг→Голос", callback_data="audio:media_to_voice")
    kb.button(text="↩️ Назад", callback_data="menu:back")
    kb.adjust(1)
    return kb.as_markup()

def video_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🎥➡️⭕ Видео → Кружок", callback_data="video:to_circle")
    kb.button(text="⭕➡️🎥 Кружок → Видео", callback_data="video:to_video")
    kb.button(text="↩️ Назад", callback_data="menu:back")
    kb.adjust(1)
    return kb.as_markup()

# ---- State ----

class Flow(StatesGroup):
    waiting_input = State()  # store action name in state data: {"action": "video_to_circle"}

# ---- Helpers ----

def bytes_to_mb(n: int) -> float:
    return round(n / (1024 * 1024), 2)

async def run_ffmpeg(cmd: list):
    """Run ffmpeg command in thread executor; raise on non-zero return."""
    loop = asyncio.get_running_loop()
    def _run():
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[-4000:])
        return proc
    return await loop.run_in_executor(None, _run)

async def tg_download_to_temp(file_id: str, suffix: str) -> str:
    f = await bot.get_file(file_id)
    # size validation if known
    size = getattr(f, "file_size", None)
    if size is not None and size > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(status_code=400, detail=f"Файл слишком большой: {bytes_to_mb(size)} MB (лимит {MAX_FILE_MB} MB)")
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    await bot.download(f, destination=path)
    return path

async def ff_video_to_circle(src: str) -> str:
    """Crop center square, scale to 240, no audio, h264 mp4 for video_note."""
    dst = src.rsplit(".", 1)[0] + "_circle.mp4"
    vf = "crop='min(iw,ih)':'min(iw,ih)',scale=240:240,fps=30"
    cmd = ["ffmpeg", "-y", "-i", src, "-vf", vf, "-an",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", dst]
    await run_ffmpeg(cmd)
    return dst

async def ff_circle_to_video(src: str) -> str:
    dst = src.rsplit(".", 1)[0] + "_video.mp4"
    cmd = ["ffmpeg", "-y", "-i", src, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", dst]
    await run_ffmpeg(cmd)
    return dst

async def ff_extract_audio(src: str) -> str:
    dst = src.rsplit(".", 1)[0] + ".mp3"
    cmd = ["ffmpeg", "-y", "-i", src, "-vn", "-acodec", "libmp3lame", "-ar", "48000", "-b:a", "128k", dst]
    await run_ffmpeg(cmd)
    return dst

async def ff_to_voice(src: str) -> str:
    """Any audio -> ogg/opus voice."""
    dst = src.rsplit(".", 1)[0] + ".ogg"
    cmd = ["ffmpeg", "-y", "-i", src, "-c:a", "libopus", "-b:a", "64k", "-vbr", "on", "-ac", "1", "-ar", "48000", dst]
    await run_ffmpeg(cmd)
    return dst

# ---- Handlers ----

@router.message(CommandStart())
async def on_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Выбери действие:", reply_markup=main_kb())

@router.callback_query(F.data == "menu:audio")
async def cb_audio(c: CallbackQuery, state: FSMContext):
    await state.set_state(Flow.waiting_input)
    await state.update_data(action=None)  # clear
    await c.message.edit_text("🎧 Аудио: выбери функцию", reply_markup=audio_kb())
    await c.answer()

@router.callback_query(F.data == "menu:video")
async def cb_video(c: CallbackQuery, state: FSMContext):
    await state.set_state(Flow.waiting_input)
    await state.update_data(action=None)
    await c.message.edit_text("🎦 Видео / Кружок: выбери функцию", reply_markup=video_kb())
    await c.answer()

@router.callback_query(F.data == "menu:back")
async def cb_back(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.edit_text("Выбери действие:", reply_markup=main_kb())
    await c.answer()

# Audio actions selection
@router.callback_query(F.data.startswith("audio:"))
async def select_audio(c: CallbackQuery, state: FSMContext):
    m = {
        "audio:from_video": "audio_from_video",
        "audio:from_circle": "audio_from_circle",
        "audio:from_voice": "audio_from_voice",
        "audio:audio_to_voice": "audio_to_voice",
        "audio:media_to_voice": "media_to_voice",
    }[c.data]
    await state.update_data(action=m)
    prompts = {
        "audio_from_video": "Пришли видео 🎬",
        "audio_from_circle": "Пришли кружок ⭕",
        "audio_from_voice": "Пришли голосовое 🗣️",
        "audio_to_voice": "Пришли аудио-файл 🎵 (mp3/wav/ogg и т.д.)",
        "media_to_voice": "Пришли видео 🎬 или кружок ⭕",
    }
    await c.message.answer(prompts[m])
    await c.answer()

# Video actions selection
@router.callback_query(F.data.startswith("video:"))
async def select_video(c: CallbackQuery, state: FSMContext):
    m = {
        "video:to_circle": "video_to_circle",
        "video:to_video": "circle_to_video",
    }[c.data]
    await state.update_data(action=m)
    prompts = {
        "video_to_circle": "Пришли видео 🎥 — сделаю кружок ⭕",
        "circle_to_video": "Пришли кружок ⭕ — сделаю видео 🎥",
    }
    await c.message.answer(prompts[m])
    await c.answer()

# --- Content handlers (process according to action) ---

@router.message(F.video | F.video_note | F.voice | F.audio, Flow.waiting_input)
async def process_media(message: Message, state: FSMContext):
    data = await state.get_data()
    action = data.get("action")
    if not action:
        return

    try:
        if action == "video_to_circle" and message.video:
            src = await tg_download_to_temp(message.video.file_id, ".mp4")
            dst = await ff_video_to_circle(src)
            await message.answer_video_note(FSInputFile(dst))
            await message.answer("Готово ✅")
            return

        if action == "circle_to_video" and message.video_note:
            src = await tg_download_to_temp(message.video_note.file_id, ".mp4")
            dst = await ff_circle_to_video(src)
            await message.answer_video(FSInputFile(dst))
            await message.answer("Готово ✅")
            return

        if action == "audio_from_video" and message.video:
            src = await tg_download_to_temp(message.video.file_id, ".mp4")
            dst = await ff_extract_audio(src)
            await message.answer_audio(FSInputFile(dst))
            await message.answer("Готово ✅")
            return

        if action == "audio_from_circle" and message.video_note:
            src = await tg_download_to_temp(message.video_note.file_id, ".mp4")
            dst = await ff_extract_audio(src)
            await message.answer_audio(FSInputFile(dst))
            await message.answer("Готово ✅")
            return

        if action == "audio_from_voice" and message.voice:
            src = await tg_download_to_temp(message.voice.file_id, ".ogg")
            dst = await ff_extract_audio(src)
            await message.answer_audio(FSInputFile(dst))
            await message.answer("Готово ✅")
            return

        if action == "audio_to_voice" and message.audio:
            src = await tg_download_to_temp(message.audio.file_id, ".mp3")
            dst = await ff_to_voice(src)
            await message.answer_voice(FSInputFile(dst))
            await message.answer("Готово ✅")
            return

        if action == "media_to_voice" and (message.video or message.video_note):
            file_id = message.video.file_id if message.video else message.video_note.file_id
            suffix = ".mp4"
            src = await tg_download_to_temp(file_id, suffix)
            tmp_audio = await ff_extract_audio(src)
            dst = await ff_to_voice(tmp_audio)
            await message.answer_voice(FSInputFile(dst))
            await message.answer("Готово ✅")
            return

        # Fallback if wrong type
        await message.answer("Это не подходит для выбранной функции. Попробуй снова 🙌")

    except HTTPException as e:
        await message.answer(f"⚠️ {e.detail}")
    except Exception as e:
        await message.answer("❌ Ошибка обработки файла.")
        # Optional: print to logs
        print("ERROR:", repr(e))

# ---- FastAPI part ----

@app.get("/", response_class=PlainTextResponse)
@app.head("/", response_class=PlainTextResponse)
async def health():
    return "ok"

@app.post("/")
async def webhook(request: Request):
    # Secret-token protection (optional)
    if SECRET_TOKEN:
        rtoken = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if rtoken != SECRET_TOKEN:
            raise HTTPException(status_code=403, detail="Bad secret token")

    data = await request.json()
    # Parse dict -> Update object
    update = Update.model_validate(data)

    # Feed to aiogram
    await dp.feed_update(bot, update)
    return Response(status_code=200)

# ---- Startup: set webhook ----

@app.on_event("startup")
async def on_startup():
    if WEBHOOK_URL:
        await bot.set_webhook(url=WEBHOOK_URL, secret_token=(SECRET_TOKEN or None))
    else:
        print("WARNING: WEBHOOK_URL is not set; Telegram won't reach this app.")

@app.on_event("shutdown")
async def on_shutdown():
    await bot.session.close()
