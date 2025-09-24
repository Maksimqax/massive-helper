import asyncio
import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ContentType
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    FSInputFile,
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram import Router
from aiogram.types import Update

import ffmpeg

# ----------- ENV -----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
SECRET_TOKEN = os.getenv("SECRET_TOKEN", "").strip()
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "18"))  # Telegram limit for free bots ~20MB via download

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
router = Router()
dp.include_router(router)

app = FastAPI()


# ----------- Keyboards -----------
def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎧 Аудио"), KeyboardButton(text="🎦 Видео / Кружок")],
            [KeyboardButton(text="ℹ️ Помощь")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите раздел…",
    )


def audio_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎞️ Извлечь звук из видео (mp3)")],
            [KeyboardButton(text="⭕ Извлечь звук из кружка (mp3)")],
            [KeyboardButton(text="🎙️ Извлечь звук из голосового (mp3)")],
            [KeyboardButton(text="🔊 Аудиофайл → Голосовое")],
            [KeyboardButton(text="⭕/🎞️ → Голосовое")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Отправьте медиа после выбора…",
    )


def video_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎞️ Видео → Кружок")],
            [KeyboardButton(text="⭕ Кружок → Видео")],
            [KeyboardButton(text="⬅️ Назад")],
        ],
        resize_keyboard=True,
        input_field_placeholder="Отправьте медиа после выбора…",
    )


WELCOME = (
    "Привет! 👋 Я помогу с медиа-файлами.\n\n"
    "• <b>🎧 Аудио</b> — извлечение звука, создание голосовых.\n"
    "• <b>🎦 Видео / Кружок</b> — конвертация видео ↔️ кружок.\n\n"
    "Выбирайте раздел на клавиатуре ниже 👇"
)


# ----------- FSM -----------
class Mode(StatesGroup):
    audio_extract_from_video = State()
    audio_extract_from_circle = State()
    audio_extract_from_voice = State()
    audio_file_to_voice = State()
    media_to_voice = State()
    video_to_circle = State()
    circle_to_video = State()
    idle = State()


# ----------- Helpers -----------
async def send_action(msg: Message, action: ChatAction, seconds: float = 1.0):
    try:
        await bot.send_chat_action(msg.chat.id, action)
    except Exception:
        return
    await asyncio.sleep(seconds)


def _fits_limit(file_size: int) -> bool:
    return file_size <= MAX_FILE_MB * 1024 * 1024


async def tg_download_to_temp(file_id: str, suffix: str) -> Path:
    f = await bot.get_file(file_id)
    if not _fits_limit(f.file_size or 0):
        raise HTTPException(status_code=413, detail="File too big for bot setting MAX_FILE_MB")
    tmpdir = Path(tempfile.mkdtemp())
    out_path = tmpdir / f"src{suffix}"
    # aiogram v3: bot.download can accept File or str id
    await bot.download(f, destination=str(out_path))
    return out_path


def ffmpeg_square_240(src: Path, dst: Path):
    """Center-crop to square and scale to 240x240 — required for video_note."""
    probe = ffmpeg.probe(str(src))
    streams = [s for s in probe["streams"] if s["codec_type"] == "video"]
    if not streams:
        raise RuntimeError("No video stream found")
    w = int(streams[0]["width"])
    h = int(streams[0]["height"])
    side = min(w, h)
    x = (w - side) // 2
    y = (h - side) // 2

    (
        ffmpeg
        .input(str(src))
        .crop(x, y, side, side)
        .filter("scale", 240, 240)
        .output(str(dst), vcodec="libx264", acodec="aac", pix_fmt="yuv420p", video_bitrate="500k", audio_bitrate="64k", movflags="+faststart")
        .overwrite_output()
        .run(quiet=True)
    )


def ffmpeg_extract_audio(src: Path, dst: Path):
    (
        ffmpeg
        .input(str(src))
        .output(str(dst), acodec="libmp3lame", audio_bitrate="128k", vn=None)
        .overwrite_output()
        .run(quiet=True)
    )


def ffmpeg_video_from_circle(src: Path, dst: Path):
    """Just rewrap/reencode to standard mp4 1:1 for sending as video."""
    (
        ffmpeg
        .input(str(src))
        .filter("scale", "min(iw,ih)", "min(iw,ih)")
        .output(str(dst), vcodec="libx264", acodec="aac", pix_fmt="yuv420p", movflags="+faststart")
        .overwrite_output()
        .run(quiet=True)
    )


# ----------- Handlers -----------
@router.message(F.text == "/start")
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(WELCOME, reply_markup=main_menu())


@router.message(F.text == "ℹ️ Помощь")
async def help_msg(message: Message):
    txt = (
        "Вот что я умею:\n\n"
        "• 🎞️ Видео → Кружок — квадрат 1:1, масштаб 240×240, корректно для Telegram.\n"
        "• ⭕ Кружок → Видео — верну обычное видео mp4.\n"
        "• 🎧 Аудио — извлечение звука (mp3) и создание голосовых сообщений.\n\n"
        "Отправляйте медиа <b>после</b> выбора нужной функции."
    )
    await message.answer(txt, reply_markup=main_menu())


@router.message(F.text == "⬅️ Назад")
async def back_to_main(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_menu())


# --- Open submenus
@router.message(F.text == "🎧 Аудио")
async def open_audio_menu(message: Message, state: FSMContext):
    await state.set_state(Mode.idle)
    await message.answer(
        "Раздел <b>Аудио</b>:\n"
        "• Извлечь mp3 из видео/кружка/голосового\n"
        "• Превратить аудио/видео/кружок в <b>голосовое</b>\n\n"
        "Выберите действие ниже.",
        reply_markup=audio_menu(),
    )


@router.message(F.text == "🎦 Видео / Кружок")
async def open_video_menu(message: Message, state: FSMContext):
    await state.set_state(Mode.idle)
    await message.answer(
        "Раздел <b>Видео / Кружок</b>:\n"
        "• Видео → Кружок (квадрат 240×240)\n"
        "• Кружок → Видео (mp4)\n\n"
        "Выберите действие ниже.",
        reply_markup=video_menu(),
    )


# --- Audio actions selection
@router.message(F.text == "🎞️ Извлечь звук из видео (mp3)")
async def sel_audio_from_video(message: Message, state: FSMContext):
    await state.set_state(Mode.audio_extract_from_video)
    await message.answer("Отправьте видео. Я <i>извлекаю звук…</i>")


@router.message(F.text == "⭕ Извлечь звук из кружка (mp3)")
async def sel_audio_from_circle(message: Message, state: FSMContext):
    await state.set_state(Mode.audio_extract_from_circle)
    await message.answer("Отправьте кружок. Я <i>извлекаю звук…</i>")


@router.message(F.text == "🎙️ Извлечь звук из голосового (mp3)")
async def sel_audio_from_voice(message: Message, state: FSMContext):
    await state.set_state(Mode.audio_extract_from_voice)
    await message.answer("Отправьте голосовое. Я <i>извлекаю звук…</i>")


@router.message(F.text == "🔊 Аудиофайл → Голосовое")
async def sel_audio_to_voice(message: Message, state: FSMContext):
    await state.set_state(Mode.audio_file_to_voice)
    await message.answer("Отправьте аудио-файл. Я <i>записываю голосовое…</i>")


@router.message(F.text == "⭕/🎞️ → Голосовое")
async def sel_media_to_voice(message: Message, state: FSMContext):
    await state.set_state(Mode.media_to_voice)
    await message.answer("Отправьте видео или кружок. Я <i>записываю голосовое…</i>")


# --- Video/Circle actions selection
@router.message(F.text == "🎞️ Видео → Кружок")
async def sel_video_to_circle(message: Message, state: FSMContext):
    await state.set_state(Mode.video_to_circle)
    await message.answer("Отправьте видео. Я <i>отправляю кружок…</i>")


@router.message(F.text == "⭕ Кружок → Видео")
async def sel_circle_to_video(message: Message, state: FSMContext):
    await state.set_state(Mode.circle_to_video)
    await message.answer("Отправьте кружок. Я <i>отправляю видео…</i>")


# ----------- MEDIA HANDLERS -----------
@router.message(Mode.audio_extract_from_video, F.video | F.document)
async def handle_audio_from_video(message: Message, state: FSMContext):
    await send_action(message, ChatAction.UPLOAD_VOICE, 0.5)
    file_id = message.video.file_id if message.video else message.document.file_id
    src = await tg_download_to_temp(file_id, ".mp4")
    out_mp3 = src.with_suffix(".mp3")
    ffmpeg_extract_audio(src, out_mp3)

    await message.answer_document(FSInputFile(out_mp3), caption=None)
    await message.answer("Готово ✅")


@router.message(Mode.audio_extract_from_circle, F.video_note)
async def handle_audio_from_circle(message: Message, state: FSMContext):
    await send_action(message, ChatAction.UPLOAD_VOICE, 0.5)
    src = await tg_download_to_temp(message.video_note.file_id, ".mp4")
    out_mp3 = src.with_suffix(".mp3")
    ffmpeg_extract_audio(src, out_mp3)

    await message.answer_document(FSInputFile(out_mp3), caption=None)
    await message.answer("Готово ✅")


@router.message(Mode.audio_extract_from_voice, F.voice)
async def handle_audio_from_voice(message: Message, state: FSMContext):
    await send_action(message, ChatAction.UPLOAD_VOICE, 0.5)
    src = await tg_download_to_temp(message.voice.file_id, ".oga")
    out_mp3 = src.with_suffix(".mp3")
    ffmpeg_extract_audio(src, out_mp3)

    await message.answer_document(FSInputFile(out_mp3), caption=None)
    await message.answer("Готово ✅")


@router.message(Mode.audio_file_to_voice, F.audio | F.document)
async def handle_audio_to_voice(message: Message, state: FSMContext):
    await send_action(message, ChatAction.RECORD_VOICE, 0.8)
    file_id = message.audio.file_id if message.audio else message.document.file_id
    src = await tg_download_to_temp(file_id, ".mp3")
    # Re-encode to OGG/OPUS voice container
    out_oga = src.with_suffix(".ogg")
    (
        ffmpeg
        .input(str(src))
        .output(str(out_oga), acodec="libopus", audio_bitrate="48k", ar=48000, vn=None)
        .overwrite_output()
        .run(quiet=True)
    )
    await message.answer_voice(FSInputFile(out_oga), caption=None)
    await message.answer("Готово ✅")


@router.message(Mode.media_to_voice, (F.video | F.video_note))
async def handle_media_to_voice(message: Message, state: FSMContext):
    await send_action(message, ChatAction.RECORD_VOICE, 0.8)
    if message.video:
        src = await tg_download_to_temp(message.video.file_id, ".mp4")
    else:
        src = await tg_download_to_temp(message.video_note.file_id, ".mp4")
    out_oga = src.with_suffix(".ogg")
    (
        ffmpeg
        .input(str(src))
        .output(str(out_oga), acodec="libopus", audio_bitrate="48k", ar=48000, vn=None)
        .overwrite_output()
        .run(quiet=True)
    )
    await message.answer_voice(FSInputFile(out_oga), caption=None)
    await message.answer("Готово ✅")


@router.message(Mode.video_to_circle, F.video | F.document)
async def handle_video_to_circle(message: Message, state: FSMContext):
    await send_action(message, ChatAction.RECORD_VIDEO_NOTE, 0.8)
    file_id = message.video.file_id if message.video else message.document.file_id
    src = await tg_download_to_temp(file_id, ".mp4")
    out_circle = src.with_name("circle.mp4")
    ffmpeg_square_240(src, out_circle)

    await message.answer_video_note(FSInputFile(out_circle))
    await message.answer("Готово ✅")


@router.message(Mode.circle_to_video, F.video_note)
async def handle_circle_to_video(message: Message, state: FSMContext):
    await send_action(message, ChatAction.UPLOAD_VIDEO, 0.8)
    src = await tg_download_to_temp(message.video_note.file_id, ".mp4")
    out_video = src.with_name("from_circle.mp4")
    ffmpeg_video_from_circle(src, out_video)

    await message.answer_video(FSInputFile(out_video), caption=None)
    await message.answer("Готово ✅")


# Fallbacks when media sent without chosen mode
@router.message(F.video | F.video_note | F.voice | F.audio | F.document)
async def ask_choose_mode(message: Message, state: FSMContext):
    await message.answer("Сначала выберите действие на клавиатуре ниже 👇", reply_markup=main_menu())


# ----------- FastAPI endpoints -----------
@app.get("/", response_class=PlainTextResponse)
async def root():
    return "OK"


@app.post("/webhook")
async def webhook(request: Request):
    if SECRET_TOKEN:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if secret != SECRET_TOKEN:
            raise HTTPException(status_code=403, detail="Bad secret")

    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}


# Optional helper to set webhook (call once manually)
@app.get("/set-webhook", response_class=PlainTextResponse)
async def set_webhook():
    if not WEBHOOK_URL:
        raise HTTPException(status_code=400, detail="WEBHOOK_URL env not set")
    await bot.set_webhook(url=WEBHOOK_URL + "/webhook", secret_token=SECRET_TOKEN or None)
    return "Webhook set"