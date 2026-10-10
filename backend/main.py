"""
Koefii Mini App Backend
Telegram Mini App + Bot hybrid
"""
import asyncio
import json
import os
import base64
import logging
import sqlite3
import hmac
import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, List, Dict, Any
from urllib.parse import parse_qsl

import aiohttp
from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

# ====================== НАСТРОЙКИ ======================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("koefiiminiapp")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
BOT_USERNAME = os.getenv("BOT_USERNAME", "koefiibot")

YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "")
BSD_API_KEY = os.getenv("BSD_API_KEY", "")

PREMIUM_PRICE = 300
tz = ZoneInfo("Europe/Moscow")
DB_FILE = os.path.join(os.path.dirname(__file__), "bot.db")

admin_users = {str(ADMIN_ID)}

app = FastAPI(title="Koefii Mini App API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ====================== TELEGRAM AUTH ======================

def validate_init_data(init_data: str) -> dict:
    """Validate Telegram WebApp initData and return user data."""
    if not init_data or not BOT_TOKEN:
        raise HTTPException(status_code=401, detail="No initData")

    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", None)
        if not received_hash:
            raise HTTPException(status_code=401, detail="No hash")

        data_check_string = "\n".join(
            f"{k}={v}" for k, v in sorted(parsed.items())
        )
        secret_key = hmac.new(
            b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256
        ).digest()
        calculated_hash = hmac.new(
            secret_key, data_check_string.encode(), hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            raise HTTPException(status_code=401, detail="Invalid hash")

        user_data = json.loads(parsed.get("user", "{}"))
        return user_data
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("initData validation error: %s", e)
        raise HTTPException(status_code=401, detail="Auth failed")


async def get_current_user(x_telegram_init_data: Optional[str] = Header(None)):
    if not x_telegram_init_data:
        raise HTTPException(status_code=401, detail="Missing X-Telegram-Init-Data")
    return validate_init_data(x_telegram_init_data)


# ====================== COUNTRY FLAGS & LEAGUES ======================

COUNTRY_FLAGS = {
    "Russia": "🇷🇺", "England": "🇬🇧", "Spain": "🇪🇸", "Italy": "🇮🇹",
    "Germany": "🇩🇪", "France": "🇫🇷", "Portugal": "🇵🇹", "Turkey": "🇹🇷",
    "Serbia": "🇷🇸", "Kazakhstan": "🇰🇿", "Ukraine": "🇺🇦", "Belarus": "🇧🇾",
    "USA": "🇺🇸", "Brazil": "🇧🇷", "Argentina": "🇦🇷", "Austria": "🇦🇹",
    "Belgium": "🇧🇪", "Bulgaria": "🇧🇬", "Hungary": "🇭🇺", "Greece": "🇬🇷",
    "Denmark": "🇩🇰", "Ireland": "🇮🇪", "Netherlands": "🇳🇱", "Norway": "🇳🇴",
    "Poland": "🇵🇱", "Croatia": "🇭🇷", "Sweden": "🇸🇪", "Switzerland": "🇨🇭",
    "Czech Republic": "🇨🇿", "Romania": "🇷🇴", "Slovakia": "🇸🇰",
    "Slovenia": "🇸🇮", "Montenegro": "🇲🇪", "China": "🇨🇳", "Mexico": "🇲🇽",
    "Finland": "🇫🇮", "Saudi Arabia": "🇸🇦", "Morocco": "🇲🇦",
    "World": "🌍", "Europe": "🇪🇺",
}

SUPPORT_CATEGORIES = {
    "bug": "🐛 Баг / ошибка",
    "pay": "💳 Оплата",
    "idea": "💡 Предложение",
    "q": "❓ Вопрос",
}

LEAGUE_PRIORITIES = {
    "World Cup": 1, "European Championship": 1, "UEFA Nations League": 1,
    "UEFA Champions League": 2, "UEFA Europa League": 2, "UEFA Europa Conference League": 2,
    "Premier League": 3, "La Liga": 3, "Serie A": 3, "Bundesliga": 3,
    "Ligue 1": 3, "Eredivisie": 3, "Russian Premier League": 3, "Primeira Liga": 3,
    "Belgian Pro League": 4, "Bulgarian First League": 4, "Danish Superliga": 4,
    "Swiss Super League": 4, "Süper Lig": 4, "League of Ireland": 4,
    "Major League Soccer": 4, "Serie A Brasileirão": 4, "Primera División": 4,
    "Czech First League": 4, "Super League Greece": 4, "Eliteserien": 4,
    "Ekstraklasa": 4, "Allsvenskan": 4, "Croatian Football League": 4,
    "Serbian SuperLiga": 4, "Nemzeti Bajnokság I": 4, "Austrian Bundesliga": 4,
    "Belarusian Premier League": 4, "Liga I": 4, "Slovak Super Liga": 4,
    "Slovenian PrvaLiga": 4, "Ukrainian Premier League": 4, "Montenegrin First League": 4,
    "EFL Championship": 5, "Segunda División": 5, "Serie B": 5, "2. Bundesliga": 5, "FNL": 5,
}

COUNTRY_NORMALIZE = {
    "Czech-Republic": "Czech Republic",
    "Czech Republic": "Czech Republic",
    "USA": "USA",
    "United States": "USA",
    "UK": "England",
    "World": "World",
    "Europe": "Europe",
}

def get_flag(country: str) -> str:
    return COUNTRY_FLAGS.get(country, "🏳️")

# ====================== БАЗА ДАННЫХ ======================

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id      INTEGER PRIMARY KEY,
        username     TEXT DEFAULT '',
        first_name   TEXT DEFAULT '',
        photo_url    TEXT DEFAULT '',
        subscription TEXT DEFAULT 'Free',
        end_date     TEXT DEFAULT NULL,
        reg_date     TEXT,
        referred_by  INTEGER DEFAULT NULL,
        streak_days  INTEGER DEFAULT 0,
        last_visit   TEXT DEFAULT NULL,
        status       TEXT DEFAULT 'active',
        notified_expire INTEGER DEFAULT 0
    )""")

    # Миграция: добавить photo_url если нет
    try:
        c.execute("ALTER TABLE users ADD COLUMN photo_url TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass

    c.execute("""CREATE TABLE IF NOT EXISTS matches (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        name      TEXT,
        country   TEXT,
        league    TEXT,
        match_time TEXT DEFAULT '',
        text      TEXT,
        created_at TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS viewed_matches (
        user_id   INTEGER,
        match_id  INTEGER,
        viewed_date TEXT,
        PRIMARY KEY (user_id, match_id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS favorites (
        user_id  INTEGER,
        match_id INTEGER,
        added_at TEXT,
        PRIMARY KEY (user_id, match_id)
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS express_picks (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT,
        country    TEXT,
        league     TEXT,
        prediction  TEXT,
        coef       TEXT,
        created_at TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS express_results (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        type       TEXT,
        coefs      TEXT,
        coef       REAL,
        result     TEXT DEFAULT 'pending',
        created_at TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS support_threads (
        thread_id INTEGER PRIMARY KEY,
        user_id   INTEGER,
        user_name TEXT,
        category  TEXT,
        created_at TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS support_messages (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id  INTEGER,
        sender     TEXT,
        text       TEXT,
        created_at TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS pending_payments (
        payment_id TEXT PRIMARY KEY,
        user_id    INTEGER,
        amount     REAL,
        status     TEXT DEFAULT 'pending',
        created_at TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS admin_log (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id   INTEGER,
        action     TEXT,
        details    TEXT,
        created_at TEXT
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS parsed_matches (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT,
        country     TEXT,
        country_en  TEXT,
        league      TEXT,
        league_id   INTEGER,
        match_time  TEXT DEFAULT '',
        match_date  TEXT,
        priority    INTEGER DEFAULT 99
    )""")

    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

# --- Sync DB helpers (для FastAPI) ---

def db_execute(query: str, params: tuple = ()):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.execute(query, params)
    conn.commit()
    conn.close()

def db_fetchone(query: str, params: tuple = ()):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    c = conn.cursor()
    c.execute(query, params)
    row = c.fetchone()
    conn.close()
    return row

def db_fetchall(query: str, params: tuple = ()):
    conn = sqlite3.connect(DB_FILE, timeout=10)
    c = conn.cursor()
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    return rows

# --- User helpers ---

def get_or_create_user(user_id: int, first_name: str = "", username: str = "", photo_url: str = "") -> dict:
    row = db_fetchone("SELECT * FROM users WHERE user_id = ?", (user_id,))
    if row:
        # Обновляем photo_url и имя если изменились
        if photo_url or first_name or username:
            db_execute(
                "UPDATE users SET photo_url = COALESCE(NULLIF(?, ''), photo_url), "
                "first_name = COALESCE(NULLIF(?, ''), first_name), "
                "username = COALESCE(NULLIF(?, ''), username) WHERE user_id = ?",
                (photo_url, first_name, username, user_id)
            )
        row = db_fetchone("SELECT * FROM users WHERE user_id = ?", (user_id,))
        return {
            "user_id": row[0], "username": row[1], "first_name": row[2],
            "photo_url": row[3] if len(row) > 3 else "",
            "subscription": row[4], "end_date": row[5], "reg_date": row[6],
            "referred_by": row[7], "streak_days": row[8], "last_visit": row[9],
            "status": row[10], "notified_expire": row[11],
        }
    now = datetime.now(tz).strftime("%Y-%m-%d")
    db_execute(
        "INSERT INTO users (user_id, username, first_name, photo_url, subscription, reg_date, status) "
        "VALUES (?, ?, ?, ?, 'Free', ?, 'active')",
        (user_id, username, first_name, photo_url, now)
    )
    return {
        "user_id": user_id, "username": username, "first_name": first_name,
        "photo_url": photo_url, "subscription": "Free", "end_date": None,
        "reg_date": now, "referred_by": None, "streak_days": 0,
        "last_visit": None, "status": "active", "notified_expire": 0,
    }

def update_user_subscription(user_id: int, sub: str, end_date: str = None):
    db_execute(
        "UPDATE users SET subscription = ?, end_date = ?, notified_expire = 0 WHERE user_id = ?",
        (sub, end_date, user_id)
    )

def check_match_limit(user_id: int, match_id: int) -> bool:
    user = get_or_create_user(user_id)
    if user["subscription"] == "Premium":
        return True
    today = datetime.now(tz).date()
    viewed = db_fetchall(
        "SELECT match_id FROM viewed_matches WHERE user_id = ? AND viewed_date = ?",
        (user_id, str(today))
    )
    viewed_ids = [r[0] for r in viewed]
    if match_id in viewed_ids:
        return True
    if len(viewed_ids) >= 3:
        return False
    db_execute(
        "INSERT OR IGNORE INTO viewed_matches (user_id, match_id, viewed_date) VALUES (?, ?, ?)",
        (user_id, match_id, str(today))
    )
    return True

def update_streak(user_id: int):
    user = get_or_create_user(user_id)
    today = datetime.now(tz).date()
    last_visit = user.get("last_visit")
    if last_visit:
        try:
            last_date = datetime.strptime(last_visit, "%Y-%m-%d").date()
            if last_date == today:
                return
            diff = (today - last_date).days
            if diff == 1:
                new_streak = user.get("streak_days", 0) + 1
            elif diff > 1:
                new_streak = 1
            else:
                new_streak = user.get("streak_days", 0)
        except ValueError:
            new_streak = 1
    else:
        new_streak = 1
    db_execute(
        "UPDATE users SET last_visit = ?, streak_days = ? WHERE user_id = ?",
        (str(today), new_streak, user_id)
    )

def calculate_express_stats() -> dict:
    rows = db_fetchall(
        "SELECT coef, result FROM express_results WHERE result IN ('win', 'lose')"
    )
    total = len(rows)
    wins = sum(1 for r in rows if r[1] == "win")
    losses = sum(1 for r in rows if r[1] == "lose")
    winrate = (wins / total * 100) if total > 0 else 0
    profit = 0.0
    for coef, result in rows:
        if result == "win":
            profit += (float(coef) - 1)
        else:
            profit += (-1)
    roi = (profit / total * 100) if total > 0 else 0
    return {
        "total": total, "wins": wins, "losses": losses,
        "winrate": winrate, "profit": profit, "roi": roi,
    }

def activate_premium(user_id: int, days: int = 30) -> str:
    user = get_or_create_user(user_id)
    if user["subscription"] == "Premium" and user["end_date"]:
        try:
            cur_end = datetime.strptime(user["end_date"], "%Y-%m-%d")
            if cur_end.date() > datetime.now(tz).date():
                new_end = (cur_end + timedelta(days=days)).strftime("%Y-%m-%d")
            else:
                new_end = (datetime.now(tz) + timedelta(days=days)).strftime("%Y-%m-%d")
        except ValueError:
            new_end = (datetime.now(tz) + timedelta(days=days)).strftime("%Y-%m-%d")
    else:
        new_end = (datetime.now(tz) + timedelta(days=days)).strftime("%Y-%m-%d")
    update_user_subscription(user_id, "Premium", new_end)
    return new_end

# ====================== YOOKASSA ======================

async def create_yookassa_payment(user_id: int, amount: float = PREMIUM_PRICE):
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        logger.error("YOOKASSA credentials missing")
        return None

    auth_string = f"{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}"
    auth_header = base64.b64encode(auth_string.encode("utf-8")).decode("utf-8")

    url = "https://api.yookassa.ru/v3/payments"
    idempotence_key = f"{user_id}-{int(datetime.now(tz).timestamp())}"

    payload = {
        "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": f"https://t.me/{BOT_USERNAME}"
        },
        "capture": True,
        "metadata": {"user_id": str(user_id)},
        "description": "Premium на 30 дней",
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {auth_header}",
        "Idempotence-Key": idempotence_key,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                data = await resp.json()
                if resp.status in (200, 201):
                    pid = data.get("id")
                    curl = data.get("confirmation", {}).get("confirmation_url")
                    if pid and curl:
                        now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
                        db_execute(
                            "INSERT OR REPLACE INTO pending_payments "
                            "(payment_id, user_id, amount, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                            (pid, user_id, amount, now)
                        )
                        return {"payment_id": pid, "confirmation_url": curl}
                logger.error("[YooKassa] Error: %s %s", resp.status, data)
                return None
    except Exception as e:
        logger.exception("[YooKassa] %s", e)
        return None

async def check_yookassa_payment(payment_id: str) -> str:
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        return "error"
    auth_string = f"{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}"
    auth_header = base64.b64encode(auth_string.encode("utf-8")).decode("utf-8")
    url = f"https://api.yookassa.ru/v3/payments/{payment_id}"
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                data = await resp.json()
                if resp.status == 200:
                    return data.get("status", "unknown")
                return "error"
    except Exception as e:
        logger.exception("[YooKassa check] %s", e)
        return "error"

# ====================== API MODELS ======================

class SupportMessage(BaseModel):
    category: str
    text: str

class MatchTextUpdate(BaseModel):
    match_id: int
    text: str

class ExpressPick(BaseModel):
    match_id: int
    prediction: str
    coef: str

class ResultCreate(BaseModel):
    type: str  # double / triple
    coefs: List[float]
    result: str  # win / lose

# ====================== API ENDPOINTS ======================

@app.on_event("startup")
async def startup():
    init_db()
    logger.info("Koefii Mini App backend started")

@app.get("/api/health")
async def health():
    return {"status": "ok", "time": datetime.now(tz).isoformat()}

@app.get("/api/me")
async def api_me(user: dict = Depends(get_current_user)):
    uid = user["id"]
    first_name = user.get("first_name", "")
    username = user.get("username", "")
    photo_url = user.get("photo_url", "")

    db_user = get_or_create_user(uid, first_name, username, photo_url)
    update_streak(uid)

    # Test Drive для новых
    is_new = db_user["reg_date"] == datetime.now(tz).strftime("%Y-%m-%d") and db_user["subscription"] == "Free"
    bonus = None
    if is_new and not db_user.get("end_date"):
        # Проверяем, был ли уже бонус (по факту если reg_date сегодня и Free — даём)
        end_date = activate_premium(uid, 30)
        db_user = get_or_create_user(uid)
        bonus = {
            "text": "🎁 Добро пожаловать! Вам активирован Test Drive — 30 дней Premium бесплатно!",
            "end_date": end_date
        }

    ref_count = len(db_fetchall("SELECT user_id FROM users WHERE referred_by = ?", (uid,)))
    fav_count = db_fetchone("SELECT COUNT(*) FROM favorites WHERE user_id = ?", (uid,))[0]
    today = str(datetime.now(tz).date())
    viewed_today = db_fetchone(
        "SELECT COUNT(*) FROM viewed_matches WHERE user_id = ? AND viewed_date = ?",
        (uid, today)
    )[0]
    total_users = db_fetchone("SELECT COUNT(*) FROM users")[0]
    premium_users = db_fetchone("SELECT COUNT(*) FROM users WHERE subscription = 'Premium'")[0]
    total_matches = db_fetchone("SELECT COUNT(*) FROM matches")[0]

    days_in_project = 0
    if db_user.get("reg_date"):
        try:
            reg = datetime.strptime(db_user["reg_date"], "%Y-%m-%d").date()
            days_in_project = (datetime.now(tz).date() - reg).days
        except ValueError:
            pass

    matches_left = "∞" if db_user["subscription"] == "Premium" else max(0, 3 - viewed_today)

    return {
        "user": {
            "id": uid,
            "first_name": db_user["first_name"] or first_name,
            "username": db_user["username"] or username,
            "photo_url": db_user.get("photo_url") or photo_url or "",
            "subscription": db_user["subscription"],
            "end_date": db_user.get("end_date"),
            "reg_date": db_user.get("reg_date"),
            "streak_days": db_user.get("streak_days", 0),
            "matches_left": matches_left,
            "days_in_project": days_in_project,
            "is_admin": str(uid) in admin_users,
        },
        "stats": {
            "ref_count": ref_count,
            "fav_count": fav_count,
            "total_users": total_users,
            "premium_users": premium_users,
            "total_matches": total_matches,
        },
        "bonus": bonus,
        "ref_link": f"https://t.me/{BOT_USERNAME}?start=ref_{uid}",
    }

@app.get("/api/matches")
async def api_matches(user: dict = Depends(get_current_user)):
    rows = db_fetchall("SELECT id, name, country, league, match_time FROM matches ORDER BY country, league, match_time")
    groups = {}
    for r in rows:
        key = f"{r[2]}|{r[3]}"
        groups.setdefault(key, []).append({
            "id": r[0], "name": r[1], "country": r[2], "league": r[3], "match_time": r[4] or ""
        })

    result = []
    for key, matches in sorted(groups.items(), key=lambda x: (
        LEAGUE_PRIORITIES.get(x[0].split("|")[1], 99),
        x[0]
    )):
        country, league = key.split("|", 1)
        result.append({
            "country": country,
            "league": league,
            "flag": get_flag(country),
            "count": len(matches),
            "matches": matches,
        })
    return {"groups": result}

@app.get("/api/matches/{match_id}")
async def api_match_detail(match_id: int, user: dict = Depends(get_current_user)):
    uid = user["id"]
    row = db_fetchone(
        "SELECT id, name, country, league, match_time, text FROM matches WHERE id = ?",
        (match_id,)
    )
    if not row:
        raise HTTPException(404, "Матч не найден")

    allowed = check_match_limit(uid, match_id)
    if not allowed:
        raise HTTPException(403, "⚠️ Лимит матчей исчерпан (3 в день). Купите ⭐ Premium.")

    fav = db_fetchone(
        "SELECT 1 FROM favorites WHERE user_id = ? AND match_id = ?",
        (uid, match_id)
    )
    return {
        "id": row[0],
        "name": row[1],
        "country": row[2],
        "league": row[3],
        "match_time": row[4] or "",
        "text": row[5] or "",
        "flag": get_flag(row[2]),
        "is_favorite": bool(fav),
    }

@app.post("/api/favorites/{match_id}")
async def api_toggle_favorite(match_id: int, user: dict = Depends(get_current_user)):
    uid = user["id"]
    existing = db_fetchone(
        "SELECT 1 FROM favorites WHERE user_id = ? AND match_id = ?",
        (uid, match_id)
    )
    if existing:
        db_execute("DELETE FROM favorites WHERE user_id = ? AND match_id = ?", (uid, match_id))
        return {"is_favorite": False, "message": "💔 Убрано из избранного"}
    else:
        now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
        db_execute(
            "INSERT OR IGNORE INTO favorites (user_id, match_id, added_at) VALUES (?, ?, ?)",
            (uid, match_id, now)
        )
        return {"is_favorite": True, "message": "⭐ Добавлено в избранное!"}

@app.get("/api/favorites")
async def api_favorites(user: dict = Depends(get_current_user)):
    uid = user["id"]
    rows = db_fetchall(
        "SELECT m.id, m.name, m.country, m.match_time FROM matches m "
        "INNER JOIN favorites f ON m.id = f.match_id WHERE f.user_id = ?",
        (uid,)
    )
    return {
        "favorites": [
            {"id": r[0], "name": r[1], "country": r[2], "match_time": r[3] or "", "flag": get_flag(r[2])}
            for r in rows
        ]
    }

@app.get("/api/express")
async def api_express(user: dict = Depends(get_current_user), page: int = 0):
    uid = user["id"]
    db_user = get_or_create_user(uid)
    is_premium = db_user["subscription"] == "Premium"

    picks = []
    if is_premium:
        rows = db_fetchall(
            "SELECT name, country, league, prediction, coef FROM express_picks ORDER BY id"
        )
        total_coef = 1.0
        for r in rows:
            try:
                total_coef *= float(r[4])
            except (ValueError, TypeError):
                pass
            picks.append({
                "name": r[0], "country": r[1] or "", "league": r[2] or "",
                "prediction": r[3] or "", "coef": r[4] or "",
                "flag": get_flag(r[1] or "")
            })
        total_coef = round(total_coef, 2)
    else:
        total_coef = None

    stats = calculate_express_stats()

    results_rows = db_fetchall(
        "SELECT id, type, coefs, coef, result, created_at FROM express_results "
        "WHERE result IN ('win', 'lose') ORDER BY id DESC"
    )
    per_page = 7
    total_pages = max(1, (len(results_rows) + per_page - 1) // per_page) if results_rows else 1
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    page_results = [
        {
            "id": r[0], "type": r[1], "coefs": r[2], "coef": r[3],
            "result": r[4], "created_at": r[5][:10] if r[5] else "—",
            "type_label": "🎲 Двойник" if r[1] == "double" else "🎲 Тройник",
            "result_emoji": "✅" if r[4] == "win" else "❌"
        }
        for r in results_rows[start:start + per_page]
    ]

    return {
        "is_premium": is_premium,
        "picks": picks,
        "total_coef": total_coef,
        "stats": stats,
        "results": page_results,
        "page": page,
        "total_pages": total_pages,
    }

@app.post("/api/premium/create")
async def api_create_payment(user: dict = Depends(get_current_user)):
    uid = user["id"]
    result = await create_yookassa_payment(uid, PREMIUM_PRICE)
    if not result:
        raise HTTPException(500, "Не удалось создать платёж. Попробуйте позже.")
    return result

@app.post("/api/premium/check/{payment_id}")
async def api_check_payment(payment_id: str, user: dict = Depends(get_current_user)):
    status = await check_yookassa_payment(payment_id)
    if status == "succeeded":
        row = db_fetchone("SELECT user_id, status FROM pending_payments WHERE payment_id = ?", (payment_id,))
        if row and row[1] != "succeeded":
            end_date = activate_premium(row[0], 30)
            db_execute("UPDATE pending_payments SET status = 'succeeded' WHERE payment_id = ?", (payment_id,))
            return {"status": "succeeded", "end_date": end_date, "message": "🎉 Оплата прошла успешно! Premium активирован."}
        return {"status": "succeeded", "message": "Уже активировано"}
    elif status in ("pending", "waiting_for_capture"):
        return {"status": "pending", "message": "⏳ Платёж ещё обрабатывается."}
    elif status == "canceled":
        return {"status": "canceled", "message": "❌ Платёж отменён."}
    return {"status": "error", "message": "⚠️ Не удалось проверить статус."}

@app.post("/api/support")
async def api_support(msg: SupportMessage, user: dict = Depends(get_current_user)):
    uid = user["id"]
    user_name = user.get("first_name", "Без имени")
    cat = msg.category
    if cat not in SUPPORT_CATEGORIES:
        raise HTTPException(400, "Неизвестная категория")
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")

    thread = db_fetchone("SELECT thread_id FROM support_threads WHERE thread_id = ?", (uid,))
    if not thread:
        db_execute(
            "INSERT INTO support_threads (thread_id, user_id, user_name, category, created_at) VALUES (?, ?, ?, ?, ?)",
            (uid, uid, user_name, cat, now)
        )
    db_execute(
        "INSERT INTO support_messages (thread_id, sender, text, created_at) VALUES (?, 'user', ?, ?)",
        (uid, msg.text, now)
    )
    # Уведомление админу через бота (если токен есть) — упрощённо логируем
    logger.info("Support message from %s [%s]: %s", uid, cat, msg.text[:100])
    return {"ok": True, "message": "✅ Ваше сообщение отправлено Администрации!"}

# ====================== ADMIN ENDPOINTS ======================

def require_admin(user: dict = Depends(get_current_user)):
    if str(user["id"]) not in admin_users:
        raise HTTPException(403, "Нет доступа")
    return user

@app.get("/api/admin/stats")
async def admin_stats(user: dict = Depends(require_admin)):
    total_users = db_fetchone("SELECT COUNT(*) FROM users")[0]
    premium = db_fetchone("SELECT COUNT(*) FROM users WHERE subscription = 'Premium'")[0]
    matches = db_fetchone("SELECT COUNT(*) FROM matches")[0]
    return {"total_users": total_users, "premium_users": premium, "matches": matches}

@app.get("/api/admin/matches")
async def admin_matches(user: dict = Depends(require_admin)):
    rows = db_fetchall("SELECT id, name, country, league, match_time, text FROM matches ORDER BY id DESC")
    return {
        "matches": [
            {
                "id": r[0], "name": r[1], "country": r[2], "league": r[3],
                "match_time": r[4] or "", "has_text": bool(r[5]),
                "flag": get_flag(r[2])
            }
            for r in rows
        ]
    }

@app.post("/api/admin/matches")
async def admin_add_match(data: dict, user: dict = Depends(require_admin)):
    name = data.get("name", "").strip()
    country = data.get("country", "").strip()
    league = data.get("league", "").strip()
    match_time = data.get("match_time", "").strip()
    text = data.get("text", "")
    if not name or not country or not league:
        raise HTTPException(400, "name, country, league required")
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    db_execute(
        "INSERT INTO matches (name, country, league, match_time, text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, country, league, match_time, text, now)
    )
    return {"ok": True, "message": f"✅ Матч добавлен: {name}"}

@app.put("/api/admin/matches/{match_id}/text")
async def admin_update_text(match_id: int, data: MatchTextUpdate, user: dict = Depends(require_admin)):
    db_execute("UPDATE matches SET text = ? WHERE id = ?", (data.text, match_id))
    return {"ok": True, "message": "✅ Текст сохранён"}

@app.delete("/api/admin/matches/{match_id}")
async def admin_delete_match(match_id: int, user: dict = Depends(require_admin)):
    db_execute("DELETE FROM matches WHERE id = ?", (match_id,))
    db_execute("DELETE FROM viewed_matches WHERE match_id = ?", (match_id,))
    db_execute("DELETE FROM favorites WHERE match_id = ?", (match_id,))
    return {"ok": True, "message": "✅ Матч удалён"}

@app.delete("/api/admin/matches")
async def admin_delete_all_matches(user: dict = Depends(require_admin)):
    db_execute("DELETE FROM matches")
    db_execute("DELETE FROM viewed_matches")
    db_execute("DELETE FROM favorites")
    return {"ok": True, "message": "✅ Все матчи удалены"}

@app.get("/api/admin/express")
async def admin_express(user: dict = Depends(require_admin)):
    picks = db_fetchall("SELECT id, name, country, league, prediction, coef FROM express_picks ORDER BY id")
    stats = calculate_express_stats()
    return {
        "picks": [
            {"id": p[0], "name": p[1], "country": p[2], "league": p[3],
             "prediction": p[4], "coef": p[5], "flag": get_flag(p[2] or "")}
            for p in picks
        ],
        "stats": stats
    }

@app.post("/api/admin/express")
async def admin_add_express(data: ExpressPick, user: dict = Depends(require_admin)):
    row = db_fetchone("SELECT name, country, league FROM matches WHERE id = ?", (data.match_id,))
    if not row:
        raise HTTPException(404, "Матч не найден")
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    db_execute(
        "INSERT INTO express_picks (name, country, league, prediction, coef, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (row[0], row[1] or "", row[2] or "", data.prediction, data.coef, now)
    )
    return {"ok": True, "message": f"✅ Добавлено в экспресс: {row[0]}"}

@app.delete("/api/admin/express")
async def admin_clear_express(user: dict = Depends(require_admin)):
    db_execute("DELETE FROM express_picks")
    return {"ok": True, "message": "✅ Экспресс очищен"}

@app.post("/api/admin/results")
async def admin_add_result(data: ResultCreate, user: dict = Depends(require_admin)):
    if data.type not in ("double", "triple") or data.result not in ("win", "lose"):
        raise HTTPException(400, "Invalid type/result")
    total = 1.0
    for c in data.coefs:
        total *= c
    total = round(total, 2)
    coefs_str = ", ".join(f"{c:.2f}" for c in data.coefs)
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    db_execute(
        "INSERT INTO express_results (type, coefs, coef, result, created_at) VALUES (?, ?, ?, ?, ?)",
        (data.type, coefs_str, total, data.result, now)
    )
    return {"ok": True, "message": "✅ Результат сохранён"}

@app.post("/api/admin/premium/{user_id}")
async def admin_give_premium(user_id: int, days: int = 30, user: dict = Depends(require_admin)):
    end = activate_premium(user_id, days)
    return {"ok": True, "end_date": end, "message": f"✅ Premium выдан на {days} дней"}

@app.delete("/api/admin/premium/{user_id}")
async def admin_remove_premium(user_id: int, user: dict = Depends(require_admin)):
    update_user_subscription(user_id, "Free", None)
    return {"ok": True, "message": "✅ Premium снят"}

@app.post("/api/admin/premium/all")
async def admin_give_all_premium(data: dict, user: dict = Depends(require_admin)):
    days = data.get("days", 30)
    if days == "forever":
        end_date = "9999-12-31"
        days_text = "навсегда"
    else:
        days = int(days)
        end_date = (datetime.now(tz) + timedelta(days=days)).strftime("%Y-%m-%d")
        days_text = f"{days} дней"
    rows = db_fetchall("SELECT user_id FROM users WHERE subscription = 'Free'")
    count = 0
    for row in rows:
        update_user_subscription(row[0], "Premium", end_date)
        count += 1
    return {"ok": True, "message": f"✅ Premium выдан {count} Free-пользователям ({days_text})"}

@app.delete("/api/admin/premium/all")
async def admin_remove_all_premium(user: dict = Depends(require_admin)):
    rows = db_fetchall("SELECT user_id FROM users WHERE subscription = 'Premium'")
    for row in rows:
        update_user_subscription(row[0], "Free", None)
    return {"ok": True, "message": f"✅ Premium снят с {len(rows)} пользователей"}

# --- Support admin ---
@app.get("/api/admin/support")
async def admin_support_list(user: dict = Depends(require_admin)):
    rows = db_fetchall(
        "SELECT thread_id, user_name, category, created_at FROM support_threads ORDER BY created_at DESC"
    )
    return {
        "threads": [
            {
                "thread_id": r[0], "user_name": r[1],
                "category": r[2], "category_label": SUPPORT_CATEGORIES.get(r[2], "❓"),
                "created_at": r[3] or ""
            }
            for r in rows
        ]
    }

@app.get("/api/admin/support/{thread_id}")
async def admin_support_thread(thread_id: int, user: dict = Depends(require_admin)):
    msgs = db_fetchall(
        "SELECT sender, text, created_at FROM support_messages WHERE thread_id = ? ORDER BY id",
        (thread_id,)
    )
    return {
        "messages": [
            {"sender": m[0], "text": m[1], "created_at": m[2] or ""}
            for m in msgs
        ]
    }

@app.post("/api/admin/support/{thread_id}/reply")
async def admin_support_reply(thread_id: int, data: dict, user: dict = Depends(require_admin)):
    text = data.get("text", "").strip()
    if not text:
        raise HTTPException(400, "Пустой текст")
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    db_execute(
        "INSERT INTO support_messages (thread_id, sender, text, created_at) VALUES (?, 'admin', ?, ?)",
        (thread_id, text, now)
    )
    return {"ok": True, "message": "✅ Ответ сохранён"}

@app.delete("/api/admin/support/{thread_id}")
async def admin_clear_thread(thread_id: int, user: dict = Depends(require_admin)):
    db_execute("DELETE FROM support_messages WHERE thread_id = ?", (thread_id,))
    db_execute("DELETE FROM support_threads WHERE thread_id = ?", (thread_id,))
    return {"ok": True, "message": "✅ Переписка очищена"}

@app.delete("/api/admin/support")
async def admin_clear_all_support(user: dict = Depends(require_admin)):
    db_execute("DELETE FROM support_messages")
    db_execute("DELETE FROM support_threads")
    return {"ok": True, "message": "✅ Все переписки очищены"}

# --- Results ---
@app.get("/api/admin/results")
async def admin_results_list(user: dict = Depends(require_admin)):
    rows = db_fetchall(
        "SELECT id, type, coefs, coef, result, created_at FROM express_results ORDER BY id DESC"
    )
    return {
        "results": [
            {
                "id": r[0], "type": r[1], "coefs": r[2], "coef": r[3],
                "result": r[4], "created_at": r[5] or "",
                "type_label": "Двойник" if r[1] == "double" else "Тройник",
                "result_emoji": "✅" if r[4] == "win" else "❌"
            }
            for r in rows
        ]
    }

@app.delete("/api/admin/results/{result_id}")
async def admin_delete_result(result_id: int, user: dict = Depends(require_admin)):
    db_execute("DELETE FROM express_results WHERE id = ?", (result_id,))
    return {"ok": True, "message": "✅ Результат удалён"}

@app.delete("/api/admin/results")
async def admin_delete_all_results(user: dict = Depends(require_admin)):
    db_execute("DELETE FROM express_results")
    return {"ok": True, "message": "✅ Все результаты удалены"}

# --- Admin logs ---
@app.get("/api/admin/logs")
async def admin_logs_api(user: dict = Depends(require_admin)):
    rows = db_fetchall(
        "SELECT admin_id, action, details, created_at FROM admin_log ORDER BY id DESC LIMIT 30"
    )
    return {
        "logs": [
            {"admin_id": r[0], "action": r[1], "details": r[2] or "", "created_at": r[3] or ""}
            for r in rows
        ]
    }

def log_admin(admin_id: int, action: str, details: str = ""):
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    db_execute(
        "INSERT INTO admin_log (admin_id, action, details, created_at) VALUES (?, ?, ?, ?)",
        (admin_id, action, details, now)
    )

# ====================== ПАРСЕР (BSD API) ======================

async def fetch_leagues_from_api() -> dict:
    if not BSD_API_KEY or not BSD_API_KEY.strip():
        return {}
    headers = {"Authorization": f"Token {BSD_API_KEY.strip()}"}
    base_url = "https://sports.bzzoiro.com/api/v2/leagues/"
    leagues = {}
    offset = 0
    limit = 200
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(base_url, headers=headers, params={"limit": limit, "offset": offset}) as resp:
                    if resp.status != 200:
                        break
                    data = await resp.json()
                    for lg in data.get("results", []):
                        leagues[lg["id"]] = {"name": lg.get("name", ""), "country": lg.get("country", "")}
                    if data.get("next"):
                        offset += limit
                    else:
                        break
            except Exception as e:
                logger.exception("[BSD leagues] %s", e)
                break
    return leagues

async def fetch_matches_from_api(date_str: str) -> list:
    if not BSD_API_KEY or not BSD_API_KEY.strip():
        logger.error("[BSD] BSD_API_KEY пуст")
        return []
    headers = {"Authorization": f"Token {BSD_API_KEY.strip()}"}
    base_url = "https://sports.bzzoiro.com/api/v2/events/"
    all_matches = []
    offset = 0
    limit = 200
    async with aiohttp.ClientSession() as session:
        while True:
            params = {"date_from": date_str, "date_to": date_str, "limit": limit, "offset": offset}
            try:
                async with session.get(base_url, headers=headers, params=params) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        all_matches.extend(data.get("results", []))
                        if data.get("next"):
                            offset += limit
                        else:
                            break
                    elif resp.status == 429:
                        await asyncio.sleep(60)
                        continue
                    else:
                        break
            except Exception as e:
                logger.exception("[BSD] %s", e)
                break
    return all_matches

def get_league_priority(league_name: str) -> int:
    return LEAGUE_PRIORITIES.get(league_name, 99)

@app.get("/api/admin/parser/dates")
async def parser_dates(user: dict = Depends(require_admin)):
    today = datetime.now(tz)
    return {
        "dates": [
            {"label": f"📅 Сегодня ({today.strftime('%Y-%m-%d')})", "value": today.strftime("%Y-%m-%d")},
            {"label": f"📅 Завтра ({(today + timedelta(days=1)).strftime('%Y-%m-%d')})", "value": (today + timedelta(days=1)).strftime("%Y-%m-%d")},
            {"label": f"📅 Послезавтра ({(today + timedelta(days=2)).strftime('%Y-%m-%d')})", "value": (today + timedelta(days=2)).strftime("%Y-%m-%d")},
        ],
        "has_api_key": bool(BSD_API_KEY and BSD_API_KEY.strip()),
    }

@app.post("/api/admin/parser/load")
async def parser_load(data: dict, user: dict = Depends(require_admin)):
    date_str = data.get("date", "")
    if not date_str:
        raise HTTPException(400, "Укажите дату")
    if not BSD_API_KEY or not BSD_API_KEY.strip():
        raise HTTPException(400, "BSD_API_KEY не задан в .env")

    matches = await fetch_matches_from_api(date_str)
    if not matches:
        return {"ok": True, "total": 0, "leagues": [], "message": "❌ На эту дату нет матчей"}

    leagues_map = await fetch_leagues_from_api()
    db_execute("DELETE FROM parsed_matches")

    for match in matches:
        home = match.get("home_team", "?")
        away = match.get("away_team", "?")
        league_id = match.get("league_id", 0)
        league_info = leagues_map.get(league_id, {})
        league_name = league_info.get("name", "Unknown")
        country_en = league_info.get("country", "")
        country_en = COUNTRY_NORMALIZE.get(country_en, country_en)
        priority = get_league_priority(league_name)
        match_time = ""
        event_date = match.get("event_date", "")
        if event_date:
            try:
                dt_parsed = datetime.fromisoformat(event_date)
                if dt_parsed.tzinfo is None:
                    dt_parsed = dt_parsed.replace(tzinfo=ZoneInfo("UTC"))
                match_time = dt_parsed.astimezone(tz).strftime("%H:%M")
            except Exception:
                pass
        name = f"{home} — {away}"
        db_execute(
            "INSERT INTO parsed_matches (name, country, country_en, league, league_id, match_time, match_date, priority) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, country_en, country_en, league_name, league_id, match_time, date_str, priority)
        )

    rows = db_fetchall(
        "SELECT DISTINCT league, priority, league_id, country FROM parsed_matches ORDER BY priority ASC, league ASC"
    )
    leagues = []
    for league_name, priority, lid, country in rows:
        cnt = db_fetchone("SELECT COUNT(*) FROM parsed_matches WHERE league_id = ?", (lid,))[0]
        leagues.append({
            "league_id": lid, "league": league_name, "country": country,
            "flag": get_flag(country), "priority": priority, "count": cnt
        })

    total = db_fetchone("SELECT COUNT(*) FROM parsed_matches")[0]
    log_admin(user["id"], "parser_load", f"{date_str}: {total} матчей")
    return {"ok": True, "total": total, "leagues": leagues, "message": f"📋 Найдено {total} матчей"}

@app.get("/api/admin/parser/matches")
async def parser_matches_list(user: dict = Depends(require_admin), league_id: int = None, page: int = 0):
    per_page = 15
    if league_id is not None:
        total = db_fetchone("SELECT COUNT(*) FROM parsed_matches WHERE league_id = ?", (league_id,))[0]
        rows = db_fetchall(
            "SELECT id, name, match_time, country, league, league_id FROM parsed_matches "
            "WHERE league_id = ? ORDER BY match_time ASC LIMIT ? OFFSET ?",
            (league_id, per_page, page * per_page)
        )
    else:
        total = db_fetchone("SELECT COUNT(*) FROM parsed_matches")[0]
        rows = db_fetchall(
            "SELECT id, name, match_time, country, league, league_id FROM parsed_matches "
            "ORDER BY priority ASC, match_time ASC LIMIT ? OFFSET ?",
            (per_page, page * per_page)
        )
    total_pages = max(1, (total + per_page - 1) // per_page)
    return {
        "matches": [
            {
                "id": r[0], "name": r[1], "match_time": r[2] or "",
                "country": r[3], "league": r[4], "league_id": r[5],
                "flag": get_flag(r[3] or "")
            }
            for r in rows
        ],
        "total": total, "page": page, "total_pages": total_pages
    }

@app.post("/api/admin/parser/add")
async def parser_add(data: dict, user: dict = Depends(require_admin)):
    mode = data.get("mode", "all")
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    count = 0

    if mode == "single":
        parsed_id = data.get("parsed_id")
        row = db_fetchone(
            "SELECT name, country, league, match_time FROM parsed_matches WHERE id = ?", (parsed_id,)
        )
        if not row:
            raise HTTPException(404, "Матч не найден")
        existing = db_fetchone("SELECT id FROM matches WHERE name = ? AND league = ?", (row[0], row[2]))
        if existing:
            return {"ok": False, "message": "⚠️ Этот матч уже добавлен"}
        db_execute(
            "INSERT INTO matches (name, country, league, match_time, text, created_at) VALUES (?, ?, ?, ?, '', ?)",
            (row[0], row[1], row[2], row[3], now)
        )
        count = 1
        log_admin(user["id"], "parser_add_match", row[0])

    elif mode == "league":
        league_id = data.get("league_id")
        rows = db_fetchall(
            "SELECT name, country, league, match_time FROM parsed_matches WHERE league_id = ?",
            (league_id,)
        )
        for r in rows:
            existing = db_fetchone("SELECT id FROM matches WHERE name = ? AND league = ?", (r[0], r[2]))
            if not existing:
                db_execute(
                    "INSERT INTO matches (name, country, league, match_time, text, created_at) VALUES (?, ?, ?, ?, '', ?)",
                    (r[0], r[1], r[2], r[3], now)
                )
                count += 1
        league_row = db_fetchone("SELECT league FROM parsed_matches WHERE league_id = ?", (league_id,))
        log_admin(user["id"], "parser_add_league", f"{league_row[0] if league_row else ''} ({count})")

    else:
        limit = int(data.get("limit", 80))
        rows = db_fetchall(
            "SELECT name, country, league, match_time FROM parsed_matches ORDER BY priority ASC LIMIT ?",
            (limit,)
        )
        for r in rows:
            existing = db_fetchone("SELECT id FROM matches WHERE name = ? AND league = ?", (r[0], r[2]))
            if not existing:
                db_execute(
                    "INSERT INTO matches (name, country, league, match_time, text, created_at) VALUES (?, ?, ?, ?, '', ?)",
                    (r[0], r[1], r[2], r[3], now)
                )
                count += 1
        log_admin(user["id"], "parser_add_all", f"{count} матчей")

    return {"ok": True, "count": count, "message": f"✅ Добавлено {count} матчей"}

@app.post("/api/admin/matches/manual")
async def admin_manual_add(data: dict, user: dict = Depends(require_admin)):
    name = data.get("name", "").strip()
    country = data.get("country", "").strip()
    league = data.get("league", "").strip()
    match_time = data.get("match_time", "").strip()
    text = data.get("text", "")
    if not name or not country or not league:
        raise HTTPException(400, "Нужны: название, страна, лига")
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    db_execute(
        "INSERT INTO matches (name, country, league, match_time, text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (name, country, league, match_time, text, now)
    )
    log_admin(user["id"], "add_match", name)
    return {"ok": True, "message": f"✅ Матч добавлен: {name}"}

@app.get("/api/admin/matches/no-text")
async def admin_matches_no_text(user: dict = Depends(require_admin)):
    rows = db_fetchall(
        "SELECT id, name, country, league, match_time FROM matches WHERE text IS NULL OR text = '' ORDER BY id DESC"
    )
    return {
        "matches": [
            {
                "id": r[0], "name": r[1], "country": r[2], "league": r[3],
                "match_time": r[4] or "", "flag": get_flag(r[2])
            }
            for r in rows
        ]
    }

# ====================== SERVE FRONTEND ======================

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Ищем frontend рядом с backend или в /app/frontend (Bothost)
_CANDIDATES = [
    os.path.join(_BASE_DIR, "..", "frontend"),
    os.path.join(_BASE_DIR, "frontend"),
    "/app/frontend",
    "/app/backend/../frontend",
]
FRONTEND_DIR = None
for _c in _CANDIDATES:
    _p = os.path.abspath(_c)
    if os.path.isdir(_p) and os.path.isfile(os.path.join(_p, "index.html")):
        FRONTEND_DIR = _p
        break
if FRONTEND_DIR is None:
    FRONTEND_DIR = os.path.abspath(os.path.join(_BASE_DIR, "..", "frontend"))
    logger.warning("Frontend dir not found, using fallback: %s", FRONTEND_DIR)
else:
    logger.info("Frontend dir: %s", FRONTEND_DIR)

@app.get("/")
async def serve_index():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if not os.path.isfile(index_path):
        return JSONResponse(
            {"error": "index.html not found", "frontend_dir": FRONTEND_DIR, "hint": "Проверьте структуру: /app/frontend/index.html"},
            status_code=404,
        )
    return FileResponse(index_path)

@app.get("/index.html")
async def serve_index_html():
    return await serve_index()

if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "3000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
