
import os
import asyncio
import logging
from uuid import uuid4
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, Update
from aiogram.filters import CommandStart, Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.types.input_file import FSInputFile

# ------------ Config ------------
logging.basicConfig(level=logging.INFO)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN env var is not set")

bot = Bot(BOT_TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

# ------------ UI -------------
MENU_BUTTONS = [
    [KeyboardButton(text="ВИДЕО → АУДИО"), KeyboardButton(text="КРУЖОК → АУДИО")],
    [KeyboardButton(text="ГОЛОС → MP3"), KeyboardButton(text="АУДИО → ГОЛОС")],
    [KeyboardButton(text="ВИДЕО/КРУЖОК → ГОЛОС")],
    [KeyboardButton(text="ВИДЕО → КРУЖОК"), KeyboardButton(text="КРУЖОК → ВИДЕО")],
]

kb_main = ReplyKeyboardMarkup(keyboard=MENU_BUTTONS, resize_keyboard=True)

class ConvState(StatesGroup):
    video_to_audio = State()
    circle_to_audio = State()
    voice_to_mp3 = State()
    audio_to_voice = State()
    vid_or_circle_to_voice = State()
    video_to_circle = State()
    circle_to_video = State()

# ------------ Helpers -------------

TMP = Path("/tmp")

async def run_ffmpeg(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {stderr.decode('utf-8', errors='ignore')}")

async def make_in_path_from_file_id(file_id: str, default_ext: str = ".dat") -> Path:
    """
    Resolves Telegram file extension via get_file.
    Returns a unique path to store the downloaded source file.
    """
    f = await bot.get_file(file_id)
    ext = Path(f.file_path).suffix or default_ext
    in_path = TMP / f"{uuid4()}{ext}"
    await bot.download(file_id, destination=in_path)
    return in_path

def out_path(ext: str) -> Path:
    return TMP / f"{uuid4()}{ext}"

# ------------ Handlers -------------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Выберите функцию и пришлите подходящий файл.\n"
        "Поддержка:\n"
        "• Видео/кружок → аудио OGG\n"
        "• Голос → MP3\n"
        "• Аудио → голос (OGG/Opus)\n"
        "• Видео/кружок → голос\n"
        "• Видео ↔️ кружок",
        reply_markup=kb_main,
    )

@router.message(Command("ping"))
async def cmd_ping(message: Message):
    await message.answer("pong")

# --- choose modes
@router.message(F.text == "ВИДЕО → АУДИО")
async def choose_v2a(message: Message, state: FSMContext):
    await state.set_state(ConvState.video_to_audio)
    await message.answer("Ок. Пришлите видео.")

@router.message(F.text == "КРУЖОК → АУДИО")
async def choose_c2a(message: Message, state: FSMContext):
    await state.set_state(ConvState.circle_to_audio)
    await message.answer("Ок. Пришлите видеокружок.")

@router.message(F.text == "ГОЛОС → MP3")
async def choose_vc2mp3(message: Message, state: FSMContext):
    await state.set_state(ConvState.voice_to_mp3)
    await message.answer("Ок. Пришлите голосовое сообщение.")

@router.message(F.text == "АУДИО → ГОЛОС")
async def choose_a2v(message: Message, state: FSMContext):
    await state.set_state(ConvState.audio_to_voice)
    await message.answer("Ок. Пришлите аудио (mp3/wav/ogg).")

@router.message(F.text == "ВИДЕО/КРУЖОК → ГОЛОС")
async def choose_any2voice(message: Message, state: FSMContext):
    await state.set_state(ConvState.vid_or_circle_to_voice)
    await message.answer("Ок. Пришлите видео или кружок.")

@router.message(F.text == "ВИДЕО → КРУЖОК")
async def choose_v2circle(message: Message, state: FSMContext):
    await state.set_state(ConvState.video_to_circle)
    await message.answer("Ок. Пришлите видео (лучше ≤ 60 с).")

@router.message(F.text == "КРУЖОК → ВИДЕО")
async def choose_circle2v(message: Message, state: FSMContext):
    await state.set_state(ConvState.circle_to_video)
    await message.answer("Ок. Пришлите видеокружок.")

# --- conversions

@router.message(ConvState.video_to_audio, F.video)
async def handle_video_to_audio(message: Message, state: FSMContext):
    in_path = await make_in_path_from_file_id(message.video.file_id, ".mp4")
    out = out_path(".ogg")
    await run_ffmpeg(["-y", "-i", str(in_path), "-vn", "-ac", "1", "-ar", "48000",
                      "-c:a", "libopus", "-b:a", "64k", str(out)])
    await message.answer_audio(audio=FSInputFile(out), caption="Аудио (OGG/Opus)")
    await state.clear()

@router.message(ConvState.circle_to_audio, F.video_note)
async def handle_circle_to_audio(message: Message, state: FSMContext):
    in_path = await make_in_path_from_file_id(message.video_note.file_id, ".mp4")
    out = out_path(".ogg")
    await run_ffmpeg(["-y", "-i", str(in_path), "-vn", "-ac", "1", "-ar", "48000",
                      "-c:a", "libopus", "-b:a", "64k", str(out)])
    await message.answer_audio(audio=FSInputFile(out), caption="Аудио (OGG/Opus)")
    await state.clear()

@router.message(ConvState.voice_to_mp3, F.voice)
async def handle_voice_to_mp3(message: Message, state: FSMContext):
    in_path = await make_in_path_from_file_id(message.voice.file_id, ".ogg")
    out = out_path(".mp3")
    await run_ffmpeg(["-y", "-i", str(in_path), "-vn", "-c:a", "libmp3lame", "-b:a", "128k", str(out)])
    await message.answer_audio(audio=FSInputFile(out), caption="MP3 из голосового")
    await state.clear()

@router.message(ConvState.audio_to_voice, F.audio)
async def handle_audio_to_voice(message: Message, state: FSMContext):
    in_path = await make_in_path_from_file_id(message.audio.file_id, ".mp3")
    out = out_path(".ogg")
    await run_ffmpeg(["-y", "-i", str(in_path), "-vn", "-ac", "1", "-ar", "48000",
                      "-c:a", "libopus", "-b:a", "24k", str(out)])
    await message.answer_voice(voice=FSInputFile(out), caption="Голосовое из аудио")
    await state.clear()

@router.message(ConvState.vid_or_circle_to_voice, F.video | F.video_note)
async def handle_video_or_circle_to_voice(message: Message, state: FSMContext):
    if message.video:
        file_id = message.video.file_id
    else:
        file_id = message.video_note.file_id
    in_path = await make_in_path_from_file_id(file_id, ".mp4")
    out = out_path(".ogg")
    await run_ffmpeg(["-y", "-i", str(in_path), "-vn", "-ac", "1", "-ar", "48000",
                      "-c:a", "libopus", "-b:a", "32k", str(out)])
    await message.answer_voice(voice=FSInputFile(out), caption="Голосовое из видео/кружка")
    await state.clear()

@router.message(ConvState.video_to_circle, F.video)
async def handle_video_to_circle(message: Message, state: FSMContext):
    in_path = await make_in_path_from_file_id(message.video.file_id, ".mp4")
    out = out_path(".mp4")
    # Square pad + sane params for video note
    vf = "scale=512:512:force_original_aspect_ratio=decrease,pad=512:512:(ow-iw)/2:(oh-ih)/2,setsar=1"
    await run_ffmpeg(["-y", "-i", str(in_path), "-vf", vf,
                      "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "main",
                      "-pix_fmt", "yuv420p",
                      "-c:a", "aac", "-b:a", "96k",
                      "-movflags", "+faststart",
                      str(out)])
    await message.answer_video_note(video_note=FSInputFile(out), length=512)
    await state.clear()

@router.message(ConvState.circle_to_video, F.video_note)
async def handle_circle_to_video(message: Message, state: FSMContext):
    in_path = await make_in_path_from_file_id(message.video_note.file_id, ".mp4")
    out = out_path(".mp4")
    # Just re-mux to a standard MP4 container if needed
    await run_ffmpeg(["-y", "-i", str(in_path), "-c", "copy", str(out)])
    await message.answer_video(video=FSInputFile(out), caption="Видео из кружка")
    await state.clear()

# Fallback: user in wrong state sends wrong type
@router.message(ConvState.video_to_audio)
@router.message(ConvState.circle_to_audio)
@router.message(ConvState.voice_to_mp3)
@router.message(ConvState.audio_to_voice)
@router.message(ConvState.vid_or_circle_to_voice)
@router.message(ConvState.video_to_circle)
@router.message(ConvState.circle_to_video)
async def remind_expected(message: Message):
    await message.answer("Пришлите подходящий файл для выбранной функции 🙂")

# ------------- FastAPI & webhook -------------

app = FastAPI()

@app.get("/ping")
async def ping():
    return PlainTextResponse("ok")

@app.post("/")
async def webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return JSONResponse({"ok": True})
