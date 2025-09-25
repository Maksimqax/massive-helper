
import os
import tempfile
import asyncio
import subprocess
from typing import Optional

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import PlainTextResponse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import Message, CallbackQuery, FSInputFile, Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest

from dotenv import load_dotenv


# ---- User Tracking (SQLite) ----
import sqlite3
from datetime import date

DB_PATH = "users.sqlite"

def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, first_seen TEXT, last_seen TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS hits (dt TEXT, user_id INTEGER, PRIMARY KEY (dt, user_id))")
    con.commit()
    con.close()

def touch_user(user_id: int):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO users(user_id, first_seen, last_seen) VALUES (?, datetime('now'), datetime('now'))", (user_id,))
    cur.execute("UPDATE users SET last_seen = datetime('now') WHERE user_id = ?", (user_id,))
    cur.execute("INSERT OR IGNORE INTO hits(dt, user_id) VALUES (?, ?)", (date.today().isoformat(), user_id))
    con.commit()
    con.close()

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
    kb.button(text="🎦 Видео/Кружок", callback_data="menu:video")
    kb.adjust(1)
    return kb.as_markup()

def main_reply_kb():
    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=[
            [KeyboardButton(text="🎦 Видео/Кружок"), KeyboardButton(text="🎧 Аудио")],
            [KeyboardButton(text="ℹ️ Справка")]
        ]
    )

def video_reply_kb():
    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=[
            [KeyboardButton(text="🎥 Видео → ⭕ Кружок")],
            [KeyboardButton(text="⭕ Кружок → 🎥 Видео")],
            [KeyboardButton(text="⬅ Назад")]
        ]
    )

def audio_reply_kb():
    return ReplyKeyboardMarkup(
        resize_keyboard=True,
        keyboard=[
            [KeyboardButton(text="🎬 Видео → 🔊 Аудио (MP3)")],
            [KeyboardButton(text="⭕ Кружок → 🔊 Аудио (MP3)")],
            [KeyboardButton(text="🗣️ Голосовое → 🔊 Аудио (MP3)")],
            [KeyboardButton(text="🎵 Аудио → 🗣️ Голосовое")],
            [KeyboardButton(text="🎬/⭕ Видео/Кружок → 🗣️ Голосовое")],
            [KeyboardButton(text="⬅ Назад")]
        ]
    )

    kb = InlineKeyboardBuilder()
    kb.button(text="🎧 Аудио", callback_data="menu:audio")
    kb.button(text="🎦 Видео/Кружок", callback_data="menu:video")
    kb.adjust(1)
    return kb.as_markup()

def audio_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🎬 Видео → 🔊 Аудио (MP3)", callback_data="audio:from_video")
    kb.button(text="⭕ Кружок → 🔊 Аудио (MP3)", callback_data="audio:from_circle")
    kb.button(text="🗣️ Голосовое → 🔊 Аудио (MP3)", callback_data="audio:from_voice")
    kb.button(text="🎵 Аудио → 🗣️ Голосовое", callback_data="audio:audio_to_voice")
    kb.button(text="🎬/⭕ Видео/Кружок → 🗣️ Голосовое", callback_data="audio:media_to_voice")
    kb.button(text="↩️ Назад", callback_data="menu:back")
    kb.adjust(1)
    return kb.as_markup()

def video_kb():
    kb = InlineKeyboardBuilder()
    kb.button(text="🎥 Видео → ⭕ Кружок", callback_data="video:to_circle")
    kb.button(text="⭕ Кружок → 🎥 Видео", callback_data="video:to_video")
    kb.button(text="↩️ Назад", callback_data="menu:back")
    kb.adjust(1)
    return kb.as_markup()

# ---- State ----

class Flow(StatesGroup):
    waiting_input = State()  # store action name in state data: {"action": "video_to_circle"}

# ---- Helpers ----

def bytes_to_mb(n: int) -> float:
    return round(n / (1024 * 1024), 2)


async def _send_action_periodically(chat_id: int, action: ChatAction):
    """Send chat action every ~4s while long task runs."""
    try:
        while True:
            await bot.send_chat_action(chat_id, action=action)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass

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
    """Crop to square for video_note and **preserve audio** (AAC)."""
    dst = src.rsplit(".", 1)[0] + "_circle.mp4"
    # 1:1 square, 480x480, keep audio (AAC), 30fps
    vf = "crop='min(iw,ih)':'min(iw,ih)',scale=480:480:flags=lanczos,fps=30,format=yuv420p"
    cmd = [
        "ffmpeg", "-y", "-i", src,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "baseline", "-level", "3.0",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000",
        "-movflags", "+faststart",
        dst
    ]
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

async def ff_to_mp3(src: str) -> str:
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
    text = (
        "👋 Привет! С помощью этого бота можно превратить:\n\n"
        
        " 🎥 Видео в ⭕ Кружок\n\n"
        
        " ⭕ Кружок в 🎥 Видео\n\n"
        
        " 🎥 Видео / ⭕ Кружок в 🔊 Аудиофайл\n\n"
        
        " 🔊 Аудиофайл, 🎥 Видео, ⭕ Кружок в 🗣️ Голосовое сообщение\n\n"
        
        "Выбери нужный раздел в меню ниже:"
    )
    await message.answer(text, reply_markup=main_reply_kb())


@router.callback_query(F.data == "menu:audio")
async def cb_audio(c: CallbackQuery, state: FSMContext):
    await state.set_state(Flow.waiting_input)
    await state.update_data(action=None)  # clear
    try:
        await c.message.edit_text("🎧 Аудио: выбери функцию", reply_markup=audio_kb())
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await c.answer("Вы уже в этом меню", show_alert=False)
        else:
            raise
    else:
        await c.answer()

@router.callback_query(F.data == "menu:video")
async def cb_video(c: CallbackQuery, state: FSMContext):
    await state.set_state(Flow.waiting_input)
    await state.update_data(action=None)
    try:
        await c.message.edit_text("🎦 Видео / Кружок: выбери функцию", reply_markup=video_kb())
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await c.answer("Вы уже в этом меню", show_alert=False)
        else:
            raise
    else:
        await c.answer()

@router.callback_query(F.data == "menu:back")
async def cb_back(c: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await c.message.edit_text("Выбери действие:", reply_markup=main_kb())
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await c.answer("Вы уже в этом меню", show_alert=False)
        else:
            raise
    else:
        await c.answer()

# Audio actions selection

@router.callback_query(F.data.startswith("audio:"))
async def select_audio(c: CallbackQuery, state: FSMContext):
    if not await ensure_subscribed(bot, c.from_user.id):
        await c.message.edit_text('Чтобы пользоваться этой функцией, подпишитесь на канал:', reply_markup=subscribe_keyboard())
        return
    m = {
        "audio:from_video": "audio_from_video",
        "audio:from_circle": "audio_from_circle",
        "audio:from_voice": "audio_from_voice",
        "audio:audio_to_voice": "audio_to_voice",
        "audio:media_to_voice": "media_to_voice",
    }[c.data]
    await state.update_data(action=m)
    prompts = {
        "audio_from_video": "🔊 **Достаю звук из видео**\nПришли видео 🎬 — верну отдельно аудио (mp3).",
        "audio_from_circle": "🔊 **Достаю звук из кружка**\nПришли кружок ⭕ — верну аудио (mp3).",
        "audio_from_voice": "🔊 **Голосовое → аудио**\nПришли голосовое 🗣️ — преобразую в .ogg/.mp3.",
        "audio_to_voice": "🗣️ **Аудиофайл → голосовое**\nПришли аудиофайл (mp3/wav/ogg) — сделаю голосовое сообщение (ogg/opus).",
        "media_to_voice": "🗣️ **Видео/кружок → голосовое**\nПришли видео 🎬 или кружок ⭕ — сделаю голосовое (ogg/opus).",
    }
    await state.set_state(Flow.waiting_input)
    try:
        await c.message.edit_text(prompts[m], reply_markup=audio_kb(), parse_mode="Markdown")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await c.answer("Вы уже в этом меню", show_alert=False)
        else:
            raise
    else:
        await c.answer()


# Video actions selection

@router.callback_query(F.data.startswith("video:"))
async def select_video(c: CallbackQuery, state: FSMContext):
    if not await ensure_subscribed(bot, c.from_user.id):
        await c.message.edit_text('Чтобы пользоваться этой функцией, подпишитесь на канал:', reply_markup=subscribe_keyboard())
        return
    m = {
        "video:to_circle": "video_to_circle",
        "video:to_video": "circle_to_video",
    }[c.data]
    await state.update_data(action=m)
    prompts = {
        "video_to_circle": "⭕ **Видео → Кружок**\nПришли обычное видео 🎥 — я сделаю из него кружок. Видео должно быть не дольше ~60 сек и не больше лимита файла.\n\nГотов? Отправляй файл.",
        "circle_to_video": "🎥 **Кружок → Видео**\nПришли кружок ⭕ — верну его в обычный видеофайл с квадратной картинкой.\n\nГотов? Отправляй файл.",
    }
    try:
        await c.message.edit_text(prompts[m], reply_markup=video_kb(), parse_mode="Markdown")
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            await c.answer("Вы уже в этом меню", show_alert=False)
        else:
            raise
    else:
        await c.answer()



# ---- Reply Keyboard handlers ----

@router.message(F.text == "🎦 Видео/Кружок")
async def on_text_menu_video(message: Message, state: FSMContext):
    await state.set_state(Flow.waiting_input)
    await state.update_data(action=None)
    await message.answer("🎦 Видео / Кружок: выбери функцию на клавиатуре ⤵️", reply_markup=video_reply_kb())

@router.message(F.text == "🎧 Аудио")
async def on_text_menu_audio(message: Message, state: FSMContext):
    await state.set_state(Flow.waiting_input)
    await state.update_data(action=None)
    await message.answer("🎧 Аудио: выбери функцию на клавиатуре ⤵️", reply_markup=audio_reply_kb())

@router.message(F.text == "⬅ Назад")
async def on_text_back(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Главное меню:", reply_markup=main_reply_kb())

# Functions (reply keyboard)
@router.message(F.text == "🎥 Видео → ⭕ Кружок")
async def on_text_v_to_circle(message: Message, state: FSMContext):
    await state.update_data(action="video_to_circle")
    await message.answer("Пришли видео 🎥 — сделаю **кружок** ⭕.", reply_markup=video_reply_kb())

@router.message(F.text == "⭕ Кружок → 🎥 Видео")
async def on_text_circle_to_v(message: Message, state: FSMContext):
    await state.update_data(action="circle_to_video")
    await message.answer("Пришли кружок ⭕ — верну обычное **видео** 🎥.", reply_markup=video_reply_kb())

@router.message(F.text == "🎬 Видео → 🔊 Аудио (MP3)")
async def on_text_a_from_video(message: Message, state: FSMContext):
    await state.update_data(action="audio_from_video")
    await message.answer("Пришли видео 🎬 — достану **аудио (MP3)** 🔊.", reply_markup=audio_reply_kb())

@router.message(F.text == "⭕ Кружок → 🔊 Аудио (MP3)")
async def on_text_a_from_circle(message: Message, state: FSMContext):
    await state.update_data(action="audio_from_circle")
    await message.answer("Пришли кружок ⭕ — достану **аудио (MP3)** 🔊.", reply_markup=audio_reply_kb())

@router.message(F.text == "🗣️ Голосовое → 🔊 Аудио (MP3)")
async def on_text_a_from_voice(message: Message, state: FSMContext):
    await state.update_data(action="audio_from_voice")
    await message.answer("Пришли голосовое 🗣️ — сделаю **аудио (MP3)** 🔊.", reply_markup=audio_reply_kb())

@router.message(F.text == "🎵 Аудио → 🗣️ Голосовое")
async def on_text_audio_to_voice(message: Message, state: FSMContext):
    await state.update_data(action="audio_to_voice")
    await message.answer("Пришли аудиофайл 🎵 — верну **голосовое** 🗣️ (ogg/opus).", reply_markup=audio_reply_kb())

@router.message(F.text == "🎬/⭕ Видео/Кружок → 🗣️ Голосовое")
async def on_text_media_to_voice(message: Message, state: FSMContext):
    await state.update_data(action="media_to_voice")
    await message.answer("Пришли **видео** 🎬 или **кружок** ⭕ — сделаю **голосовое** 🗣️.", reply_markup=audio_reply_kb())

# --- Content handlers (process according to action) ---


@router.message(F.video | F.video_note | F.voice | F.audio, Flow.waiting_input)
async def process_media(message: Message, state: FSMContext):
    data = await state.get_data()
    action = data.get("action")
    if not action:
        return

    async def action_loop(act: ChatAction):
        task = asyncio.create_task(_send_action_periodically(message.chat.id, act))
        return task

    try:
        # VIDEO -> CIRCLE (video note)
        if action == "video_to_circle" and message.video:
            act = await action_loop(ChatAction.RECORD_VIDEO_NOTE)
            try:
                src = await tg_download_to_temp(message.video.file_id, ".mp4")
                dst = await ff_video_to_circle(src)
            finally:
                act.cancel()
            await bot.send_chat_action(message.chat.id, action=ChatAction.UPLOAD_VIDEO_NOTE)
            await message.answer_video_note(FSInputFile(dst))
            await message.answer("Готово ✅")
            return

        # CIRCLE -> VIDEO
        if action == "circle_to_video" and message.video_note:
            act = await action_loop(ChatAction.RECORD_VIDEO)
            try:
                src = await tg_download_to_temp(message.video_note.file_id, ".mp4")
                dst = await ff_circle_to_video(src)
            finally:
                act.cancel()
            await bot.send_chat_action(message.chat.id, action=ChatAction.UPLOAD_VIDEO)
            await message.answer_video(FSInputFile(dst))
            await message.answer("Готово ✅")
            return

        # AUDIO FROM VIDEO
        if action == "audio_from_video" and message.video:
            act = await action_loop(ChatAction.RECORD_VOICE)
            try:
                src = await tg_download_to_temp(message.video.file_id, ".mp4")
                dst = await ff_extract_audio(src)
            finally:
                act.cancel()
            await bot.send_chat_action(message.chat.id, action=ChatAction.UPLOAD_DOCUMENT)
            await message.answer_audio(FSInputFile(dst))
            await message.answer("Готово ✅")
            return

        # AUDIO FROM CIRCLE
        if action == "audio_from_circle" and message.video_note:
            act = await action_loop(ChatAction.RECORD_VOICE)
            try:
                src = await tg_download_to_temp(message.video_note.file_id, ".mp4")
                dst = await ff_extract_audio(src)
            finally:
                act.cancel()
            await bot.send_chat_action(message.chat.id, action=ChatAction.UPLOAD_DOCUMENT)
            await message.answer_audio(FSInputFile(dst))
            await message.answer("Готово ✅")
            return

        # AUDIO FROM VOICE
        if action == "audio_from_voice" and message.voice:
            act = await action_loop(ChatAction.RECORD_VOICE)
            try:
                src = await tg_download_to_temp(message.voice.file_id, ".ogg")
                dst = await ff_to_mp3(src)
            finally:
                act.cancel()
            await bot.send_chat_action(message.chat.id, action=ChatAction.UPLOAD_DOCUMENT)
            await message.answer_audio(FSInputFile(dst))
            await message.answer("Готово ✅")
            return

        # AUDIOFILE -> VOICE
        if action == "audio_to_voice" and message.audio:
            act = await action_loop(ChatAction.RECORD_VOICE)
            try:
                file_id = message.audio.file_id
                suffix = ".mp3" if (message.audio.file_name or "").endswith(".mp3") else ".ogg"
                src = await tg_download_to_temp(file_id, suffix)
                dst = await ff_to_voice(src)
            finally:
                act.cancel()
            await bot.send_chat_action(message.chat.id, action=ChatAction.UPLOAD_VOICE)
            await message.answer_voice(FSInputFile(dst))
            await message.answer("Готово ✅")
            return

        # VIDEO/CIRCLE -> VOICE
        if action == "media_to_voice" and (message.video or message.video_note):
            act = await action_loop(ChatAction.RECORD_VOICE)
            try:
                if message.video:
                    file_id, suffix = message.video.file_id, ".mp4"
                else:
                    file_id, suffix = message.video_note.file_id, ".mp4"
                src = await tg_download_to_temp(file_id, suffix)
                tmp_audio = await ff_extract_audio(src)
                dst = await ff_to_voice(tmp_audio)
            finally:
                act.cancel()
            await bot.send_chat_action(message.chat.id, action=ChatAction.UPLOAD_VOICE)
            await message.answer_voice(FSInputFile(dst))
            await message.answer("Готово ✅")
            return

        # Fallback if wrong type
        await message.answer("Это не подходит для выбранной функции. Попробуй снова 🙌")

    except HTTPException as e:
        await message.answer(f"⚠️ {e.detail}")
    except Exception as e:
        await message.answer("❌ Ошибка обработки файла.")
        print("ERROR:", repr(e))



@app.get("/health")
async def health():
    return PlainTextResponse("ok")
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
@app.on_event("startup")
async def on_startup_event():
    init_db()

@router.message(F.text == "/stats")
async def stats_cmd(message: Message):
    if message.from_user.username != "Maksimqax":
        return
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    total = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    today = cur.execute("SELECT COUNT(*) FROM hits WHERE dt = ?", (date.today().isoformat(),)).fetchone()[0]
    con.close()
    await message.answer(f"👥 Всего пользователей: {total}\n📅 Сегодня активных: {today}")

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram import types

async def ensure_subscribed(bot: Bot, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id="@mediahelperbottt", user_id=user_id)
        return member.status in ("member","administrator","creator")
    except Exception:
        return False

def subscribe_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подписаться", url="https://t.me/mediahelperbottt")],
        [InlineKeyboardButton(text="🔄 Проверить", callback_data="check_sub")]
    ])
    return kb

@router.callback_query(F.data == "check_sub")
async def cb_check_sub(c: CallbackQuery, bot: Bot):
    if await ensure_subscribed(bot, c.from_user.id):
        await c.message.edit_text("✅ Спасибо! Теперь выбери функцию заново в меню.")
    else:
        await c.answer("Вы ещё не подписались.", show_alert=True)