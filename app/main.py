
import asyncio
import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

from aiogram import Bot, Dispatcher, F
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    Message,
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.client.default import DefaultBotProperties

# ---------- Config ----------

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN env var is required")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "secret")
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

app = FastAPI()

# ---------- Helpers ----------

TMP_DIR = Path("/tmp/massive_helper")
TMP_DIR.mkdir(parents=True, exist_ok=True)

AUDIO_MAX_MB = int(os.getenv("AUDIO_MAX_MB", "20"))  # для подсказок пользователю


def kb_main() -> ReplyKeyboardMarkup:
    # короткие подписи, чтобы не обрезались
    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=[
            [
                KeyboardButton(text="🎥→🎤 Видео/Кружок → Голос"),
            ],
            [
                KeyboardButton(text="🔵→📹 Кружок → Видео"),
                KeyboardButton(text="🎙️→🎵 Голос → MP3"),
            ],
            [
                KeyboardButton(text="ℹ️ Помощь"),
            ],
        ],
    )


async def run_ffmpeg(args: list[str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode(), err.decode()


async def tg_download_file_by_id(message: Message, file_id: str, suffix: str) -> Path:
    """
    Корректное скачивание файла в aiogram v3:
    1) file = await bot.get_file(file_id)
    2) await bot.download(file, destination=path)
    """
    in_path = TMP_DIR / f"in_{uuid.uuid4().hex}{suffix}"
    try:
        file = await bot.get_file(file_id)
        await bot.download(file, destination=in_path)
        return in_path
    except TelegramBadRequest as e:
        # Например: "file is too big"
        await message.answer(
            "❌ Не удалось скачать файл из Telegram.\n"
            f"<code>{e.message}</code>\n\n"
            f"Попробуйте отправить файл меньшего размера (обычно до ~{AUDIO_MAX_MB} МБ) "
            "или предварительно сжать его.",
            reply_markup=kb_main(),
        )
        raise
    except Exception as e:
        await message.answer(f"❌ Ошибка скачивания: <code>{e}</code>", reply_markup=kb_main())
        raise


async def convert_to_voice_ogg(in_path: Path) -> Path:
    out_path = TMP_DIR / f"out_{uuid.uuid4().hex}.ogg"
    # opus voice-compatible ogg
    args = [
        "-y",
        "-i", str(in_path),
        "-ac", "1",
        "-ar", "48000",
        "-c:a", "libopus",
        "-b:a", "64k",
        str(out_path),
    ]
    code, _, err = await run_ffmpeg(args)
    if code != 0:
        raise RuntimeError(f"ffmpeg error: {err}")
    return out_path


async def convert_circlenote_to_mp4(in_path: Path) -> Path:
    out_path = TMP_DIR / f"out_{uuid.uuid4().hex}.mp4"
    args = [
        "-y",
        "-i", str(in_path),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        str(out_path),
    ]
    code, _, err = await run_ffmpeg(args)
    if code != 0:
        raise RuntimeError(f"ffmpeg error: {err}")
    return out_path


async def convert_voice_to_mp3(in_path: Path) -> Path:
    out_path = TMP_DIR / f"out_{uuid.uuid4().hex}.mp3"
    args = [
        "-y",
        "-i", str(in_path),
        "-vn",
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        str(out_path),
    ]
    code, _, err = await run_ffmpeg(args)
    if code != 0:
        raise RuntimeError(f"ffmpeg error: {err}")
    return out_path


# ---------- Handlers ----------

@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "Привет! Я помогу с конвертацией аудио/видео.\n"
        "Выберите нужную услугу ниже 👇",
        reply_markup=kb_main(),
    )


@dp.message(F.text == "ℹ️ Помощь")
async def help_cmd(message: Message):
    await message.answer(
        "Доступные услуги:\n"
        "• 🎥→🎤 Видео/Кружок → Голос — пришлите видео или кружок, верну голосовое (OGG/Opus).\n"
        "• 🔵→📹 Кружок → Видео — пришлите кружок, верну обычное MP4.\n"
        "• 🎙️→🎵 Голос → MP3 — пришлите голосовое, верну MP3.\n",
        reply_markup=kb_main(),
    )


# Видео или кружок -> голос (OGG)
@dp.message(F.video | F.video_note)
async def handle_video_or_circlenote(message: Message):
    text = message.text or ""
    # Определяем по тексту/кнопке — пользователь хочет голос
    # иначе, если это кружок, покажем подсказку про конверсию в mp4
    if text == "🎥→🎤 Видео/Кружок → Голос":
        pass  # уже в нужном режиме
    # Скачиваем исходник
    if message.video:
        file_id = message.video.file_id
        in_path = await tg_download_file_by_id(message, file_id, ".mp4")
    else:
        # video_note (кружок)
        file_id = message.video_note.file_id
        in_path = await tg_download_file_by_id(message, file_id, ".mp4")

    await message.answer("⏳ Конвертирую в голосовое...")
    try:
        out_path = await convert_to_voice_ogg(in_path)
        await message.answer_voice(voice=out_path.open("rb"))
    except Exception as e:
        await message.answer(f"❌ Ошибка конвертации: <code>{e}</code>")
    finally:
        try:
            in_path.unlink(missing_ok=True)
        except Exception:
            pass


# Кружок -> видео MP4 (по кнопке)
@dp.message(F.text == "🔵→📹 Кружок → Видео")
async def prompt_circle_to_video(message: Message):
    await message.answer("Отправьте кружок (видеосообщение), я верну обычное MP4.", reply_markup=kb_main())


@dp.message(F.video_note & ~F.text)
async def circle_to_video_convert(message: Message):
    file_id = message.video_note.file_id
    in_path = await tg_download_file_by_id(message, file_id, ".mp4")
    await message.answer("⏳ Конвертирую кружок в MP4...")
    try:
        out_path = await convert_circlenote_to_mp4(in_path)
        await message.answer_document(document=out_path.open("rb"), caption="Готово ✅")
    except Exception as e:
        await message.answer(f"❌ Ошибка конвертации: <code>{e}</code>", reply_markup=kb_main())
    finally:
        try:
            in_path.unlink(missing_ok=True)
        except Exception:
            pass


# Голос -> MP3
@dp.message(F.text == "🎙️→🎵 Голос → MP3")
async def prompt_voice_to_mp3(message: Message):
    await message.answer("Отправьте голосовое сообщение, я верну MP3-файл.", reply_markup=kb_main())


@dp.message(F.voice & ~F.text)
async def voice_to_mp3(message: Message):
    file_id = message.voice.file_id
    in_path = await tg_download_file_by_id(message, file_id, ".ogg")
    await message.answer("⏳ Конвертирую в MP3...")
    try:
        out_path = await convert_voice_to_mp3(in_path)
        await message.answer_document(document=out_path.open("rb"), caption="Готово ✅")
    except Exception as e:
        await message.answer(f"❌ Ошибка конвертации: <code>{e}</code>", reply_markup=kb_main())
    finally:
        try:
            in_path.unlink(missing_ok=True)
        except Exception:
            pass


# Fallback для непойманных апдейтов — чтобы видеть в логах, но без 500
@dp.message()
async def fallback(message: Message):
    await message.answer("Выберите действие на клавиатуре ниже 👇", reply_markup=kb_main())


# ---------- FastAPI ----------

@app.get("/", response_class=PlainTextResponse)
async def root_get():
    # чтобы не было 405 Method Not Allowed в логах от health-check
    return "OK"


@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    # валидация секрета от Telegram
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="bad secret")

    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}
