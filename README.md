# TG Media Bot (FastAPI + aiogram 3.x)

## Что умеет
- 🎥 Видео → 🎤 Голос
- 🔵 Кружок → 🎤 Голос
- 🎤 Голос → 🎵 MP3
- 🎵 Аудио → 🎤 Голос
- Ограничение по размеру файла (MAX_FILE_MB, по умолчанию 18)

> Требуется `ffmpeg` в окружении. На Render обычно недоступен через apt, используйте кастомный образ или внешнюю сборку, либо поставьте статику. Если ffmpeg нет — бот корректно сообщит об этом.

## Переменные окружения
- `BOT_TOKEN` — токен бота
- `WEBHOOK_URL` — публичный URL сервиса (https://your-app.onrender.com)
- `SECRET_TOKEN` — опционально, должен совпадать с секретом в setWebhook
- `MAX_FILE_MB` — лимит размера файла в мегабайтах (по умолчанию 18)

## Запуск локально
```
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export BOT_TOKEN=... WEBHOOK_URL=http://localhost:10000  # или просто не задавать
uvicorn app.main:app --reload --port 10000
```

## Render (render.yaml)
- Подключи репо, выбери **Web Service**
- Build: `pip install -r requirements.txt`
- Start: `./start.sh`
- Добавь env vars: BOT_TOKEN, WEBHOOK_URL, SECRET_TOKEN (необязательно), MAX_FILE_MB
