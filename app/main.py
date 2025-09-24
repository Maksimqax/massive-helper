
import os
import asyncio
import tempfile
import pathlib
import subprocess
from typing import Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from aiogram import F, Router, Dispatcher
from aiogram.types import Message, Update, ReplyKeyboardMarkup, KeyboardButton
from aiogram.client.bot import Bot
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart

import aiohttp

# ----------------- ENV -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")  # e.g., https://your-app.onrender.com
SECRET_TOKEN = os.getenv("SECRET_TOKEN", "")  # optional
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "18"))
MAX_BYTES = MAX_FILE_MB * 1024 * 1024

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in environment variables")

# ----------------- Bot & DP -----------------
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
router = Router()
dp = Dispatcher()
dp.include_router(router)

# ----------------- UI (Keyboard) -----------------
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🎥 Видео → 🎤 Голос"),
         KeyboardButton(text="🔵 Кружок → 🎤 Голос")],
        [KeyboardButton(text="🎤 Голос → 🎵 MP3"),
         KeyboardButton(text="🎵 Аудио → 🎤 Голос")],
        [KeyboardButton(text="ℹ️ Помощь")]
    ],
    resize_keyboard=True,
    one_time_keyboard=False,
    input_field_placeholder="Выбери действие…"
)

# ----------------- Helpers -----------------
def _get_obj_and_size_from_message(message: Message) -> Tuple[Optional[object], int, str]:
    """
    Returns (obj, size_bytes, kind) for supported message types.
    """
    if message.video:            # regular video
        return message.video, message.video.file_size or 0, "video"
    if message.video_note:       # round video (circle)
        return message.video_note, message.video_note.file_size or 0, "circle"
    if message.document:         # document
        return message.document, message.document.file_size or 0, "document"
    if message.voice:            # voice (OGG/OPUS)
        return message.voice, message.voice.file_size or 0, "voice"
    if message.audio:            # audio file (mp3, etc.)
        return message.audio, message.audio.file_size or 0, "audio"
    if message.photo:            # photo — take the biggest
        p = message.photo[-1]
        return p, p.file_size or 0, "photo"
    return None, 0, "unknown"


async def tg_download_to_temp_or_reply_too_big(message: Message, suffix: str) -> Optional[str]:
    """
    Safely downloads Telegram file to temp path. Handles "file is too big" gracefully.
    Returns local file path or None (when replied with error).
    """
    obj, size, kind = _get_obj_and_size_from_message(message)
    if not obj:
        await message.answer("Не понял тип вложения. Пришли файл ещё раз.")
        return None

    if size > MAX_BYTES:
        mb = round(size / (1024*1024), 1)
        await message.answer(
            f"⚠️ Файл слишком большой: {mb} МБ.\n"
            f"Лимит для обработки — {MAX_FILE_MB} МБ.\n\n"
            f"Что можно сделать:\n"
            f"• Обрезать/сжать видео перед отправкой\n"
            f"• Отправить короткий кружок (до {MAX_FILE_MB} МБ)\n"
            f"• Закинуть как документ поменьше"
        )
        return None

    # Try via get_file -> https download
    try:
        f = await bot.get_file(obj.file_id)
        tg_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f.file_path}"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp_path = pathlib.Path(tmp.name)
        async with aiohttp.ClientSession() as session:
            async with session.get(tg_url) as resp:
                resp.raise_for_status()
                with open(tmp_path, "wb") as out:
                    while True:
                        chunk = await resp.content.read(1024 * 64)
                        if not chunk:
                            break
                        out.write(chunk)
        return str(tmp_path)

    except TelegramBadRequest as e:
        # Extra safety
        if "file is too big" in str(e).lower():
            await message.answer(
                f"⚠️ Telegram не отдаёт файл (слишком большой). "
                f"Отправь, пожалуйста, файл не больше {MAX_FILE_MB} МБ."
            )
            return None
        raise


def _ffmpeg_exists() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return True
    except Exception:
        return False


def run_ffmpeg(cmd: list) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "ignore"))


async def convert_video_to_voice(in_path: str) -> str:
    """
    Convert any video (incl. circle) to OGG OPUS for Telegram voice.
    """
    out_path = str(pathlib.Path(tempfile.gettempdir()) / (pathlib.Path(in_path).stem + "_voice.ogg"))
    if not _ffmpeg_exists():
        raise RuntimeError("ffmpeg недоступен в окружении.")
    # mono, 48kHz, opus ~48k
    cmd = ["ffmpeg", "-y", "-i", in_path, "-vn", "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "48k", out_path]
    run_ffmpeg(cmd)
    return out_path


async def convert_voice_to_mp3(in_path: str) -> str:
    out_path = str(pathlib.Path(tempfile.gettempdir()) / (pathlib.Path(in_path).stem + ".mp3"))
    if not _ffmpeg_exists():
        raise RuntimeError("ffmpeg недоступен в окружении.")
    cmd = ["ffmpeg", "-y", "-i", in_path, "-vn", "-c:a", "libmp3lame", "-b:a", "128k", out_path]
    run_ffmpeg(cmd)
    return out_path


async def convert_audio_to_voice(in_path: str) -> str:
    out_path = str(pathlib.Path(tempfile.gettempdir()) / (pathlib.Path(in_path).stem + "_voice.ogg"))
    if not _ffmpeg_exists():
        raise RuntimeError("ffmpeg недоступен в окружении.")
    cmd = ["ffmpeg", "-y", "-i", in_path, "-vn", "-ac", "1", "-ar", "48000", "-c:a", "libopus", "-b:a", "48k", out_path]
    run_ffmpeg(cmd)
    return out_path


# ----------------- Handlers -----------------
@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я конвертирую видео/кружки/аудио/голосовые.\n"
        "Выбери действие ниже 👇",
        reply_markup=main_kb
    )


@router.message(F.text.in_({"ℹ️ Помощь", "Помощь", "help", "/help"}))
async def help_cmd(message: Message):
    await message.answer(
        "Доступно:\n"
        "• 🎥 Видео → 🎤 Голос — пришли видео/кружок\n"
        "• 🔵 Кружок → 🎤 Голос — пришли кружок\n"
        "• 🎤 Голос → 🎵 MP3 — пришли голосовое\n"
        "• 🎵 Аудио → 🎤 Голос — пришли mp3/аудио\n\n"
        f"Максимальный размер — <b>{MAX_FILE_MB} МБ</b>."
    )


# Кнопки-подсказки (только текст)
@router.message(F.text == "🎥 Видео → 🎤 Голос")
async def hint_video_to_voice(message: Message):
    await message.answer("Пришли видео (или документ с видео) — конвертирую в голосовое.")

@router.message(F.text == "🔵 Кружок → 🎤 Голос")
async def hint_circle_to_voice(message: Message):
    await message.answer("Пришли видеокружок — конвертирую в голосовое.")

@router.message(F.text == "🎤 Голос → 🎵 MP3")
async def hint_voice_to_mp3(message: Message):
    await message.answer("Пришли голосовое — конвертирую в MP3.")

@router.message(F.text == "🎵 Аудио → 🎤 Голос")
async def hint_audio_to_voice(message: Message):
    await message.answer("Пришли аудиофайл (mp3 и т.п.) — конвертирую в голосовое.")


# === Media handlers ===
# 1) Видео/кружок -> голос
@router.message(F.video | F.video_note | F.document.as_("doc"))
async def handle_video_or_circle_to_voice(message: Message, doc: Optional[object] = None):
    # Detect what we have
    obj = None
    if message.video:
        obj = message.video
        suffix = ".mp4"
    elif message.video_note:
        obj = message.video_note
        suffix = ".mp4"
    elif doc and getattr(doc, "mime_type", "") and "video" in doc.mime_type:
        obj = doc
        suffix = ".mp4"
    else:
        return  # ignore non-video documents here

    # Temporarily attach obj into message-like wrapper for size check
    class _FakeMsg:
        def __init__(self, m, o):
            self._m = m
            self._o = o
        @property
        def video(self): return None
        @property
        def video_note(self): return None
        @property
        def document(self): return None
        @property
        def voice(self): return None
        @property
        def audio(self): return None
        @property
        def photo(self): return None

    # Use original message but size check will read from real fields
    in_path = await tg_download_to_temp_or_reply_too_big(message, suffix)
    if not in_path:
        return

    try:
        out_path = await convert_video_to_voice(in_path)
        from aiogram.types import FSInputFile
        await message.answer_voice(FSInputFile(out_path), caption="Готово: 🎥 → 🎤")
    except Exception as e:
        await message.answer(f"Не удалось конвертировать видео: {e}")


# 2) Голос -> MP3
@router.message(F.voice)
async def handle_voice_to_mp3(message: Message):
    in_path = await tg_download_to_temp_or_reply_too_big(message, ".ogg")
    if not in_path:
        return
    try:
        out_path = await convert_voice_to_mp3(in_path)
        from aiogram.types import FSInputFile
        await message.answer_document(FSInputFile(out_path), caption="Готово: 🎤 → 🎵 MP3")
    except Exception as e:
        await message.answer(f"Не удалось конвертировать в MP3: {e}")


# 3) Аудио (mp3 и пр.) -> голос
@router.message(F.audio | F.document.as_("doc_audio"))
async def handle_audio_to_voice(message: Message, doc_audio: Optional[object] = None):
    # Accept audio or document with audio mime
    suffix = ".mp3"
    if message.audio:
        pass
    elif doc_audio and getattr(doc_audio, "mime_type", "") and "audio" in doc_audio.mime_type:
        pass
    else:
        return

    in_path = await tg_download_to_temp_or_reply_too_big(message, suffix)
    if not in_path:
        return
    try:
        out_path = await convert_audio_to_voice(in_path)
        from aiogram.types import FSInputFile
        await message.answer_voice(FSInputFile(out_path), caption="Готово: 🎵 → 🎤")
    except Exception as e:
        await message.answer(f"Не удалось конвертировать в голос: {e}")


# ----------------- FastAPI -----------------
app = FastAPI()

@app.get("/")
async def health():
    # Render делает HEAD/GET — вернём 200, чтобы не было 404/405 в логах
    return {"ok": True, "service": "tg-media-bot"}

@app.post("/")
async def webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        return PlainTextResponse("bad request", status_code=400)

    # Optional secret header check
    if SECRET_TOKEN:
        if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != SECRET_TOKEN:
            return PlainTextResponse("forbidden", status_code=403)

    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return JSONResponse({"ok": True})


@app.on_event("startup")
async def on_startup():
    if WEBHOOK_URL:
        # set webhook
        await bot.set_webhook(
            url=WEBHOOK_URL,
            secret_token=SECRET_TOKEN or None,
            drop_pending_updates=True,
        )


@app.on_event("shutdown")
async def on_shutdown():
    await bot.session.close()
