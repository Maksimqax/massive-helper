
import os
import tempfile
import aiohttp
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import PlainTextResponse
from aiogram import Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.client.bot import Bot
from aiogram.types import Update, Message, FSInputFile
from aiogram.dispatcher.dispatcher import Dispatcher
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")
SECRET_TOKEN = os.getenv("SECRET_TOKEN", "").strip()
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "18"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()
router = Router()
dp.include_router(router)

app = FastAPI()

def main_menu_kb():
    kb = ReplyKeyboardBuilder()
    # Порядок без путаницы + переименовано 'голос' -> 'голосовое'
    kb.button(text="🎦 Видео / Кружок")
    kb.button(text="🎧 Голосовое / MP3")
    kb.button(text="📝 Текст → Голосовое")
    kb.button(text="ℹ️ Помощь")
    kb.adjust(1)
    return kb.as_markup(resize_keyboard=True)

def video_circle_kb():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🎬 Видео → 🔵 Кружок")
    kb.button(text="🔵 Кружок → 🎬 Видео")
    kb.button(text="⬅️ Назад")
    kb.adjust(1)
    return kb.as_markup(resize_keyboard=True)

@router.message(CommandStart())
async def start(m: Message):
    await m.answer("Привет! Выбери действие 👇", reply_markup=main_menu_kb())

@router.message(F.text == "⬅️ Назад")
async def back_to_menu(m: Message):
    await m.answer("Главное меню 👇", reply_markup=main_menu_kb())

@router.message(F.text == "🎦 Видео / Кружок")
async def open_video_circle(m: Message):
    await m.answer("Выбери режим 👇", reply_markup=video_circle_kb())

@router.message(F.text == "🎬 Видео → 🔵 Кружок")
async def ask_video_for_circle(m: Message):
    await m.answer("Пришли <b>видео</b> (до {} МБ).".format(MAX_FILE_MB))

@router.message(F.text == "🔵 Кружок → 🎬 Видео")
async def ask_circle_for_video(m: Message):
    await m.answer("Пришли <b>кружок</b> (video note) (до {} МБ).".format(MAX_FILE_MB))

async def tg_download_to_temp(file_id: str, suffix: str) -> str:
    # Универсальная и стабильная загрузка через file_path URL
    f = await bot.get_file(file_id)
    file_path = f.file_path
    url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = tmp.name
    tmp.close()
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise RuntimeError(f"TG download failed: {resp.status}")
            with open(tmp_path, "wb") as out:
                out.write(await resp.read())
    size_mb = os.path.getsize(tmp_path) / (1024*1024)
    if size_mb > MAX_FILE_MB:
        os.unlink(tmp_path)
        raise HTTPException(status_code=400, detail="Файл слишком большой для обработки")
    return tmp_path

@router.message(F.video)
async def handle_video_to_circle(m: Message):
    # Обрабатываем только когда пользователь выбирал соответствующий пункт
    # (простая проверка на последнюю кнопку — для краткости опущено состояние FSM)
    try:
        src_path = await tg_download_to_temp(m.video.file_id, ".mp4")
    except Exception as e:
        await m.answer(f"Ошибка загрузки: {e}")
        return
    try:
        # Отправляем сначала результат БЕЗ подписи (чистое медиа для пересылки)
        await m.answer_video_note(video_note=FSInputFile(src_path))
        # Затем отдельным сообщением статус
        await m.answer("Готово ✅", reply_markup=video_circle_kb())
    finally:
        try:
            os.unlink(src_path)
        except Exception:
            pass

@router.message(F.video_note)
async def handle_circle_to_video(m: Message):
    try:
        src_path = await tg_download_to_temp(m.video_note.file_id, ".mp4")
    except Exception as e:
        await m.answer(f"Ошибка загрузки: {e}")
        return
    try:
        await m.answer_video(video=FSInputFile(src_path), supports_streaming=True)
        await m.answer("Готово ✅", reply_markup=video_circle_kb())
    finally:
        try:
            os.unlink(src_path)
        except Exception:
            pass

# Заглушки для других пунктов меню (именовали 'голос' -> 'голосовое')
@router.message(F.text == "🎧 Голосовое / MP3")
async def voice_mp3(m: Message):
    await m.answer("Раздел в разработке.")

@router.message(F.text == "📝 Текст → Голосовое")
async def tts(m: Message):
    await m.answer("Раздел в разработке.")

@router.message(F.text == "ℹ️ Помощь")
async def help_msg(m: Message):
    await m.answer("• 🎦 Видео / Кружок — конвертации между форматом видео и кружка.\n• «Готово ✅» приходит отдельным сообщением.")

@app.get("/", response_class=PlainTextResponse)
async def root():
    return "OK"

@app.post("/")
async def webhook(request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if SECRET_TOKEN and (x_telegram_bot_api_secret_token or "") != SECRET_TOKEN:
        raise HTTPException(status_code=403, detail="Wrong secret")
    data = await request.json()
    update = Update.model_validate(data)
    await dp.feed_update(bot, update)
    return {"ok": True}
