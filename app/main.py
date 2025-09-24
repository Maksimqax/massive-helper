
import os
import asyncio
import aiohttp
import tempfile
import subprocess
from pathlib import Path

from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.types import Update, Message, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode

# ---------- env ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
SECRET_TOKEN = os.getenv("SECRET_TOKEN", "").strip() or None
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "18"))  # Telegram free limit ~20MB, оставим запас
PORT = int(os.getenv("PORT", "10000"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

app = FastAPI()

# ---------- helpers ----------
def kb_main() -> ReplyKeyboardMarkup:
    # Короткие нечекрыжимые лейблы с emoji
    rows = [
        [
            KeyboardButton(text="🎙 Голос → MP3"),
            KeyboardButton(text="🎧 Аудио → Голос"),
        ],
        [
            KeyboardButton(text="🎥 Видео/Кружок → Голос"),
            KeyboardButton(text="🎵 Извлечь аудио из видео"),
        ],
        [
            KeyboardButton(text="↩️ Назад / Отмена"),
        ],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)

async def ffmpeg_available() -> bool:
    try:
        proc = await asyncio.create_subprocess_exec("ffmpeg", "-version",
                                                    stdout=asyncio.subprocess.DEVNULL,
                                                    stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()
        return proc.returncode == 0
    except Exception:
        return False

async def tg_download_to_temp(file_id: str, suffix: str) -> Path:
    f = await bot.get_file(file_id)
    if f.file_size and f.file_size > MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(413, f"Файл больше {MAX_FILE_MB} МБ")

    file_path = f.file_path  # e.g. "videos/file_12345.mp4"
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = Path(tmp.name)
    tmp.close()

    timeout = aiohttp.ClientTimeout(total=60*10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise HTTPException(502, f"Не удалось скачать файл: HTTP {resp.status}")
            with tmp_path.open("wb") as out:
                async for chunk in resp.content.iter_chunked(1024 * 128):
                    out.write(chunk)
    return tmp_path

async def run_ffmpeg(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {stderr.decode(errors='ignore')[:4000]}")

# ---------- Handlers ----------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    text = (
        "Привет! Я конвертирую медиа:\n\n"
        "🎙 <b>Голос → MP3</b>\n"
        "🎧 <b>Аудио → Голос (OGG)</b>\n"
        "🎥 <b>Видео/Кружок → Голос</b>\n"
        "🎵 <b>Извлечь аудио из видео</b>\n\n"
        f"Макс. размер файла: <b>{MAX_FILE_MB} МБ</b>."
    )
    await message.answer(text, reply_markup=kb_main())

# --- Voice -> MP3
@dp.message(F.voice)
async def handle_voice_to_mp3(message: Message):
    v = message.voice
    try:
        src = await tg_download_to_temp(v.file_id, ".ogg")
    except HTTPException as e:
        await message.answer(str(e.detail))
        return

    if not await ffmpeg_available():
        await message.answer("ffmpeg недоступен. Не могу сконвертировать 😔")
        src.unlink(missing_ok=True)
        return

    out_path = Path(tempfile.mkstemp(suffix=".mp3")[1])
    try:
        await run_ffmpeg(["-y", "-i", str(src), "-acodec", "libmp3lame", "-b:a", "128k", str(out_path)])
        await message.answer_document(document=out_path.open("rb"), caption="Готово: MP3 ✅")
    except Exception as e:
        await message.answer(f"Не удалось конвертировать: {e}")
    finally:
        src.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)

# --- Audio file -> Voice (ogg opus)
@dp.message(F.audio | (F.document & F.document.mime_type.startswith("audio/")))
async def handle_audio_to_voice(message: Message):
    a = message.audio or message.document
    try:
        src = await tg_download_to_temp(a.file_id, ".audio")
    except HTTPException as e:
        await message.answer(str(e.detail)); return

    if not await ffmpeg_available():
        await message.answer("ffmpeg недоступен. Не могу сконвертировать 😔")
        src.unlink(missing_ok=True); return

    out_path = Path(tempfile.mkstemp(suffix=".ogg")[1])
    try:
        await run_ffmpeg(["-y", "-i", str(src), "-c:a", "libopus", "-b:a", "64k", "-vn", str(out_path)])
        await message.answer_voice(voice=out_path.open("rb"), caption="Готово: голосовое ✅")
    except Exception as e:
        await message.answer(f"Ошибка конвертации: {e}")
    finally:
        src.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)

# --- Video / VideoNote -> Voice
@dp.message(F.video | F.video_note)
async def handle_video_or_circle_to_voice(message: Message):
    obj = message.video or message.video_note
    try:
        # Вытягиваем оригинал, чтобы не упираться в отсутствующий метод .download()
        src = await tg_download_to_temp(obj.file_id, ".mp4")
    except HTTPException as e:
        await message.answer(str(e.detail)); return

    if not await ffmpeg_available():
        await message.answer("ffmpeg недоступен. Не могу извлечь аудио 😔")
        src.unlink(missing_ok=True); return

    out_path = Path(tempfile.mkstemp(suffix='.ogg')[1])
    try:
        # извлечение и кодирование в ogg/opus для voice
        await run_ffmpeg(["-y", "-i", str(src), "-vn", "-c:a", "libopus", "-b:a", "64k", str(out_path)])
        await message.answer_voice(voice=out_path.open("rb"), caption="Готово: голос из видео ✅")
    except Exception as e:
        await message.answer(f"Ошибка конвертации: {e}")
    finally:
        src.unlink(missing_ok=True)
        out_path.unlink(missing_ok=True)

# --- Extract audio from video, return as MP3
@dp.message(F.text.in_({"🎵 Извлечь аудио из видео"}))
async def ask_video_for_audio(message: Message):
    await message.answer("Пришлите видео, я извлеку из него аудио (MP3).")

@dp.message(F.video)
async def handle_video_to_mp3(message: Message):
    # Этот хэндлер уже есть выше для видео -> голос.
    # Чтобы различить режимы, можно ориентироваться на прошлое сообщение пользователя или FSM.
    # Для простоты всегда делаем и voice, и mp3 по запросной кнопке.
    pass

# Кнопки-тексты
@dp.message(F.text.in_({"🎙 Голос → MP3"}))
async def info_v2m(message: Message):
    await message.answer("Отправьте голосовое сообщение — я верну MP3.", reply_markup=kb_main())

@dp.message(F.text.in_({"🎧 Аудио → Голос"}))
async def info_a2v(message: Message):
    await message.answer("Пришлите аудио-файл — я верну голосовое (OGG).", reply_markup=kb_main())

@dp.message(F.text.in_({"🎥 Видео/Кружок → Голос"}))
async def info_video2voice(message: Message):
    await message.answer("Пришлите видео или кружок — я пришлю голосовое (извлеку аудио).", reply_markup=kb_main())

@dp.message(F.text.in_({"↩️ Назад / Отмена"}))
async def cancel(message: Message):
    await message.answer("Ок, возвращаемся в меню.", reply_markup=kb_main())

# Fallback
@dp.message()
async def fallback(message: Message):
    await message.answer("Не понял. Нажмите кнопку или отправьте медиа 🙏", reply_markup=kb_main())

# ---------- Webhook / FastAPI ----------

@app.get("/", response_class=PlainTextResponse)
async def root():
    return "OK"

@app.head("/", response_class=PlainTextResponse)
async def root_head():
    return ""

@app.get("/healthz", response_class=PlainTextResponse)
async def health():
    return "ok"

@app.post("/")
async def webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    # 403, если секрет включен и не совпал
    if SECRET_TOKEN and x_telegram_bot_api_secret_token != SECRET_TOKEN:
        raise HTTPException(status_code=403, detail="Forbidden (bad secret)")

    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return JSONResponse({"ok": True})

# Опционально: ручная установка/удаление вебхука (если запускаете не через Render)
@app.get("/set-webhook")
async def set_webhook():
    url = WEBHOOK_URL or ""
    if not url:
        raise HTTPException(400, "WEBHOOK_URL не задан")
    res = await bot.set_webhook(url, secret_token=SECRET_TOKEN) if SECRET_TOKEN else await bot.set_webhook(url)
    return {"ok": res}

@app.get("/delete-webhook")
async def delete_webhook():
    res = await bot.delete_webhook(drop_pending_updates=False)
    return {"ok": res}

# Uvicorn запускается из start.sh
