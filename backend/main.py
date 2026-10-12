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
        priority    INTEGER DEFAULT 99,
        logo_home   TEXT DEFAULT '',
        logo_away   TEXT DEFAULT ''
    )""")

    c.execute("""CREATE TABLE IF NOT EXISTS news (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        title      TEXT DEFAULT '',
        text       TEXT NOT NULL,
        created_at TEXT
    )""")

    # migrations
    try:
        c.execute("ALTER TABLE matches ADD COLUMN logo_home TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE matches ADD COLUMN logo_away TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE parsed_matches ADD COLUMN logo_home TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE parsed_matches ADD COLUMN logo_away TEXT DEFAULT ''")
    except Exception:
        pass

    # express_picks: время, логотипы, match_id
    for col, typ in [
        ("match_time", "TEXT DEFAULT ''"),
        ("logo_home", "TEXT DEFAULT ''"),
        ("logo_away", "TEXT DEFAULT ''"),
        ("match_id", "INTEGER DEFAULT NULL"),
    ]:
        try:
            c.execute(f"ALTER TABLE express_picks ADD COLUMN {col} {typ}")
        except Exception:
            pass

    # matches / parsed_matches: BSD ids + stats pack
    for table in ("matches", "parsed_matches"):
        for col, typ in [
            ("event_id", "INTEGER DEFAULT NULL"),
            ("home_team_id", "INTEGER DEFAULT NULL"),
            ("away_team_id", "INTEGER DEFAULT NULL"),
            ("stats_json", "TEXT DEFAULT ''"),
        ]:
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            except Exception:
                pass

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
    if days <= 0 or days >= 36500:
        new_end = "9999-12-31"
        update_user_subscription(user_id, "Premium", new_end)
        return new_end
    user = get_or_create_user(user_id)
    if user["subscription"] == "Premium" and user["end_date"]:
        try:
            cur_end = datetime.strptime(user["end_date"], "%Y-%m-%d")
            if cur_end.date() > datetime.now(tz).date() and user["end_date"] != "9999-12-31":
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

    # Test Drive только один раз: для новых пользователей, у которых ещё не было end_date
    # и статус Free, и notified_expire == 0 (флаг «бонус ещё не выдавали»)
    bonus = None
    is_new = (
        db_user.get("reg_date") == datetime.now(tz).strftime("%Y-%m-%d")
        and db_user.get("subscription") == "Free"
        and not db_user.get("end_date")
        and int(db_user.get("notified_expire") or 0) == 0
    )
    if is_new:
        end_date = activate_premium(uid, 30)
        # помечаем, что test drive уже выдан (чтобы снятие Premium не реактивировало его)
        db_execute("UPDATE users SET notified_expire = 1 WHERE user_id = ?", (uid,))
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
    rows = db_fetchall("SELECT id, name, country, league, match_time, COALESCE(logo_home,''), COALESCE(logo_away,'') FROM matches ORDER BY country, league, match_time")
    groups = {}
    for r in rows:
        key = f"{r[2]}|{r[3]}"
        groups.setdefault(key, []).append({
            "id": r[0], "name": r[1], "country": r[2], "league": r[3], "match_time": r[4] or "",
            "logo_home": r[5] or "", "logo_away": r[6] or ""
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
async def api_match_detail(match_id: int, user: dict = Depends(get_current_user), refresh: int = 0):
    uid = user["id"]
    # Try full select; fallback if columns missing
    try:
        row = db_fetchone(
            "SELECT id, name, country, league, match_time, text, COALESCE(logo_home,''), COALESCE(logo_away,''), "
            "event_id, home_team_id, away_team_id, COALESCE(stats_json,'') FROM matches WHERE id = ?",
            (match_id,)
        )
    except Exception:
        row = db_fetchone(
            "SELECT id, name, country, league, match_time, text, COALESCE(logo_home,''), COALESCE(logo_away,'') "
            "FROM matches WHERE id = ?",
            (match_id,)
        )
        if row:
            row = tuple(list(row) + [None, None, None, ""])

    if not row:
        raise HTTPException(404, "Матч не найден")

    allowed = check_match_limit(uid, match_id)
    if not allowed:
        raise HTTPException(403, "⚠️ Лимит матчей исчерпан (3 в день). Купите ⭐ Premium.")

    fav = db_fetchone(
        "SELECT 1 FROM favorites WHERE user_id = ? AND match_id = ?",
        (uid, match_id)
    )

    stats = {}
    stats_raw = row[11] if len(row) > 11 else ""
    if stats_raw and isinstance(stats_raw, str) and stats_raw.strip():
        try:
            stats = json.loads(stats_raw)
        except Exception:
            stats = {}
    if not isinstance(stats, dict):
        stats = {}

    def _has_useful(st: dict) -> bool:
        if not st:
            return False
        h2h = st.get("h2h") or {}
        if h2h.get("total") or (h2h.get("recent") or []):
            return True
        form = st.get("form") or {}
        if (form.get("home") or {}).get("form") or (form.get("away") or {}).get("form"):
            return True
        last5 = st.get("last5") or {}
        if last5.get("home") or last5.get("away"):
            return True
        inj = st.get("injuries") or {}
        if inj.get("home") or inj.get("away"):
            return True
        coaches = st.get("coaches") or {}
        if (coaches.get("home") or {}).get("name") or (coaches.get("away") or {}).get("name"):
            return True
        lineups = st.get("lineups") or {}
        if (lineups.get("home") or {}).get("players") or (lineups.get("away") or {}).get("players"):
            return True
        standings = st.get("standings") or {}
        if standings.get("table"):
            return True
        xg = st.get("xg") or {}
        if (xg.get("home") or {}).get("avg") is not None or (xg.get("away") or {}).get("avg") is not None:
            return True
        halves = st.get("halves") or {}
        for side in ("home", "away"):
            h = halves.get(side) or {}
            if (h.get("first_half") or {}).get("matches") or (h.get("second_half") or {}).get("matches"):
                return True
        gs = st.get("goal_segments") or {}
        for side in ("home", "away"):
            g = gs.get(side) or {}
            if g.get("matches"):
                return True
            scored = g.get("scored") or {}
            if any(v for v in scored.values()):
                return True
        fc = st.get("forecast") or {}
        if (fc.get("singles") or fc.get("combos")):
            return True
        return False

    event_id = row[8] if len(row) > 8 else None
    home_tid = row[9] if len(row) > 9 else None
    away_tid = row[10] if len(row) > 10 else None
    match_name = row[1] or ""
    match_time = row[4] or ""

    need_enrich = (not _has_useful(stats)) or bool(refresh)
    if need_enrich and BSD_API_KEY and BSD_API_KEY.strip():
        try:
            async with aiohttp.ClientSession() as session:
                if refresh:
                    event_id = None  # force re-resolve
                if not event_id:
                    eid, hid, aid = await resolve_event_id(session, match_name, match_time)
                    logger.info("[enrich] resolve %s -> event=%s home=%s away=%s", match_name, eid, hid, aid)
                    if eid:
                        event_id, home_tid, away_tid = eid, hid or home_tid, aid or away_tid
                        try:
                            db_execute(
                                "UPDATE matches SET event_id = ?, home_team_id = ?, away_team_id = ? WHERE id = ?",
                                (event_id, home_tid, away_tid, match_id)
                            )
                        except Exception as e:
                            logger.warning("[enrich] save ids: %s", e)
                if event_id:
                    stats = await fetch_match_enrichment(
                        session, int(event_id), home_tid, away_tid, None
                    )
                    logger.info(
                        "[enrich] pack for %s: h2h=%s form=%s last5=%s coaches=%s inj=%s",
                        match_name,
                        bool((stats.get("h2h") or {}).get("total") or (stats.get("h2h") or {}).get("recent")),
                        bool((stats.get("form") or {}).get("home")),
                        bool((stats.get("last5") or {}).get("home")),
                        bool((stats.get("coaches") or {}).get("home", {}).get("name")),
                        bool((stats.get("injuries") or {}).get("home")),
                    )
                    try:
                        db_execute(
                            "UPDATE matches SET stats_json = ?, event_id = COALESCE(?, event_id) WHERE id = ?",
                            (json.dumps(stats, ensure_ascii=False), event_id, match_id)
                        )
                    except Exception as e:
                        logger.warning("[enrich] save stats: %s", e)
                else:
                    logger.warning("[enrich] could not resolve event for: %s", match_name)
        except Exception as e:
            logger.exception("[enrich] failed: %s", e)

    return {
        "id": row[0],
        "name": row[1],
        "country": row[2],
        "league": row[3],
        "match_time": row[4] or "",
        "text": row[5] or "",
        "logo_home": row[6] or "",
        "logo_away": row[7] or "",
        "flag": get_flag(row[2]),
        "is_favorite": bool(fav),
        "stats": stats or {},
        "event_id": event_id,
        "stats_loaded": _has_useful(stats or {}),
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
        "SELECT m.id, m.name, m.country, m.match_time, COALESCE(m.logo_home,''), COALESCE(m.logo_away,'') "
        "FROM matches m INNER JOIN favorites f ON m.id = f.match_id WHERE f.user_id = ?",
        (uid,)
    )
    return {
        "favorites": [
            {
                "id": r[0], "name": r[1], "country": r[2], "match_time": r[3] or "",
                "flag": get_flag(r[2]), "logo_home": r[4] or "", "logo_away": r[5] or "",
            }
            for r in rows
        ]
    }

def _express_pick_from_row(r, has_extra: bool):
    """Build pick dict; r indices depend on query columns."""
    name = r[0] or ""
    country = r[1] or ""
    league = r[2] or ""
    prediction = r[3] or ""
    coef = r[4] or ""
    mt, lh, la = "", "", ""
    if has_extra and len(r) >= 8:
        mt = r[5] or ""
        lh = r[6] or ""
        la = r[7] or ""
    # fallback from matches table
    if (not lh or not la or not mt) and name:
        try:
            mrow = db_fetchone(
                "SELECT COALESCE(match_time,''), COALESCE(logo_home,''), COALESCE(logo_away,'') "
                "FROM matches WHERE name = ? LIMIT 1",
                (name,),
            )
            if mrow:
                mt = mt or (mrow[0] or "")
                lh = lh or (mrow[1] or "")
                la = la or (mrow[2] or "")
        except Exception:
            pass
    return {
        "name": name,
        "country": country,
        "league": league,
        "prediction": prediction,
        "coef": coef,
        "flag": get_flag(country),
        "match_time": mt,
        "logo_home": lh,
        "logo_away": la,
    }


@app.get("/api/express")
async def api_express(user: dict = Depends(get_current_user), page: int = 0):
    uid = user["id"]
    db_user = get_or_create_user(uid)
    is_premium = db_user["subscription"] == "Premium"

    picks = []
    total_coef = None
    if is_premium:
        rows = None
        has_extra = True
        try:
            rows = db_fetchall(
                "SELECT name, country, league, prediction, coef, "
                "COALESCE(match_time,''), COALESCE(logo_home,''), COALESCE(logo_away,''), match_id "
                "FROM express_picks ORDER BY id"
            )
        except Exception:
            has_extra = False
            rows = db_fetchall(
                "SELECT name, country, league, prediction, coef FROM express_picks ORDER BY id"
            )
        total_coef = 1.0
        for r in rows:
            try:
                total_coef *= float(r[4])
            except (ValueError, TypeError):
                pass
            picks.append(_express_pick_from_row(r, has_extra))
        total_coef = round(total_coef, 2)

    stats = calculate_express_stats()

    results_rows = db_fetchall(
        "SELECT id, type, coefs, coef, result, created_at FROM express_results "
        "WHERE result IN ('win', 'lose') ORDER BY id DESC"
    )
    per_page = 7
    total_pages = max(1, (len(results_rows) + per_page - 1) // per_page) if results_rows else 1
    page = max(0, min(page, total_pages - 1))
    start = page * per_page
    page_results = []
    for r in results_rows[start:start + per_page]:
        created = r[5]
        if created and len(str(created)) >= 10:
            created = str(created)[:10]
        else:
            created = "—"
        page_results.append({
            "id": r[0],
            "type": r[1],
            "coefs": r[2],
            "coef": r[3],
            "result": r[4],
            "created_at": created,
            "type_label": "🎲 Двойник" if r[1] == "double" else "🎲 Тройник",
            "result_emoji": "✅" if r[4] == "win" else "❌",
        })

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
    rows = db_fetchall(
        "SELECT id, name, country, league, match_time, text, COALESCE(logo_home,''), COALESCE(logo_away,'') "
        "FROM matches ORDER BY id DESC"
    )
    return {
        "matches": [
            {
                "id": r[0], "name": r[1], "country": r[2], "league": r[3],
                "match_time": r[4] or "", "has_text": bool(r[5]),
                "flag": get_flag(r[2]),
                "logo_home": r[6] or "", "logo_away": r[7] or "",
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
    try:
        picks_rows = db_fetchall(
            "SELECT id, name, country, league, prediction, coef, "
            "COALESCE(match_time,''), COALESCE(logo_home,''), COALESCE(logo_away,'') "
            "FROM express_picks ORDER BY id"
        )
        has_extra = True
    except Exception:
        picks_rows = db_fetchall(
            "SELECT id, name, country, league, prediction, coef FROM express_picks ORDER BY id"
        )
        has_extra = False
    picks_out = []
    for p in picks_rows:
        mt = (p[6] if has_extra and len(p) > 6 else "") or ""
        lh = (p[7] if has_extra and len(p) > 7 else "") or ""
        la = (p[8] if has_extra and len(p) > 8 else "") or ""
        if (not mt or not lh or not la) and p[1]:
            try:
                mrow = db_fetchone(
                    "SELECT COALESCE(match_time,''), COALESCE(logo_home,''), COALESCE(logo_away,'') "
                    "FROM matches WHERE name = ? LIMIT 1", (p[1],)
                )
                if mrow:
                    mt = mt or (mrow[0] or "")
                    lh = lh or (mrow[1] or "")
                    la = la or (mrow[2] or "")
            except Exception:
                pass
        picks_out.append({
            "id": p[0], "name": p[1], "country": p[2], "league": p[3],
            "prediction": p[4], "coef": p[5], "flag": get_flag(p[2] or ""),
            "match_time": mt, "logo_home": lh, "logo_away": la,
        })
    stats = calculate_express_stats()
    return {"picks": picks_out, "stats": stats}

@app.post("/api/admin/express")
async def admin_add_express(data: ExpressPick, user: dict = Depends(require_admin)):
    row = db_fetchone(
        "SELECT name, country, league, COALESCE(match_time,''), COALESCE(logo_home,''), COALESCE(logo_away,'') "
        "FROM matches WHERE id = ?", (data.match_id,)
    )
    if not row:
        # try without logo columns
        row = db_fetchone(
            "SELECT name, country, league FROM matches WHERE id = ?", (data.match_id,)
        )
        if not row:
            raise HTTPException(404, "Матч не найден")
        row = (row[0], row[1], row[2], "", "", "")
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    try:
        db_execute(
            "INSERT INTO express_picks (name, country, league, prediction, coef, created_at, match_time, logo_home, logo_away, match_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row[0], row[1] or "", row[2] or "", data.prediction, data.coef, now,
             (row[3] if len(row) > 3 else "") or "",
             (row[4] if len(row) > 4 else "") or "",
             (row[5] if len(row) > 5 else "") or "",
             data.match_id)
        )
    except Exception:
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
        db_execute(
            "UPDATE users SET subscription = 'Free', end_date = NULL, notified_expire = 1 WHERE user_id = ?",
            (row[0],)
        )
    log_admin(user["id"], "remove_all_premium", f"{len(rows)} users")
    return {"ok": True, "message": f"✅ Premium снят с {len(rows)} пользователей"}

@app.post("/api/admin/premium/by-username")
async def admin_premium_by_username(data: dict, user: dict = Depends(require_admin)):
    username = (data.get("username") or "").strip().lstrip("@")
    if not username:
        raise HTTPException(400, "Укажите username или ID")
    row = db_fetchone(
        "SELECT user_id, username FROM users WHERE username = ? COLLATE NOCASE",
        (username,)
    )
    if not row:
        # try as numeric id
        try:
            uid = int(username)
            row = db_fetchone("SELECT user_id, username FROM users WHERE user_id = ?", (uid,))
        except ValueError:
            row = None
    if not row:
        # partial match fallback
        like = f"%{username}%"
        row = db_fetchone(
            "SELECT user_id, username FROM users WHERE username LIKE ? COLLATE NOCASE LIMIT 1",
            (like,)
        )
    if not row:
        raise HTTPException(404, f"Пользователь @{username} не найден")
    uid = row[0]
    uname = row[1] or str(uid)
    days = data.get("days", 30)
    if days == "forever" or days is None or str(days).lower() in ("0", "forever", "none"):
        days = 0
    else:
        try:
            days = int(days)
        except (TypeError, ValueError):
            days = 30
    end = activate_premium(uid, days)
    days_text = "навсегда" if end == "9999-12-31" else f"{days} дн."
    display = f"@{uname}" if row[1] else f"ID {uid}"
    return {"ok": True, "end_date": end, "user_id": uid, "message": f"✅ Premium выдан {display} ({days_text})"}

@app.post("/api/admin/premium/remove-by-username")
async def admin_remove_premium_by_username(data: dict, user: dict = Depends(require_admin)):
    username = (data.get("username") or "").strip().lstrip("@")
    if not username:
        raise HTTPException(400, "Укажите username или ID")
    row = db_fetchone(
        "SELECT user_id, username, subscription FROM users WHERE username = ? COLLATE NOCASE",
        (username,)
    )
    if not row:
        try:
            uid = int(username)
            row = db_fetchone("SELECT user_id, username, subscription FROM users WHERE user_id = ?", (uid,))
        except ValueError:
            row = None
    if not row:
        like = f"%{username}%"
        row = db_fetchone(
            "SELECT user_id, username, subscription FROM users WHERE username LIKE ? COLLATE NOCASE LIMIT 1",
            (like,)
        )
    if not row:
        raise HTTPException(404, f"Пользователь @{username} не найден")
    uid = row[0]
    # Явно снимаем Premium: Free + пустая дата + помечаем, что бонус уже был
    db_execute(
        "UPDATE users SET subscription = 'Free', end_date = NULL, notified_expire = 1 WHERE user_id = ?",
        (uid,)
    )
    # Проверяем, что действительно снято
    check = db_fetchone("SELECT subscription, end_date FROM users WHERE user_id = ?", (uid,))
    if not check or check[0] != "Free":
        raise HTTPException(500, "Не удалось снять Premium — попробуйте ещё раз")
    display = f"@{row[1]}" if row[1] else f"ID {uid}"
    log_admin(user["id"], "remove_premium", f"{display} (было: {row[2]})")
    return {"ok": True, "message": f"✅ Premium снят с {display}", "subscription": "Free", "user_id": uid}


@app.get("/api/admin/users/search")
async def admin_search_users(q: str = "", user: dict = Depends(require_admin)):
    q = (q or "").strip().lstrip("@")
    if not q:
        return {"users": []}
    like = f"%{q}%"
    rows = db_fetchall(
        "SELECT user_id, username, first_name, subscription, end_date FROM users "
        "WHERE username LIKE ? OR CAST(user_id AS TEXT) = ? OR first_name LIKE ? LIMIT 20",
        (like, q, like)
    )
    return {
        "users": [
            {
                "user_id": r[0],
                "username": r[1] or "",
                "first_name": r[2] or "",
                "subscription": r[3] or "Free",
                "end_date": r[4] or "",
            }
            for r in rows
        ]
    }

@app.post("/api/admin/premium/{user_id}")
async def admin_give_premium(user_id: int, days: int = 30, user: dict = Depends(require_admin)):
    # days=0 means forever (activate_premium handles it)
    end = activate_premium(user_id, days)
    days_text = "навсегда" if end == "9999-12-31" else f"{days} дней"
    return {"ok": True, "end_date": end, "message": f"✅ Premium выдан на {days_text}"}

@app.delete("/api/admin/premium/{user_id}")
async def admin_remove_premium(user_id: int, user: dict = Depends(require_admin)):
    db_execute(
        "UPDATE users SET subscription = 'Free', end_date = NULL, notified_expire = 1 WHERE user_id = ?",
        (user_id,)
    )
    check = db_fetchone("SELECT subscription FROM users WHERE user_id = ?", (user_id,))
    if not check or check[0] != "Free":
        raise HTTPException(500, "Не удалось снять Premium")
    log_admin(user["id"], "remove_premium", f"ID {user_id}")
    return {"ok": True, "message": "✅ Premium снят", "subscription": "Free"}

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

def _bsd_headers() -> dict:
    return {"Authorization": f"Token {BSD_API_KEY.strip()}"}


def _player_photo(pid) -> str:
    if not pid:
        return ""
    return f"https://sports.bzzoiro.com/img/player/{pid}/?sor=true&bg=transparent"


def _manager_photo(mid) -> str:
    if not mid:
        return ""
    return f"https://sports.bzzoiro.com/img/manager/{mid}/"


async def _bsd_get(session: aiohttp.ClientSession, path: str, params: dict = None):
    """GET BSD v2 endpoint, return JSON or None."""
    url = path if path.startswith("http") else f"https://sports.bzzoiro.com/api/v2/{path.lstrip('/')}"
    try:
        async with session.get(url, headers=_bsd_headers(), params=params or {}, timeout=aiohttp.ClientTimeout(total=20)) as resp:
            if resp.status == 200:
                return await resp.json()
            if resp.status == 429:
                await asyncio.sleep(2)
                async with session.get(url, headers=_bsd_headers(), params=params or {}, timeout=aiohttp.ClientTimeout(total=20)) as resp2:
                    if resp2.status == 200:
                        return await resp2.json()
            return None
    except Exception as e:
        logger.warning("[BSD get] %s %s", path, e)
        return None


def _extract_coach_ids(event: dict) -> tuple:
    """Return (home_coach_id, away_coach_id) from event detail."""
    if not event or not isinstance(event, dict):
        return None, None
    return event.get("home_coach_id"), event.get("away_coach_id")


def _parse_injuries(squad_data) -> list:
    """Extract injured/doubtful/suspended players from squad response."""
    if not squad_data:
        return []
    players = []
    if isinstance(squad_data, dict):
        players = squad_data.get("players") or []
    elif isinstance(squad_data, list):
        players = squad_data
    out = []
    for p in players:
        if not isinstance(p, dict):
            continue
        status = (p.get("availability") or "").lower()
        if status not in ("injured", "doubtful", "suspended"):
            continue
        pid = p.get("id")
        name = p.get("name") or p.get("short_name") or "?"
        pos = (p.get("position") or p.get("pos") or p.get("role") or "").strip()
        out.append({
            "id": pid,
            "name": name,
            "photo": _player_photo(pid),
            "position": pos,
            "status": status,
        })
    return out[:15]


def _is_defender_pos(pos: str) -> bool:
    if not pos:
        return False
    p = str(pos).upper().strip()
    if p in ("D", "DEF", "DF", "CB", "LB", "RB", "LWB", "RWB", "WB", "SW", "DEFENDER"):
        return True
    if "BACK" in p or "DEF" in p or "ЦЗ" in p or "ЗЩ" in p or "ЗАЩ" in p:
        return True
    return False


def _count_missing_defenders(injuries: list) -> tuple:
    """Return (count, names) of missing defenders (injured/suspended preferred over doubtful)."""
    defs = []
    for p in injuries or []:
        if not isinstance(p, dict):
            continue
        if not _is_defender_pos(p.get("position") or ""):
            continue
        st = (p.get("status") or "").lower()
        # prioritize injured/suspended
        defs.append((0 if st in ("injured", "suspended") else 1, p.get("name") or "?"))
    defs.sort()
    names = [n for _, n in defs]
    return len(names), names


def _safe_float(v, default=None):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_odds_bag(odds_data: dict) -> dict:
    """Normalize BSD odds into flat dict of market_key -> coef."""
    out = {}
    if not odds_data or not isinstance(odds_data, dict):
        return out
    bag = odds_data.get("odds") if isinstance(odds_data.get("odds"), dict) else odds_data
    if not isinstance(bag, dict):
        return out
    mapping = {
        "home_win": ("home_win", "home", "1"),
        "draw": ("draw", "x", "X"),
        "away_win": ("away_win", "away", "2"),
        "over_15": ("over_15_goals", "over_15", "over15"),
        "under_15": ("under_15_goals", "under_15", "under15"),
        "over_25": ("over_25_goals", "over_25", "over25"),
        "under_25": ("under_25_goals", "under_25", "under25"),
        "over_35": ("over_35_goals", "over_35", "over35"),
        "under_35": ("under_35_goals", "under_35", "under35"),
        "btts_yes": ("btts_yes", "btts_y", "yes"),
        "btts_no": ("btts_no", "btts_n", "no"),
    }
    # also nested match_winner / over_under / btts
    mw = bag.get("match_winner") if isinstance(bag.get("match_winner"), dict) else {}
    ou = bag.get("over_under") if isinstance(bag.get("over_under"), dict) else {}
    btts = bag.get("btts") if isinstance(bag.get("btts"), dict) else {}
    for key, aliases in mapping.items():
        val = None
        for a in aliases:
            if bag.get(a) is not None:
                val = bag.get(a)
                break
        if val is None and key == "home_win":
            val = mw.get("home")
        elif val is None and key == "draw":
            val = mw.get("draw")
        elif val is None and key == "away_win":
            val = mw.get("away")
        elif val is None and key.startswith("over_"):
            val = ou.get(key) or ou.get(key.replace("_", ""))
        elif val is None and key.startswith("under_"):
            val = ou.get(key) or ou.get(key.replace("_", ""))
        elif val is None and key == "btts_yes":
            val = btts.get("yes")
        elif val is None and key == "btts_no":
            val = btts.get("no")
        f = _safe_float(val)
        if f and f > 1.01:
            out[key] = round(f, 2)
    return out


def _parse_prediction_bag(pred_data: dict) -> dict:
    """Extract useful model probs from BSD prediction."""
    out = {}
    if not pred_data or not isinstance(pred_data, dict):
        return out
    markets = pred_data.get("markets") if isinstance(pred_data.get("markets"), dict) else pred_data
    if not isinstance(markets, dict):
        return out
    mr = markets.get("match_result") or markets.get("1x2") or {}
    if isinstance(mr, dict):
        out["prob_home"] = _safe_float(mr.get("prob_home") or mr.get("home"))
        out["prob_draw"] = _safe_float(mr.get("prob_draw") or mr.get("draw"))
        out["prob_away"] = _safe_float(mr.get("prob_away") or mr.get("away"))
        out["predicted_1x2"] = mr.get("predicted")
    xg = markets.get("expected_goals") or {}
    if isinstance(xg, dict):
        out["xg_home"] = _safe_float(xg.get("home"))
        out["xg_away"] = _safe_float(xg.get("away"))
    ou = markets.get("over_under") or {}
    if isinstance(ou, dict):
        out["prob_over_15"] = _safe_float(ou.get("prob_over_15") or ou.get("over_15"))
        out["prob_over_25"] = _safe_float(ou.get("prob_over_25") or ou.get("over_25"))
        out["prob_over_35"] = _safe_float(ou.get("prob_over_35") or ou.get("over_35"))
    btts = markets.get("btts") or {}
    if isinstance(btts, dict):
        out["prob_btts_yes"] = _safe_float(btts.get("prob_yes") or btts.get("yes"))
    score = markets.get("score") or {}
    if isinstance(score, dict):
        out["most_likely_score"] = score.get("most_likely")
    return out


MARKET_LABELS = {
    "home_win": "П1 (победа хозяев)",
    "draw": "Ничья (X)",
    "away_win": "П2 (победа гостей)",
    "over_15": "ТБ 1.5",
    "under_15": "ТМ 1.5",
    "over_25": "ТБ 2.5",
    "under_25": "ТМ 2.5",
    "over_35": "ТБ 3.5",
    "under_35": "ТМ 3.5",
    "btts_yes": "Обе забьют — да",
    "btts_no": "Обе забьют — нет",
}


def _build_forecast_variants(pack: dict, odds_raw=None, pred_raw=None) -> dict:
    """
    Build 2 singles + 2 combos with odds in [1.6, 2.7] and multi-line reasons.
    Uses form, xG, halves, goal segments, standings, injuries (defenders), H2H, model probs.
    """
    COEF_MIN, COEF_MAX = 1.60, 2.70
    odds = _parse_odds_bag(odds_raw or {})
    pred = _parse_prediction_bag(pred_raw or {})
    home_name = pack.get("home_team") or "Хозяева"
    away_name = pack.get("away_team") or "Гости"

    form_h = (pack.get("form") or {}).get("home") or {}
    form_a = (pack.get("form") or {}).get("away") or {}
    xg_h = (pack.get("xg") or {}).get("home") or {}
    xg_a = (pack.get("xg") or {}).get("away") or {}
    gs_h = (pack.get("goal_segments") or {}).get("home") or {}
    gs_a = (pack.get("goal_segments") or {}).get("away") or {}
    halves_h = (pack.get("halves") or {}).get("home") or {}
    halves_a = (pack.get("halves") or {}).get("away") or {}
    inj_h = (pack.get("injuries") or {}).get("home") or []
    inj_a = (pack.get("injuries") or {}).get("away") or []
    h2h = pack.get("h2h") or {}
    standings = pack.get("standings") or {}

    def_h_n, def_h_names = _count_missing_defenders(inj_h)
    def_a_n, def_a_names = _count_missing_defenders(inj_a)

    xg_avg_h = _safe_float(xg_h.get("avg")) or _safe_float(pred.get("xg_home"))
    xg_avg_a = _safe_float(xg_a.get("avg")) or _safe_float(pred.get("xg_away"))
    # goals for/against from form
    gf_h = _safe_float(form_h.get("goals_for"), 0) or 0
    ga_h = _safe_float(form_h.get("goals_against"), 0) or 0
    gf_a = _safe_float(form_a.get("goals_for"), 0) or 0
    ga_a = _safe_float(form_a.get("goals_against"), 0) or 0
    mc_h = max(int(form_h.get("matches_count") or xg_h.get("matches") or 1), 1)
    mc_a = max(int(form_a.get("matches_count") or xg_a.get("matches") or 1), 1)
    gpg_h, gapg_h = gf_h / mc_h, ga_h / mc_h
    gpg_a, gapg_a = gf_a / mc_a, ga_a / mc_a

    # score candidates: higher = stronger pick relative to odds
    candidates = []  # list of {key, score, why_lines}

    def add_cand(key, score, why_lines):
        coef = odds.get(key)
        if coef is None or not (COEF_MIN <= coef <= COEF_MAX):
            return
        candidates.append({
            "key": key,
            "label": MARKET_LABELS.get(key, key),
            "coef": coef,
            "score": score,
            "why": why_lines,
        })

    # --- 1X2 signals ---
    ph = pred.get("prob_home")
    pd_ = pred.get("prob_draw")
    pa = pred.get("prob_away")
    if ph is not None:
        # model probs sometimes 0-100
        if ph > 1.5:
            ph, pd_, pa = ph / 100.0, (pd_ or 0) / 100.0, (pa or 0) / 100.0
        implied = 1.0 / odds["home_win"] if odds.get("home_win") else None
        edge = (ph - implied) if implied else 0
        why = []
        if ph >= 0.42:
            why.append(f"Модель оценивает победу {home_name} ≈ {ph*100:.0f}%.")
        if xg_avg_h is not None and xg_avg_a is not None and xg_avg_h > xg_avg_a + 0.15:
            why.append(f"xG за сезон: {home_name} {xg_avg_h:.2f} vs {away_name} {xg_avg_a:.2f}.")
        if form_h.get("won", 0) >= form_h.get("lost", 0) + 2:
            why.append(f"Форма хозяев сильнее: {form_h.get('won',0)}П / {form_h.get('drawn',0)}Н / {form_h.get('lost',0)}П.")
        if def_a_n >= 2:
            names = ", ".join(def_a_names[:3])
            why.append(f"У {away_name} вне игры ≥2 защитника ({names}) — выше шанс пропустить.")
        if why:
            add_cand("home_win", 50 + ph * 40 + max(edge, 0) * 80, why)

    if pa is not None:
        if pa > 1.5:
            pa = pa / 100.0 if pa > 1.5 else pa
        # re-normalize if needed already done above
        implied = 1.0 / odds["away_win"] if odds.get("away_win") else None
        edge = ((pa if pa <= 1 else pa/100) - implied) if implied else 0
        p_away = pa if pa <= 1 else pa / 100.0
        why = []
        if p_away >= 0.38:
            why.append(f"Модель склоняется к победе {away_name} (≈ {p_away*100:.0f}%).")
        if xg_avg_a is not None and xg_avg_h is not None and xg_avg_a > xg_avg_h + 0.15:
            why.append(f"xG гостей выше: {xg_avg_a:.2f} против {xg_avg_h:.2f} у хозяев.")
        if def_h_n >= 2:
            names = ", ".join(def_h_names[:3])
            why.append(f"У {home_name} отсутствуют ≥2 защитника ({names}) — оборона ослаблена.")
        if why:
            add_cand("away_win", 48 + p_away * 40 + max(edge, 0) * 80, why)

    if pd_ is not None:
        p_draw = pd_ / 100.0 if pd_ > 1.5 else pd_
        why = []
        if p_draw >= 0.28:
            why.append(f"Модель даёт ничьей заметный шанс (≈ {p_draw*100:.0f}%).")
        if abs((xg_avg_h or 0) - (xg_avg_a or 0)) < 0.2 and xg_avg_h is not None:
            why.append("xG команд близки — матч может закончиться вничью.")
        if why:
            add_cand("draw", 40 + p_draw * 50, why)

    # --- totals ---
    exp_total = None
    if xg_avg_h is not None and xg_avg_a is not None:
        exp_total = xg_avg_h + xg_avg_a
    elif gpg_h or gpg_a:
        exp_total = (gpg_h + gapg_h + gpg_a + gapg_a) / 2.0

    # goal segments intensity
    def _seg_total(gs, key):
        sc = (gs.get(key) or {})
        return sum(int(v or 0) for v in sc.values()) if isinstance(sc, dict) else 0

    scored_h = _seg_total(gs_h, "scored")
    scored_a = _seg_total(gs_a, "scored")
    conc_h = _seg_total(gs_h, "conceded")
    conc_a = _seg_total(gs_a, "conceded")

    # defender boost for over/btts
    def_boost_over = 0
    def_why = []
    if def_h_n >= 2:
        def_boost_over += 8
        def_why.append(f"Оборона {home_name}: вне {def_h_n} защитника ({', '.join(def_h_names[:2])}) — выше риск пропустить.")
    if def_a_n >= 2:
        def_boost_over += 8
        def_why.append(f"Оборона {away_name}: вне {def_a_n} защитника ({', '.join(def_a_names[:2])}) — выше риск пропустить.")

    p_o25 = pred.get("prob_over_25")
    if p_o25 is not None and p_o25 > 1.5:
        p_o25 = p_o25 / 100.0
    p_o15 = pred.get("prob_over_15")
    if p_o15 is not None and p_o15 > 1.5:
        p_o15 = p_o15 / 100.0
    p_btts = pred.get("prob_btts_yes")
    if p_btts is not None and p_btts > 1.5:
        p_btts = p_btts / 100.0

    if (exp_total is not None and exp_total >= 2.35) or (p_o25 and p_o25 >= 0.48) or def_boost_over:
        why = []
        if exp_total is not None:
            why.append(f"Ожидаемый тотал по xG/форме ≈ {exp_total:.2f} гола.")
        if p_o25:
            why.append(f"Модель: ТБ 2.5 с вероятностью ≈ {p_o25*100:.0f}%.")
        if scored_h + scored_a >= 20:
            why.append("По голевым отрезкам обе команды регулярно забивают в разных интервалах.")
        why.extend(def_why)
        if not why:
            why.append("Статистика указывает на результативный матч.")
        add_cand("over_25", 55 + def_boost_over + (10 if exp_total and exp_total >= 2.6 else 0), why)

    if (exp_total is not None and exp_total >= 1.7) or (p_o15 and p_o15 >= 0.65):
        why = []
        if exp_total is not None:
            why.append(f"Суммарный xG/форма ≈ {exp_total:.2f} — минимум 2 гола в матче вероятны.")
        if p_o15:
            why.append(f"Модель: ТБ 1.5 ≈ {p_o15*100:.0f}%.")
        why.extend(def_why[:1])
        add_cand("over_15", 52 + def_boost_over * 0.5, why or ["Высокая вероятность хотя бы двух голов."])

    if exp_total is not None and exp_total <= 2.2:
        why = [f"Ожидаемый тотал умеренный (≈ {exp_total:.2f})."]
        if gapg_h < 1.1 and gapg_a < 1.1:
            why.append("Обе команды пропускают мало в среднем за матч.")
        add_cand("under_25", 50, why)
        add_cand("under_35", 48, why + ["ТМ 3.5 — страховка от одного всплеска."])

    if exp_total is not None and exp_total >= 2.8:
        why = [f"Высокий ожидаемый тотал ≈ {exp_total:.2f}."]
        why.extend(def_why)
        add_cand("over_35", 50 + def_boost_over, why)

    # BTTS
    both_score_signal = (gpg_h >= 1.0 and gpg_a >= 1.0) or (p_btts and p_btts >= 0.50) or def_boost_over >= 8
    if both_score_signal:
        why = []
        if gpg_h and gpg_a:
            why.append(f"Средняя результативность: {home_name} {gpg_h:.1f} и {away_name} {gpg_a:.1f} гола/матч.")
        if p_btts:
            why.append(f"Модель: обе забьют ≈ {p_btts*100:.0f}%.")
        if def_h_n >= 2 or def_a_n >= 2:
            why.extend(def_why)
        if scored_h and scored_a:
            why.append("Голевые отрезки показывают удары в разных таймах у обеих сторон.")
        add_cand("btts_yes", 54 + def_boost_over, why or ["Обе команды способны забить."])

    if (gapg_h < 0.9 or gapg_a < 0.9) and not def_boost_over:
        why = ["Одна из команд стабильно играет «на ноль» — обе могут не забить."]
        add_cand("btts_no", 45, why)

    # rank singles
    candidates.sort(key=lambda c: (-c["score"], c["coef"]))
    singles = []
    used_keys = set()
    for c in candidates:
        if c["key"] in used_keys:
            continue
        used_keys.add(c["key"])
        singles.append({
            "pick": c["label"],
            "key": c["key"],
            "coef": c["coef"],
            "why": "\n".join(c["why"][:4]),
        })
        if len(singles) >= 2:
            break

    # combos: pairs of compatible markets, product in [1.6, 2.7]
    COMPAT = {
        "over_15": {"btts_yes", "home_win", "away_win", "draw"},
        "over_25": {"btts_yes", "home_win", "away_win"},
        "under_25": {"btts_no", "draw", "home_win", "away_win"},
        "under_35": {"btts_yes", "btts_no", "home_win", "away_win", "draw", "over_15"},
        "btts_yes": {"over_15", "over_25", "home_win", "away_win", "under_35"},
        "btts_no": {"under_25", "under_35", "home_win", "away_win"},
        "home_win": {"over_15", "over_25", "btts_yes", "under_35", "btts_no"},
        "away_win": {"over_15", "over_25", "btts_yes", "under_35", "btts_no"},
        "draw": {"under_25", "under_35", "btts_yes", "over_15"},
    }
    # score map for keys
    score_map = {c["key"]: c for c in candidates}
    combo_cands = []
    keys = list(score_map.keys())
    for i, k1 in enumerate(keys):
        for k2 in keys[i + 1:]:
            if k2 not in COMPAT.get(k1, set()) and k1 not in COMPAT.get(k2, set()):
                continue
            # avoid contradictory
            if {k1, k2} <= {"over_25", "under_25"} or {k1, k2} <= {"btts_yes", "btts_no"}:
                continue
            if {k1, k2} <= {"over_15", "under_15"} or {k1, k2} <= {"over_35", "under_35"}:
                continue
            if {k1, k2} <= {"home_win", "away_win", "draw"} and k1 != k2:
                # only one 1x2 in combo
                if k1 in ("home_win", "away_win", "draw") and k2 in ("home_win", "away_win", "draw"):
                    continue
            c1, c2 = odds.get(k1), odds.get(k2)
            if not c1 or not c2:
                continue
            prod = round(c1 * c2, 2)
            if not (COEF_MIN <= prod <= COEF_MAX):
                continue
            s = score_map[k1]["score"] + score_map[k2]["score"]
            why_lines = [
                f"Связка: {MARKET_LABELS.get(k1, k1)} ({c1}) + {MARKET_LABELS.get(k2, k2)} ({c2}).",
                f"Итоговый коэффициент ≈ {prod}.",
            ]
            # merge unique reasons
            for line in (score_map[k1]["why"] + score_map[k2]["why"]):
                if line not in why_lines:
                    why_lines.append(line)
                if len(why_lines) >= 5:
                    break
            combo_cands.append({
                "pick": f"{MARKET_LABELS.get(k1, k1)} + {MARKET_LABELS.get(k2, k2)}",
                "keys": [k1, k2],
                "coef": prod,
                "score": s,
                "why": "\n".join(why_lines[:5]),
            })
    combo_cands.sort(key=lambda c: (-c["score"], c["coef"]))
    combos = []
    used_pairs = set()
    for c in combo_cands:
        pair = tuple(sorted(c["keys"]))
        if pair in used_pairs:
            continue
        used_pairs.add(pair)
        combos.append({
            "pick": c["pick"],
            "keys": c["keys"],
            "coef": c["coef"],
            "why": c["why"],
        })
        if len(combos) >= 2:
            break

    return {
        "singles": singles,
        "combos": combos,
        "coef_range": [COEF_MIN, COEF_MAX],
        "odds": odds,
        "model": {k: pred[k] for k in pred if k.startswith("prob_") or k.startswith("xg_") or k in ("predicted_1x2", "most_likely_score")},
        "defenders_out": {
            "home": {"count": def_h_n, "names": def_h_names[:4]},
            "away": {"count": def_a_n, "names": def_a_names[:4]},
        },
    }


def _extract_xg_from_stats_block(stats_obj) -> dict:
    """Pull xG total/avg from form.overall.stats or similar bag."""
    if not stats_obj or not isinstance(stats_obj, dict):
        return {}
    # BSD form uses keys like "xg", "expected_goals", sometimes nested {average, total, matches}
    for key in ("xg", "expected_goals", "xG", "xg_for"):
        val = stats_obj.get(key)
        if val is None:
            continue
        if isinstance(val, dict):
            avg = val.get("average") or val.get("avg") or val.get("actual")
            total = val.get("total")
            matches = val.get("matches")
            if avg is not None or total is not None:
                return {
                    "avg": float(avg) if avg is not None else None,
                    "total": float(total) if total is not None else None,
                    "matches": int(matches) if matches is not None else None,
                }
        elif isinstance(val, (int, float)):
            return {"avg": float(val), "total": None, "matches": None}
    return {}


def _parse_form(form_data) -> dict:
    if not form_data or not isinstance(form_data, dict):
        return {}
    overall = form_data.get("overall") or {}
    if not overall and form_data.get("form"):
        overall = form_data
    xg_info = _extract_xg_from_stats_block(overall.get("stats") or {})
    # also try top-level overall xg fields
    if not xg_info:
        for k in ("xg", "expected_goals", "avg_xg", "xg_for"):
            v = overall.get(k)
            if isinstance(v, (int, float)):
                xg_info = {"avg": float(v), "total": None, "matches": overall.get("matches")}
                break
            if isinstance(v, dict) and (v.get("average") is not None or v.get("actual") is not None):
                xg_info = {
                    "avg": float(v.get("average") or v.get("actual")),
                    "total": float(v["total"]) if v.get("total") is not None else None,
                    "matches": v.get("matches") or overall.get("matches"),
                }
                break
    return {
        "form": overall.get("form") or "",
        "won": overall.get("won", 0) or 0,
        "drawn": overall.get("drawn", 0) or 0,
        "lost": overall.get("lost", 0) or 0,
        "goals_for": overall.get("goals_for", 0) or 0,
        "goals_against": overall.get("goals_against", 0) or 0,
        "matches_count": overall.get("matches") or 0,
        "points_per_match": overall.get("points_per_match"),
        "team_name": form_data.get("team_name") or "",
        "xg_avg": xg_info.get("avg"),
        "xg_total": xg_info.get("total"),
        "xg_matches": xg_info.get("matches") or overall.get("matches") or 0,
    }


def _parse_recent_fixtures(fixtures_data) -> list:
    if not fixtures_data:
        return []
    results = fixtures_data.get("results") if isinstance(fixtures_data, dict) else fixtures_data
    if not isinstance(results, list):
        return []
    results = list(results)
    try:
        results.sort(key=lambda m: (m.get("event_date") or m.get("date") or ""), reverse=True)
    except Exception:
        pass
    out = []
    for m in results:
        if not isinstance(m, dict):
            continue
        st = (m.get("status") or "").lower()
        if st in ("upcoming", "live", "postponed", "cancelled", "unresolved"):
            continue
        home = m.get("home_team") or m.get("home") or m.get("home_team_name") or "?"
        away = m.get("away_team") or m.get("away") or m.get("away_team_name") or "?"
        if isinstance(home, dict):
            home = home.get("name") or "?"
        if isinstance(away, dict):
            away = away.get("name") or "?"
        hs = m.get("home_score")
        aws = m.get("away_score")
        # only with a real score
        if hs is None or aws is None:
            continue
        score = f"{hs}:{aws}"
        date = (m.get("event_date") or m.get("date") or "")[:10]
        out.append({
            "date": date,
            "home": str(home),
            "away": str(away),
            "score": score,
            "home_score": hs,
            "away_score": aws,
            "event_id": m.get("id") or m.get("event_id"),
            "home_team_id": m.get("home_team_id"),
            "away_team_id": m.get("away_team_id"),
        })
        if len(out) >= 15:
            break
    return out


def _parse_h2h(h2h_data) -> dict:
    if not h2h_data or not isinstance(h2h_data, dict):
        return {}
    recent = []
    for m in (h2h_data.get("recent_matches") or [])[:8]:
        if not isinstance(m, dict):
            continue
        hs, aws = m.get("home_score"), m.get("away_score")
        score = m.get("score") or (f"{hs}-{aws}" if hs is not None and aws is not None else "—")
        recent.append({
            "date": (m.get("date") or "")[:10],
            "home": m.get("home") or "?",
            "away": m.get("away") or "?",
            "score": str(score),
        })
    return {
        "total": h2h_data.get("total_matches") or 0,
        "home_wins": h2h_data.get("home_wins", 0) or 0,
        "draws": h2h_data.get("draws", 0) or 0,
        "away_wins": h2h_data.get("away_wins", 0) or 0,
        "home_goals": h2h_data.get("home_goals", 0) or 0,
        "away_goals": h2h_data.get("away_goals", 0) or 0,
        "avg_goals": h2h_data.get("avg_total_goals"),
        "recent": recent,
    }


def _parse_lineups(data) -> dict:
    """Parse BSD lineups into compact home/away starters + formation."""
    if not data or not isinstance(data, dict):
        return {}
    status = data.get("lineup_status") or "unavailable"
    if status == "unavailable":
        return {"status": status, "home": {}, "away": {}}
    sides = data.get("lineups") or {}
    if not isinstance(sides, dict):
        sides = {}

    def side(key):
        s = sides.get(key) or {}
        if not isinstance(s, dict):
            return {}
        players = []
        for p in (s.get("players") or s.get("starters") or [])[:11]:
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            players.append({
                "id": pid,
                "name": p.get("short_name") or p.get("name") or "?",
                "position": p.get("position") or "",
                "number": p.get("jersey_number"),
                "photo": _player_photo(pid),
            })
        return {
            "team_name": s.get("team_name") or "",
            "formation": s.get("formation") or s.get("predicted_formation") or "",
            "players": players,
        }

    return {
        "status": status,  # predicted | confirmed
        "home": side("home"),
        "away": side("away"),
    }


def _parse_standings(data, home_tid=None, away_tid=None) -> dict:
    """Parse league table; mark home/away teams."""
    if not data or not isinstance(data, dict):
        return {}
    rows = []
    if data.get("grouped") and isinstance(data.get("groups"), dict):
        # find group containing our teams, else first group
        groups = data["groups"]
        chosen = None
        for gname, grow in groups.items():
            if not isinstance(grow, list):
                continue
            ids = {r.get("team_id") for r in grow if isinstance(r, dict)}
            if (home_tid and home_tid in ids) or (away_tid and away_tid in ids):
                chosen = grow
                break
        if chosen is None and groups:
            chosen = next(iter(groups.values()))
        rows = chosen or []
    else:
        rows = data.get("standings") or []

    table = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        tid = r.get("team_id")
        table.append({
            "pos": r.get("position"),
            "team_id": tid,
            "team": r.get("team_name") or r.get("team") or "?",
            "played": r.get("played", 0),
            "won": r.get("won", 0),
            "drawn": r.get("drawn", 0),
            "lost": r.get("lost", 0),
            "gf": r.get("gf", 0),
            "ga": r.get("ga", 0),
            "gd": r.get("gd", 0),
            "pts": r.get("pts", 0),
            "form": r.get("form") or "",
            "highlight": tid in (home_tid, away_tid) if tid else False,
        })
    season = data.get("season") or {}
    return {
        "season": season.get("name") or season.get("year") or "",
        "table": table,
    }


async def resolve_event_id(
    session: aiohttp.ClientSession,
    match_name: str,
    match_time: str = "",
    league_id: int = None,
    match_date: str = None,
) -> tuple:
    """Find BSD event_id + team ids by match name like 'Torino — Udinese'."""
    if not match_name:
        return None, None, None

    parts = None
    for sep in ("—", " – ", " - ", "–", "-"):
        if sep in match_name:
            cand = [p.strip() for p in match_name.split(sep, 1)]
            if len(cand) == 2 and cand[0] and cand[1]:
                parts = cand
                break
    if not parts:
        return None, None, None
    home, away = parts[0], parts[1]
    home_l, away_l = home.lower(), away.lower()

    def _tname(raw):
        if isinstance(raw, dict):
            return (raw.get("name") or raw.get("title") or "").strip().lower()
        return str(raw or "").strip().lower()

    def _match_ev(ev):
        h = _tname(ev.get("home_team") or ev.get("home_team_name"))
        a = _tname(ev.get("away_team") or ev.get("away_team_name"))
        if not h or not a:
            return False
        home_ok = home_l in h or h in home_l or home_l.split()[0] in h
        away_ok = away_l in a or a in away_l or away_l.split()[0] in a
        return home_ok and away_ok

    def _ids(ev):
        return (
            ev.get("id"),
            ev.get("home_team_id"),
            ev.get("away_team_id"),
        )

    today = datetime.now(tz)
    dates = []
    if match_date and len(str(match_date)) >= 10:
        dates.append(str(match_date)[:10])
    for d in range(-1, 5):
        ds = (today + timedelta(days=d)).strftime("%Y-%m-%d")
        if ds not in dates:
            dates.append(ds)

    for ds in dates:
        params = {"date_from": ds, "date_to": ds, "limit": 200}
        if league_id:
            params["league_id"] = int(league_id)
        data = await _bsd_get(session, "events/", params)
        results = (data or {}).get("results") or []
        for ev in results:
            if _match_ev(ev):
                return _ids(ev)

    # fallback team_name
    for q in (home, home.split()[0]):
        if not q:
            continue
        data = await _bsd_get(session, "events/", {
            "team_name": q,
            "date_from": dates[0],
            "date_to": dates[-1],
            "limit": 100,
        })
        for ev in (data or {}).get("results") or []:
            if _match_ev(ev):
                return _ids(ev)
    return None, None, None


def _xg_value(block) -> Optional[float]:
    """Extract numeric xG from a stats side block (home/away or half)."""
    if not block or not isinstance(block, dict):
        return None
    xg = block.get("xg") or block.get("expected_goals")
    if isinstance(xg, dict):
        for k in ("actual", "average", "avg", "value", "total"):
            if xg.get(k) is not None:
                try:
                    return float(xg[k])
                except (TypeError, ValueError):
                    pass
    elif isinstance(xg, (int, float)):
        return float(xg)
    return None


def _side_stat(block, *keys, default=None):
    if not block or not isinstance(block, dict):
        return default
    for k in keys:
        v = block.get(k)
        if v is not None:
            return v
    return default


GOAL_INTERVAL_LABELS = ["0-18", "19-36", "37-54", "55-72", "73-90"]


def _minute_to_goal_interval(minute) -> Optional[str]:
    """Map match minute to 18-min band (0-90, stoppage → 73-90)."""
    if minute is None:
        return None
    try:
        m = int(float(minute))
    except (TypeError, ValueError):
        return None
    if m < 0:
        return None
    if m > 90:
        m = 90  # 2nd half injury time
    if m <= 18:
        return "0-18"
    if m <= 36:
        return "19-36"
    if m <= 54:
        return "37-54"
    if m <= 72:
        return "55-72"
    return "73-90"


def _empty_goal_segments() -> dict:
    return {
        "scored": {lab: 0 for lab in GOAL_INTERVAL_LABELS},
        "conceded": {lab: 0 for lab in GOAL_INTERVAL_LABELS},
        "matches": 0,
        "intervals": list(GOAL_INTERVAL_LABELS),
    }


def _incident_list(data) -> list:
    if not data:
        return []
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("incidents", "results", "events", "items", "data"):
        v = data.get(key)
        if isinstance(v, list):
            return v
    return []


def _incident_is_goal(inc: dict) -> bool:
    if not isinstance(inc, dict):
        return False
    t = str(
        inc.get("type")
        or inc.get("incident_type")
        or inc.get("event_type")
        or inc.get("kind")
        or ""
    ).lower()
    if t in ("goal", "own_goal", "owngoal", "penalty_goal", "penalty"):
        return True
    if "goal" in t and "cancel" not in t and "disallow" not in t and "var" not in t:
        return True
    return False


def _incident_minute(inc: dict) -> Optional[int]:
    for k in ("minute", "time", "min", "match_minute", "clock"):
        v = inc.get(k)
        if v is None:
            continue
        if isinstance(v, dict):
            v = v.get("minute") or v.get("min") or v.get("value")
        try:
            return int(float(v))
        except (TypeError, ValueError):
            # strings like "45+2"
            s = str(v).replace("'", "").strip()
            if "+" in s:
                parts = s.split("+", 1)
                try:
                    return int(parts[0]) + int(parts[1])
                except (TypeError, ValueError):
                    pass
            try:
                return int(float(s.split()[0]))
            except (TypeError, ValueError, IndexError):
                pass
    # period_second → rough minute
    ps = inc.get("period_second")
    if ps is not None:
        try:
            return int(float(ps)) // 60
        except (TypeError, ValueError):
            pass
    return None


def _incident_period(inc: dict) -> Optional[int]:
    p = inc.get("period") or inc.get("period_id") or inc.get("half")
    if p is None:
        return None
    try:
        return int(p)
    except (TypeError, ValueError):
        s = str(p).lower()
        if "1" in s or "first" in s:
            return 1
        if "2" in s or "second" in s:
            return 2
        return None


def _incident_side(inc: dict, home_tid=None, away_tid=None) -> Optional[str]:
    """Return 'home' or 'away' for the scoring team."""
    team = inc.get("team") or inc.get("side") or inc.get("team_side")
    if isinstance(team, dict):
        tid = team.get("id") or team.get("team_id")
        if home_tid is not None and tid is not None and int(tid) == int(home_tid):
            return "home"
        if away_tid is not None and tid is not None and int(tid) == int(away_tid):
            return "away"
        name = (team.get("name") or "").lower()
        # can't resolve by name without context
        team = team.get("side") or team.get("home_away")
    if isinstance(team, str):
        t = team.lower()
        if t in ("home", "h", "1"):
            return "home"
        if t in ("away", "a", "2"):
            return "away"
    if "is_home" in inc:
        return "home" if inc.get("is_home") else "away"
    if "home" in inc and isinstance(inc.get("home"), bool):
        return "home" if inc["home"] else "away"
    tid = inc.get("team_id") or inc.get("scoring_team_id")
    if tid is not None:
        if home_tid is not None and int(tid) == int(home_tid):
            return "home"
        if away_tid is not None and int(tid) == int(away_tid):
            return "away"
    return None


def _shotmap_list(stats_data) -> list:
    if not stats_data or not isinstance(stats_data, dict):
        return []
    sm = stats_data.get("shotmap")
    if isinstance(sm, list):
        return sm
    bag = stats_data.get("stats")
    if isinstance(bag, dict) and isinstance(bag.get("shotmap"), list):
        return bag["shotmap"]
    return []


def _goals_from_stats_shotmap(stats_data, our_side: str) -> list:
    """
    Extract (interval_label, 'scored'|'conceded') from stats.shotmap goals.
    shotmap rows: { type: 'goal', min: 23, home: true/false, ... }
    """
    out = []
    if not our_side:
        return out
    for shot in _shotmap_list(stats_data):
        if not isinstance(shot, dict):
            continue
        t = str(shot.get("type") or "").lower()
        if t != "goal":
            continue
        # skip shootout / ET-ish
        sit = str(shot.get("sit") or shot.get("situation") or "").lower()
        if sit in ("shootout", "penalty_shootout"):
            continue
        minute = shot.get("min") if shot.get("min") is not None else shot.get("minute")
        try:
            m_int = int(float(minute)) if minute is not None else None
        except (TypeError, ValueError):
            m_int = None
        if m_int is not None and m_int > 120:
            continue
        label = _minute_to_goal_interval(minute)
        if not label:
            continue
        is_home = shot.get("home")
        if is_home is None and "team" in shot:
            team = shot.get("team")
            if isinstance(team, str):
                is_home = team.lower() in ("home", "h", "1")
            elif isinstance(team, dict):
                side = str(team.get("side") or team.get("home_away") or "").lower()
                is_home = side in ("home", "h", "1")
        if is_home is None:
            continue
        scoring_side = "home" if is_home else "away"
        kind = "scored" if scoring_side == our_side else "conceded"
        out.append((label, kind))
    return out


def _aggregate_goal_segments_from_stats(
    stats_list: list,
    match_sides: list,
) -> dict:
    """Build goal time segments from stats.shotmap (primary) + optional incidents."""
    seg = _empty_goal_segments()
    used = 0
    for st, our_side in zip(stats_list, match_sides):
        if not our_side or not st:
            continue
        goals = _goals_from_stats_shotmap(st, our_side)
        # count match if we got any shotmap at all OR any goals; still count finished sample
        has_sm = bool(_shotmap_list(st))
        if has_sm or goals:
            used += 1
        elif st:
            # stats present but no shotmap — still count the match (0-0 possible)
            used += 1
        for label, kind in goals:
            seg[kind][label] = seg[kind].get(label, 0) + 1
    seg["matches"] = used
    return seg


def _aggregate_goal_segments(
    incidents_list: list,
    match_sides: list,
) -> dict:
    """Fallback: incidents responses aligned with match_sides."""
    seg = _empty_goal_segments()
    used = 0
    for data, our_side in zip(incidents_list, match_sides):
        if not our_side:
            continue
        items = _incident_list(data)
        if not items:
            continue
        used += 1
        for inc in items:
            if not _incident_is_goal(inc):
                continue
            period = _incident_period(inc)
            if period is not None and period >= 3:
                continue
            minute = _incident_minute(inc)
            label = _minute_to_goal_interval(minute)
            if not label:
                continue
            scoring_side = _incident_side(inc)
            if scoring_side is None:
                continue
            t = str(inc.get("type") or inc.get("incident_type") or "").lower()
            if "own" in t:
                scoring_side = "away" if scoring_side == "home" else "home"
            if scoring_side == our_side:
                seg["scored"][label] = seg["scored"].get(label, 0) + 1
            else:
                seg["conceded"][label] = seg["conceded"].get(label, 0) + 1
    seg["matches"] = used
    return seg


def _aggregate_half_stats(stats_list: list, side_key: str) -> dict:
    """Average first_half / second_half stats across match stats responses for one team side."""
    # side_key is 'home' or 'away' relative to the match; we pass already-resolved team sides
    fh_acc = {"possession": [], "shots": [], "shots_on": [], "xg": [], "corners": [], "fouls": [], "goals": []}
    sh_acc = {"possession": [], "shots": [], "shots_on": [], "xg": [], "corners": [], "fouls": [], "goals": []}

    def _collect(half_block, acc):
        if not half_block or not isinstance(half_block, dict):
            return
        side = half_block.get(side_key) if side_key in half_block else half_block
        if not isinstance(side, dict):
            return
        poss = _side_stat(side, "ball_possession", "possession")
        if poss is not None:
            try:
                acc["possession"].append(float(poss))
            except (TypeError, ValueError):
                pass
        shots = _side_stat(side, "total_shots", "shots_total", "shots")
        if shots is not None:
            try:
                acc["shots"].append(float(shots))
            except (TypeError, ValueError):
                pass
        sot = _side_stat(side, "shots_on_target", "shots_on")
        if sot is not None:
            try:
                acc["shots_on"].append(float(sot))
            except (TypeError, ValueError):
                pass
        xg = _xg_value(side)
        if xg is not None:
            acc["xg"].append(xg)
        corners = _side_stat(side, "corners", "corner_kicks")
        if corners is not None:
            try:
                acc["corners"].append(float(corners))
            except (TypeError, ValueError):
                pass
        fouls = _side_stat(side, "fouls")
        if fouls is not None:
            try:
                acc["fouls"].append(float(fouls))
            except (TypeError, ValueError):
                pass
        goals = _side_stat(side, "goals", "score")
        if goals is not None:
            try:
                acc["goals"].append(float(goals))
            except (TypeError, ValueError):
                pass

    for st in stats_list:
        if not st or not isinstance(st, dict):
            continue
        bag = st.get("stats") if isinstance(st.get("stats"), dict) else st
        _collect(bag.get("first_half") or bag.get("1st_half") or {}, fh_acc)
        _collect(bag.get("second_half") or bag.get("2nd_half") or {}, sh_acc)

    def _avg(acc):
        out = {}
        n = 0
        for k, vals in acc.items():
            if vals:
                out[k] = round(sum(vals) / len(vals), 2)
                n = max(n, len(vals))
            else:
                out[k] = None
        out["matches"] = n
        return out

    return {"first_half": _avg(fh_acc), "second_half": _avg(sh_acc)}


async def fetch_match_enrichment(
    session: aiohttp.ClientSession,
    event_id: int,
    home_tid: int = None,
    away_tid: int = None,
    league_id: int = None,
) -> dict:
    """Fetch coaches, injuries, form, last 5, H2H, xG season, halves — BSD v2 schema."""
    pack = {
        "event_id": event_id,
        "coaches": {"home": {}, "away": {}},
        "injuries": {"home": [], "away": []},
        "form": {"home": {}, "away": {}},
        "last5": {"home": [], "away": []},
        "h2h": {},
        "lineups": {},
        "standings": {},
        "xg": {"home": {}, "away": {}},
        "halves": {"home": {}, "away": {}},
        "goal_segments": {"home": {}, "away": {}},
        "forecast": {},
    }
    if not event_id:
        return pack

    event, h2h, lineups_raw, odds_raw, pred_raw = await asyncio.gather(
        _bsd_get(session, f"events/{event_id}/"),
        _bsd_get(session, f"events/{event_id}/h2h/"),
        _bsd_get(session, f"events/{event_id}/lineups/"),
        _bsd_get(session, f"events/{event_id}/odds/"),
        _bsd_get(session, f"events/{event_id}/prediction/"),
    )

    home_coach_id = away_coach_id = None
    season_id = None
    if event and isinstance(event, dict):
        home_tid = home_tid or event.get("home_team_id")
        away_tid = away_tid or event.get("away_team_id")
        league_id = league_id or event.get("league_id")
        season_id = event.get("season_id")
        home_coach_id, away_coach_id = _extract_coach_ids(event)
        pack["home_team"] = event.get("home_team") or ""
        pack["away_team"] = event.get("away_team") or ""

    pack["h2h"] = _parse_h2h(h2h)
    pack["lineups"] = _parse_lineups(lineups_raw)

    # League standings
    if league_id:
        st_params = {}
        if season_id:
            st_params["season_id"] = season_id
        standings_raw = await _bsd_get(session, f"leagues/{league_id}/standings/", st_params)
        pack["standings"] = _parse_standings(standings_raw, home_tid, away_tid)

    # Managers (coaches) by id
    mgr_tasks = []
    mgr_sides = []
    if home_coach_id:
        mgr_tasks.append(_bsd_get(session, f"managers/{home_coach_id}/"))
        mgr_sides.append("home")
    if away_coach_id:
        mgr_tasks.append(_bsd_get(session, f"managers/{away_coach_id}/"))
        mgr_sides.append("away")
    if mgr_tasks:
        mgr_results = await asyncio.gather(*mgr_tasks)
        for side, mgr in zip(mgr_sides, mgr_results):
            if mgr and isinstance(mgr, dict):
                mid = mgr.get("id") or (home_coach_id if side == "home" else away_coach_id)
                pack["coaches"][side] = {
                    "id": mid,
                    "name": mgr.get("name") or mgr.get("short_name") or "",
                    "photo": _manager_photo(mid),
                }

    # Form (season window), last 5 results, squad + half-stats sampling
    async def _team_pack(tid):
        if not tid:
            return {}, [], [], {}, {}, {}
        form_params = {"last": 30}
        if league_id:
            form_params["league_id"] = league_id
        form_p = _bsd_get(session, f"teams/{tid}/form/", form_params)
        # short form for display (last 5)
        form5_p = _bsd_get(session, f"teams/{tid}/form/", {"last": 5, **({"league_id": league_id} if league_id else {})})
        # last results: events by team_id (most reliable for scores)
        ev_p = _bsd_get(session, "events/", {"team_id": tid, "status": "finished", "limit": 20})
        fix_p = _bsd_get(session, f"teams/{tid}/fixtures/", {"status": "finished", "limit": 15})
        squad_p = _bsd_get(session, f"teams/{tid}/squad/")
        form_d, form5_d, ev_d, fix_d, squad_d = await asyncio.gather(form_p, form5_p, ev_p, fix_p, squad_p)
        form = _parse_form(form5_d)
        if not form.get("form"):
            form = _parse_form(form_d)
        season_form = _parse_form(form_d)
        last5 = _parse_recent_fixtures(ev_d)
        if not last5:
            last5 = _parse_recent_fixtures(fix_d)
        if not last5:
            ev2 = await _bsd_get(session, "events/", {"team_id": tid, "limit": 20})
            last5 = _parse_recent_fixtures(ev2)
        injuries = _parse_injuries(squad_d)

        # xG from season form
        xg_block = {
            "avg": season_form.get("xg_avg"),
            "total": season_form.get("xg_total"),
            "matches": season_form.get("xg_matches") or season_form.get("matches_count") or 0,
        }
        # if total missing but avg + matches known — compute
        if xg_block["total"] is None and xg_block["avg"] is not None and xg_block["matches"]:
            xg_block["total"] = round(float(xg_block["avg"]) * int(xg_block["matches"]), 2)

        # Halves + goal segments: sample up to 15 recent finished events
        half_stats = {}
        goal_seg = _empty_goal_segments()
        event_ids = []
        match_meta = []  # parallel list of last5 / raw event dicts
        for m in (last5 or [])[:15]:
            eid = m.get("event_id") or m.get("id")
            if eid:
                event_ids.append(int(eid))
                match_meta.append(m if isinstance(m, dict) else {})
        if len(event_ids) < 15 and isinstance(ev_d, dict):
            seen = set(event_ids)
            for m in (ev_d.get("results") or []):
                if not isinstance(m, dict) or not m.get("id"):
                    continue
                eid = int(m["id"])
                if eid in seen:
                    continue
                seen.add(eid)
                event_ids.append(eid)
                match_meta.append(m)
                if len(event_ids) >= 15:
                    break
        event_ids = event_ids[:15]
        match_meta = match_meta[: len(event_ids)]

        def _resolve_side(m: dict) -> str:
            if not isinstance(m, dict):
                return "home"
            hid = m.get("home_team_id")
            aid = m.get("away_team_id")
            try:
                if hid is not None and int(hid) == int(tid):
                    return "home"
                if aid is not None and int(aid) == int(tid):
                    return "away"
            except (TypeError, ValueError):
                pass
            # name fallback
            our_name = (season_form.get("team_name") or form.get("team_name") or "").lower().strip()
            if our_name:
                hn = str(m.get("home") or m.get("home_team") or "").lower()
                an = str(m.get("away") or m.get("away_team") or "").lower()
                if isinstance(m.get("home_team"), dict):
                    hn = str(m["home_team"].get("name") or "").lower()
                if isinstance(m.get("away_team"), dict):
                    an = str(m["away_team"].get("name") or "").lower()
                if our_name and hn and (our_name in hn or hn in our_name):
                    return "home"
                if our_name and an and (our_name in an or an in our_name):
                    return "away"
            return "home"

        if event_ids:
            # only stats (shotmap has goals with minutes) — no extra incidents round-trip
            stats_raw = await asyncio.gather(*[
                _bsd_get(session, f"events/{eid}/stats/") for eid in event_ids
            ])

            resolved = []  # (stats, side)
            sides_for_goals = []
            stats_aligned = []
            for st, m in zip(stats_raw, match_meta):
                side = _resolve_side(m)
                sides_for_goals.append(side)
                stats_aligned.append(st)
                if st:
                    resolved.append((st, side))

            normalized = []
            for st, side in resolved:
                bag = st.get("stats") if isinstance(st.get("stats"), dict) else (st if isinstance(st, dict) else {})
                if not bag:
                    continue
                norm = {"stats": {}}
                for half_key in ("first_half", "second_half", "1st_half", "2nd_half"):
                    half = bag.get(half_key)
                    if not isinstance(half, dict):
                        continue
                    team_half = half.get(side) if isinstance(half.get(side), dict) else None
                    if team_half is None:
                        continue
                    out_key = "first_half" if "first" in half_key or half_key.startswith("1") else "second_half"
                    norm["stats"][out_key] = {"home": team_half or {}}
                if norm["stats"]:
                    normalized.append(norm)
            half_stats = _aggregate_half_stats(normalized, "home")

            goal_seg = _aggregate_goal_segments_from_stats(stats_aligned, sides_for_goals)
            # if shotmap empty everywhere — try incidents for a subset (max 8) as fallback
            total_goals = sum((goal_seg.get("scored") or {}).values()) + sum((goal_seg.get("conceded") or {}).values())
            if total_goals == 0 and event_ids:
                sample = list(zip(event_ids, sides_for_goals))[:8]
                inc_raw = await asyncio.gather(*[
                    _bsd_get(session, f"events/{eid}/incidents/") for eid, _ in sample
                ])
                sides_sample = [s for _, s in sample]
                inc_seg = _aggregate_goal_segments(inc_raw, sides_sample)
                if sum((inc_seg.get("scored") or {}).values()) + sum((inc_seg.get("conceded") or {}).values()) > 0:
                    goal_seg = inc_seg
            # always mark sample size so UI can show the block after refresh
            if not goal_seg.get("matches"):
                goal_seg["matches"] = len([1 for st in stats_aligned if st]) or len(event_ids)

        return form, last5, injuries, xg_block, half_stats, goal_seg

    home_data, away_data = await asyncio.gather(
        _team_pack(home_tid),
        _team_pack(away_tid),
    )
    (pack["form"]["home"], pack["last5"]["home"], pack["injuries"]["home"],
     pack["xg"]["home"], pack["halves"]["home"], pack["goal_segments"]["home"]) = home_data
    (pack["form"]["away"], pack["last5"]["away"], pack["injuries"]["away"],
     pack["xg"]["away"], pack["halves"]["away"], pack["goal_segments"]["away"]) = away_data
    pack["home_team_id"] = home_tid
    pack["away_team_id"] = away_tid

    try:
        pack["forecast"] = _build_forecast_variants(pack, odds_raw, pred_raw)
    except Exception as e:
        logger.warning("[forecast] build failed: %s", e)
        pack["forecast"] = {"singles": [], "combos": [], "error": str(e)}

    return pack


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
        home_raw = match.get("home_team", "?")
        away_raw = match.get("away_team", "?")
        # home/away may be string or object
        if isinstance(home_raw, dict):
            home = home_raw.get("name") or home_raw.get("title") or "?"
            home_tid = home_raw.get("id")
        else:
            home = home_raw or "?"
            home_tid = match.get("home_team_id") or match.get("home_id")
        if isinstance(away_raw, dict):
            away = away_raw.get("name") or away_raw.get("title") or "?"
            away_tid = away_raw.get("id")
        else:
            away = away_raw or "?"
            away_tid = match.get("away_team_id") or match.get("away_id")

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

        # BSD image proxy: https://sports.bzzoiro.com/img/team/{id}/
        def _logo_url(m, side, tid):
            for key in (f"{side}_team_logo", f"{side}_logo", f"{side}_crest"):
                val = m.get(key)
                if isinstance(val, str) and val.startswith("http"):
                    return val
            obj = m.get(f"{side}_team_obj") or m.get(f"{side}_team_info") or {}
            if isinstance(obj, dict):
                for k in ("logo", "crest", "image", "badge"):
                    val = obj.get(k)
                    if isinstance(val, str) and val.startswith("http"):
                        return val
                if obj.get("id"):
                    tid = obj.get("id")
            if tid:
                return f"https://sports.bzzoiro.com/img/team/{tid}/"
            return ""
        logo_home = _logo_url(match, "home", home_tid)
        logo_away = _logo_url(match, "away", away_tid)
        name = f"{home} — {away}"
        event_id = match.get("id") or match.get("event_id")
        try:
            home_tid_i = int(home_tid) if home_tid is not None else None
        except (TypeError, ValueError):
            home_tid_i = None
        try:
            away_tid_i = int(away_tid) if away_tid is not None else None
        except (TypeError, ValueError):
            away_tid_i = None
        try:
            event_id_i = int(event_id) if event_id is not None else None
        except (TypeError, ValueError):
            event_id_i = None
        db_execute(
            "INSERT INTO parsed_matches (name, country, country_en, league, league_id, match_time, match_date, priority, logo_home, logo_away, event_id, home_team_id, away_team_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, country_en, country_en, league_name, league_id, match_time, date_str, priority,
             logo_home or "", logo_away or "", event_id_i, home_tid_i, away_tid_i)
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
            "SELECT id, name, match_time, country, league, league_id, COALESCE(logo_home,''), COALESCE(logo_away,'') FROM parsed_matches "
            "WHERE league_id = ? ORDER BY match_time ASC LIMIT ? OFFSET ?",
            (league_id, per_page, page * per_page)
        )
    else:
        total = db_fetchone("SELECT COUNT(*) FROM parsed_matches")[0]
        rows = db_fetchall(
            "SELECT id, name, match_time, country, league, league_id, COALESCE(logo_home,''), COALESCE(logo_away,'') FROM parsed_matches "
            "ORDER BY priority ASC, match_time ASC LIMIT ? OFFSET ?",
            (per_page, page * per_page)
        )
    total_pages = max(1, (total + per_page - 1) // per_page)
    return {
        "matches": [
            {
                "id": r[0], "name": r[1], "match_time": r[2] or "",
                "country": r[3], "league": r[4], "league_id": r[5],
                "logo_home": r[6] or "", "logo_away": r[7] or "",
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

    async def _insert_from_parsed(row_full):
        """row: name, country, league, match_time, logo_home, logo_away, event_id, home_tid, away_tid, league_id"""
        nonlocal count
        name, country, league, match_time, logo_home, logo_away = row_full[0], row_full[1], row_full[2], row_full[3], row_full[4], row_full[5]
        event_id = row_full[6] if len(row_full) > 6 else None
        home_tid = row_full[7] if len(row_full) > 7 else None
        away_tid = row_full[8] if len(row_full) > 8 else None
        league_id = row_full[9] if len(row_full) > 9 else None
        existing = db_fetchone("SELECT id FROM matches WHERE name = ? AND league = ?", (name, league))
        stats_json = ""
        # resolve event if missing
        if (not event_id) and BSD_API_KEY:
            try:
                async with aiohttp.ClientSession() as session:
                    eid, hid, aid = await resolve_event_id(session, name, match_time or "", league_id)
                    if eid:
                        event_id = eid
                        home_tid = home_tid or hid
                        away_tid = away_tid or aid
            except Exception as e:
                logger.warning("[resolve] %s: %s", name, e)
        if event_id and BSD_API_KEY:
            try:
                async with aiohttp.ClientSession() as session:
                    pack = await fetch_match_enrichment(session, int(event_id), home_tid, away_tid, league_id)
                    stats_json = json.dumps(pack, ensure_ascii=False)
            except Exception as e:
                logger.warning("[enrich] %s: %s", name, e)
        if existing:
            # update stats on already-added match
            if stats_json or event_id:
                db_execute(
                    "UPDATE matches SET event_id = COALESCE(?, event_id), home_team_id = COALESCE(?, home_team_id), "
                    "away_team_id = COALESCE(?, away_team_id), stats_json = CASE WHEN ? != '' THEN ? ELSE stats_json END, "
                    "logo_home = CASE WHEN ? != '' THEN ? ELSE logo_home END, "
                    "logo_away = CASE WHEN ? != '' THEN ? ELSE logo_away END WHERE id = ?",
                    (event_id, home_tid, away_tid, stats_json, stats_json,
                     logo_home or "", logo_home or "", logo_away or "", logo_away or "", existing[0])
                )
                count += 1
            return True
        db_execute(
            "INSERT INTO matches (name, country, league, match_time, text, created_at, logo_home, logo_away, event_id, home_team_id, away_team_id, stats_json) "
            "VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)",
            (name, country, league, match_time or "", now, logo_home or "", logo_away or "",
             event_id, home_tid, away_tid, stats_json)
        )
        count += 1
        return True

    select_cols = (
        "name, country, league, match_time, COALESCE(logo_home,''), COALESCE(logo_away,''), "
        "event_id, home_team_id, away_team_id, league_id"
    )

    if mode == "single":
        parsed_id = data.get("parsed_id")
        row = db_fetchone(f"SELECT {select_cols} FROM parsed_matches WHERE id = ?", (parsed_id,))
        if not row:
            raise HTTPException(404, "Матч не найден")
        ok = await _insert_from_parsed(row)
        if not ok:
            return {"ok": False, "message": "⚠️ Этот матч уже добавлен"}
        log_admin(user["id"], "parser_add_match", row[0])

    elif mode == "league":
        league_id = data.get("league_id")
        rows = db_fetchall(f"SELECT {select_cols} FROM parsed_matches WHERE league_id = ?", (league_id,))
        for r in rows:
            await _insert_from_parsed(r)
            await asyncio.sleep(0.15)  # soft rate-limit
        league_row = db_fetchone("SELECT league FROM parsed_matches WHERE league_id = ?", (league_id,))
        log_admin(user["id"], "parser_add_league", f"{league_row[0] if league_row else ''} ({count})")

    else:
        limit = int(data.get("limit", 80))
        rows = db_fetchall(
            f"SELECT {select_cols} FROM parsed_matches ORDER BY priority ASC LIMIT ?",
            (limit,)
        )
        for r in rows:
            await _insert_from_parsed(r)
            await asyncio.sleep(0.15)
        log_admin(user["id"], "parser_add_all", f"{count} матчей")

    return {"ok": True, "count": count, "message": f"✅ Добавлено {count} матчей (со статистикой)"}


@app.post("/api/admin/matches/refresh-stats")
async def admin_refresh_stats(data: dict = None, user: dict = Depends(require_admin)):
    """Re-fetch stats for matches. Optional match_id, or all without stats."""
    data = data or {}
    match_id = data.get("match_id")
    if match_id:
        rows = db_fetchall(
            "SELECT id, name, match_time, event_id, home_team_id, away_team_id FROM matches WHERE id = ?",
            (match_id,)
        )
    else:
        rows = db_fetchall(
            "SELECT id, name, match_time, event_id, home_team_id, away_team_id FROM matches "
            "WHERE stats_json IS NULL OR stats_json = '' OR stats_json = '{}' ORDER BY id DESC LIMIT 40"
        )
    if not rows:
        return {"ok": True, "count": 0, "message": "Нет матчей для обновления"}
    if not BSD_API_KEY or not BSD_API_KEY.strip():
        raise HTTPException(400, "BSD_API_KEY не задан")

    updated = 0
    async with aiohttp.ClientSession() as session:
        for r in rows:
            mid, name, mtime, eid, hid, aid = r[0], r[1], r[2], r[3], r[4], r[5]
            try:
                if not eid:
                    eid, hid2, aid2 = await resolve_event_id(session, name, mtime or "")
                    hid = hid or hid2
                    aid = aid or aid2
                if not eid:
                    continue
                pack = await fetch_match_enrichment(session, int(eid), hid, aid, None)
                db_execute(
                    "UPDATE matches SET event_id = ?, home_team_id = ?, away_team_id = ?, stats_json = ? WHERE id = ?",
                    (eid, hid, aid, json.dumps(pack, ensure_ascii=False), mid)
                )
                updated += 1
                await asyncio.sleep(0.2)
            except Exception as e:
                logger.warning("[refresh-stats] %s: %s", name, e)
    log_admin(user["id"], "refresh_stats", f"{updated} матчей")
    return {"ok": True, "count": updated, "message": f"✅ Обновлена статистика: {updated} матчей"}


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


# ====================== NEWS ======================

@app.get("/api/news")
async def api_news(page: int = 0, user: dict = Depends(get_current_user)):
    per_page = 10
    total = db_fetchone("SELECT COUNT(*) FROM news")[0]
    total_pages = max(1, (total + per_page - 1) // per_page) if total else 1
    page = max(0, min(page, total_pages - 1))
    rows = db_fetchall(
        "SELECT id, title, text, created_at FROM news ORDER BY id DESC LIMIT ? OFFSET ?",
        (per_page, page * per_page)
    )
    # global numbers: newest is highest number
    # number = total - offset - index
    news_list = []
    for i, r in enumerate(rows):
        num = total - (page * per_page) - i
        news_list.append({
            "id": r[0], "number": num, "title": r[1] or "", "text": r[2] or "",
            "created_at": r[3] or "", "author": "Администрация проекта"
        })
    return {"news": news_list, "page": page, "total_pages": total_pages, "total": total}

@app.get("/api/admin/news")
async def admin_news_list(user: dict = Depends(require_admin)):
    rows = db_fetchall("SELECT id, title, text, created_at FROM news ORDER BY id DESC")
    return {"news": [{"id": r[0], "title": r[1] or "", "text": r[2] or "", "created_at": r[3] or ""} for r in rows]}

@app.post("/api/admin/news")
async def admin_news_add(data: dict, user: dict = Depends(require_admin)):
    text = (data.get("text") or "").strip()
    title = (data.get("title") or "").strip()
    if not text:
        raise HTTPException(400, "Текст новости обязателен")
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    db_execute("INSERT INTO news (title, text, created_at) VALUES (?, ?, ?)", (title, text, now))
    log_admin(user["id"], "add_news", title or text[:40])
    return {"ok": True, "message": "✅ Новость добавлена"}

@app.delete("/api/admin/news/{news_id}")
async def admin_news_delete(news_id: int, user: dict = Depends(require_admin)):
    db_execute("DELETE FROM news WHERE id = ?", (news_id,))
    log_admin(user["id"], "delete_news", str(news_id))
    return {"ok": True, "message": "✅ Новость удалена"}

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
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
