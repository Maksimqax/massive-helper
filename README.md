# tg_media_bot

Телеграм-бот для конвертаций медиа: извлечение аудио, видео↔кружок, создание голосовых.

## Быстрый запуск локально

```bash
python -m venv .venv
source .venv/bin/activate  # или .venv\Scripts\activate в Windows
pip install -r requirements.txt
export BOT_TOKEN=...
export WEBHOOK_URL=http://localhost:10000
uvicorn app.main:app --reload --port 10000
```

## Render

- Залейте этот репозиторий.
- В разделе **Environment**/Secret Files задайте переменные: `BOT_TOKEN`, `WEBHOOK_URL`, опционально `SECRET_TOKEN`, `MAX_FILE_MB`.
- После деплоя вызовите GET `https://<ваш-домен>/set-webhook` один раз.