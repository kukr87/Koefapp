# Koefii Mini App

Telegram Mini App для футбольных прогнозов. Полный аналог бота с тёмным футбольным дизайном и фото профиля пользователей.

## Возможности

- 👤 Аккаунт с фото профиля из Telegram
- ⚽ Матчи по странам/лигам, лимит 3/день для Free
- ⭐ Избранное
- 🚆 Экспресс дня (только Premium)
- 📊 Статистика экспрессов (винрейт, ROI, прибыль)
- 💳 Покупка Premium через YooKassa
- 🎁 Test Drive — 30 дней Premium новым пользователям
- 🔗 Реферальная система (+3 дня Premium)
- ✉️ Техподдержка
- 🔧 Админ-API (матчи, экспресс, premium)

## Стек

- **Backend**: FastAPI + SQLite
- **Frontend**: Vanilla JS + Telegram WebApp API
- **Дизайн**: тёмная футбольная тема (зелёный акцент, Orbitron + Inter)

## Быстрый старт

### 1. Переменные окружения

```bash
cp .env.example .env
# Заполните BOT_TOKEN, ADMIN_ID, BOT_USERNAME
# Опционально: YOOKASSA_*, BSD_API_KEY
```

### 2. Установка зависимостей

```bash
cd backend
pip install -r requirements.txt
```

### 3. Запуск

```bash
cd backend
python main.py
# или
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Приложение будет доступно на `http://localhost:8000`.

### 4. Настройка Mini App в BotFather

1. Откройте [@BotFather](https://t.me/BotFather)
2. `/mybots` → выберите бота → **Bot Settings** → **Menu Button** / **Configure Mini App**
3. Укажите HTTPS URL вашего сервера (например через ngrok / VPS):
   ```
   https://your-domain.com
   ```
4. В боте добавьте кнопку меню или команду `/app`, которая открывает Mini App.

### 5. Локальная разработка с ngrok

```bash
ngrok http 8000
# Скопируйте HTTPS URL в BotFather
```

## Структура

```
koefii-miniapp/
├── backend/
│   ├── main.py          # FastAPI + вся бизнес-логика
│   ├── requirements.txt
│   └── bot.db           # создаётся автоматически
├── frontend/
│   ├── index.html
│   ├── css/style.css    # тёмный футбольный дизайн
│   └── js/app.js        # логика Mini App
├── .env.example
└── README.md
```

## Авторизация

Все API-запросы требуют заголовок:

```
X-Telegram-Init-Data: <initData из Telegram.WebApp>
```

Сервер проверяет подпись по `BOT_TOKEN`.

## Фото профиля

Берётся из `Telegram.WebApp.initDataUnsafe.user.photo_url` (если доступно) и сохраняется в БД. Отображается в шапке и на странице аккаунта.

## Важно

- Mini App **должен** открываться из Telegram (иначе нет `initData`).
- Для платежей нужен YooKassa.
- База SQLite общая с исходным ботом (можно скопировать `bot.db`).

## Лицензия

Для личного использования.
