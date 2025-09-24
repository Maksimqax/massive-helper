import os
import asyncio
import tempfile
from pathlib import Path

from fastapi import FastAPI, Request
from aiogram import Bot, Dispatcher, types
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Update
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN env var is not set")

bot = Bot(TOKEN)
dp = Dispatcher(storage=MemoryStorage())
app = FastAPI()

# ---------- Healthcheck ----------
@app.get("/ping")
async def ping():
    return {"status": "ok"}

# ---------- Helper: run ffmpeg ----------
async def run_ffmpeg(cmd: list):
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="ignore")[:4000]
        raise RuntimeError(f"ffmpeg error: {err}")

# ---------- FSM States ----------
class Flow(StatesGroup):
    audio_from_video = State()
    audio_from_circle = State()
    audio_from_voice = State()
    audio_to_voice = State()
    video_to_voice = State()
    video_to_circle = State()
    circle_to_video = State()

# ---------- Keyboards ----------
def main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="Аудио 🔊", callback_data="menu_audio")
    kb.button(text="Видео / Кружок 🎦", callback_data="menu_video")
    kb.adjust(2)
    return kb.as_markup()

def audio_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="Видео → аудио", callback_data="audio_from_video")
    kb.button(text="Кружок → аудио", callback_data="audio_from_circle")
    kb.button(text="Голосов. → аудио", callback_data="audio_from_voice")
    kb.button(text="Аудио → голос.", callback_data="audio_to_voice")
    kb.button(text="Вид/Круж → голос.", callback_data="video_to_voice")
    kb.button(text="↩️ Назад", callback_data="back_main")
    kb.adjust(2, 2, 2)
    return kb.as_markup()

def video_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="Видео → кружок", callback_data="video_to_circle")
    kb.button(text="Кружок → видео", callback_data="circle_to_video")
    kb.button(text="↩️ Назад", callback_data="back_main")
    kb.adjust(2, 1)
    return kb.as_markup()

# ---------- Entrypoints ----------
@dp.message(Command("start"))
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("👋 Привет! Выберите категорию:", reply_markup=main_menu())

@dp.callback_query()
async def callbacks(cb: types.CallbackQuery, state: FSMContext):
    if cb.data == "menu_audio":
        await state.clear()
        await cb.message.edit_text(
            "🔊 **Аудио — выберите услугу:**\n"
            "• Видео → аудио (извлечь звук)\n"
            "• Кружок → аудио (звук из video note)\n"
            "• Голосовое → аудио (mp3)\n"
            "• Аудио → голосовое (voice)\n"
            "• Видео/кружок → голосовое (voice)",
            reply_markup=audio_menu(),
            parse_mode="Markdown"
        )
    elif cb.data == "menu_video":
        await state.clear()
        await cb.message.edit_text(
            "🎦 **Видео / Кружок — выберите услугу:**\n"
            "• Видео → кружок (video note)\n"
            "• Кружок → видео (mp4)",
            reply_markup=video_menu(),
            parse_mode="Markdown"
        )
    elif cb.data == "back_main":
        await state.clear()
        await cb.message.edit_text("↩️ Возврат в главное меню:", reply_markup=main_menu())

    # Set states and prompt
    elif cb.data == "audio_from_video":
        await state.set_state(Flow.audio_from_video)
        await cb.message.answer("Отправьте **видео** (mp4/mov ≤ ~50 МБ). Я извлеку из него звук и пришлю файл.", parse_mode="Markdown")
    elif cb.data == "audio_from_circle":
        await state.set_state(Flow.audio_from_circle)
        await cb.message.answer("Отправьте **кружок** (video note). Я извлеку из него звук и пришлю файл.", parse_mode="Markdown")
    elif cb.data == "audio_from_voice":
        await state.set_state(Flow.audio_from_voice)
        await cb.message.answer("Отправьте **голосовое**. Я конвертирую его в обычный аудиофайл (mp3).", parse_mode="Markdown")
    elif cb.data == "audio_to_voice":
        await state.set_state(Flow.audio_to_voice)
        await cb.message.answer("Отправьте **аудиофайл** (mp3/m4a/ogg). Я превращу его в *voice*.", parse_mode="Markdown")
    elif cb.data == "video_to_voice":
        await state.set_state(Flow.video_to_voice)
        await cb.message.answer("Отправьте **видео или кружок**. Я извлеку звук и пришлю *voice*.", parse_mode="Markdown")
    elif cb.data == "video_to_circle":
        await state.set_state(Flow.video_to_circle)
        await cb.message.answer("Отправьте **видео** (желательно ≤ 60с). Сделаю из него *кружок* (video note).", parse_mode="Markdown")
    elif cb.data == "circle_to_video":
        await state.set_state(Flow.circle_to_video)
        await cb.message.answer("Отправьте **кружок**. Конвертирую его в обычное **видео** (mp4).", parse_mode="Markdown")

# ---------- Handlers Implementation ----------

# 1) Видео → аудио (OGG/Opus)
@dp.message(Flow.audio_from_video)
async def handle_video_to_audio(message: types.Message, state: FSMContext):
    video = message.video or message.document if (message.document and message.document.mime_type and message.document.mime_type.startswith("video/")) else None
    if not video:
        await message.answer("Пришлите **видеофайл** (mp4/mov).", parse_mode="Markdown")
        return

    await message.answer("⏳ Обрабатываю видео...")
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "input.mp4"
        out_path = Path(tmp) / "audio.ogg"
        await (video if isinstance(video, types.Video) else message.document).download(destination=in_path)

        cmd = ["ffmpeg", "-i", str(in_path), "-vn", "-acodec", "libopus", "-ar", "48000", "-ac", "1", "-b:a", "64k", str(out_path), "-y"]
        try:
            await run_ffmpeg(cmd)
            await message.answer_document(types.FSInputFile(str(out_path)), caption="Готово: извлечённое аудио (OGG/Opus).")
        except Exception as e:
            await message.answer(f"Ошибка: {e}")

    await state.clear()
    await message.answer("Ещё что-то сделать?", reply_markup=main_menu())

# 2) Кружок → аудио (OGG/Opus)
@dp.message(Flow.audio_from_circle)
async def handle_circle_to_audio(message: types.Message, state: FSMContext):
    if not message.video_note:
        await message.answer("Пришлите **кружок** (video note).", parse_mode="Markdown")
        return
    await message.answer("⏳ Обрабатываю кружок...")
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "circle.mp4"
        out_path = Path(tmp) / "audio.ogg"
        await message.video_note.download(destination=in_path)
        cmd = ["ffmpeg", "-i", str(in_path), "-vn", "-acodec", "libopus", "-ar", "48000", "-ac", "1", "-b:a", "64k", str(out_path), "-y"]
        try:
            await run_ffmpeg(cmd)
            await message.answer_document(types.FSInputFile(str(out_path)), caption="Готово: звук из кружка (OGG/Opus).")
        except Exception as e:
            await message.answer(f"Ошибка: {e}")
    await state.clear()
    await message.answer("Ещё что-то сделать?", reply_markup=main_menu())

# 3) Голосовое → аудио (MP3)
@dp.message(Flow.audio_from_voice)
async def handle_voice_to_audio(message: types.Message, state: FSMContext):
    if not message.voice:
        await message.answer("Пришлите **голосовое**.", parse_mode="Markdown")
        return
    await message.answer("⏳ Конвертирую голосовое в mp3...")
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "voice.ogg"
        out_path = Path(tmp) / "voice.mp3"
        await message.voice.download(destination=in_path)
        cmd = ["ffmpeg", "-i", str(in_path), "-acodec", "libmp3lame", "-b:a", "128k", str(out_path), "-y"]
        try:
            await run_ffmpeg(cmd)
            await message.answer_document(types.FSInputFile(str(out_path)), caption="Готово: аудиофайл MP3 из голосового.")
        except Exception as e:
            await message.answer(f"Ошибка: {e}")
    await state.clear()
    await message.answer("Ещё что-то сделать?", reply_markup=main_menu())

# 4) Аудио → голосовое (voice OGG/Opus)
@dp.message(Flow.audio_to_voice)
async def handle_audio_to_voice(message: types.Message, state: FSMContext):
    audio = message.audio or message.document if (message.document and message.document.mime_type and message.document.mime_type.startswith("audio/")) else None
    if not audio:
        await message.answer("Пришлите **аудиофайл** (mp3/m4a/ogg).", parse_mode="Markdown")
        return
    await message.answer("⏳ Делаю voice из аудио...")
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "in_audio"
        out_path = Path(tmp) / "voice.ogg"
        await (audio if isinstance(audio, types.Audio) else message.document).download(destination=in_path)
        cmd = ["ffmpeg", "-i", str(in_path), "-acodec", "libopus", "-ar", "48000", "-ac", "1", "-b:a", "48k", str(out_path), "-y"]
        try:
            await run_ffmpeg(cmd)
            await message.answer_voice(types.FSInputFile(str(out_path)), caption="Готово: voice.")
        except Exception as e:
            await message.answer(f"Ошибка: {e}")
    await state.clear()
    await message.answer("Ещё что-то сделать?", reply_markup=main_menu())

# 5) Видео/Кружок → голосовое (voice)
@dp.message(Flow.video_to_voice)
async def handle_video_or_circle_to_voice(message: types.Message, state: FSMContext):
    src = None
    kind = None
    if message.video:
        src = message.video; kind = "video"
    elif message.video_note:
        src = message.video_note; kind = "circle"
    elif message.document and message.document.mime_type and message.document.mime_type.startswith("video/"):
        src = message.document; kind = "video"
    if not src:
        await message.answer("Пришлите **видео или кружок**.", parse_mode="Markdown"); return

    await message.answer("⏳ Извлекаю звук и делаю voice...")
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / ("input.mp4" if kind != "circle" else "circle.mp4")
        out_path = Path(tmp) / "voice.ogg"
        await src.download(destination=in_path)
        cmd = ["ffmpeg", "-i", str(in_path), "-vn", "-acodec", "libopus", "-ar", "48000", "-ac", "1", "-b:a", "48k", str(out_path), "-y"]
        try:
            await run_ffmpeg(cmd)
            await message.answer_voice(types.FSInputFile(str(out_path)), caption="Готово: voice из видео/кружка.")
        except Exception as e:
            await message.answer(f"Ошибка: {e}")
    await state.clear()
    await message.answer("Ещё что-то сделать?", reply_markup=main_menu())

# 6) Видео → кружок (video note, square h264+aac)
@dp.message(Flow.video_to_circle)
async def handle_video_to_circle(message: types.Message, state: FSMContext):
    video = message.video or message.document if (message.document and message.document.mime_type and message.document.mime_type.startswith("video/")) else None
    if not video:
        await message.answer("Пришлите **видеофайл** (mp4/mov).", parse_mode="Markdown")
        return
    await message.answer("⏳ Делаю кружок...")
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "input.mp4"
        out_path = Path(tmp) / "circle.mp4"
        await (video if isinstance(video, types.Video) else message.document).download(destination=in_path)

        # square 720x720, pad if needed; h264 + aac
        vf = "scale=720:720:force_original_aspect_ratio=decrease,pad=720:720:(ow-iw)/2:(oh-ih)/2"
        cmd = [
            "ffmpeg", "-i", str(in_path),
            "-vf", vf,
            "-vcodec", "libx264", "-preset", "veryfast", "-profile:v", "main", "-level", "3.1", "-b:v", "1200k",
            "-acodec", "aac", "-b:a", "96k",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            str(out_path), "-y"
        ]
        try:
            await run_ffmpeg(cmd)
            await message.answer_video_note(types.FSInputFile(str(out_path)))
        except Exception as e:
            await message.answer(f"Ошибка: {e}")
    await state.clear()
    await message.answer("Ещё что-то сделать?", reply_markup=main_menu())

# 7) Кружок → видео (mp4)
@dp.message(Flow.circle_to_video)
async def handle_circle_to_video(message: types.Message, state: FSMContext):
    if not message.video_note:
        await message.answer("Пришлите **кружок** (video note).", parse_mode="Markdown")
        return
    await message.answer("⏳ Конвертирую кружок в видео...")
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "circle.mp4"
        out_path = Path(tmp) / "video.mp4"
        await message.video_note.download(destination=in_path)

        # Перекодируем (на случай несовместимости)
        cmd = [
            "ffmpeg", "-i", str(in_path),
            "-c:v", "libx264", "-preset", "veryfast", "-b:v", "1200k",
            "-c:a", "aac", "-b:a", "96k",
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            str(out_path), "-y"
        ]
        try:
            await run_ffmpeg(cmd)
            await message.answer_video(types.FSInputFile(str(out_path)), caption="Готово: обычное видео (mp4).")
        except Exception as e:
            await message.answer(f"Ошибка: {e}")
    await state.clear()
    await message.answer("Ещё что-то сделать?", reply_markup=main_menu())

# ---------- Webhook ----------
@app.post("/")
async def webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}
