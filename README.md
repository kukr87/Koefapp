# Koefii Mini App

Telegram Mini App — футбольные прогнозы, тёмный дизайн, фото профиля, полная админка с парсером.

## Структура (важно для Render)

```
koefii-miniapp/
├── requirements.txt      ← в КОРНЕ (для Render)
├── render.yaml
├── Procfile
├── backend/
│   ├── main.py
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── css/style.css
│   └── js/app.js
├── .env.example
└── README.md
```

## Деплой на Render

1. Залейте **всю** папку на GitHub.
2. render.com → **New → Web Service** → GitHub-репозиторий.
3. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Root Directory: **пусто**
4. Environment variables:
   - `BOT_TOKEN` — токен бота
   - `ADMIN_ID` — ваш Telegram ID (число)
   - `BOT_USERNAME` — username бота без @
   - `BSD_API_KEY` — для парсера матчей
   - `YOOKASSA_SHOP_ID`, `YOOKASSA_SECRET_KEY` — для оплаты
5. Deploy → URL вида `https://xxx.onrender.com` вставьте в BotFather (Mini App / Menu Button).

Проверка: `https://xxx.onrender.com/api/health`

## Локальный запуск

```bash
pip install -r requirements.txt
cd backend && uvicorn main:app --host 0.0.0.0 --port 8000
```

## Админка

Откройте Mini App под аккаунтом с `ADMIN_ID`.  
Внизу появится вкладка **Админ** → **Парсер матчей**, ручное добавление, экспресс, Premium, поддержка.

## Возможности

- Аккаунт + фото профиля из Telegram
- Матчи, избранное, лимит 3/день для Free
- Экспресс дня (Premium)
- Статистика, рефералка, Test Drive 30 дней
- YooKassa Premium
- Парсер BSD API + ручное добавление
