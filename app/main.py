
import os
import tempfile
import asyncio
import aiohttp
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Header
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.types import (
    Update, Message, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
)
from aiogram.filters import CommandStart

# ---------- ENV ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()
SECRET_TOKEN = os.getenv("SECRET_TOKEN", "").strip() or None
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "18"))
TTS_LANG = os.getenv("TTS_LANG", "ru")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("app")

app = FastAPI()

# ---------- Helpers ----------
def bytes_to_mb(n: int) -> float:
    return n / (1024 * 1024)

async def tg_download_to_temp(file_id: str, suffix: str) -> Path:
    f = await bot.get_file(file_id)
    if f.file_size and bytes_to_mb(f.file_size) > MAX_FILE_MB:
        raise ValueError(f"Файл слишком большой ({bytes_to_mb(f.file_size):.1f} МБ). Лимит {MAX_FILE_MB} МБ.")
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{f.file_path}"
    tmp = Path(tempfile.mkstemp(suffix=suffix)[1])
    async with aiohttp.ClientSession() as s:
        async with s.get(url) as r:
            r.raise_for_status()
            with tmp.open("wb") as w:
                w.write(await r.read())
    return tmp

async def run_ffmpeg(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {err.decode('utf-8', errors='ignore')}")

async def make_square_video(src: Path, dst: Path, size: int = 640) -> None:
    # Crop or pad to square, scale to size, keep audio
    vf = f"scale={size}:-2:flags=lanczos,setsar=1," \
         f"pad=max(iw\\,ih):max(iw\\,ih):(ow-iw)/2:(oh-ih)/2:color=black," \
         f"scale={size}:{size}:flags=lanczos"
    await run_ffmpeg([
        "ffmpeg", "-y", "-i", str(src),
        "-vf", vf,
        "-c:v", "libx264", "-profile:v", "main", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "128k",
        str(dst)
    ])

async def to_mp3(src: Path, dst: Path) -> None:
    await run_ffmpeg([
        "ffmpeg", "-y", "-i", str(src),
        "-vn",
        "-c:a", "libmp3lame", "-b:a", "192k",
        str(dst)
    ])

async def to_ogg_opus(src: Path, dst: Path) -> None:
    await run_ffmpeg([
        "ffmpeg", "-y", "-i", str(src),
        "-vn",
        "-c:a", "libopus", "-b:a", "64k",
        "-ac", "1",
        str(dst)
    ])

# ---------- UI ----------
def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎦 Видео / Кружок", callback_data="menu_video_circle")],
        [InlineKeyboardButton(text="🎧 Голосовое / MP3", callback_data="menu_voice_mp3")],
        [InlineKeyboardButton(text="📝 Текст → Голосовое", callback_data="action_tts")],
        [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="menu_help")],
    ])

def video_circle_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Видео → 🔵 Кружок", callback_data="action_video_to_circle")],
        [InlineKeyboardButton(text="🔵 Кружок → 🎬 Видео", callback_data="action_circle_to_video")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
    ])

def voice_mp3_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎙️ Голосовое → MP3", callback_data="action_voice_to_mp3")],
        [InlineKeyboardButton(text="🎵 Аудио → Голосовое", callback_data="action_audio_to_voice")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
    ])

START_TEXT = (
    "<b>Привет!</b> Я конвертирую медиа.\n\n"
    "Вот что я умею:\n"
    "• <b>🎦 Видео / Кружок</b>\n"
    "  ├─ <b>Видео → Кружок</b> — пришли видео, я верну видеосообщение-кружок.\n"
    "  └─ <b>Кружок → Видео</b> — пришли «кружок», верну обычное видео.\n"
    "• <b>🎧 Голосовое / MP3</b>\n"
    "  ├─ <b>Голосовое → MP3</b> — пришли голосовое, верну mp3.\n"
    "  └─ <b>Аудио → Голосовое</b> — пришли аудио-файл, верну голосовое (ogg/opus).\n"
    "• <b>📝 Текст → Голосовое</b> — напиши текст, верну голосовое сообщение.\n\n"
    "Советы:\n"
    "• Можно кидать видео любого разрешения — для кружка я приведу к квадрату 1:1.\n"
    f"• Лимит размера файла: <b>{MAX_FILE_MB} МБ</b>.\n"
    "• Сначала отправляю <u>чистый результат</u> без подписи (чтобы удобнее пересылать),"
    " потом отдельным сообщением — «Готово ✅».")
)

HELP_TEXT = (
    "ℹ️ <b>Помощь</b>\n\n"
    "1) Выбери нужный раздел в меню и следуй подсказке — просто пришли нужный тип медиа.\n"
    "2) Если файл слишком большой — уменьши в любом редакторе (или обрежь) и пришли снова.\n"
    "3) Для Текст → Голосовое — просто напиши текст после выбора действия."
)

# ---------- States (простая машинка в памяти) ----------
user_state: dict[int, str] = {}  # user_id -> state

def set_state(user_id: int, state: Optional[str]):
    if state is None:
        user_state.pop(user_id, None)
    else:
        user_state[user_id] = state

def get_state(user_id: int) -> Optional[str]:
    return user_state.get(user_id)

# ---------- Handlers ----------
@dp.message(CommandStart())
async def on_start(m: Message):
    await m.answer(START_TEXT, reply_markup=main_menu_kb())

@dp.callback_query(F.data == "menu_help")
async def on_help(cb):
    await cb.message.edit_text(HELP_TEXT, reply_markup=main_menu_kb())
    await cb.answer()

@dp.callback_query(F.data == "back_main")
async def on_back_main(cb):
    await cb.message.edit_text(START_TEXT, reply_markup=main_menu_kb())
    await cb.answer()

@dp.callback_query(F.data == "menu_video_circle")
async def on_menu_video_circle(cb):
    set_state(cb.from_user.id, None)
    await cb.message.edit_text("Выбери действие:", reply_markup=video_circle_kb())
    await cb.answer()

@dp.callback_query(F.data == "menu_voice_mp3")
async def on_menu_voice_mp3(cb):
    set_state(cb.from_user.id, None)
    await cb.message.edit_text("Выбери действие:", reply_markup=voice_mp3_kb())
    await cb.answer()

@dp.callback_query(F.data == "action_video_to_circle")
async def on_action_v2c(cb):
    set_state(cb.from_user.id, "await_video_for_circle")
    await cb.message.edit_text("Пришли <b>видео</b> — конвертирую в <b>кружок</b>.")
    await cb.answer()

@dp.callback_query(F.data == "action_circle_to_video")
async def on_action_c2v(cb):
    set_state(cb.from_user.id, "await_circle_for_video")
    await cb.message.edit_text("Пришли <b>кружок</b> — конвертирую в <b>видео</b>.")
    await cb.answer()

@dp.callback_query(F.data == "action_voice_to_mp3")
async def on_action_v2mp3(cb):
    set_state(cb.from_user.id, "await_voice_for_mp3")
    await cb.message.edit_text("Пришли <b>голосовое</b> — конвертирую в <b>MP3</b>.")
    await cb.answer()

@dp.callback_query(F.data == "action_audio_to_voice")
async def on_action_a2voice(cb):
    set_state(cb.from_user.id, "await_audio_for_voice")
    await cb.message.edit_text("Пришли <b>аудио-файл</b> — верну <b>голосовое</b>.")
    await cb.answer()

@dp.callback_query(F.data == "action_tts")
async def on_action_tts(cb):
    set_state(cb.from_user.id, "await_text_for_tts")
    await cb.message.edit_text("Напиши текст, а я верну <b>голосовое</b>.")
    await cb.answer()

# --- Media handlers ---

@dp.message(F.video | F.document)
async def handle_video(m: Message):
    state = get_state(m.from_user.id)
    # accept mp4 as document too
    if state != "await_video_for_circle":
        return
    obj = m.video or (m.document if (m.document and (m.document.mime_type or "").startswith("video/")) else None)
    if not obj:
        return
    try:
        src = await tg_download_to_temp(obj.file_id, ".mp4")
        out = Path(tempfile.mkstemp(suffix=".mp4")[1])
        # make square and send as video_note
        await make_square_video(src, out, size=640)
        await m.answer_video_note(FSInputFile(out))
        await m.answer("Готово ✅")
    except Exception as e:
        await m.answer(f"Ошибка: {e}")
    finally:
        set_state(m.from_user.id, None)

@dp.message(F.video_note)
async def handle_circle(m: Message):
    state = get_state(m.from_user.id)
    if state != "await_circle_for_video":
        return
    try:
        src = await tg_download_to_temp(m.video_note.file_id, ".mp4")
        # For safety, re-mux to mp4/h264
        out = Path(tempfile.mkstemp(suffix='.mp4')[1])
        await run_ffmpeg(["ffmpeg", "-y", "-i", str(src), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", str(out)])
        await m.answer_video(FSInputFile(out))
        await m.answer("Готово ✅")
    except Exception as e:
        await m.answer(f"Ошибка: {e}")
    finally:
        set_state(m.from_user.id, None)

@dp.message(F.voice)
async def handle_voice(m: Message):
    state = get_state(m.from_user.id)
    if state != "await_voice_for_mp3":
        return
    try:
        src = await tg_download_to_temp(m.voice.file_id, ".ogg")
        out = Path(tempfile.mkstemp(suffix=".mp3")[1])
        await to_mp3(src, out)
        await m.answer_audio(FSInputFile(out))
        await m.answer("Готово ✅")
    except Exception as e:
        await m.answer(f"Ошибка: {e}")
    finally:
        set_state(m.from_user.id, None)

@dp.message(F.audio | F.document)
async def handle_audio(m: Message):
    state = get_state(m.from_user.id)
    if state != "await_audio_for_voice":
        return
    obj = m.audio or (m.document if (m.document and (m.document.mime_type or "").startswith("audio/")) else None)
    if not obj:
        return
    try:
        src = await tg_download_to_temp(obj.file_id, ".bin")
        out = Path(tempfile.mkstemp(suffix=".ogg")[1])
        await to_ogg_opus(src, out)
        await m.answer_voice(FSInputFile(out))
        await m.answer("Готово ✅")
    except Exception as e:
        await m.answer(f"Ошибка: {e}")
    finally:
        set_state(m.from_user.id, None)

@dp.message(F.text)
async def handle_tts(m: Message):
    state = get_state(m.from_user.id)
    if state != "await_text_for_tts":
        return
    text = (m.text or "").strip()
    if not text:
        return
    try:
        # gTTS fallback TTS
        from gtts import gTTS
        mp3_path = Path(tempfile.mkstemp(suffix=".mp3")[1])
        gTTS(text=text, lang=TTS_LANG).write_to_fp(open(mp3_path, "wb"))
        # Convert mp3 to ogg/opus for voice
        ogg_path = Path(tempfile.mkstemp(suffix=".ogg")[1])
        await to_ogg_opus(mp3_path, ogg_path)
        await m.answer_voice(FSInputFile(ogg_path))
        await m.answer("Готово ✅")
    except Exception as e:
        await m.answer(f"Ошибка TTS: {e}")
    finally:
        set_state(m.from_user.id, None)

# ---------- FastAPI webhook ----------
class TgUpdate(BaseModel):
    update_id: int

@app.get("/", response_class=PlainTextResponse)
async def root():
    return "OK"

@app.head("/", response_class=PlainTextResponse)
async def head_root():
    return PlainTextResponse("", status_code=200)

@app.post("/", response_class=PlainTextResponse)
async def webhook(request: Request, x_telegram_bot_api_secret_token: Optional[str] = Header(default=None)):
    if SECRET_TOKEN and x_telegram_bot_api_secret_token != SECRET_TOKEN:
        return PlainTextResponse("forbidden", status_code=403)

    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return "ok"
