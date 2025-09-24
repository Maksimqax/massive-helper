
import os
import io
import asyncio
import aiohttp
import tempfile
import uuid
import subprocess
from typing import Optional

from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums.parse_mode import ParseMode
from aiogram.types import Message, ReplyKeyboardMarkup, KeyboardButton, FSInputFile
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage

# ------------------ ENV ------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
SECRET_TOKEN = os.getenv("SECRET_TOKEN")  # optional
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "18"))

# ------------------ BOT CORE ------------------
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

app = FastAPI()

# ------------------ KEYBOARDS ------------------
def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(text="🎧 Аудио"),
            KeyboardButton(text="🎦 Видео / Кружок"),
        ]],
        resize_keyboard=True
    )

def audio_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎬→🔊 Извлечь звук из видео")],
            [KeyboardButton(text="⭕→🔊 Извлечь звук из кружка")],
            [KeyboardButton(text="🗣️→🔊 Извлечь звук из голосового")],
            [KeyboardButton(text="🎵→🗣️ Аудио → голосовое")],
            [KeyboardButton(text="🎬/⭕→🗣️ Видео/кружок → голосовое")],
            [KeyboardButton(text="↩️ Назад")]
        ],
        resize_keyboard=True
    )

def video_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎬→⭕ Видео → кружок")],
            [KeyboardButton(text="⭕→🎬 Кружок → видео")],
            [KeyboardButton(text="↩️ Назад")]
        ],
        resize_keyboard=True
    )

# ------------------ FSM ------------------
class Mode(StatesGroup):
    idle = State()
    wait_video_to_circle = State()
    wait_circle_to_video = State()
    wait_extract_audio_from_video = State()
    wait_extract_audio_from_circle = State()
    wait_extract_audio_from_voice = State()
    wait_audio_to_voice = State()
    wait_media_to_voice = State()

# ------------------ UTILS ------------------
API_FILE_URL = "https://api.telegram.org/file/bot{token}/{path}"

async def tg_download_to_temp(file_id: str, suffix: str) -> str:
    """Download any Telegram file to a temp path. Returns path."""
    f = await bot.get_file(file_id)
    url = API_FILE_URL.format(token=BOT_TOKEN, path=f.file_path)
    # Size check (if available in get_file response? Not always. So skip hard check)
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(tmp_fd)
    async with aiohttp.ClientSession() as sess:
        async with sess.get(url) as resp:
            resp.raise_for_status()
            with open(tmp_path, "wb") as w:
                while True:
                    chunk = await resp.content.read(1 << 14)
                    if not chunk:
                        break
                    w.write(chunk)
    return tmp_path

def run_ffmpeg(args: list) -> None:
    # Ensure ffmpeg exists
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error"] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8","ignore"))

def ensure_square_circle(src_path: str, dst_path: str) -> None:
    """
    Make a square 240x240 circle-friendly MP4 (h264/aac) from arbitrary video.
    We center-crop to min(width,height), scale 240, keep 30 fps, short re-encode.
    """
    # crop to square, scale 240, make it very small
    # Using filters that work broadly
    vf = "crop='min(in_w,in_h)':'min(in_w,in_h)',scale=240:240,fps=30"
    run_ffmpeg(["-y", "-i", src_path, "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-an", dst_path])

def circle_mp4_to_regular(src_path: str, dst_path: str) -> None:
    """Convert small circle mp4 to normal h264/aac mp4 (keeps size)."""
    run_ffmpeg(["-y", "-i", src_path, "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "128k", dst_path])

def extract_audio(src_path: str, dst_path: str) -> None:
    """Extract audio as MP3 160k."""
    run_ffmpeg(["-y", "-i", src_path, "-vn", "-acodec", "libmp3lame", "-b:a", "160k", dst_path])

def to_voice(src_path: str, dst_path: str) -> None:
    """Convert any audio/video to Telegram voice (OGG/OPUS)."""
    run_ffmpeg(["-y", "-i", src_path, "-vn", "-c:a", "libopus", "-b:a", "48k", "-ar", "48000", "-ac", "1", dst_path])

# ------------------ HANDLERS ------------------
@router.message(F.text == "/start")
async def cmd_start(m: Message, state: FSMContext):
    await state.set_state(Mode.idle)
    await m.answer("Выберите раздел:", reply_markup=main_kb())

@router.message(F.text == "↩️ Назад")
async def back_to_main(m: Message, state: FSMContext):
    await state.set_state(Mode.idle)
    await m.answer("Главное меню:", reply_markup=main_kb())

@router.message(F.text == "🎧 Аудио")
async def open_audio(m: Message, state: FSMContext):
    await state.set_state(Mode.idle)
    await m.answer("Аудио-инструменты:", reply_markup=audio_kb())

@router.message(F.text == "🎦 Видео / Кружок")
async def open_video(m: Message, state: FSMContext):
    await state.set_state(Mode.idle)
    await m.answer("Видео и кружки:", reply_markup=video_kb())

# ---- Audio submenu selections
@router.message(F.text == "🎬→🔊 Извлечь звук из видео")
async def select1(m: Message, state: FSMContext):
    await state.set_state(Mode.wait_extract_audio_from_video)
    await m.answer("Пришлите видео, я извлеку MP3.")

@router.message(F.text == "⭕→🔊 Извлечь звук из кружка")
async def select2(m: Message, state: FSMContext):
    await state.set_state(Mode.wait_extract_audio_from_circle)
    await m.answer("Пришлите кружок, я извлеку MP3.")

@router.message(F.text == "🗣️→🔊 Извлечь звук из голосового")
async def select3(m: Message, state: FSMContext):
    await state.set_state(Mode.wait_extract_audio_from_voice)
    await m.answer("Пришлите голосовое, я сделаю MP3.")

@router.message(F.text == "🎵→🗣️ Аудио → голосовое")
async def select4(m: Message, state: FSMContext):
    await state.set_state(Mode.wait_audio_to_voice)
    await m.answer("Пришлите аудиофайл, я сделаю голосовое.")

@router.message(F.text == "🎬/⭕→🗣️ Видео/кружок → голосовое")
async def select5(m: Message, state: FSMContext):
    await state.set_state(Mode.wait_media_to_voice)
    await m.answer("Пришлите видео/кружок, я сделаю голосовое.")

# ---- Video submenu selections
@router.message(F.text == "🎬→⭕ Видео → кружок")
async def v_to_c(m: Message, state: FSMContext):
    await state.set_state(Mode.wait_video_to_circle)
    await m.answer("Пришлите видео. Я сделаю кружок.")

@router.message(F.text == "⭕→🎬 Кружок → видео")
async def c_to_v(m: Message, state: FSMContext):
    await state.set_state(Mode.wait_circle_to_video)
    await m.answer("Пришлите кружок. Я сделаю видео.")

# ---- Workers
@router.message(Mode.wait_video_to_circle, F.video)
async def handle_video_to_circle(m: Message, state: FSMContext):
    # download
    in_path = await tg_download_to_temp(m.video.file_id, ".mp4")
    out_path = tempfile.mktemp(suffix=".mp4")
    try:
        ensure_square_circle(in_path, out_path)
        # First: clean result
        await m.answer_video_note(video_note=FSInputFile(out_path))
        # Second: status message
        await m.answer("Готово ✅")
    finally:
        for p in (in_path, out_path):
            try: os.remove(p)
            except: pass
    await state.set_state(Mode.idle)

@router.message(Mode.wait_circle_to_video, F.video_note)
async def handle_circle_to_video(m: Message, state: FSMContext):
    in_path = await tg_download_to_temp(m.video_note.file_id, ".mp4")
    out_path = tempfile.mktemp(suffix=".mp4")
    try:
        circle_mp4_to_regular(in_path, out_path)
        await m.answer_video(video=FSInputFile(out_path))
        await m.answer("Готово ✅")
    finally:
        for p in (in_path, out_path):
            try: os.remove(p)
            except: pass
    await state.set_state(Mode.idle)

@router.message(Mode.wait_extract_audio_from_video, F.video)
async def handle_audio_from_video(m: Message, state: FSMContext):
    in_path = await tg_download_to_temp(m.video.file_id, ".mp4")
    out_path = tempfile.mktemp(suffix=".mp3")
    try:
        extract_audio(in_path, out_path)
        await m.answer_document(FSInputFile(out_path, filename="audio_from_video.mp3"))
        await m.answer("Готово ✅")
    finally:
        for p in (in_path, out_path):
            try: os.remove(p)
            except: pass
    await state.set_state(Mode.idle)

@router.message(Mode.wait_extract_audio_from_circle, F.video_note)
async def handle_audio_from_circle(m: Message, state: FSMContext):
    in_path = await tg_download_to_temp(m.video_note.file_id, ".mp4")
    out_path = tempfile.mktemp(suffix=".mp3")
    try:
        extract_audio(in_path, out_path)
        await m.answer_document(FSInputFile(out_path, filename="audio_from_circle.mp3"))
        await m.answer("Готово ✅")
    finally:
        for p in (in_path, out_path):
            try: os.remove(p)
            except: pass
    await state.set_state(Mode.idle)

@router.message(Mode.wait_extract_audio_from_voice, F.voice)
async def handle_audio_from_voice(m: Message, state: FSMContext):
    in_path = await tg_download_to_temp(m.voice.file_id, ".ogg")
    out_path = tempfile.mktemp(suffix=".mp3")
    try:
        extract_audio(in_path, out_path)
        await m.answer_document(FSInputFile(out_path, filename="voice_to_mp3.mp3"))
        await m.answer("Готово ✅")
    finally:
        for p in (in_path, out_path):
            try: os.remove(p)
            except: pass
    await state.set_state(Mode.idle)

@router.message(Mode.wait_audio_to_voice, F.audio)
async def handle_audio_to_voice(m: Message, state: FSMContext):
    in_path = await tg_download_to_temp(m.audio.file_id, ".mp3")
    out_path = tempfile.mktemp(suffix=".ogg")
    try:
        to_voice(in_path, out_path)
        await m.answer_voice(voice=FSInputFile(out_path))
        await m.answer("Готово ✅")
    finally:
        for p in (in_path, out_path):
            try: os.remove(p)
            except: pass
    await state.set_state(Mode.idle)

@router.message(Mode.wait_media_to_voice, (F.video | F.video_note))
async def handle_media_to_voice(m: Message, state: FSMContext):
    file_id = m.video.file_id if m.video else m.video_note.file_id
    in_path = await tg_download_to_temp(file_id, ".mp4")
    out_path = tempfile.mktemp(suffix=".ogg")
    try:
        to_voice(in_path, out_path)
        await m.answer_voice(voice=FSInputFile(out_path))
        await m.answer("Готово ✅")
    finally:
        for p in (in_path, out_path):
            try: os.remove(p)
            except: pass
    await state.set_state(Mode.idle)

# Fall-through hints
@router.message(F.text == "Главное меню")
async def ignore(m: Message):  # pragma: no cover
    await m.answer("Выберите раздел:", reply_markup=main_kb())

# ------------------ FASTAPI ------------------
@app.get("/")
def root():
    return {"ok": True}

@app.post("/")
async def webhook(request: Request, x_telegram_bot_api_secret_token: Optional[str] = Header(None)):
    if SECRET_TOKEN and x_telegram_bot_api_secret_token != SECRET_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden")
    data = await request.json()
    await dp.feed_update(bot, data)
    return JSONResponse({"ok": True})
