CARCITYPRO BACKEND — RENDER

Файлы этого проекта можно загрузить в отдельный GitHub-репозиторий.

Содержимое репозитория:
- server.py
- pdf_generator.py
- requirements.txt
- render.yaml
- .gitignore

ВАЖНО:
Не загружай token.txt.
Токен Telegram-бота хранится в Render как BOT_TOKEN.

Вариант 1 — через render.yaml / Blueprint:
1. Создай отдельный GitHub-репозиторий CarcityPRO-backend.
2. Загрузи в корень все файлы из этой папки.
3. В Render создай Blueprint и подключи репозиторий.
4. Render увидит render.yaml.
5. Когда Render попросит значение BOT_TOKEN, вставь токен CarcityPRO-бота.
6. Запусти создание сервиса.
7. После успешного deploy открой:
   https://<твой-сервис>.onrender.com/health

Ожидаемый ответ:
{"status":"ok","service":"CarcityPRO PDF API"}

После этого:
1. Скопируй постоянный адрес Render без /health.
2. В CarcityPRO-app/index.html найди const API_URL.
3. Замени trycloudflare.com на адрес Render.
4. Commit changes.

После этого Uvicorn и Cloudflare на ноутбуке для PDF больше не нужны.
