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
BOT_USERNAME = os.getenv("BOT_USERNAME", "Koefrubot")

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
    "UEFA Champions League": 2, "Champions League": 2,
    "UEFA Europa League": 2, "Europa League": 2,
    "UEFA Europa Conference League": 2, "UEFA Conference League": 2,
    "Conference League": 2, "Europa Conference League": 2,
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

# Подстроки для euro-кубков (API часто шлёт разные названия)
_EURO_CUP_PRIORITY_2 = (
    "champions league",
    "europa league",
    "conference league",
    "liga campeones",
    "liga europa",
    "liga conferencia",
)

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

    # matches: league_id + league_logo for group display
    for col, typ in [
        ("league_id", "INTEGER DEFAULT NULL"),
        ("league_logo", "TEXT DEFAULT ''"),
    ]:
        try:
            c.execute(f"ALTER TABLE matches ADD COLUMN {col} {typ}")
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
    try:
        rows = db_fetchall(
            "SELECT id, name, country, league, match_time, COALESCE(logo_home,''), COALESCE(logo_away,''), "
            "league_id, COALESCE(league_logo,'') FROM matches ORDER BY country, league, match_time"
        )
        has_logo = True
    except Exception:
        rows = db_fetchall(
            "SELECT id, name, country, league, match_time, COALESCE(logo_home,''), COALESCE(logo_away,'') "
            "FROM matches ORDER BY country, league, match_time"
        )
        has_logo = False
    groups = {}
    group_meta = {}  # key -> league_logo
    for r in rows:
        key = f"{r[2]}|{r[3]}"
        groups.setdefault(key, []).append({
            "id": r[0], "name": r[1], "country": r[2], "league": r[3], "match_time": r[4] or "",
            "logo_home": r[5] or "", "logo_away": r[6] or ""
        })
        if has_logo and len(r) > 8 and r[8] and key not in group_meta:
            group_meta[key] = r[8]
        elif has_logo and len(r) > 7 and r[7] and key not in group_meta:
            group_meta[key] = f"https://sports.bzzoiro.com/img/league/{r[7]}/"

    result = []
    for key, matches in sorted(groups.items(), key=lambda x: (
        get_league_priority(x[0].split("|")[1] if "|" in x[0] else x[0]),
        x[0]
    )):
        country, league = key.split("|", 1)
        result.append({
            "country": country,
            "league": league,
            "flag": get_flag(country),
            "league_logo": group_meta.get(key, ""),
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
        ms = st.get("match_stats") or {}
        for side in ("home", "away"):
            if (ms.get(side) or {}).get("matches"):
                return True
        return False

    event_id = row[8] if len(row) > 8 else None
    home_tid = row[9] if len(row) > 9 else None
    away_tid = row[10] if len(row) > 10 else None
    match_name = row[1] or ""
    match_time = row[4] or ""

    def _needs_new_blocks(st: dict) -> bool:
        """Old stats_json without match_stats / betting / model must be re-fetched."""
        if not st:
            return True
        ms = st.get("match_stats") or {}
        has_ms = bool((ms.get("home") or {}).get("matches") or (ms.get("away") or {}).get("matches"))
        if "match_stats" not in st:
            return True
        if not has_ms:
            return True
        betting = st.get("betting") or {}
        has_bet = bool((betting.get("home") or {}).get("matches") or (betting.get("away") or {}).get("matches"))
        if not has_bet:
            return True
        # model prediction + rest days are new deep blocks
        if not st.get("model") and not st.get("forecast"):
            return True
        form = st.get("form") or {}
        if not (form.get("home") or {}).get("home") and not (form.get("away") or {}).get("home"):
            if (form.get("home") or {}).get("form") or (form.get("away") or {}).get("form"):
                return True
        return False

    need_enrich = (not _has_useful(stats)) or bool(refresh) or _needs_new_blocks(stats)
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
        # Same naming as lineups/squad block: short_name (initials + surname) preferred
        name = p.get("short_name") or p.get("name") or "?"
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


def _is_attacker_pos(pos: str) -> bool:
    if not pos:
        return False
    p = str(pos).upper().strip()
    if p in ("F", "FW", "ST", "CF", "SS", "LF", "RF", "ATT", "FORWARD", "STRIKER"):
        return True
    if "FORWARD" in p or "STRIKER" in p or "ATTACK" in p or "НАП" in p or "ФОРВ" in p:
        return True
    # pure W often midfield — count only if clearly wing forward
    if p in ("LW", "RW") or p.endswith("W"):
        return True
    return False


def _count_missing_by_role(injuries: list, role_fn) -> tuple:
    """Return (count, names) for injured/suspended(/doubtful) matching role_fn(pos)."""
    found = []
    for p in injuries or []:
        if not isinstance(p, dict):
            continue
        if not role_fn(p.get("position") or ""):
            continue
        st = (p.get("status") or "").lower()
        found.append((0 if st in ("injured", "suspended") else 1, p.get("name") or "?"))
    found.sort()
    names = [n for _, n in found]
    return len(names), names


def _count_missing_defenders(injuries: list) -> tuple:
    return _count_missing_by_role(injuries, _is_defender_pos)


def _count_missing_attackers(injuries: list) -> tuple:
    return _count_missing_by_role(injuries, _is_attacker_pos)


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

    def _num(v):
        if isinstance(v, dict):
            for k in ("price", "odds", "value", "coef", "decimal"):
                if v.get(k) is not None:
                    return _safe_float(v.get(k))
            return None
        return _safe_float(v)

    # flat keys (consensus endpoint)
    flat_map = {
        "home_win": ["home_win", "home", "1", "odds_home"],
        "draw": ["draw", "x", "X", "odds_draw"],
        "away_win": ["away_win", "away", "2", "odds_away"],
        "over_15": ["over_15_goals", "over_15", "over15", "over_1_5"],
        "under_15": ["under_15_goals", "under_15", "under15", "under_1_5"],
        "over_25": ["over_25_goals", "over_25", "over25", "over_2_5"],
        "under_25": ["under_25_goals", "under_25", "under25", "under_2_5"],
        "over_35": ["over_35_goals", "over_35", "over35", "over_3_5"],
        "under_35": ["under_35_goals", "under_35", "under35", "under_3_5"],
        "btts_yes": ["btts_yes", "btts_y", "gg", "both_score_yes"],
        "btts_no": ["btts_no", "btts_n", "ng", "both_score_no"],
    }
    for key, aliases in flat_map.items():
        for a in aliases:
            f = _num(bag.get(a))
            if f and f > 1.01:
                out[key] = round(f, 2)
                break

    # nested shapes
    mw = bag.get("match_winner") if isinstance(bag.get("match_winner"), dict) else {}
    for key, src in (("home_win", "home"), ("draw", "draw"), ("away_win", "away")):
        if key not in out:
            f = _num(mw.get(src))
            if f and f > 1.01:
                out[key] = round(f, 2)

    ou = bag.get("over_under") if isinstance(bag.get("over_under"), dict) else {}
    for key in ("over_15", "under_15", "over_25", "under_25", "over_35", "under_35"):
        if key in out:
            continue
        f = _num(ou.get(key)) or _num(ou.get(key.replace("_", "")))
        if not f:
            # over_15 -> over_1.5 style
            alt = key.replace("over_", "over_").replace("15", "1.5").replace("25", "2.5").replace("35", "3.5")
            f = _num(ou.get(alt))
        if f and f > 1.01:
            out[key] = round(f, 2)

    btts = bag.get("btts") if isinstance(bag.get("btts"), dict) else {}
    if "btts_yes" not in out:
        f = _num(btts.get("yes")) or _num(btts.get("btts_yes"))
        if f and f > 1.01:
            out["btts_yes"] = round(f, 2)
    if "btts_no" not in out:
        f = _num(btts.get("no")) or _num(btts.get("btts_no"))
        if f and f > 1.01:
            out["btts_no"] = round(f, 2)

    return out


def _coef_from_prob(p: float, lo=1.60, hi=2.70) -> Optional[float]:
    """Fair-ish decimal odds from probability, clamped to corridor if possible."""
    if p is None:
        return None
    try:
        p = float(p)
    except (TypeError, ValueError):
        return None
    if p > 1.5:
        p = p / 100.0
    if p <= 0.05 or p >= 0.95:
        return None
    raw = 1.0 / p
    # soft margin ~5%
    raw = raw * 0.95
    if raw < lo or raw > hi:
        # still return if close — clamp only when within 15% of corridor
        if raw < lo * 0.85 or raw > hi * 1.15:
            return None
        raw = max(lo, min(hi, raw))
    return round(raw, 2)


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
    corners = markets.get("corners") or {}
    if isinstance(corners, dict):
        out["prob_corners_over_85"] = _safe_float(corners.get("prob_over_85") or corners.get("over_85"))
        out["prob_corners_over_95"] = _safe_float(corners.get("prob_over_95") or corners.get("over_95"))
        out["prob_corners_over_105"] = _safe_float(corners.get("prob_over_105") or corners.get("over_105"))
    model = pred_data.get("model") if isinstance(pred_data.get("model"), dict) else {}
    if model:
        out["confidence"] = _safe_float(model.get("confidence"))
        out["model_version"] = model.get("version")
    rec = pred_data.get("recommendations") if isinstance(pred_data.get("recommendations"), dict) else {}
    if rec:
        out["rec_favorite"] = rec.get("favorite")
        out["rec_favorite_prob"] = _safe_float(rec.get("favorite_prob"))
        out["rec_over_25"] = rec.get("over_25")
        out["rec_btts"] = rec.get("btts")
    return out


def _compute_rest_days(last_matches: list, kickoff_iso: str = None) -> Optional[int]:
    """Days since last competitive match relative to this fixture kickoff."""
    if not last_matches:
        return None
    last_date = None
    for m in last_matches:
        d = (m.get("date") or "")[:10]
        if d and len(d) >= 10:
            last_date = d
            break
    if not last_date:
        return None
    try:
        from datetime import date as _date
        last_d = _date.fromisoformat(last_date)
        if kickoff_iso:
            kick = str(kickoff_iso)[:10]
            if len(kick) >= 10:
                kick_d = _date.fromisoformat(kick)
            else:
                kick_d = datetime.now(tz).date()
        else:
            kick_d = datetime.now(tz).date()
        delta = (kick_d - last_d).days
        return max(0, delta) if delta is not None else None
    except Exception:
        return None


def _compute_ht_rates(last_matches: list, team_id: int = None) -> dict:
    """Half-time over rates and HT lead conversion from recent matches."""
    if not last_matches:
        return {}
    n = 0
    ht_over05 = ht_over15 = 0
    ht_lead = ft_win_after_lead = 0
    for m in last_matches:
        if not isinstance(m, dict):
            continue
        ht_h, ht_a = m.get("home_score_ht"), m.get("away_score_ht")
        if ht_h is None or ht_a is None:
            continue
        try:
            ht_h, ht_a = int(ht_h), int(ht_a)
            hs, aws = int(m.get("home_score")), int(m.get("away_score"))
        except (TypeError, ValueError):
            continue
        n += 1
        ht_total = ht_h + ht_a
        if ht_total >= 1:
            ht_over05 += 1
        if ht_total >= 2:
            ht_over15 += 1
        hid = m.get("home_team_id")
        aid = m.get("away_team_id")
        is_home = team_id is not None and hid is not None and int(hid) == int(team_id)
        is_away = team_id is not None and aid is not None and int(aid) == int(team_id)
        if is_home and ht_h > ht_a:
            ht_lead += 1
            if hs > aws:
                ft_win_after_lead += 1
        elif is_away and ht_a > ht_h:
            ht_lead += 1
            if aws > hs:
                ft_win_after_lead += 1
    if n == 0:
        return {}
    out = {
        "matches_ht": n,
        "ht_over_05_pct": round(100.0 * ht_over05 / n, 1),
        "ht_over_15_pct": round(100.0 * ht_over15 / n, 1),
    }
    if ht_lead:
        out["ht_lead_n"] = ht_lead
        out["ht_lead_converted_pct"] = round(100.0 * ft_win_after_lead / ht_lead, 1)
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
    Uses form, xG, goal segments, injuries (defenders + attackers), model probs, live odds.
    If live odds missing — estimates coef from model probability.
    """
    COEF_MIN, COEF_MAX = 1.60, 2.70
    odds = _parse_odds_bag(odds_raw or {})
    pred = _parse_prediction_bag(pred_raw or {})

    def _tname(v, fallback):
        if isinstance(v, dict):
            return (v.get("name") or v.get("short_name") or fallback).strip()
        s = str(v or "").strip()
        return s or fallback

    home_name = _tname(pack.get("home_team"), "Хозяева")
    away_name = _tname(pack.get("away_team"), "Гости")

    form_h = (pack.get("form") or {}).get("home") or {}
    form_a = (pack.get("form") or {}).get("away") or {}
    xg_h = (pack.get("xg") or {}).get("home") or {}
    xg_a = (pack.get("xg") or {}).get("away") or {}
    gs_h = (pack.get("goal_segments") or {}).get("home") or {}
    gs_a = (pack.get("goal_segments") or {}).get("away") or {}
    inj_h = (pack.get("injuries") or {}).get("home") or []
    inj_a = (pack.get("injuries") or {}).get("away") or []

    def_h_n, def_h_names = _count_missing_defenders(inj_h)
    def_a_n, def_a_names = _count_missing_defenders(inj_a)
    att_h_n, att_h_names = _count_missing_attackers(inj_h)
    att_a_n, att_a_names = _count_missing_attackers(inj_a)

    xg_avg_h = _safe_float(xg_h.get("avg")) or _safe_float(pred.get("xg_home"))
    xg_avg_a = _safe_float(xg_a.get("avg")) or _safe_float(pred.get("xg_away"))
    # attackers out → lower expected goals for that side
    if xg_avg_h is not None and att_h_n >= 1:
        xg_avg_h = max(0.2, xg_avg_h - (0.25 if att_h_n == 1 else 0.45))
    if xg_avg_a is not None and att_a_n >= 1:
        xg_avg_a = max(0.2, xg_avg_a - (0.25 if att_a_n == 1 else 0.45))

    gf_h = _safe_float(form_h.get("goals_for"), 0) or 0
    ga_h = _safe_float(form_h.get("goals_against"), 0) or 0
    gf_a = _safe_float(form_a.get("goals_for"), 0) or 0
    ga_a = _safe_float(form_a.get("goals_against"), 0) or 0
    mc_h = max(int(form_h.get("matches_count") or xg_h.get("matches") or 1), 1)
    mc_a = max(int(form_a.get("matches_count") or xg_a.get("matches") or 1), 1)
    gpg_h, gapg_h = gf_h / mc_h, ga_h / mc_h
    gpg_a, gapg_a = gf_a / mc_a, ga_a / mc_a
    if att_h_n >= 1:
        gpg_h = max(0.15, gpg_h * (0.75 if att_h_n == 1 else 0.55))
    if att_a_n >= 1:
        gpg_a = max(0.15, gpg_a * (0.75 if att_a_n == 1 else 0.55))

    candidates = []

    def add_cand(key, score, why_lines, est_prob=None):
        coef = odds.get(key)
        estimated = False
        if coef is None and est_prob is not None:
            coef = _coef_from_prob(est_prob, COEF_MIN, COEF_MAX)
            estimated = True
        if coef is None and score >= 52:
            # soft default inside corridor so block is not empty
            coef = 1.85
            estimated = True
        if coef is None:
            return
        if not (COEF_MIN <= float(coef) <= COEF_MAX):
            return
        why = list(why_lines or [])
        if estimated:
            why.append("Коэффициент ориентировочный (нет котировки в ленте) — сверьте в БК.")
        candidates.append({
            "key": key,
            "label": MARKET_LABELS.get(key, key),
            "coef": round(float(coef), 2),
            "score": score,
            "why": why,
            "estimated": estimated,
        })

    # --- 1X2 signals ---
    def _norm_p(v):
        if v is None:
            return None
        v = float(v)
        return v / 100.0 if v > 1.5 else v

    ph = _norm_p(pred.get("prob_home"))
    pd_ = _norm_p(pred.get("prob_draw"))
    pa = _norm_p(pred.get("prob_away"))

    att_why_h = []
    att_why_a = []
    if att_h_n >= 1:
        att_why_h.append(
            f"Атака {home_name}: вне {att_h_n} нападающ. ({', '.join(att_h_names[:2])}) — забьют меньше обычного."
        )
    if att_a_n >= 1:
        att_why_a.append(
            f"Атака {away_name}: вне {att_a_n} нападающ. ({', '.join(att_a_names[:2])}) — забьют меньше обычного."
        )

    if ph is not None:
        implied = 1.0 / odds["home_win"] if odds.get("home_win") else None
        edge = (ph - implied) if implied else 0
        why = []
        if ph >= 0.40:
            why.append(f"Модель оценивает победу {home_name} ≈ {ph*100:.0f}%.")
        if xg_avg_h is not None and xg_avg_a is not None and xg_avg_h > xg_avg_a + 0.15:
            why.append(f"xG: {home_name} {xg_avg_h:.2f} vs {away_name} {xg_avg_a:.2f}.")
        if form_h.get("won", 0) >= form_h.get("lost", 0) + 2:
            why.append(f"Форма хозяев: {form_h.get('won',0)}В / {form_h.get('drawn',0)}Н / {form_h.get('lost',0)}П.")
        if def_a_n >= 2:
            why.append(f"У {away_name} вне ≥2 защитника ({', '.join(def_a_names[:2])}) — выше шанс пропустить.")
        if att_a_n >= 2:
            why.extend(att_why_a)  # weaker away attack → home win more likely
        # penalize if home attackers missing
        score = 50 + ph * 40 + max(edge, 0) * 80 - (12 if att_h_n >= 2 else (6 if att_h_n == 1 else 0))
        if att_h_n >= 1:
            why.extend(att_why_h)
        if why and score >= 40:
            add_cand("home_win", score, why, est_prob=ph)

    if pa is not None:
        implied = 1.0 / odds["away_win"] if odds.get("away_win") else None
        edge = (pa - implied) if implied else 0
        why = []
        if pa >= 0.36:
            why.append(f"Модель: победа {away_name} ≈ {pa*100:.0f}%.")
        if xg_avg_a is not None and xg_avg_h is not None and xg_avg_a > xg_avg_h + 0.15:
            why.append(f"xG гостей выше: {xg_avg_a:.2f} против {xg_avg_h:.2f}.")
        if def_h_n >= 2:
            why.append(f"У {home_name} вне ≥2 защитника ({', '.join(def_h_names[:2])}) — оборона ослаблена.")
        if att_h_n >= 2:
            why.extend(att_why_h)
        score = 48 + pa * 40 + max(edge, 0) * 80 - (12 if att_a_n >= 2 else (6 if att_a_n == 1 else 0))
        if att_a_n >= 1:
            why.extend(att_why_a)
        if why and score >= 40:
            add_cand("away_win", score, why, est_prob=pa)

    if pd_ is not None:
        why = []
        if pd_ >= 0.26:
            why.append(f"Модель даёт ничьей шанс ≈ {pd_*100:.0f}%.")
        if abs((xg_avg_h or 0) - (xg_avg_a or 0)) < 0.2 and xg_avg_h is not None:
            why.append("xG команд близки — возможен обмен очками.")
        if att_h_n >= 1 and att_a_n >= 1:
            why.append("У обеих команд ослаблена атака — ниже темп, выше шанс ничьей.")
        if why:
            add_cand("draw", 40 + pd_ * 50, why, est_prob=pd_)

    # --- totals ---
    exp_total = None
    if xg_avg_h is not None and xg_avg_a is not None:
        exp_total = xg_avg_h + xg_avg_a
    elif gpg_h or gpg_a:
        exp_total = (gpg_h + gapg_h + gpg_a + gapg_a) / 2.0

    def _seg_total(gs, key):
        sc = (gs.get(key) or {})
        return sum(int(v or 0) for v in sc.values()) if isinstance(sc, dict) else 0

    scored_h = _seg_total(gs_h, "scored")
    scored_a = _seg_total(gs_a, "scored")

    def_boost_over = 0
    def_why = []
    if def_h_n >= 2:
        def_boost_over += 8
        def_why.append(f"Оборона {home_name}: вне {def_h_n} защит. ({', '.join(def_h_names[:2])}) — выше риск пропустить.")
    if def_a_n >= 2:
        def_boost_over += 8
        def_why.append(f"Оборона {away_name}: вне {def_a_n} защит. ({', '.join(def_a_names[:2])}) — выше риск пропустить.")

    att_boost_under = 0
    att_why = []
    if att_h_n >= 1:
        att_boost_under += 6 if att_h_n == 1 else 12
        att_why.extend(att_why_h)
    if att_a_n >= 1:
        att_boost_under += 6 if att_a_n == 1 else 12
        att_why.extend(att_why_a)

    p_o25 = _norm_p(pred.get("prob_over_25"))
    p_o15 = _norm_p(pred.get("prob_over_15"))
    p_btts = _norm_p(pred.get("prob_btts_yes"))

    # OVER signals (weaker if attackers missing on both sides)
    over_ok = True
    if att_h_n >= 2 and att_a_n >= 2:
        over_ok = False

    if over_ok and ((exp_total is not None and exp_total >= 2.35) or (p_o25 and p_o25 >= 0.48) or def_boost_over):
        why = []
        if exp_total is not None:
            why.append(f"Ожидаемый тотал по xG/форме ≈ {exp_total:.2f} гола.")
        if p_o25:
            why.append(f"Модель: ТБ 2.5 ≈ {p_o25*100:.0f}%.")
        if scored_h + scored_a >= 20:
            why.append("Голевые отрезки: обе команды регулярно забивают.")
        why.extend(def_why)
        score = 55 + def_boost_over + (10 if exp_total and exp_total >= 2.6 else 0) - att_boost_under * 0.5
        add_cand("over_25", score, why or ["Статистика указывает на результативный матч."], est_prob=p_o25 or 0.52)

    if over_ok and ((exp_total is not None and exp_total >= 1.7) or (p_o15 and p_o15 >= 0.65)):
        why = []
        if exp_total is not None:
            why.append(f"Суммарный xG/форма ≈ {exp_total:.2f} — минимум 2 гола вероятны.")
        if p_o15:
            why.append(f"Модель: ТБ 1.5 ≈ {p_o15*100:.0f}%.")
        why.extend(def_why[:1])
        add_cand("over_15", 52 + def_boost_over * 0.5, why or ["Высокая вероятность ≥2 голов."], est_prob=p_o15 or 0.70)

    # UNDER — stronger when attackers out
    if (exp_total is not None and exp_total <= 2.25) or att_boost_under >= 6:
        why = []
        if exp_total is not None:
            why.append(f"Ожидаемый тотал умеренный (≈ {exp_total:.2f}).")
        why.extend(att_why)
        if gapg_h < 1.1 and gapg_a < 1.1:
            why.append("Обе команды в среднем мало пропускают.")
        add_cand("under_25", 50 + att_boost_under, why or ["Низкий атакующий потенциал."], est_prob=0.55)
        add_cand("under_35", 48 + att_boost_under * 0.7, (why or []) + ["ТМ 3.5 — страховка от всплеска."], est_prob=0.62)

    if over_ok and exp_total is not None and exp_total >= 2.8:
        why = [f"Высокий ожидаемый тотал ≈ {exp_total:.2f}."]
        why.extend(def_why)
        add_cand("over_35", 50 + def_boost_over - att_boost_under * 0.3, why, est_prob=0.42)

    # BTTS
    both_score_signal = (
        (gpg_h >= 0.9 and gpg_a >= 0.9)
        or (p_btts and p_btts >= 0.50)
        or def_boost_over >= 8
    ) and not (att_h_n >= 2 or att_a_n >= 2)
    if both_score_signal:
        why = []
        if gpg_h and gpg_a:
            why.append(f"Результативность: {home_name} {gpg_h:.1f} и {away_name} {gpg_a:.1f} гола/матч.")
        if p_btts:
            why.append(f"Модель: обе забьют ≈ {p_btts*100:.0f}%.")
        why.extend(def_why)
        add_cand("btts_yes", 54 + def_boost_over - att_boost_under * 0.4, why or ["Обе команды способны забить."], est_prob=p_btts or 0.52)

    if (att_h_n >= 2 or att_a_n >= 2) or ((gapg_h < 0.9 or gapg_a < 0.9) and not def_boost_over):
        why = []
        why.extend(att_why)
        if not why:
            why.append("Одна из команд стабильно «сушит» игру — обе могут не забить.")
        add_cand("btts_no", 48 + att_boost_under, why, est_prob=0.50)

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
            c1 = score_map[k1].get("coef") or odds.get(k1)
            c2 = score_map[k2].get("coef") or odds.get(k2)
            if not c1 or not c2:
                continue
            prod = round(float(c1) * float(c2), 2)
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
        "odds_count": len(odds),
        "model": {k: pred[k] for k in pred if k.startswith("prob_") or k.startswith("xg_") or k in ("predicted_1x2", "most_likely_score")},
        "defenders_out": {
            "home": {"count": def_h_n, "names": def_h_names[:4]},
            "away": {"count": def_a_n, "names": def_a_names[:4]},
        },
        "attackers_out": {
            "home": {"count": att_h_n, "names": att_h_names[:4]},
            "away": {"count": att_a_n, "names": att_a_names[:4]},
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


def _parse_form_side(side_data: dict) -> dict:
    """Parse one form bucket (overall / home / away)."""
    if not side_data or not isinstance(side_data, dict):
        return {}
    xg_info = _extract_xg_from_stats_block(side_data.get("stats") or {})
    if not xg_info:
        for k in ("xg", "expected_goals", "avg_xg", "xg_for"):
            v = side_data.get(k)
            if isinstance(v, (int, float)):
                xg_info = {"avg": float(v), "total": None, "matches": side_data.get("matches")}
                break
            if isinstance(v, dict) and (v.get("average") is not None or v.get("actual") is not None):
                xg_info = {
                    "avg": float(v.get("average") or v.get("actual")),
                    "total": float(v["total"]) if v.get("total") is not None else None,
                    "matches": v.get("matches") or side_data.get("matches"),
                }
                break
    stats_avg = {}
    raw_stats = side_data.get("stats") or {}
    if isinstance(raw_stats, dict):
        for key, aliases in (
            ("possession", ("ball_possession", "possession")),
            ("shots", ("total_shots", "shots", "shots_total")),
            ("shots_on", ("shots_on_target", "shots_on", "on_target")),
            ("corners", ("corner_kicks", "corners")),
            ("yellow", ("yellow_cards", "yellowcards", "yellow")),
            ("fouls", ("fouls", "fouls_committed")),
        ):
            for a in aliases:
                block = raw_stats.get(a)
                if isinstance(block, dict) and block.get("average") is not None:
                    try:
                        stats_avg[key] = round(float(block["average"]), 2)
                    except (TypeError, ValueError):
                        pass
                    break
                if isinstance(block, (int, float)):
                    stats_avg[key] = round(float(block), 2)
                    break
    matches = side_data.get("matches") or 0
    gf = side_data.get("goals_for", 0) or 0
    ga = side_data.get("goals_against", 0) or 0
    return {
        "form": side_data.get("form") or "",
        "won": side_data.get("won", 0) or 0,
        "drawn": side_data.get("drawn", 0) or 0,
        "lost": side_data.get("lost", 0) or 0,
        "goals_for": gf,
        "goals_against": ga,
        "matches_count": matches,
        "points_per_match": side_data.get("points_per_match"),
        "gpg": round(gf / matches, 2) if matches else None,
        "gapg": round(ga / matches, 2) if matches else None,
        "xg_avg": xg_info.get("avg"),
        "xg_total": xg_info.get("total"),
        "xg_matches": xg_info.get("matches") or matches or 0,
        "stats_avg": stats_avg,
    }


def _parse_form(form_data) -> dict:
    if not form_data or not isinstance(form_data, dict):
        return {}
    overall = form_data.get("overall") or {}
    if not overall and form_data.get("form"):
        overall = form_data
    base = _parse_form_side(overall)
    base["team_name"] = form_data.get("team_name") or ""
    # Home / Away splits — critical for betting edge
    home_side = form_data.get("home") or {}
    away_side = form_data.get("away") or {}
    if home_side:
        base["home"] = _parse_form_side(home_side)
    if away_side:
        base["away"] = _parse_form_side(away_side)
    # vs stronger / weaker table positions — useful edge signal
    vs_s = form_data.get("vs_stronger") or {}
    vs_w = form_data.get("vs_weaker") or {}
    if vs_s:
        base["vs_stronger"] = _parse_form_side(vs_s)
    if vs_w:
        base["vs_weaker"] = _parse_form_side(vs_w)
    return base


def _compute_betting_rates(last_matches: list, team_id: int = None) -> dict:
    """
    Deep betting rates from recent finished matches:
    over_25, btts, clean_sheets, failed_to_score, avg goals, home/away splits.
    """
    if not last_matches:
        return {}
    n = 0
    over15 = over25 = over35 = btts = cs = fts = 0
    goals_for = goals_against = 0
    home_n = away_n = home_w = away_w = 0
    home_gf = home_ga = away_gf = away_ga = 0
    for m in last_matches:
        if not isinstance(m, dict):
            continue
        try:
            hs = int(m.get("home_score"))
            aws = int(m.get("away_score"))
        except (TypeError, ValueError):
            continue
        total = hs + aws
        n += 1
        if total >= 2:
            over15 += 1
        if total >= 3:
            over25 += 1
        if total >= 4:
            over35 += 1
        if hs > 0 and aws > 0:
            btts += 1
        hid = m.get("home_team_id")
        aid = m.get("away_team_id")
        is_home = (team_id is not None and hid is not None and int(hid) == int(team_id))
        is_away = (team_id is not None and aid is not None and int(aid) == int(team_id))
        if team_id is None:
            gf, ga = hs, aws
        elif is_home:
            gf, ga = hs, aws
            home_n += 1
            home_gf += hs
            home_ga += aws
            if hs > aws:
                home_w += 1
        elif is_away:
            gf, ga = aws, hs
            away_n += 1
            away_gf += aws
            away_ga += hs
            if aws > hs:
                away_w += 1
        else:
            gf, ga = hs, aws
        goals_for += gf
        goals_against += ga
        if ga == 0:
            cs += 1
        if gf == 0:
            fts += 1
    if n == 0:
        return {}
    def pct(x):
        return round(100.0 * x / n, 1)
    out = {
        "matches": n,
        "over_15_pct": pct(over15),
        "over_25_pct": pct(over25),
        "over_35_pct": pct(over35),
        "btts_pct": pct(btts),
        "clean_sheet_pct": pct(cs),
        "failed_to_score_pct": pct(fts),
        "avg_goals_for": round(goals_for / n, 2),
        "avg_goals_against": round(goals_against / n, 2),
        "avg_total_goals": round((goals_for + goals_against) / n, 2),
    }
    if home_n:
        out["home"] = {
            "matches": home_n,
            "win_pct": round(100.0 * home_w / home_n, 1),
            "avg_gf": round(home_gf / home_n, 2),
            "avg_ga": round(home_ga / home_n, 2),
        }
    if away_n:
        out["away"] = {
            "matches": away_n,
            "win_pct": round(100.0 * away_w / away_n, 1),
            "avg_gf": round(away_gf / away_n, 2),
            "avg_ga": round(away_ga / away_n, 2),
        }
    return out


def _is_friendly_match(m: dict) -> bool:
    """Skip club / international friendlies — only official competitive games."""
    if not isinstance(m, dict):
        return False
    name = (
        m.get("league")
        or m.get("league_name")
        or m.get("competition")
        or m.get("competition_name")
        or ""
    )
    if isinstance(name, dict):
        name = name.get("name") or name.get("title") or ""
    name = str(name).lower()
    if "friendly" in name or "friendlie" in name or "товарищ" in name:
        return True
    # Some feeds put type / stage
    stage = str(m.get("stage") or m.get("stage_name") or "").lower()
    if "friendly" in stage:
        return True
    return False


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
        if _is_friendly_match(m):
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

        def _first_num(*vals):
            """0 is a valid HT score — never use truthiness (or-chain)."""
            for v in vals:
                if v is None or v == "":
                    continue
                try:
                    return int(v)
                except (TypeError, ValueError):
                    continue
            return None

        sc_obj = m.get("score") if isinstance(m.get("score"), dict) else {}
        ht_obj = m.get("ht_score") if isinstance(m.get("ht_score"), dict) else {}
        period = m.get("period_scores") if isinstance(m.get("period_scores"), dict) else {}
        p1 = period.get("1") or period.get("first") or period.get("1st") or {}
        if not isinstance(p1, dict):
            p1 = {}
        ht_h = _first_num(
            m.get("home_score_ht"), m.get("ht_home_score"), m.get("home_ht_score"),
            m.get("score_ht_home"), m.get("home_score_period_1"),
            sc_obj.get("ht_home"), sc_obj.get("home_ht"), sc_obj.get("halftime_home"),
            sc_obj.get("home_halftime"), ht_obj.get("home"), ht_obj.get("home_score"),
            p1.get("home"), p1.get("home_score"),
        )
        ht_a = _first_num(
            m.get("away_score_ht"), m.get("ht_away_score"), m.get("away_ht_score"),
            m.get("score_ht_away"), m.get("away_score_period_1"),
            sc_obj.get("ht_away"), sc_obj.get("away_ht"), sc_obj.get("halftime_away"),
            sc_obj.get("away_halftime"), ht_obj.get("away"), ht_obj.get("away_score"),
            p1.get("away"), p1.get("away_score"),
        )
        out.append({
            "date": date,
            "home": str(home),
            "away": str(away),
            "score": score,
            "home_score": hs,
            "away_score": aws,
            "home_score_ht": ht_h,
            "away_score_ht": ht_a,
            "event_id": m.get("id") or m.get("event_id"),
            "home_team_id": m.get("home_team_id"),
            "away_team_id": m.get("away_team_id"),
            "league_id": m.get("league_id"),
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
        if v is None:
            continue
        # nested {actual: n} / {value: n}
        if isinstance(v, dict):
            for nk in ("actual", "value", "avg", "total", "count"):
                if v.get(nk) is not None:
                    return v.get(nk)
            continue
        return v
    # case-insensitive fallback
    lower_map = {str(k).lower(): v for k, v in block.items()}
    for k in keys:
        v = lower_map.get(str(k).lower())
        if v is None:
            continue
        if isinstance(v, dict):
            for nk in ("actual", "value", "avg", "total", "count"):
                if v.get(nk) is not None:
                    return v.get(nk)
            continue
        return v
    return default


def _team_block_from_stats_bag(bag: dict, side: str) -> dict:
    """Resolve team stats dict from various BSD shapes."""
    if not isinstance(bag, dict) or side not in ("home", "away"):
        return {}
    # top-level stats.home / stats.away (full match)
    t = bag.get(side)
    if isinstance(t, dict) and t:
        return t
    for fk in ("full", "match", "all", "ft", "full_time", "overall"):
        full = bag.get(fk)
        if isinstance(full, dict):
            if isinstance(full.get(side), dict):
                return full[side]
            # sometimes full is already one side
            if any(k in full for k in ("total_shots", "shots", "corner_kicks", "corners", "fouls")):
                return full
    return {}


def _goals_by_half_from_shotmap(st, side: str):
    """Return (goals_1h, goals_2h) for side from shotmap; None if no shotmap goals."""
    shots = _shotmap_list(st)
    if not shots:
        return None, None
    g1 = g2 = 0
    found = False
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        if str(shot.get("type") or "").lower() != "goal":
            continue
        is_home = shot.get("home")
        if is_home is None:
            team = str(shot.get("team") or shot.get("side") or "").lower()
            is_home = team in ("home", "h", "1")
        scorer = "home" if is_home else "away"
        if scorer != side:
            continue
        found = True
        minute = shot.get("min") or shot.get("minute")
        period = shot.get("period") or shot.get("period_id")
        try:
            minute = int(float(minute)) if minute is not None else None
        except (TypeError, ValueError):
            minute = None
        try:
            period = int(period) if period is not None else None
        except (TypeError, ValueError):
            period = None
        if period == 1 or (minute is not None and minute <= 45):
            g1 += 1
        else:
            g2 += 1
    if not found:
        return None, None
    return g1, g2


def _pull_match_metrics(team_block: dict) -> dict:
    """Pull goals/corners/yellow/fouls/shots/sot from a team stats block."""
    if not team_block:
        return {}
    out = {}
    mapping = {
        "goals": ("goals", "score", "goals_scored"),
        "corners": ("corner_kicks", "corners", "total_corners", "corner"),
        "yellow": ("yellow_cards", "yellowcards", "yellow_card", "yellow", "yc"),
        "fouls": ("fouls", "fouls_committed", "total_fouls"),
        "shots": ("total_shots", "shots_total", "shots", "attempts"),
        "shots_on": ("shots_on_target", "shots_on", "on_target", "shots_on_goal"),
    }
    for key, aliases in mapping.items():
        v = _side_stat(team_block, *aliases)
        if v is not None:
            try:
                out[key] = float(v)
            except (TypeError, ValueError):
                pass
    return out


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
    keys = [
        "possession", "shots", "shots_on", "shots_off", "blocked",
        "corners", "fouls", "yellow", "red", "offsides",
        "saves", "passes", "pass_acc", "tackles", "goals",
    ]
    fh_acc = {k: [] for k in keys}
    sh_acc = {k: [] for k in keys}

    def _push(acc, key, val):
        if val is None:
            return
        try:
            acc[key].append(float(val))
        except (TypeError, ValueError):
            pass

    def _collect(half_block, acc):
        if not half_block or not isinstance(half_block, dict):
            return
        side = half_block.get(side_key) if side_key in half_block else half_block
        if not isinstance(side, dict):
            return
        _push(acc, "possession", _side_stat(side, "ball_possession", "possession"))
        _push(acc, "shots", _side_stat(side, "total_shots", "shots_total", "shots"))
        _push(acc, "shots_on", _side_stat(side, "shots_on_target", "shots_on"))
        _push(acc, "shots_off", _side_stat(side, "shots_off_target", "shots_off"))
        _push(acc, "blocked", _side_stat(side, "blocked_shots", "blocked"))
        _push(acc, "corners", _side_stat(side, "corner_kicks", "corners"))
        _push(acc, "fouls", _side_stat(side, "fouls"))
        _push(acc, "yellow", _side_stat(side, "yellow_cards", "yellowcards", "yellow_card", "yellow"))
        _push(acc, "red", _side_stat(side, "red_cards", "redcards", "red_card", "red"))
        _push(acc, "offsides", _side_stat(side, "offsides", "offside"))
        _push(acc, "saves", _side_stat(side, "saves", "goalkeeper_saves"))
        _push(acc, "passes", _side_stat(side, "passes", "total_passes"))
        _push(acc, "pass_acc", _side_stat(side, "passes_accurate_pct", "pass_accuracy", "accurate_passes_percentage"))
        _push(acc, "tackles", _side_stat(side, "tackles", "total_tackles"))
        _push(acc, "goals", _side_stat(side, "goals", "score"))

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


def _aggregate_match_stats(stats_list: list, side_key: str) -> dict:
    """Average full-match team stats (corners, cards, fouls, shots) over sampled games."""
    keys = ["corners", "yellow", "fouls", "shots", "shots_on"]
    acc = {k: [] for k in keys}

    def _push(key, val):
        if val is None:
            return
        try:
            acc[key].append(float(val))
        except (TypeError, ValueError):
            pass

    def _team_block(block):
        if not isinstance(block, dict):
            return {}
        if side_key in block and isinstance(block.get(side_key), dict):
            return block[side_key]
        return block

    for st in stats_list or []:
        if not st or not isinstance(st, dict):
            continue
        bag = st.get("stats") if isinstance(st.get("stats"), dict) else st
        # prefer explicit full-match block
        full = bag.get("full") or bag.get("match") or bag.get("all") or bag.get("ft") or bag.get("full_time")
        team = _team_block(full) if full else {}
        if not team:
            # sum first + second half for this side
            fh = _team_block(bag.get("first_half") or bag.get("1st_half") or {})
            sh = _team_block(bag.get("second_half") or bag.get("2nd_half") or {})
            if not fh and not sh:
                continue
            for key, aliases in (
                ("corners", ("corners", "corner_kicks")),
                ("yellow", ("yellow_cards", "yellowcards", "yellow_card", "yellow")),
                ("fouls", ("fouls",)),
                ("shots", ("total_shots", "shots_total", "shots")),
                ("shots_on", ("shots_on_target", "shots_on")),
            ):
                vals = []
                for blk in (fh, sh):
                    if not blk:
                        continue
                    v = _side_stat(blk, *aliases)
                    if v is not None:
                        try:
                            vals.append(float(v))
                        except (TypeError, ValueError):
                            pass
                if vals:
                    _push(key, sum(vals))
            continue
        _push("corners", _side_stat(team, "corners", "corner_kicks"))
        _push("yellow", _side_stat(team, "yellow_cards", "yellowcards", "yellow_card", "yellow"))
        _push("fouls", _side_stat(team, "fouls"))
        _push("shots", _side_stat(team, "total_shots", "shots_total", "shots"))
        _push("shots_on", _side_stat(team, "shots_on_target", "shots_on"))

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


def _ht_ft_from_stats(st, side: str) -> Optional[dict]:
    """Extract our/opp HT and FT goals from stats halves and/or shotmap."""
    if not st or not isinstance(st, dict) or side not in ("home", "away"):
        return None
    bag = st.get("stats") if isinstance(st.get("stats"), dict) else st
    if not isinstance(bag, dict):
        bag = {}
    opp = "away" if side == "home" else "home"

    def _goals(half_block, who):
        if not isinstance(half_block, dict):
            return None
        tb = half_block.get(who) if isinstance(half_block.get(who), dict) else None
        if tb is None:
            return None
        return _side_stat(tb, "goals", "score")

    fh = bag.get("first_half") or bag.get("1st_half") or {}
    sh = bag.get("second_half") or bag.get("2nd_half") or {}
    our_ht = _goals(fh, side)
    opp_ht = _goals(fh, opp)
    our_sh = _goals(sh, side)
    opp_sh = _goals(sh, opp)

    # Fallback: shotmap goals by minute / period
    if our_ht is None and opp_ht is None:
        our_ht = opp_ht = 0
        our_sh = opp_sh = 0
        found = False
        for shot in _shotmap_list(st):
            if not isinstance(shot, dict):
                continue
            if str(shot.get("type") or "").lower() != "goal":
                continue
            found = True
            is_home = shot.get("home")
            if is_home is None:
                team = str(shot.get("team") or shot.get("side") or "").lower()
                is_home = team in ("home", "h", "1")
            minute = shot.get("min") or shot.get("minute")
            period = shot.get("period") or shot.get("period_id")
            try:
                minute = int(float(minute)) if minute is not None else None
            except (TypeError, ValueError):
                minute = None
            try:
                period = int(period) if period is not None else None
            except (TypeError, ValueError):
                period = None
            is_first = (period == 1) or (minute is not None and minute <= 45)
            scorer_side = "home" if is_home else "away"
            if is_first:
                if scorer_side == side:
                    our_ht += 1
                else:
                    opp_ht += 1
            else:
                if scorer_side == side:
                    our_sh = (our_sh or 0) + 1
                else:
                    opp_sh = (opp_sh or 0) + 1
        if not found:
            our_ht = opp_ht = None

    if our_ht is None and opp_ht is None:
        return None
    try:
        our_ht = int(float(our_ht or 0))
        opp_ht = int(float(opp_ht or 0))
    except (TypeError, ValueError):
        return None
    our_ft = our_ht
    opp_ft = opp_ht
    try:
        if our_sh is not None:
            our_ft += int(float(our_sh))
        if opp_sh is not None:
            opp_ft += int(float(opp_sh))
    except (TypeError, ValueError):
        pass
    return {
        "our_ht": our_ht,
        "opp_ht": opp_ht,
        "our_ft": our_ft,
        "opp_ft": opp_ft,
    }


def _compute_live_patterns(rows: list) -> dict:
    """
    HT→FT patterns for live / handicap / DC style reading.
    rows: [{our_ht, opp_ht, our_ft, opp_ft}, ...]
    """
    empty = {
        "matches": 0,
        "lead_ht": 0, "lead_hold": 0, "lead_drop": 0,
        "trail_ht": 0, "trail_comeback": 0, "trail_fail": 0,
        "draw_ht": 0, "draw_ht_win": 0, "draw_ht_draw": 0, "draw_ht_lose": 0,
        "ht_00": 0, "ht_00_over15": 0, "ht_00_btts": 0,
        "lead_hold_pct": 0, "lead_drop_pct": 0,
        "comeback_pct": 0, "ht_00_over15_pct": 0,
    }
    if not rows:
        return empty
    n = 0
    lead_ht = lead_hold = lead_drop = 0
    trail_ht = trail_cb = trail_fail = 0
    draw_ht = d_win = d_draw = d_lose = 0
    ht00 = ht00_o15 = ht00_btts = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        try:
            oh, ph = int(r["our_ht"]), int(r["opp_ht"])
            of, pf = int(r["our_ft"]), int(r["opp_ft"])
        except (KeyError, TypeError, ValueError):
            continue
        n += 1
        total_ft = of + pf
        if oh == 0 and ph == 0:
            ht00 += 1
            if total_ft >= 2:
                ht00_o15 += 1
            if of > 0 and pf > 0:
                ht00_btts += 1
        if oh > ph:
            lead_ht += 1
            if of > pf:
                lead_hold += 1
            else:
                lead_drop += 1  # draw or loss after leading
        elif oh < ph:
            trail_ht += 1
            if of > pf:
                trail_cb += 1
            else:
                trail_fail += 1
        else:
            draw_ht += 1
            if of > pf:
                d_win += 1
            elif of == pf:
                d_draw += 1
            else:
                d_lose += 1
    if n == 0:
        return empty

    def pct(a, b):
        return round(100.0 * a / b, 1) if b else 0.0

    return {
        "matches": n,
        "lead_ht": lead_ht,
        "lead_hold": lead_hold,
        "lead_drop": lead_drop,
        "trail_ht": trail_ht,
        "trail_comeback": trail_cb,
        "trail_fail": trail_fail,
        "draw_ht": draw_ht,
        "draw_ht_win": d_win,
        "draw_ht_draw": d_draw,
        "draw_ht_lose": d_lose,
        "ht_00": ht00,
        "ht_00_over15": ht00_o15,
        "ht_00_btts": ht00_btts,
        "lead_hold_pct": pct(lead_hold, lead_ht),
        "lead_drop_pct": pct(lead_drop, lead_ht),
        "comeback_pct": pct(trail_cb, trail_ht),
        "ht_00_over15_pct": pct(ht00_o15, ht00),
        "ht_00_btts_pct": pct(ht00_btts, ht00),
        "draw_ht_win_pct": pct(d_win, draw_ht),
    }


def _compute_team_trends(last_matches: list, tid=None, team_name: str = "") -> dict:
    """Trends from last up to 15 finished matches (W/D/L, goals, BTTS, O2.5, clean sheets)."""
    empty = {
        "matches": 0, "wins": 0, "draws": 0, "losses": 0,
        "gf": 0, "ga": 0, "avg_gf": 0, "avg_ga": 0,
        "btts_pct": 0, "over25_pct": 0, "under25_pct": 0,
        "clean_sheets": 0, "failed_to_score": 0,
        "form": "", "points": 0, "ppg": 0,
    }
    if not last_matches:
        return empty
    tname = (team_name or "").lower().strip()
    wins = draws = losses = 0
    gf = ga = 0
    btts = over25 = cs = fts = 0
    form_chars = []
    n = 0
    for m in (last_matches or [])[:15]:
        if not isinstance(m, dict):
            continue
        hs, aws = m.get("home_score"), m.get("away_score")
        if hs is None or aws is None:
            continue
        try:
            hs, aws = int(hs), int(aws)
        except (TypeError, ValueError):
            continue
        side = None
        hid, aid = m.get("home_team_id"), m.get("away_team_id")
        try:
            if tid is not None and hid is not None and int(hid) == int(tid):
                side = "home"
            elif tid is not None and aid is not None and int(aid) == int(tid):
                side = "away"
        except (TypeError, ValueError):
            pass
        if side is None and tname:
            hn = str(m.get("home") or "").lower()
            an = str(m.get("away") or "").lower()
            if tname in hn or hn in tname:
                side = "home"
            elif tname in an or an in tname:
                side = "away"
        if side is None:
            side = "home"
        scored = hs if side == "home" else aws
        conceded = aws if side == "home" else hs
        n += 1
        gf += scored
        ga += conceded
        if scored > conceded:
            wins += 1
            form_chars.append("W")
        elif scored == conceded:
            draws += 1
            form_chars.append("D")
        else:
            losses += 1
            form_chars.append("L")
        if scored > 0 and conceded > 0:
            btts += 1
        if scored + conceded > 2.5:
            over25 += 1
        if conceded == 0:
            cs += 1
        if scored == 0:
            fts += 1
    if n == 0:
        return empty
    pts = wins * 3 + draws
    under25 = n - over25
    return {
        "matches": n,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "gf": gf,
        "ga": ga,
        "avg_gf": round(gf / n, 2),
        "avg_ga": round(ga / n, 2),
        "btts": btts,
        "btts_pct": round(100.0 * btts / n, 1),
        "over25": over25,
        "over25_pct": round(100.0 * over25 / n, 1),
        "under25": under25,
        "under25_pct": round(100.0 * under25 / n, 1),
        "clean_sheets": cs,
        "failed_to_score": fts,
        "form": "".join(form_chars),
        "points": pts,
        "ppg": round(pts / n, 2),
        # half goals filled later from stats sample
        "scored_1h": 0, "scored_1h_pct": 0,
        "scored_2h": 0, "scored_2h_pct": 0,
        "conceded_1h": 0, "conceded_1h_pct": 0,
        "conceded_2h": 0, "conceded_2h_pct": 0,
        "half_matches": 0,
    }



def _score_key(gf: int, ga: int) -> str:
    return f"{int(gf)}-{int(ga)}"


def _goals_timeline_from_stats(stats_data, our_side: str) -> list:
    """Return list of (minute, 'scored'|'conceded') sorted by minute from shotmap."""
    events = []
    if not our_side:
        return events
    for shot in _shotmap_list(stats_data):
        if not isinstance(shot, dict):
            continue
        t = str(shot.get("type") or "").lower()
        if t != "goal":
            continue
        sit = str(shot.get("sit") or shot.get("situation") or "").lower()
        if sit in ("shootout", "penalty_shootout"):
            continue
        minute = shot.get("min") if shot.get("min") is not None else shot.get("minute")
        try:
            m_int = int(float(minute)) if minute is not None else None
        except (TypeError, ValueError):
            m_int = None
        if m_int is None or m_int > 120:
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
        events.append((m_int, kind))
    events.sort(key=lambda x: x[0])
    return events


def _score_at_minute(timeline: list, minute: int) -> tuple:
    gf = ga = 0
    for m, kind in timeline:
        if m > minute:
            break
        if kind == "scored":
            gf += 1
        else:
            ga += 1
    return gf, ga


def _events_between(timeline: list, start_excl: int, end_incl: int) -> tuple:
    gf = ga = 0
    for m, kind in timeline:
        if m <= start_excl:
            continue
        if m > end_incl:
            break
        if kind == "scored":
            gf += 1
        else:
            ga += 1
    return gf, ga


def _side_stat_num(stats_data, our_side: str, *keys) -> float:
    if not stats_data or not our_side:
        return 0.0
    # try period blocks and full
    bags = []
    if isinstance(stats_data, dict):
        bags.append(stats_data)
        for k in ("stats", "statistics", "data"):
            if isinstance(stats_data.get(k), dict):
                bags.append(stats_data[k])
    for bag in bags:
        side = bag.get(our_side) or bag.get("home" if our_side == "home" else "away")
        if not isinstance(side, dict):
            continue
        for key in keys:
            if key in side and side[key] is not None:
                try:
                    return float(side[key])
                except (TypeError, ValueError):
                    pass
            # nested
            for sk, sv in side.items():
                if str(sk).lower() == key and sv is not None:
                    try:
                        return float(sv)
                    except (TypeError, ValueError):
                        pass
    return 0.0


def _compute_character_from_stats(stats_list: list, match_sides: list) -> dict:
    """
    Характер: поведение после 25' (до конца 1Т), после 45' (до 70'), после 70' (до конца)
    при конкретном счёте. Голы — по shotmap; карточки/угловые/фолы — из match stats (полный матч как ориентир).
    """
    windows_def = [
        ("after_25", 25, 45, "После 25′ → конец 1-го тайма"),
        ("after_45", 45, 70, "После 45′ → до 70′"),
        ("after_70", 70, 120, "После 70′ → конец матча"),
    ]
    out = {
        "matches": 0,
        "windows": {
            "after_25": {"label": "После 25′ → конец 1-го тайма", "by_score": {}},
            "after_45": {"label": "После 45′ → до 70′", "by_score": {}},
            "after_70": {"label": "После 70′ → конец матча", "by_score": {}},
        },
    }
    used = 0
    for st, our_side in zip(stats_list or [], match_sides or []):
        if not our_side or not st:
            continue
        timeline = _goals_timeline_from_stats(st, our_side)
        # count match even if 0-0
        used += 1
        yellow = _side_stat_num(st, our_side, "yellow_cards", "yellowcards", "yellow_card", "yellow")
        corners = _side_stat_num(st, our_side, "corners", "corner_kicks", "corner")
        fouls = _side_stat_num(st, our_side, "fouls", "fouls_committed", "total_fouls")
        for wkey, start_m, end_m, _label in windows_def:
            gf0, ga0 = _score_at_minute(timeline, start_m)
            sk = _score_key(gf0, ga0)
            # skip absurd scores beyond 5-5 for display density
            if gf0 > 5 or ga0 > 5:
                continue
            gf_a, ga_a = _events_between(timeline, start_m, end_m)
            bucket = out["windows"][wkey]["by_score"].setdefault(sk, {
                "n": 0, "gf": 0, "ga": 0, "yellow": 0.0, "corners": 0.0, "fouls": 0.0,
                "n_scored": 0, "n_conceded": 0, "n_btts": 0, "n_nogoal": 0,
            })
            bucket["n"] += 1
            bucket["gf"] += gf_a
            bucket["ga"] += ga_a
            if gf_a > 0:
                bucket["n_scored"] += 1
            if ga_a > 0:
                bucket["n_conceded"] += 1
            if gf_a > 0 and ga_a > 0:
                bucket["n_btts"] += 1
            if gf_a == 0 and ga_a == 0:
                bucket["n_nogoal"] += 1
            # full-match secondary stats averaged into the score-state samples
            bucket["yellow"] += yellow
            bucket["corners"] += corners
            bucket["fouls"] += fouls
    out["matches"] = used
    # averages + probabilities
    for w in out["windows"].values():
        for sk, b in list(w["by_score"].items()):
            n = max(1, int(b.get("n") or 1))
            b["yellow_avg"] = round(float(b.get("yellow") or 0) / n, 1)
            b["corners_avg"] = round(float(b.get("corners") or 0) / n, 1)
            b["fouls_avg"] = round(float(b.get("fouls") or 0) / n, 1)
            b["gf_avg"] = round(float(b.get("gf") or 0) / n, 2)
            b["ga_avg"] = round(float(b.get("ga") or 0) / n, 2)
            b["p_score"] = round(100.0 * int(b.get("n_scored") or 0) / n)
            b["p_concede"] = round(100.0 * int(b.get("n_conceded") or 0) / n)
            b["p_btts"] = round(100.0 * int(b.get("n_btts") or 0) / n)
            b["p_nogoal"] = round(100.0 * int(b.get("n_nogoal") or 0) / n)
    return out


def _standing_pos(standings: dict, tid) -> Optional[int]:
    if not standings or tid is None:
        return None
    for side in ("home", "away"):
        row = (standings.get(side) or {})
        if not row:
            continue
        rid = row.get("team_id") or row.get("id")
        try:
            if rid is not None and int(rid) == int(tid):
                pos = row.get("rank") or row.get("position") or row.get("pos")
                if pos is not None:
                    return int(pos)
        except (TypeError, ValueError):
            pass
    # table list
    table = standings.get("table") or standings.get("rows") or standings.get("standings")
    if isinstance(table, list):
        for i, row in enumerate(table):
            if not isinstance(row, dict):
                continue
            rid = row.get("team_id") or (row.get("team") or {}).get("id") if isinstance(row.get("team"), dict) else row.get("id")
            try:
                if rid is not None and int(rid) == int(tid):
                    pos = row.get("rank") or row.get("position") or row.get("pos") or (i + 1)
                    return int(pos)
            except (TypeError, ValueError):
                continue
    return None


def _strength_label(pos: Optional[int], ref_pos: Optional[int] = None) -> str:
    """1-6 strong, 7-14 medium, 14+ weak; relative ±4 rows = same as ref."""
    if pos is None:
        return "unknown"
    if ref_pos is not None:
        diff = pos - ref_pos  # positive => weaker (lower in table)
        if abs(diff) <= 4:
            return "same"
        if diff < -4:
            return "strong"  # opponent higher in table
        if diff > 4:
            return "weak"
    if pos <= 6:
        return "strong"
    if pos <= 14:
        return "medium"
    return "weak"


async def fetch_match_enrichment(
    session: aiohttp.ClientSession,
    event_id: int,
    home_tid: int = None,
    away_tid: int = None,
    league_id: int = None,
) -> dict:
    """Fetch coaches, injuries, form, last matches, H2H, halves, trends — BSD v2 schema."""
    pack = {
        "event_id": event_id,
        "coaches": {"home": {}, "away": {}},
        "injuries": {"home": [], "away": []},
        "form": {"home": {}, "away": {}},
        "last5": {"home": [], "away": []},
        "h2h": {},
        "lineups": {},
        "standings": {},
        "halves": {"home": {}, "away": {}},
        "goal_segments": {"home": {}, "away": {}},
        "match_stats": {"home": {}, "away": {}},
        "betting": {"home": {}, "away": {}},
        "edge": {},
        "context": {},
        "model": {},
        "odds": {},
        "forecast": {},
        "character": {"home": {}, "away": {}},
    }
    if not event_id:
        return pack

    event, h2h, lineups_raw = await asyncio.gather(
        _bsd_get(session, f"events/{event_id}/"),
        _bsd_get(session, f"events/{event_id}/h2h/"),
        _bsd_get(session, f"events/{event_id}/lineups/"),
        return_exceptions=True,
    )
    if isinstance(event, Exception):
        logger.warning("[enrich] event failed: %s", event)
        event = None
    if isinstance(h2h, Exception):
        logger.warning("[enrich] h2h failed: %s", h2h)
        h2h = None
    if isinstance(lineups_raw, Exception):
        logger.warning("[enrich] lineups failed: %s", lineups_raw)
        lineups_raw = None

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

    # Form (last 10 + home/away), last results, squad + half-stats sampling + betting rates
    kickoff_iso = None
    if event and isinstance(event, dict):
        kickoff_iso = event.get("event_date") or event.get("date") or event.get("kickoff")

    async def _team_pack(tid):
        if not tid:
            return {}, [], [], {}, {}, {}, {}
        # deeper form window (10) — includes home/away splits & avg stats from BSD
        form_params = {"last": 10}
        if league_id:
            form_params["league_id"] = league_id
        form10_p = _bsd_get(session, f"teams/{tid}/form/", form_params)
        # last results: events by team_id (most reliable for scores)
        ev_p = _bsd_get(session, "events/", {"team_id": tid, "status": "finished", "limit": 20})
        fix_p = _bsd_get(session, f"teams/{tid}/fixtures/", {"status": "finished", "limit": 15})
        squad_p = _bsd_get(session, f"teams/{tid}/squad/")
        form10_d, ev_d, fix_d, squad_d = await asyncio.gather(form10_p, ev_p, fix_p, squad_p)
        form = _parse_form(form10_d)
        last5 = _parse_recent_fixtures(ev_d)
        if not last5:
            last5 = _parse_recent_fixtures(fix_d)
        if not last5:
            ev2 = await _bsd_get(session, "events/", {"team_id": tid, "limit": 20})
            last5 = _parse_recent_fixtures(ev2)
        injuries = _parse_injuries(squad_d)
        tid_int = int(tid) if tid else None
        betting_rates = _compute_betting_rates(last5, team_id=tid_int)
        # rest days + HT rates (deep edge)
        extra = {
            "rest_days": _compute_rest_days(last5, kickoff_iso),
            "ht": _compute_ht_rates(last5, team_id=tid_int),
        }
        if betting_rates is None:
            betting_rates = {}
        if isinstance(betting_rates, dict):
            betting_rates = {**betting_rates, **extra}
        else:
            betting_rates = extra

        # Halves + goal segments + match stats: sample up to 15 recent finished events
        half_stats = {}
        match_avg = {}
        goal_seg = _empty_goal_segments()
        character = {"matches": 0, "windows": {}}
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
                if _is_friendly_match(m):
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
            ms_keys = ["goals", "corners", "yellow", "fouls", "shots", "shots_on"]
            ms_acc = {k: [] for k in ms_keys}
            games_with_metrics = 0
            # half goal incidence (scored/conceded in 1H/2H)

            def _ms_push(k, v):
                if v is None:
                    return
                try:
                    ms_acc[k].append(float(v))
                except (TypeError, ValueError):
                    pass

            for st, side, m in zip(stats_aligned, sides_for_goals, match_meta):
                if not st or not isinstance(st, dict):
                    continue
                bag = st.get("stats") if isinstance(st.get("stats"), dict) else st
                if not isinstance(bag, dict):
                    continue
                # half-time block for halves UI (this team's slice only)
                sm_g1, sm_g2 = _goals_by_half_from_shotmap(st, side)
                norm = {"stats": {}}
                for half_key in ("first_half", "second_half", "1st_half", "2nd_half"):
                    half = bag.get(half_key)
                    if not isinstance(half, dict):
                        continue
                    team_half = half.get(side) if isinstance(half.get(side), dict) else None
                    if team_half is None:
                        continue
                    team_half = dict(team_half)
                    # inject goals from shotmap when stats half has no goals field
                    out_key = "first_half" if "first" in half_key or half_key.startswith("1") else "second_half"
                    if _side_stat(team_half, "goals", "score") is None:
                        if out_key == "first_half" and sm_g1 is not None:
                            team_half["goals"] = sm_g1
                        elif out_key == "second_half" and sm_g2 is not None:
                            team_half["goals"] = sm_g2
                    norm["stats"][out_key] = {"home": team_half}
                if norm["stats"]:
                    normalized.append(norm)

                # Full-match metrics from stats.home / stats.away
                team = _team_block_from_stats_bag(bag, side)
                metrics = _pull_match_metrics(team) if team else {}
                if not metrics:
                    # sum both halves
                    fh = bag.get("first_half") or bag.get("1st_half") or {}
                    sh = bag.get("second_half") or bag.get("2nd_half") or {}
                    fh_t = fh.get(side) if isinstance(fh.get(side), dict) else {}
                    sh_t = sh.get(side) if isinstance(sh.get(side), dict) else {}
                    m1 = _pull_match_metrics(fh_t)
                    m2 = _pull_match_metrics(sh_t)
                    for key in ms_keys:
                        if key in m1 or key in m2:
                            metrics[key] = float(m1.get(key) or 0) + float(m2.get(key) or 0)
                # goals often absent on team stats — take from final score
                if "goals" not in metrics and isinstance(m, dict):
                    hs, aws = m.get("home_score"), m.get("away_score")
                    try:
                        if hs is not None and aws is not None:
                            metrics["goals"] = float(int(hs) if side == "home" else int(aws))
                    except (TypeError, ValueError):
                        pass
                if "goals" not in metrics and sm_g1 is not None and sm_g2 is not None:
                    metrics["goals"] = float(sm_g1 + sm_g2)
                if metrics:
                    games_with_metrics += 1
                for key in ms_keys:
                    if key in metrics:
                        _ms_push(key, metrics[key])

            half_stats = _aggregate_half_stats(normalized, "home")
            match_avg = {}
            n_ms = 0
            for k, vals in ms_acc.items():
                if vals:
                    match_avg[k] = round(sum(vals) / len(vals), 2)
                    n_ms = max(n_ms, len(vals))
                else:
                    match_avg[k] = None
            match_avg["matches"] = max(n_ms, games_with_metrics)
            logger.info(
                "[team_pack] tid=%s match_stats.matches=%s goals=%s halves_fh=%s samples=%s",
                tid, match_avg["matches"], match_avg.get("goals"),
                (half_stats.get("first_half") or {}).get("matches"),
                len([1 for x in stats_aligned if x]),
            )

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

            character = _compute_character_from_stats(stats_aligned, sides_for_goals)

        return form, last5, injuries, half_stats, goal_seg, match_avg, betting_rates, character

    empty_tp = ({}, [], [], {}, {}, {}, {}, {})
    try:
        home_data, away_data = await asyncio.gather(
            _team_pack(home_tid),
            _team_pack(away_tid),
            return_exceptions=True,
        )
        if isinstance(home_data, Exception):
            logger.warning("[enrich] home team_pack failed: %s", home_data)
            home_data = empty_tp
        if isinstance(away_data, Exception):
            logger.warning("[enrich] away team_pack failed: %s", away_data)
            away_data = empty_tp
        if not isinstance(home_data, (tuple, list)) or len(home_data) < 8:
            home_data = empty_tp
        if not isinstance(away_data, (tuple, list)) or len(away_data) < 8:
            away_data = empty_tp
    except Exception as e:
        logger.exception("[enrich] team_pack gather: %s", e)
        home_data = away_data = empty_tp

    (pack["form"]["home"], pack["last5"]["home"], pack["injuries"]["home"],
     pack["halves"]["home"], pack["goal_segments"]["home"],
     pack["match_stats"]["home"]) = home_data[:6]
    (pack["form"]["away"], pack["last5"]["away"], pack["injuries"]["away"],
     pack["halves"]["away"], pack["goal_segments"]["away"],
     pack["match_stats"]["away"]) = away_data[:6]
    pack["betting"] = {
        "home": home_data[6] if len(home_data) > 6 else {},
        "away": away_data[6] if len(away_data) > 6 else {},
    }
    pack["character"] = {
        "home": home_data[7] if len(home_data) > 7 else {},
        "away": away_data[7] if len(away_data) > 7 else {},
    }
    pack["home_team_id"] = home_tid
    pack["away_team_id"] = away_tid
    # surface xG from form for forecast engine
    fh_form = pack["form"].get("home") or {}
    fa_form = pack["form"].get("away") or {}
    pack["xg"] = {
        "home": {"avg": fh_form.get("xg_avg"), "matches": fh_form.get("xg_matches") or fh_form.get("matches_count")},
        "away": {"avg": fa_form.get("xg_avg"), "matches": fa_form.get("xg_matches") or fa_form.get("matches_count")},
    }

    # Match context: referee, venue, travel, weather, derby — high-value for bets
    pack["context"] = {}
    referee_id = None
    if event and isinstance(event, dict):
        ref = event.get("referee") or event.get("referee_name") or ""
        if isinstance(ref, dict):
            referee_id = ref.get("id")
            ref = ref.get("name") or ref.get("short_name") or ""
        referee_id = referee_id or event.get("referee_id")
        venue = event.get("venue") or event.get("stadium") or event.get("venue_name") or ""
        if isinstance(venue, dict):
            venue = venue.get("name") or venue.get("stadium") or ""
        weather = event.get("weather") or event.get("weather_condition") or ""
        if isinstance(weather, dict):
            weather = weather.get("condition") or weather.get("description") or weather.get("summary") or ""
        travel = event.get("travel_distance") or event.get("travel_km") or event.get("distance_km")
        try:
            travel = float(travel) if travel is not None else None
        except (TypeError, ValueError):
            travel = None
        is_derby = bool(event.get("is_derby") or event.get("derby") or event.get("is_rivalry"))
        neutral = bool(event.get("neutral_ground") or event.get("is_neutral"))
        pack["context"] = {
            "referee": str(ref or "").strip(),
            "referee_id": referee_id,
            "venue": str(venue or "").strip(),
            "weather": str(weather or "").strip(),
            "travel_km": travel,
            "is_derby": is_derby,
            "neutral_ground": neutral,
        }

    # Fetch model prediction + consensus odds + referee aggregates in parallel
    odds_raw = pred_raw = ref_raw = None
    try:
        tasks = [
            _bsd_get(session, f"events/{event_id}/odds/"),
            _bsd_get(session, f"events/{event_id}/prediction/"),
        ]
        if referee_id:
            tasks.append(_bsd_get(session, f"referees/{referee_id}/"))
        results = await asyncio.gather(*tasks, return_exceptions=True)
        odds_raw = results[0] if not isinstance(results[0], Exception) else None
        pred_raw = results[1] if not isinstance(results[1], Exception) else None
        if referee_id and len(results) > 2 and not isinstance(results[2], Exception):
            ref_raw = results[2]
    except Exception as e:
        logger.warning("[enrich] odds/pred/ref: %s", e)

    if ref_raw and isinstance(ref_raw, dict):
        # typical aggregates: yellow_cards_avg, red_cards_avg, fouls_avg, penalties_avg
        pack["context"]["referee_stats"] = {
            "name": ref_raw.get("name") or pack["context"].get("referee") or "",
            "matches": ref_raw.get("matches") or ref_raw.get("matches_officiated"),
            "yellow_avg": _safe_float(
                ref_raw.get("yellow_cards_per_match")
                or ref_raw.get("avg_yellow_cards")
                or (ref_raw.get("yellow_cards") if isinstance(ref_raw.get("yellow_cards"), (int, float)) else None)
            ),
            "red_avg": _safe_float(
                ref_raw.get("red_cards_per_match")
                or ref_raw.get("avg_red_cards")
            ),
            "fouls_avg": _safe_float(
                ref_raw.get("fouls_per_match") or ref_raw.get("avg_fouls")
            ),
            "penalties_avg": _safe_float(
                ref_raw.get("penalties_per_match") or ref_raw.get("avg_penalties")
            ),
        }
        if not pack["context"].get("referee") and ref_raw.get("name"):
            pack["context"]["referee"] = ref_raw.get("name")

    # Model + odds + auto forecast variants
    pack["model"] = _parse_prediction_bag(pred_raw or {})
    pack["odds"] = _parse_odds_bag(odds_raw or {})
    try:
        pack["forecast"] = _build_forecast_variants(pack, odds_raw=odds_raw, pred_raw=pred_raw)
    except Exception as e:
        logger.warning("[enrich] forecast build: %s", e)
        pack["forecast"] = {}

    # Combined edge metrics (both teams) for UI
    bh = pack["betting"].get("home") or {}
    ba = pack["betting"].get("away") or {}
    ms_h = pack["match_stats"].get("home") or {}
    ms_a = pack["match_stats"].get("away") or {}
    pack["edge"] = {
        "over_25_home": bh.get("over_25_pct"),
        "over_25_away": ba.get("over_25_pct"),
        "btts_home": bh.get("btts_pct"),
        "btts_away": ba.get("btts_pct"),
        "cs_home": bh.get("clean_sheet_pct"),
        "cs_away": ba.get("clean_sheet_pct"),
        "avg_total_home": bh.get("avg_total_goals"),
        "avg_total_away": ba.get("avg_total_goals"),
        "sample_home": bh.get("matches"),
        "sample_away": ba.get("matches"),
        "rest_home": bh.get("rest_days"),
        "rest_away": ba.get("rest_days"),
        "corners_home": ms_h.get("corners"),
        "corners_away": ms_a.get("corners"),
        "ht_over_05_home": (bh.get("ht") or {}).get("ht_over_05_pct"),
        "ht_over_05_away": (ba.get("ht") or {}).get("ht_over_05_pct"),
    }

    logger.info(
        "[enrich] match_stats h=%s a=%s betting h=%s a=%s model=%s odds=%s",
        (pack["match_stats"]["home"] or {}).get("matches"),
        (pack["match_stats"]["away"] or {}).get("matches"),
        bh.get("matches"),
        ba.get("matches"),
        bool(pack.get("model")),
        len(pack.get("odds") or {}),
    )
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
                        lid = lg["id"]
                        logo = ""
                        for k in ("logo", "crest", "image", "badge", "logo_url"):
                            val = lg.get(k)
                            if isinstance(val, str) and val.startswith("http"):
                                logo = val
                                break
                        if not logo and lid:
                            logo = f"https://sports.bzzoiro.com/img/league/{lid}/"
                        leagues[lid] = {"name": lg.get("name", ""), "country": lg.get("country", ""), "logo": logo}
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
    if not league_name:
        return 99
    name = str(league_name).strip()
    if name in LEAGUE_PRIORITIES:
        return LEAGUE_PRIORITIES[name]
    # case-insensitive exact
    low = name.lower()
    for k, v in LEAGUE_PRIORITIES.items():
        if k.lower() == low:
            return v
    # европейские турниры: ЛЧ / ЛЕ / ЛК → priority 2 (не 99)
    for frag in _EURO_CUP_PRIORITY_2:
        if frag in low:
            return 2
    return 99

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
        league_logo = f"https://sports.bzzoiro.com/img/league/{lid}/" if lid else ""
        leagues.append({
            "league_id": lid, "league": league_name, "country": country,
            "flag": get_flag(country), "priority": priority, "count": cnt,
            "league_logo": league_logo,
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
    errors = 0

    async def _insert_from_parsed(session, row_full):
        """Insert quickly, then enrich stats in same session (reuse connection)."""
        nonlocal count, errors
        name, country, league, match_time, logo_home, logo_away = row_full[0], row_full[1], row_full[2], row_full[3], row_full[4], row_full[5]
        event_id = row_full[6] if len(row_full) > 6 else None
        home_tid = row_full[7] if len(row_full) > 7 else None
        away_tid = row_full[8] if len(row_full) > 8 else None
        league_id = row_full[9] if len(row_full) > 9 else None
        league_logo = f"https://sports.bzzoiro.com/img/league/{league_id}/" if league_id else ""

        existing = db_fetchone("SELECT id FROM matches WHERE name = ? AND league = ?", (name, league))
        match_id = existing[0] if existing else None

        # 1) Быстро пишем матч без статистики, чтобы клиент не ждал enrichment
        if not match_id:
            try:
                db_execute(
                    "INSERT INTO matches (name, country, league, match_time, text, created_at, logo_home, logo_away, event_id, home_team_id, away_team_id, stats_json, league_id, league_logo) "
                    "VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, '', ?, ?)",
                    (name, country, league, match_time or "", now, logo_home or "", logo_away or "",
                     event_id, home_tid, away_tid, league_id, league_logo)
                )
                row_id = db_fetchone("SELECT id FROM matches WHERE name = ? AND league = ? ORDER BY id DESC LIMIT 1", (name, league))
                match_id = row_id[0] if row_id else None
                count += 1
            except Exception as e:
                logger.warning("[parser insert] %s: %s", name, e)
                errors += 1
                return False
        else:
            count += 1

        if not BSD_API_KEY or not match_id:
            return True

        # 2) Enrichment (медленнее) — ошибки не ломают ответ
        try:
            if not event_id:
                eid, hid, aid = await resolve_event_id(session, name, match_time or "", league_id)
                if eid:
                    event_id = eid
                    home_tid = home_tid or hid
                    away_tid = away_tid or aid
            stats_json = ""
            if event_id:
                pack = await fetch_match_enrichment(session, int(event_id), home_tid, away_tid, league_id)
                stats_json = json.dumps(pack, ensure_ascii=False)
            if stats_json or event_id:
                db_execute(
                    "UPDATE matches SET event_id = COALESCE(?, event_id), home_team_id = COALESCE(?, home_team_id), "
                    "away_team_id = COALESCE(?, away_team_id), stats_json = CASE WHEN ? != '' THEN ? ELSE stats_json END, "
                    "logo_home = CASE WHEN ? != '' THEN ? ELSE logo_home END, "
                    "logo_away = CASE WHEN ? != '' THEN ? ELSE logo_away END, "
                    "league_id = COALESCE(?, league_id), "
                    "league_logo = CASE WHEN ? != '' THEN ? ELSE league_logo END WHERE id = ?",
                    (event_id, home_tid, away_tid, stats_json, stats_json,
                     logo_home or "", logo_home or "", logo_away or "", logo_away or "",
                     league_id, league_logo, league_logo, match_id)
                )
        except Exception as e:
            logger.warning("[parser enrich] %s: %s", name, e)
            errors += 1
        return True

    select_cols = (
        "name, country, league, match_time, COALESCE(logo_home,''), COALESCE(logo_away,''), "
        "event_id, home_team_id, away_team_id, league_id"
    )

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=15, sock_read=45)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if mode == "single":
            parsed_id = data.get("parsed_id")
            row = db_fetchone(f"SELECT {select_cols} FROM parsed_matches WHERE id = ?", (parsed_id,))
            if not row:
                raise HTTPException(404, "Матч не найден")
            await _insert_from_parsed(session, row)
            log_admin(user["id"], "parser_add_match", row[0])

        elif mode == "league":
            league_id = data.get("league_id")
            rows = db_fetchall(f"SELECT {select_cols} FROM parsed_matches WHERE league_id = ?", (league_id,))
            for r in rows:
                await _insert_from_parsed(session, r)
                await asyncio.sleep(0.05)
            league_row = db_fetchone("SELECT league FROM parsed_matches WHERE league_id = ?", (league_id,))
            log_admin(user["id"], "parser_add_league", f"{league_row[0] if league_row else ''} ({count})")

        else:
            limit = int(data.get("limit", 80))
            rows = db_fetchall(
                f"SELECT {select_cols} FROM parsed_matches ORDER BY priority ASC LIMIT ?",
                (limit,)
            )
            for r in rows:
                await _insert_from_parsed(session, r)
                await asyncio.sleep(0.05)
            log_admin(user["id"], "parser_add_all", f"{count} матчей")

    msg = f"✅ Добавлено {count} матчей (со статистикой)"
    if errors:
        msg += f". Предупреждений: {errors}"
    return {"ok": True, "count": count, "errors": errors, "message": msg}


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
