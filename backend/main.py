"""
Koefii Mini App Backend
Telegram Mini App + Bot hybrid
"""
import asyncio
import re
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
from pathlib import Path
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
PREMIUM_PLANS = {
    "1m": {"amount": 300.0, "days": 30, "label": "1 месяц"},
    "3m": {"amount": 750.0, "days": 90, "label": "3 месяца"},
    "6m": {"amount": 1300.0, "days": 180, "label": "6 месяцев"},
    "12m": {"amount": 2200.0, "days": 365, "label": "1 год"},
}
tz = ZoneInfo("Europe/Moscow")

# ====================== ПЕРСИСТЕНТНАЯ БД (Bothost / Docker) ======================
# Bothost: папка /app/data сохраняется между обновлениями из Git.
# Переменные: DATABASE_PATH или DB_PATH → полный путь к .db
# Иначе: /app/data/bot.db (Bothost), fallback — ./data/bot.db рядом с проектом.
def _resolve_db_path() -> str:
    for key in ("DATABASE_PATH", "DB_PATH", "DATABASE_NAME"):
        raw = (os.getenv(key) or "").strip()
        if not raw:
            continue
        # DATABASE_NAME может быть только именем файла
        if key == "DATABASE_NAME" and os.sep not in raw:
            data_dir = Path("/app/data") if Path("/app/data").is_dir() or os.getenv("BOTHOST") else Path(__file__).resolve().parent.parent / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            return str(data_dir / raw)
        path = Path(raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        return str(path)

    # Bothost persistent volume
    bothost_data = Path("/app/data")
    if bothost_data.is_dir() or os.path.exists("/app"):
        try:
            bothost_data.mkdir(parents=True, exist_ok=True)
            if bothost_data.is_dir() and os.access(str(bothost_data), os.W_OK):
                return str(bothost_data / "bot.db")
        except OSError:
            pass

    # Локально / Render: data/ в корне проекта
    project_data = Path(__file__).resolve().parent.parent / "data"
    project_data.mkdir(parents=True, exist_ok=True)
    return str(project_data / "bot.db")


DB_FILE = _resolve_db_path()

# Миграция: если рядом с main.py остался старый bot.db, а в data ещё пусто — переносим
def _migrate_legacy_db():
    legacy = Path(__file__).resolve().parent / "bot.db"
    target = Path(DB_FILE)
    if legacy.is_file() and legacy.resolve() != target.resolve():
        if not target.exists() or target.stat().st_size == 0:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                import shutil
                shutil.copy2(str(legacy), str(target))
                logger.info("DB migrated: %s → %s", legacy, target)
            except Exception as e:
                logger.warning("DB migrate failed: %s", e)

_migrate_legacy_db()
logger.info("SQLite DB path: %s", DB_FILE)

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
    """Validate Telegram WebApp initData and return user data + start_param."""
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
        # start_param приходит при открытии по deep-link (start / startapp)
        start_param = (parsed.get("start_param") or "").strip()
        if start_param:
            user_data["_start_param"] = start_param
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


def apply_referral(new_user_id: int, start_param: str) -> bool:
    """Привязать реферала и начислить пригласившему 3 дня Premium.
    start_param: ref_<user_id> или просто число.
    Возвращает True если бонус выдан.
    """
    if not start_param:
        return False
    raw = start_param.strip()
    if raw.lower().startswith("ref_"):
        raw = raw[4:]
    try:
        referrer_id = int(raw)
    except (TypeError, ValueError):
        return False
    if referrer_id <= 0 or referrer_id == new_user_id:
        return False
    # пригласивший должен существовать
    ref_row = db_fetchone("SELECT user_id FROM users WHERE user_id = ?", (referrer_id,))
    if not ref_row:
        return False
    # уже привязан — не дублируем
    me = db_fetchone("SELECT referred_by FROM users WHERE user_id = ?", (new_user_id,))
    if me and me[0]:
        return False
    db_execute(
        "UPDATE users SET referred_by = ? WHERE user_id = ? AND (referred_by IS NULL OR referred_by = 0)",
        (referrer_id, new_user_id),
    )
    # начисляем 3 дня пригласившему
    try:
        activate_premium(referrer_id, 3)
        logger.info("Referral: user %s invited by %s → +3 days Premium", new_user_id, referrer_id)
        return True
    except Exception as e:
        logger.exception("Referral premium fail: %s", e)
        return False


# ====================== COUNTRY FLAGS & LEAGUES ======================

COUNTRY_FLAGS = {
    "Russia": "🇷🇺", "England": "🇬🇧", "Spain": "🇪🇸", "Italy": "🇮🇹",
    "Germany": "🇩🇪", "France": "🇫🇷", "Portugal": "🇵🇹", "Turkey": "🇹🇷",
    "Serbia": "🇷🇸", "Kazakhstan": "🇰🇿", "Ukraine": "🇺🇦", "Belarus": "🇧🇾",
    "USA": "🇺🇸", "United States": "🇺🇸", "Brazil": "🇧🇷", "Argentina": "🇦🇷", "Austria": "🇦🇹",
    "Belgium": "🇧🇪", "Bulgaria": "🇧🇬", "Hungary": "🇭🇺", "Greece": "🇬🇷",
    "Denmark": "🇩🇰", "Ireland": "🇮🇪", "Netherlands": "🇳🇱", "Norway": "🇳🇴",
    "Poland": "🇵🇱", "Croatia": "🇭🇷", "Sweden": "🇸🇪", "Switzerland": "🇨🇭",
    "Czech Republic": "🇨🇿", "Romania": "🇷🇴", "Slovakia": "🇸🇰",
    "Slovenia": "🇸🇮", "Montenegro": "🇲🇪", "China": "🇨🇳", "Mexico": "🇲🇽",
    "Finland": "🇫🇮", "Saudi Arabia": "🇸🇦", "Morocco": "🇲🇦",
    "Scotland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "Australia": "🇦🇺",
    "Nigeria": "🇳🇬", "South Korea": "🇰🇷", "Korea Republic": "🇰🇷", "Japan": "🇯🇵",
    "Angola": "🇦🇴", "Colombia": "🇨🇴",
    "World": "🌍", "Europe": "🇪🇺", "Africa": "🌍",
}


# Страна лиги (для подписей в авто-парсере)
LEAGUE_COUNTRY = {
    "NPFL": "Нигерия", "Nigeria Professional Football League": "Нигерия", "Nigerian Premier League": "Нигерия",
    "Categoría Primera A": "Колумбия", "Categoria Primera A": "Колумбия", "Liga BetPlay": "Колумбия", "Liga Betplay": "Колумбия",
    "Botola Pro": "Марокко", "Botola": "Марокко", "Moroccan Botola Pro": "Марокко",
    "J1 League": "Япония", "J.League": "Япония",
    "K League 1": "Южная Корея", "K League": "Южная Корея", "K-League": "Южная Корея",
    "Girabola": "Ангола",
    "Super League Greece": "Греция", "Super League 1": "Греция",
    "Veikkausliiga": "Финляндия",
    "Chinese Super League": "Китай", "CSL": "Китай",
    "EFL Championship": "Англия", "Championship": "Англия",
    "EFL League One": "Англия", "League One": "Англия",
    "EFL League Two": "Англия", "League Two": "Англия",
    "2. Bundesliga": "Германия", "2 Bundesliga": "Германия",
    "Segunda División": "Испания", "Segunda Division": "Испания",
    "Ligue 2": "Франция",
    "Liga Portugal 2": "Португалия", "Segunda Liga": "Португалия", "Portugal Liga 2": "Португалия",
    "Serie B Brazil": "Бразилия", "Campeonato Brasileiro Série B": "Бразилия", "Brasileirão Série B": "Бразилия",
    "Serie B": "Италия",
    "FNL": "Россия",
    "Scottish Premiership": "Шотландия", "Swiss Super League": "Швейцария",
    "Liga I": "Румыния", "SuperLiga Romania": "Румыния",
    "Belgian Pro League": "Бельгия", "Jupiler Pro League": "Бельгия",
    "Liga MX": "Мексика", "Saudi Pro League": "Саудовская Аравия",
    "Bulgarian First League": "Болгария", "Austrian Bundesliga": "Австрия",
    "Czech First League": "Чехия", "A-League": "Австралия", "A-League Men": "Австралия",
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
    # Ранг 3 — топ Европы
    "Premier League": 3, "La Liga": 3, "Serie A": 3, "Bundesliga": 3,
    "Ligue 1": 3, "Eredivisie": 3, "Russian Premier League": 3,
    # Ранг 4 — сильные
    "Serie A Brasileirão": 4, "Brasileirão": 4, "Serie A Brazil": 4, "Campeonato Brasileiro Série A": 4,
    "Primera División": 4, "Liga Profesional": 4, "Liga Profesional de Fútbol": 4,
    "Major League Soccer": 4, "MLS": 4,
    "Süper Lig": 4, "Super Lig": 4,
    "Primeira Liga": 4, "Liga Portugal": 4, "Liga Portugal Betclic": 4,
    "Ekstraklasa": 4, "Serbian SuperLiga": 4,
    "Belarusian Premier League": 4, "Slovak Super Liga": 4,
    "Slovenian PrvaLiga": 4, "Ukrainian Premier League": 4, "Montenegrin First League": 4,
    # Ранг 5 — Шотландия, Швейцария, Румыния, Бельгия
    "Scottish Premiership": 5, "Premiership": 5, "cinch Premiership": 5,
    "Swiss Super League": 5, "Super League Switzerland": 5, "Switzerland Super League": 5,
    "Belgian Pro League": 5, "Jupiler Pro League": 5, "Belgium Pro League": 5,
    "Liga I": 5, "Romanian Liga I": 5, "SuperLiga Romania": 5, "Superliga Romania": 5,
    "Romania Superliga": 5,
    "Allsvenskan": 5, "Eliteserien": 5, "Danish Superliga": 5,
    "Nemzeti Bajnokság I": 5, "Croatian Football League": 5, "HNL": 5,
    "League of Ireland": 5,
    # Ранг 6 — Мексика, Саудовская Аравия, Болгария, Австрия, Чехия
    "Liga MX": 6, "Liga MX Apertura": 6, "Liga MX Clausura": 6,
    "Saudi Pro League": 6, "Saudi Professional League": 6,
    "Bulgarian First League": 6, "efbet League": 6, "Parva Liga": 6,
    "Austrian Bundesliga": 6,
    "Czech First League": 6, "Fortuna Liga": 6,
    "A-League": 6, "A-League Men": 6,
    # Ранг 7 — Нигерия, Колумбия + ранее 7
    "NPFL": 7, "Nigeria Professional Football League": 7, "Nigerian Premier League": 7,
    "Categoría Primera A": 7, "Categoria Primera A": 7, "Liga BetPlay": 7, "Liga Betplay": 7,
    "Botola Pro": 7, "Botola": 7, "Moroccan Botola Pro": 7,
    "J1 League": 7, "J.League": 7,
    "K League 1": 7, "K League": 7, "K-League": 7,
    "Girabola": 7,
    "Super League Greece": 7, "Veikkausliiga": 7,
    "Chinese Super League": 7, "CSL": 7,
    # Ранг 8 — 2-е дивизионы
    "EFL Championship": 8, "Championship": 8,
    "EFL League One": 8, "League One": 8,
    "EFL League Two": 8, "League Two": 8,
    "2. Bundesliga": 8, "2 Bundesliga": 8,
    "Segunda División": 8, "Segunda Division": 8,
    "Ligue 2": 8,
    "Liga Portugal 2": 8, "Segunda Liga": 8, "Portugal Liga 2": 8,
    "Serie B Brazil": 8, "Campeonato Brasileiro Série B": 8, "Brasileirão Série B": 8,
    "Serie B": 8,
    "FNL": 8,
    "USL Championship": 99, "USA Championship": 99,
    # Ранг 9 — кубки
    "FA Cup": 9, "EFL Cup": 9, "Carabao Cup": 9, "League Cup": 9,
    "DFB-Pokal": 9, "DFB Pokal": 9, "Pokal": 9,
    "Coupe de France": 9, "Coppa Italia": 9, "Copa del Rey": 9,
    "Taça de Portugal": 9, "Taca de Portugal": 9,
    "KNVB Beker": 9, "Scottish Cup": 9, "Belgian Cup": 9, "Croky Cup": 9,
    "Swiss Cup": 9, "Turkish Cup": 9, "Russian Cup": 9, "Кубок России": 9,
    "Copa Argentina": 9, "Copa do Brasil": 9, "US Open Cup": 9,
    "Super Cup": 9, "Supercup": 9, "Community Shield": 9,
    # 2-я лига Бельгии — не путать с Pro League (ранг 5)
    "Challenger Pro League": 99, "Belgium Challenger Pro League": 99,
    "Challenger Pro": 99, "Belgian Challenger Pro League": 99,

}

# Подстроки для euro-кубков УЕФА (API часто шлёт разные названия)
_EURO_CUP_PRIORITY_2 = (
    "uefa champions league",
    "uefa europa league",
    "uefa europa conference",
    "uefa conference league",
    "europa conference league",
)
# «champions league» без UEFA — только если нет африканских маркеров
_CL_GENERIC = ("champions league", "liga campeones")
_AFRICA_CUP_MARKERS = (
    "africa", "african", "caf ", " caf", "caf champions",
    "totalenergies", "confederation cup",
)

COUNTRY_NORMALIZE = {
    "Czech-Republic": "Czech Republic",
    "Czech Republic": "Czech Republic",
    "USA": "USA",
    "United States": "USA",
    "UK": "England",
    "Scotland": "Scotland",
    "Morocco": "Morocco",
    "Mexico": "Mexico",
    "Belgium": "Belgium",
    "Bulgaria": "Bulgaria",
    "Finland": "Finland",
    "Portugal": "Portugal",
    "Australia": "Australia",
    "World": "World",
    "Europe": "Europe",
}

def get_flag(country: str) -> str:
    return COUNTRY_FLAGS.get(country, "🏳️")

# ====================== БАЗА ДАННЫХ ======================

def init_db():
    """Создаёт таблицы, если БД ещё нет (Bothost: /app/data/bot.db)."""
    db_path = Path(DB_FILE)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not db_path.exists()
    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    if is_new:
        logger.info("Creating new database: %s", db_path)
    else:
        logger.info("Using existing database: %s", db_path)

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
    try:
        c.execute("ALTER TABLE users ADD COLUMN bonus_pending INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE users ADD COLUMN banned_until TEXT DEFAULT NULL")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE users ADD COLUMN ban_label TEXT DEFAULT ''")
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

    c.execute("""CREATE TABLE IF NOT EXISTS app_settings (
        key   TEXT PRIMARY KEY,
        value TEXT DEFAULT ''
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

    try:
        c.execute("ALTER TABLE support_messages ADD COLUMN admin_read INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE support_messages ADD COLUMN user_read INTEGER DEFAULT 0")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE pending_payments ADD COLUMN plan_days INTEGER DEFAULT 30")
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

    # Импульс (momentum) ≥ 88 — сигналы с подтверждением ближе к голу
    c.execute("""CREATE TABLE IF NOT EXISTS impulse_signals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id    INTEGER,
        event_id    INTEGER,
        country     TEXT DEFAULT '',
        league      TEXT DEFAULT '',
        match_name  TEXT DEFAULT '',
        team        TEXT DEFAULT '',
        side        TEXT DEFAULT '',
        value       REAL,
        minute      INTEGER DEFAULT 0,
        score       TEXT DEFAULT '',
        logo_home   TEXT DEFAULT '',
        logo_away   TEXT DEFAULT '',
        created_at  TEXT,
        notified    INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS match_live (
        match_id     INTEGER PRIMARY KEY,
        event_id     INTEGER,
        is_live      INTEGER DEFAULT 0,
        minute       INTEGER DEFAULT 0,
        score_home   INTEGER DEFAULT 0,
        score_away   INTEGER DEFAULT 0,
        impulse_home REAL DEFAULT 0,
        impulse_away REAL DEFAULT 0,
        home_team    TEXT DEFAULT '',
        away_team    TEXT DEFAULT '',
        updated_at   TEXT
    )""")
    for col, typ in (
        ("da_home", "REAL DEFAULT 0"),
        ("da_away", "REAL DEFAULT 0"),
        ("press_home", "REAL DEFAULT 0"),
        ("press_away", "REAL DEFAULT 0"),
        ("sot_home", "REAL DEFAULT 0"),
        ("sot_away", "REAL DEFAULT 0"),
        ("shots_home", "REAL DEFAULT 0"),
        ("shots_away", "REAL DEFAULT 0"),
        ("corners_home", "REAL DEFAULT 0"),
        ("corners_away", "REAL DEFAULT 0"),
        ("last_goal_minute", "INTEGER"),
    ):
        try:
            c.execute(f"ALTER TABLE match_live ADD COLUMN {col} {typ}")
        except Exception:
            pass
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_impulse_created ON impulse_signals(created_at DESC)")
    except Exception:
        pass
    for col, typ in (
        ("goal_checked", "INTEGER DEFAULT 0"),
        ("goal_hit", "INTEGER"),
    ):
        try:
            c.execute(f"ALTER TABLE impulse_signals ADD COLUMN {col} {typ}")
        except Exception:
            pass

    # Накопленная статистика импульса (еженедельный rollup)
    c.execute("""CREATE TABLE IF NOT EXISTS impulse_stats_agg (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        scope         TEXT NOT NULL,
        country       TEXT DEFAULT '',
        league        TEXT DEFAULT '',
        league_id     INTEGER,
        stars         INTEGER DEFAULT 0,
        signals_count INTEGER DEFAULT 0,
        goals_count   INTEGER DEFAULT 0,
        delay_sum     REAL DEFAULT 0,
        delay_n       INTEGER DEFAULT 0,
        b_1_5         INTEGER DEFAULT 0,
        b_6_10        INTEGER DEFAULT 0,
        b_11_15       INTEGER DEFAULT 0,
        b_16_20       INTEGER DEFAULT 0,
        b_no_goal     INTEGER DEFAULT 0,
        league_logo   TEXT DEFAULT '',
        updated_at    TEXT,
        UNIQUE(scope, country, league, league_id, stars)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS impulse_stats_weeks (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        week_key       TEXT UNIQUE,
        week_start     TEXT,
        week_end       TEXT,
        signals_count  INTEGER DEFAULT 0,
        goals_count    INTEGER DEFAULT 0,
        payload_json   TEXT,
        created_at     TEXT
    )""")
    try:
        c.execute("CREATE INDEX IF NOT EXISTS idx_impulse_agg_league ON impulse_stats_agg(scope, country, league)")
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
        bonus_pending = 0
        try:
            if len(row) > 12 and row[12] is not None:
                bonus_pending = int(row[12] or 0)
        except Exception:
            bonus_pending = 0
        return {
            "user_id": row[0], "username": row[1], "first_name": row[2],
            "photo_url": row[3] if len(row) > 3 else "",
            "subscription": row[4], "end_date": row[5], "reg_date": row[6],
            "referred_by": row[7], "streak_days": row[8], "last_visit": row[9],
            "status": row[10], "notified_expire": row[11],
            "bonus_pending": bonus_pending,
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
        "bonus_pending": 0,
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
    if len(viewed_ids) >= 5:
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

async def create_yookassa_payment(user_id: int, amount: float = PREMIUM_PRICE, days: int = 30, plan_label: str = "1 месяц"):
    if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
        logger.error("YOOKASSA credentials missing")
        return None

    auth_string = f"{YOOKASSA_SHOP_ID}:{YOOKASSA_SECRET_KEY}"
    auth_header = base64.b64encode(auth_string.encode("utf-8")).decode("utf-8")

    url = "https://api.yookassa.ru/v3/payments"
    idempotence_key = f"{user_id}-{int(datetime.now(tz).timestamp())}-{days}"

    payload = {
        "amount": {"value": f"{amount:.2f}", "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": f"https://t.me/{BOT_USERNAME}"
        },
        "capture": True,
        "metadata": {"user_id": str(user_id), "days": str(days), "plan": plan_label},
        "description": f"Premium на {plan_label}",
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
                        try:
                            db_execute(
                                "INSERT OR REPLACE INTO pending_payments "
                                "(payment_id, user_id, amount, status, created_at, plan_days) VALUES (?, ?, ?, 'pending', ?, ?)",
                                (pid, user_id, amount, now, int(days))
                            )
                        except Exception:
                            db_execute(
                                "INSERT OR REPLACE INTO pending_payments "
                                "(payment_id, user_id, amount, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                                (pid, user_id, amount, now)
                            )
                        return {"payment_id": pid, "confirmation_url": curl, "days": days, "amount": amount}
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


# ====================== АВТО: очистка 06:00 / парсер 08:00 МСК ======================


def _parse_hhmm(s: str, default_minutes: int = 0) -> int:
    """'06:00' → минуты от полуночи."""
    try:
        parts = str(s or "").strip().split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h * 60 + m
    except Exception:
        pass
    return default_minutes


def _auto_get_ranks() -> list:
    raw = get_app_setting("auto_ranks", "1,2,3,4,5,6,7")
    ranks = []
    for p in str(raw).split(","):
        p = p.strip()
        if p.isdigit():
            v = int(p)
            if 1 <= v <= 99:
                ranks.append(v)
    return sorted(set(ranks)) or [1, 2, 3, 4, 5, 6, 7]


def _auto_get_key_leagues() -> list:
    """Список фрагментов названий лиг (lower) для доп. включения."""
    raw = get_app_setting("auto_key_leagues", "")
    if not raw:
        return []
    try:
        import json as _json
        data = _json.loads(raw)
        if isinstance(data, list):
            return [str(x).lower().strip() for x in data if str(x).strip()]
    except Exception:
        pass
    return [x.lower().strip() for x in str(raw).split(",") if x.strip()]


def _auto_get_max_matches() -> int:
    try:
        n = int(get_app_setting("auto_max_matches", "80") or 80)
        return max(1, min(300, n))
    except Exception:
        return 80


async def _auto_clear_all_matches() -> int:
    """Удаляет все матчи, просмотры и избранное."""
    n = 0
    try:
        row = db_fetchone("SELECT COUNT(*) FROM matches")
        n = int(row[0]) if row else 0
    except Exception:
        n = 0
    db_execute("DELETE FROM matches")
    db_execute("DELETE FROM viewed_matches")
    db_execute("DELETE FROM favorites")
    try:
        log_admin(ADMIN_ID or 0, "auto_clear_matches", f"удалено {n}, 06:00 МСК")
    except Exception:
        pass
    logger.info("[auto] cleared matches: %s", n)
    return n


async def _auto_parse_today_ranks_1_7() -> dict:
    """
    Парсит матчи на сегодня (МСК) и добавляет ранги 1–7 со статистикой.
    """
    if not BSD_API_KEY or not BSD_API_KEY.strip():
        logger.error("[auto] BSD_API_KEY пуст — парсер пропущен")
        return {"ok": False, "count": 0, "message": "нет API key"}

    date_str = datetime.now(tz).strftime("%Y-%m-%d")
    matches = await fetch_matches_from_api(date_str)
    if not matches:
        logger.info("[auto] нет матчей на %s", date_str)
        try:
            log_admin(ADMIN_ID or 0, "auto_parse", f"{date_str}: 0 матчей")
        except Exception:
            pass
        return {"ok": True, "count": 0, "message": "нет матчей"}

    leagues_map = await fetch_leagues_from_api()
    db_execute("DELETE FROM parsed_matches")

    for match in matches:
        home_raw = match.get("home_team", "?")
        away_raw = match.get("away_team", "?")
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
        league_info = leagues_map.get(league_id, {}) if isinstance(leagues_map, dict) else {}
        league_name = league_info.get("name", "Unknown") if isinstance(league_info, dict) else "Unknown"
        country_en = league_info.get("country", "") if isinstance(league_info, dict) else ""
        country_en = COUNTRY_NORMALIZE.get(country_en, country_en)
        priority = get_league_priority(league_name, country_en)
        match_time = ""
        event_date = match.get("event_date", "")
        if event_date:
            try:
                dt_parsed = datetime.fromisoformat(str(event_date).replace("Z", "+00:00"))
                if dt_parsed.tzinfo is None:
                    dt_parsed = dt_parsed.replace(tzinfo=ZoneInfo("UTC"))
                match_time = dt_parsed.astimezone(tz).strftime("%H:%M")
            except Exception:
                pass

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
            db_execute(
                "INSERT INTO parsed_matches (name, country, country_en, league, league_id, match_time, match_date, priority, logo_home, logo_away, event_id, home_team_id, away_team_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (name, country_en, country_en, league_name, league_id, match_time, date_str, priority,
                 logo_home, logo_away, event_id, home_tid, away_tid),
            )
        except Exception as e:
            logger.warning("[auto parse insert parsed] %s: %s", name, e)

    # Ранги и лимит из настроек
    ranks = _auto_get_ranks()
    key_leagues = _auto_get_key_leagues()  # lower name fragments
    max_matches = _auto_get_max_matches()
    select_cols = (
        "name, country, league, match_time, COALESCE(logo_home,''), COALESCE(logo_away,''), "
        "event_id, home_team_id, away_team_id, league_id, priority"
    )
    all_rows = db_fetchall(
        f"SELECT {select_cols} FROM parsed_matches ORDER BY priority ASC, match_time ASC"
    )
    rows = []
    for r in all_rows:
        pr = int(r[10]) if len(r) > 10 and r[10] is not None else 99
        league_name = (r[2] or "").lower()
        in_rank = pr in ranks
        in_key = any(k in league_name for k in key_leagues) if key_leagues else False
        if in_rank or in_key:
            rows.append(r[:10])  # without priority for insert loop
        if len(rows) >= max_matches:
            break

    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")
    count = 0
    errors = 0
    to_enrich = []

    for row_full in rows:
        name, country, league, match_time, logo_home, logo_away = (
            row_full[0], row_full[1], row_full[2], row_full[3], row_full[4], row_full[5]
        )
        event_id = row_full[6] if len(row_full) > 6 else None
        home_tid = row_full[7] if len(row_full) > 7 else None
        away_tid = row_full[8] if len(row_full) > 8 else None
        league_id = row_full[9] if len(row_full) > 9 else None
        league_logo = f"https://sports.bzzoiro.com/img/league/{league_id}/" if league_id else ""

        existing = db_fetchone("SELECT id FROM matches WHERE name = ? AND league = ?", (name, league))
        match_id = existing[0] if existing else None
        if not match_id:
            try:
                db_execute(
                    "INSERT INTO matches (name, country, league, match_time, text, created_at, logo_home, logo_away, event_id, home_team_id, away_team_id, stats_json, league_id, league_logo) "
                    "VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, '', ?, ?)",
                    (name, country, league, match_time or "", now, logo_home or "", logo_away or "",
                     event_id, home_tid, away_tid, league_id, league_logo),
                )
                row_id = db_fetchone(
                    "SELECT id FROM matches WHERE name = ? AND league = ? ORDER BY id DESC LIMIT 1",
                    (name, league),
                )
                match_id = row_id[0] if row_id else None
                count += 1
            except Exception as e:
                logger.warning("[auto insert] %s: %s", name, e)
                errors += 1
                continue
        else:
            count += 1
        if match_id and BSD_API_KEY:
            to_enrich.append((
                match_id, name, match_time or "", event_id, home_tid, away_tid,
                league_id, logo_home or "", logo_away or "", league_logo,
            ))

    if to_enrich:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=12, sock_read=35)
        enrich_sem = asyncio.Semaphore(3)

        async def _enrich_one(session, item):
            nonlocal errors
            match_id, name, match_time, event_id, home_tid, away_tid, league_id, logo_home, logo_away, league_logo = item
            async with enrich_sem:
                try:
                    if not event_id:
                        eid, hid, aid = await resolve_event_id(session, name, match_time, league_id)
                        if eid:
                            event_id = eid
                            home_tid = home_tid or hid
                            away_tid = away_tid or aid
                    stats_json = ""
                    if event_id:
                        pack = await fetch_match_enrichment(
                            session, int(event_id), home_tid, away_tid, league_id
                        )
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
                             logo_home, logo_home, logo_away, logo_away,
                             league_id, league_logo, league_logo, match_id),
                        )
                except Exception as e:
                    logger.warning("[auto enrich] %s: %s", name, e)
                    errors += 1

        async with aiohttp.ClientSession(timeout=timeout) as session:
            await asyncio.gather(*[_enrich_one(session, item) for item in to_enrich])

    msg = f"{date_str}: +{count} матчей (ранги 1–7), err={errors}"
    try:
        log_admin(ADMIN_ID or 0, "auto_parse", msg)
    except Exception:
        pass
    logger.info("[auto] parse done: %s", msg)
    return {"ok": True, "count": count, "errors": errors, "message": msg}


async def _auto_daily_loop():
    """Фон: каждый день 06:00 МСК — очистка, 08:00 МСК — парсер рангов 1–7."""
    await asyncio.sleep(3)
    while True:
        try:
            enabled = get_app_setting("auto_daily", "0") == "1"
            if enabled:
                now = datetime.now(tz)
                today = now.strftime("%Y-%m-%d")
                hm = now.hour * 60 + now.minute

                clear_hm = _parse_hhmm(get_app_setting("auto_clear_time", "06:00"), 6 * 60)
                parse_hm = _parse_hhmm(get_app_setting("auto_parse_time", "08:00"), 8 * 60)

                last_clear = get_app_setting("auto_last_clear", "")
                last_parse = get_app_setting("auto_last_parse", "")

                # окно очистки 15 мин
                if clear_hm <= hm < clear_hm + 15 and last_clear != today:
                    await _auto_clear_all_matches()
                    set_app_setting("auto_last_clear", today)

                # еженедельный rollup Импульса: Пн, время МСК → сохранить статистику + очистить сигналы
                if get_app_setting("impulse_weekly_rollup", "1") == "1":
                    ich = _parse_hhmm(get_app_setting("impulse_auto_clear_time", "06:00"), 6 * 60)
                    if now.weekday() == 0 and ich <= hm < ich + 30:
                        try:
                            res = _impulse_weekly_rollup(force=False)
                            if not res.get("skipped"):
                                logger.info("[impulse] weekly rollup: %s", res.get("message") or res)
                        except Exception:
                            logger.exception("[impulse] weekly rollup failed")
                # ежедневная очистка только сигналов (без rollup), если включена отдельно
                elif get_app_setting("impulse_auto_clear", "0") == "1":
                    ich = _parse_hhmm(get_app_setting("impulse_auto_clear_time", "06:00"), 6 * 60)
                    last_ic = get_app_setting("impulse_auto_last_clear", "")
                    if ich <= hm < ich + 15 and last_ic != today:
                        try:
                            _clear_impulse_data()
                            set_app_setting("impulse_auto_last_clear", today)
                            logger.info("[impulse] auto-clear done at %s", now.strftime("%H:%M"))
                        except Exception:
                            logger.exception("[impulse] auto-clear failed")

                # окно парсера 30 мин
                if parse_hm <= hm < parse_hm + 30 and last_parse != today:
                    set_app_setting("auto_last_parse", today)
                    try:
                        await _auto_parse_today_ranks_1_7()
                    except Exception:
                        logger.exception("[auto] parse failed")
                        set_app_setting("auto_last_parse", "")
        except Exception:
            logger.exception("[auto] loop error")
        await asyncio.sleep(30)


# ====================== ИМПУЛЬС (важный сигнал: порог + свежий момент) ======================
# Правила сигнала (ближе к голу):
#   • импульс ≥ 88
#   • SOT+1 → да (лучший маркер)
#   • или DA+3 + shots+1 + press ≥ 55%
#   • или новый угловой + press ≥ 60% (+ импульс ≥ 88)  [ужесточено]
#   • импульс ≥ 92 — только с finishing (SOT / удар / угловой)
#   • владение > 65% без свежего удара/углового → нет
#   • кулдаун 12′ (после 2-го сигнала по стороне → 18′)
#   • тишина 4′ после гола
#   • минуты: 11–43 (1Т); с 44′ до конца 1Т — тишина; 2Т только после 48′ (49–89)
#   • поздняя осада (мин ≥ 55, отстаём): только SOT+1 или импульс ≥ 92 + finishing
#   • с 68′ до конца — не более 1 сигнала на матч (берём первый по времени)
#   • макс. 4 сигнала на сторону за матч

IMPULSE_THRESHOLD = 88.0
# прирост опасных атак между сканами
IMPULSE_DA_GROWTH = 3.0
# минимум минут между сигналами по одной стороне матча
IMPULSE_MATCH_COOLDOWN_MIN = 12
# после 2+ сигналов по стороне — длиннее кулдаун
IMPULSE_MATCH_COOLDOWN_ESCALATED = 18
# новый сигнал только если импульс заметно выше предыдущего по стороне
IMPULSE_PEAK_DELTA = 4.0
# с этой минуты — максимум один сигнал до конца матча
IMPULSE_LATE_SINGLE_MINUTE = 68
# доля давления в последних ~12 мин (0–100), ниже — слабый момент
IMPULSE_MIN_PRESS = 55.0
# для углового и путей без SOT — жёстче
IMPULSE_MIN_PRESS_NO_SOT = 60.0
# очень сильный импульс — только с finishing
IMPULSE_STRONG = 92.0
# минут тишины после гола (любой стороны)
IMPULSE_POST_GOAL_MIN = 4
# владение выше — без удара/углового не сигналим (анти-шум)
IMPULSE_MAX_POSSESSION_NO_SHOT = 65.0
# с этой минуты — режим «осады» (строже для отстающих)
IMPULSE_SIEGE_MINUTE = 55
# максимум сигналов на одну сторону за матч
IMPULSE_MAX_SIGNALS_PER_SIDE = 4


def _impulse_signal_minute_allowed(minute, period: str = "", status: str = "", raw_clock: str = "") -> bool:
    """
    Когда можно слать сигнал:
      • 1-й тайм: только 11–43′
      • с 44′ до конца 1-го тайма (включая 45+N / компенсированные) — НЕ слать
      • 2-й тайм: только после 48′ (49–89)
    Минута 49 во время 45+4 первого тайма часто приходит как 49 — режем по period/clock.
    """
    try:
        m = int(minute or 0)
    except (TypeError, ValueError):
        return False
    if m <= 10 or m > 89:
        return False

    blob = f"{period or ''} {status or ''} {raw_clock or ''}".lower()
    clock = str(raw_clock or status or period or "")

    # Явный перерыв
    if any(x in blob for x in (
        "ht", "half time", "halftime", "half-time", "pause", "break",
        "half_time", "half-time",
    )):
        return False

    is_1h = any(x in blob for x in (
        "1st half", "first half", "1st_half", "first_half", "1h", "1 half", "period 1", "period1", "p1",
    ))
    is_2h = any(x in blob for x in (
        "2nd half", "second half", "2nd_half", "second_half", "2h", "2 half", "period 2", "period2", "p2",
    ))

    # Часы вида 45+2 / 45+4 → всё ещё конец 1-го тайма
    m45 = re.search(r"\b45\s*\+\s*(\d{1,2})\b", clock)
    if m45:
        return False
    # 46–55 без явного 2H при «1H» / без периода — зона перерыва / доигрыша 1Т
    if is_1h:
        return 11 <= m <= 43
    if is_2h:
        return 49 <= m <= 89

    # Нет явного period: 11–43 ок; 44–48 тишина; 49–89 — 2Т
    # (ложные 49′ от «45+4» отсекаются выше через 45+N → period 1st half)
    if 11 <= m <= 43:
        return True
    if 44 <= m <= 48:
        return False
    if 49 <= m <= 89:
        return True
    return False




def _stats_side_num(stats_raw, side: str, *keys) -> Optional[float]:
    """Число из /stats/ для home|away по списку ключей."""
    if not stats_raw or not isinstance(stats_raw, dict):
        return None
    bag = stats_raw.get("stats") if isinstance(stats_raw.get("stats"), dict) else stats_raw
    if not isinstance(bag, dict):
        return None
    side_bag = bag.get(side) if isinstance(bag.get(side), dict) else None
    sources = []
    if side_bag:
        sources.append(side_bag)
    sources.append(bag)
    for src in sources:
        for k in keys:
            if k not in src or src[k] is None:
                continue
            v = src[k]
            if isinstance(v, dict):
                if side in v:
                    try:
                        return float(v[side])
                    except (TypeError, ValueError):
                        pass
                v = v.get("actual") or v.get("value") or v.get("total")
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return _side_stat_num(bag, side, *keys)


def _side_impulse_confirm(
    stats_raw,
    side: str,
    prev_da: Optional[float] = None,
    prev_sot: Optional[float] = None,
    prev_shots: Optional[float] = None,
    impulse_val: float = 0.0,
    press_side: Optional[float] = None,
    prev_corners: Optional[float] = None,
    minute: int = 0,
    score_home: int = 0,
    score_away: int = 0,
) -> bool:
    """
    Подтверждение ближе к голу (меньше холостых).
    База: impulse_val ≥ IMPULSE_THRESHOLD (проверяется снаружи).
    A) +1 удар в створ (SOT) — лучший маркер.
    B) DA+3 и shots+1 и pressure ≥55%.
    B2) новый угловой + pressure ≥60% (+ импульс ≥88).
    C) импульс ≥92 — только с finishing + press ≥55%.
    Поздняя осада (мин ≥55 и сторона отстаёт): только A или C.
    Анти-шум: владение >65% без свежего удара/углового — отказ.
    """
    sot = _stats_side_num(stats_raw, side, "shots_on_target", "shots_on", "on_target")
    shots = _stats_side_num(stats_raw, side, "total_shots", "shots", "shots_total")
    corners = _stats_side_num(stats_raw, side, "corners", "corner_kicks", "corner")
    poss = _stats_side_num(
        stats_raw, side,
        "ball_possession", "possession", "possession_pct", "ball_posession",
    )

    sot_up = False
    if sot is not None:
        base_sot = float(prev_sot) if prev_sot is not None else 0.0
        sot_up = sot >= base_sot + 1.0
    shots_up = False
    if shots is not None:
        base_sh = float(prev_shots) if prev_shots is not None else 0.0
        shots_up = shots >= base_sh + 1.0
    corner_up = False
    if corners is not None:
        base_c = float(prev_corners) if prev_corners is not None else 0.0
        corner_up = corners >= base_c + 1.0
    has_sot = sot is not None and float(sot) >= 1.0

    da = _stats_side_num(
        stats_raw, side,
        "dangerous_attacks", "danger_attacks", "dangerous_attack",
    )
    da_up = False
    if da is not None:
        base = float(prev_da) if prev_da is not None else 0.0
        da_up = da >= base + IMPULSE_DA_GROWTH

    try:
        press = float(press_side) if press_side is not None else None
    except (TypeError, ValueError):
        press = None

    # анти-шум: много владения, но нет свежего удара/углового
    try:
        if poss is not None and float(poss) >= IMPULSE_MAX_POSSESSION_NO_SHOT:
            if not (sot_up or shots_up or corner_up):
                return False
    except (TypeError, ValueError):
        pass

    finishing = shots_up or corner_up or has_sot
    imp = float(impulse_val or 0)

    # Режим «осады»: после 55′ отстающая сторона — только сильные маркеры
    trailing = (side == "home" and score_home < score_away) or (
        side == "away" and score_away < score_home
    )
    siege = int(minute or 0) >= IMPULSE_SIEGE_MINUTE and trailing

    # A: свежий удар в створ — всегда лучший маркер
    if sot_up:
        return True

    # В осаде без SOT — только путь C (очень высокий импульс + finishing)
    if siege:
        if imp >= IMPULSE_STRONG and finishing:
            if press is None or press >= IMPULSE_MIN_PRESS:
                return True
        return False

    # B: DA + удар + давление
    if da_up and shots_up and imp >= IMPULSE_THRESHOLD:
        if press is None or press >= IMPULSE_MIN_PRESS:
            return True

    # B2: угловой — жёстче по давлению (≥60%)
    if corner_up and imp >= IMPULSE_THRESHOLD:
        if press is None or press >= IMPULSE_MIN_PRESS_NO_SOT:
            return True

    # C: очень высокий импульс — только с finishing
    if imp >= IMPULSE_STRONG:
        if finishing or (da_up and shots_up):
            if press is None or press >= IMPULSE_MIN_PRESS:
                return True
    return False


def _side_stat_num(bag: dict, side: str, *keys) -> Optional[float]:
    """Достаёт число из live/stats bag для стороны home/away."""
    if not isinstance(bag, dict):
        return None

    def _as_float(v):
        if v is None:
            return None
        if isinstance(v, dict):
            for kk in ("value", "total", "avg", "average", "pct", side):
                if kk in v and v[kk] is not None and not isinstance(v[kk], dict):
                    try:
                        return float(v[kk])
                    except (TypeError, ValueError):
                        pass
            if side in v and isinstance(v[side], (int, float)):
                return float(v[side])
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    side_bag = bag.get(side) if isinstance(bag.get(side), dict) else None
    for k in keys:
        if side_bag is not None and k in side_bag:
            fv = _as_float(side_bag.get(k))
            if fv is not None:
                return fv
        if k in bag:
            fv = _as_float(bag.get(k))
            if fv is not None:
                # иногда {home: x, away: y}
                raw = bag.get(k)
                if isinstance(raw, dict) and side in raw:
                    try:
                        return float(raw[side])
                    except (TypeError, ValueError):
                        pass
                if not isinstance(raw, dict):
                    return fv
    return None


def _momentum_from_series(stats_raw, side: str) -> Optional[float]:
    """
    BSD /stats/ отдаёт momentum: [{m: минута, v: давление}], v>0 — хозяева, v<0 — гости.
    Берём среднее |v| по последним 8 точкам для нужной стороны → 0–100.
    """
    if not isinstance(stats_raw, dict):
        return None
    series = stats_raw.get("momentum")
    if not isinstance(series, list) or not series:
        return None
    vals = []
    for pt in series[-12:]:
        if not isinstance(pt, dict):
            continue
        try:
            v = float(pt.get("v"))
        except (TypeError, ValueError):
            continue
        if side == "home" and v > 0:
            vals.append(v)
        elif side == "away" and v < 0:
            vals.append(abs(v))
    if not vals:
        # нет давления на эту сторону в хвосте — попробуем по всем точкам
        for pt in series:
            if not isinstance(pt, dict):
                continue
            try:
                v = float(pt.get("v"))
            except (TypeError, ValueError):
                continue
            if side == "home" and v > 0:
                vals.append(v)
            elif side == "away" and v < 0:
                vals.append(abs(v))
    if not vals:
        return 0.0
    # типичный диапазон |v| ~ 0–40+, нормализуем
    avg = sum(vals) / len(vals)
    peak = max(vals)
    score = avg * 0.55 + peak * 0.45
    return min(100.0, max(0.0, score / 28.0 * 100.0))


def _danger_last_10(stats_raw, current_minute: int) -> tuple:
    """
    Доля голевого давления за последние 10 минут по momentum
    (оптимум «кто давит и кто ближе к голу» после сигнала).
    Возвращает (home_pct, away_pct) — сумма 100, либо (None, None) если нет данных.
    """
    if not isinstance(stats_raw, dict):
        return None, None
    series = stats_raw.get("momentum")
    if not isinstance(series, list) or not series:
        return None, None
    cur = int(current_minute or 0)
    lo = max(0, cur - 10) if cur > 0 else 0
    h = a = 0.0
    for pt in series:
        if not isinstance(pt, dict):
            continue
        try:
            m = pt.get("m")
            if m is None:
                m = pt.get("minute")
            m = int(float(m if m is not None else 0))
            v = float(pt.get("v") or 0)
        except (TypeError, ValueError):
            continue
        if cur > 0 and (m < lo or m > cur + 1):
            continue
        if v > 0:
            h += v
        elif v < 0:
            a += abs(v)
    # если фильтр по минуте пуст — берём хвост серии (последние ~12 точек)
    if h + a <= 0:
        for pt in series[-14:]:
            if not isinstance(pt, dict):
                continue
            try:
                v = float(pt.get("v") or 0)
            except (TypeError, ValueError):
                continue
            if v > 0:
                h += v
            elif v < 0:
                a += abs(v)
    total = h + a
    if total <= 0:
        return None, None
    hp = round(h / total * 100.0, 1)
    return hp, round(100.0 - hp, 1)


def compute_impulse_from_stats(stats_raw, side: str) -> float:
    """
    Шкала импульса 0–100 по live-статистике BSD.
    Структура stats: { stats: { home: {ball_possession, total_shots, shots_on_target, ...}, away: {...} },
                      momentum: [{m, v}, ...] }
    """
    if not stats_raw or not isinstance(stats_raw, dict):
        return 0.0
    bag = stats_raw.get("stats") if isinstance(stats_raw.get("stats"), dict) else stats_raw
    if not isinstance(bag, dict):
        return 0.0

    # side block: stats.home / stats.away
    side_bag = bag.get(side) if isinstance(bag.get(side), dict) else None
    if side_bag is None and isinstance(bag.get("home"), dict):
        # иногда ключи на верхнем уровне
        side_bag = {}

    def _from_side(*keys):
        if side_bag:
            for k in keys:
                if k in side_bag and side_bag[k] is not None:
                    try:
                        v = side_bag[k]
                        if isinstance(v, dict):
                            v = v.get("actual") or v.get("value") or v.get("total")
                        return float(v)
                    except (TypeError, ValueError):
                        pass
        return _side_stat_num(bag, side, *keys)

    da = _from_side("dangerous_attacks", "danger_attacks", "dangerous_attack")
    at = _from_side("attacks", "total_attacks", "attack")
    sot = _from_side("shots_on_target", "shots_on", "on_target")
    shots = _from_side("total_shots", "shots", "shots_total")
    pos = _from_side("ball_possession", "possession")
    corners = _from_side("corners", "corner_kicks")
    mom = _momentum_from_series(stats_raw, side)

    parts = []
    if mom is not None and mom > 0:
        parts.append((mom, 0.50))
    if da is not None:
        parts.append((min(100.0, da / 22.0 * 100.0), 0.28))
    if at is not None and da is not None and at > 0:
        parts.append((min(100.0, (da / at) * 120.0), 0.12))
    elif at is not None:
        parts.append((min(100.0, at / 55.0 * 100.0), 0.10))
    if sot is not None:
        parts.append((min(100.0, sot / 5.0 * 100.0), 0.22))
    if shots is not None:
        parts.append((min(100.0, shots / 11.0 * 100.0), 0.12))
    if corners is not None:
        parts.append((min(100.0, corners / 6.0 * 100.0), 0.08))
    if pos is not None:
        p = pos * 100.0 if pos <= 1.5 else pos
        parts.append((min(100.0, max(0.0, p)), 0.06))

    if not parts:
        return 0.0
    wsum = sum(w for _, w in parts)
    if wsum <= 0:
        return 0.0
    val = sum(v * w for v, w in parts) / wsum
    return round(min(100.0, max(0.0, val)), 1)


def _parse_event_live(event: dict) -> dict:
    """Извлекает live-статус, минуту и счёт из BSD event."""
    out = {
        "is_live": False,
        "minute": 0,
        "score_home": 0,
        "score_away": 0,
        "home_team": "",
        "away_team": "",
        "status": "",
    }
    if not isinstance(event, dict):
        return out
    st = str(
        event.get("status")
        or event.get("state")
        or event.get("match_status")
        or event.get("event_status")
        or event.get("status_name")
        or ""
    ).lower()
    out["status"] = st
    # HT / перерыв / FT / ET / пенальти — не «активная игра» для импульса
    break_markers = (
        "ht", "half time", "halftime", "half-time", "pause", "break",
        "ft", "finished", "ended", "full time", "fulltime",
        "aet", "after et", "after extra", "extra time", "extratime", "extra-time",
        "penalty", "penalties", "penalt", "shootout", "shoot-out", "pso",
        "after pen", "apens", "pen.",
        "postponed", "cancelled", "canceled", "abandoned", "ns", "not started", "scheduled",
    )
    live_markers = (
        "live", "1h", "2h", "inplay", "in_play", "playing",
        "1st", "2nd", "1st half", "2nd half", "first half", "second half",
        "in progress", "ongoing",
    )
    # period / stage (серия пенальти часто здесь, а status остаётся live)
    period = str(
        event.get("period") or event.get("period_name") or event.get("stage")
        or event.get("phase") or event.get("match_period") or event.get("time_status")
        or ""
    ).lower()
    blob = f"{st} {period}"
    stopped = any(m in blob for m in break_markers)
    # точечно: pen/et как отдельное слово (не "pending")
    if re.search(r"(?:^|[^a-z])(?:pen|pens|et|aet|pso)(?:[^a-z]|$)", blob):
        stopped = True
    if stopped:
        out["is_live"] = False
    else:
        out["is_live"] = any(m in st for m in live_markers) or bool(event.get("is_live"))
    out["period"] = period

    def _to_minute(v):
        if v is None:
            return None
        if isinstance(v, dict):
            for kk in ("minute", "elapsed", "value", "min", "current", "display"):
                if v.get(kk) is not None:
                    got = _to_minute(v.get(kk))
                    if got is not None:
                        return got
            return None
        if isinstance(v, (int, float)):
            try:
                n = int(v)
                return n if n >= 0 else None
            except (TypeError, ValueError):
                return None
        s = str(v).strip()
        if not s:
            return None
        # "45+2", "90+3'", "67'", "HT", "1H 32"
        m = re.search(r"(\d+)\s*\+\s*(\d+)", s)
        if m:
            return int(m.group(1)) + int(m.group(2))
        m = re.search(r"(\d{1,3})", s)
        if m:
            n = int(m.group(1))
            if 0 <= n <= 130:
                return n
        return None

    # BSD live: current_minute; detail: minute / elapsed / ...
    minute_keys = (
        "current_minute", "minute", "elapsed", "match_minute", "time", "clock", "timer",
        "match_time", "game_minute", "minutes",
        "time_elapsed", "elapsed_time", "status_time",
    )
    for k in minute_keys:
        got = _to_minute(event.get(k))
        if got is not None and got > 0:
            out["minute"] = got
            break
    # nested timer / period objects
    if out["minute"] <= 0:
        for nest_key in ("timer", "clock", "time_info", "match_clock", "period"):
            nest = event.get(nest_key)
            got = _to_minute(nest)
            if got is not None and got > 0:
                out["minute"] = got
                break
    # status string like "2H 67" / "67'"
    if out["minute"] <= 0 and st:
        got = _to_minute(st)
        if got is not None and got > 0:
            out["minute"] = got

    # 45+N / 90+N: компенсированное — помечаем period, чтобы не слать импульс в конце 1Т
    try:
        clock_blob = " ".join(
            str(event.get(k) or "")
            for k in ("current_minute", "minute", "elapsed", "time", "clock", "timer", "status", "status_name")
        )
        if re.search(r"\b45\s*\+\s*\d{1,2}\b", clock_blob):
            # конец первого тайма (добавленное время)
            if not out.get("period"):
                out["period"] = "1st half"
            else:
                per = str(out.get("period") or "")
                if "2nd" not in per and "second" not in per and "2h" not in per:
                    out["period"] = (per + " 1st half").strip()
        if re.search(r"\b90\s*\+\s*\d{1,2}\b", clock_blob):
            if not out.get("period"):
                out["period"] = "2nd half"
    except Exception:
        pass

    # счёт
    for hk, ak in (
        ("home_score", "away_score"),
        ("score_home", "score_away"),
        ("home_goals", "away_goals"),
        ("goals_home", "goals_away"),
        ("home_team_score", "away_team_score"),
    ):
        if event.get(hk) is not None and event.get(ak) is not None:
            try:
                out["score_home"] = int(event.get(hk))
                out["score_away"] = int(event.get(ak))
                break
            except (TypeError, ValueError):
                pass
    sc = event.get("score") or event.get("ss") or event.get("result")
    if isinstance(sc, dict):
        try:
            out["score_home"] = int(sc.get("home") or sc.get("home_score") or sc.get("1") or out["score_home"])
            out["score_away"] = int(sc.get("away") or sc.get("away_score") or sc.get("2") or out["score_away"])
        except (TypeError, ValueError):
            pass
    elif isinstance(sc, str) and re.match(r"^\d+\s*[-:]\s*\d+$", sc.strip()):
        parts = re.split(r"[-:]", sc)
        try:
            out["score_home"] = int(parts[0].strip())
            out["score_away"] = int(parts[1].strip())
        except (TypeError, ValueError):
            pass
    # имена
    ht = event.get("home_team") or event.get("home") or ""
    at = event.get("away_team") or event.get("away") or ""
    if isinstance(ht, dict):
        ht = ht.get("name") or ""
    if isinstance(at, dict):
        at = at.get("name") or ""
    out["home_team"] = str(ht or "")
    out["away_team"] = str(at or "")
    # перерыв / конец — никогда не live для сигналов
    fin_or_break = (
        "ft", "finished", "ended", "after", "aet", "pen", "postponed",
        "cancelled", "ns", "not started", "scheduled", "canceled", "abandoned",
        "ht", "half time", "halftime", "half-time", "pause", "break",
        "full time", "fulltime", "match finished",
    )
    if any(f in st for f in fin_or_break):
        out["is_live"] = False
    elif out["minute"] > 0 and out["is_live"]:
        pass
    elif out["minute"] > 0 and not any(f in st for f in fin_or_break):
        # минута есть, статус не break/FT — можно считать live
        out["is_live"] = True
    return out


def _tg_match_open_markup(match_id: int, tab: str = None) -> dict:
    """Кнопка «Открыть матч» → Mini App. tab='impulse' открывает вкладку Импульс."""
    mid = int(match_id or 0)
    public = (os.getenv("PUBLIC_URL") or "").rstrip("/")
    mini_short = (os.getenv("MINI_APP_SHORT_NAME") or "").strip()
    tab_s = (tab or "").strip().lower()
    if mid and tab_s == "impulse":
        start_param = f"match_{mid}_impulse"
    elif mid:
        start_param = f"match_{mid}"
    else:
        start_param = "startapp"
    if public and mid:
        q_tab = f"&tab={tab_s}" if tab_s else ""
        url = f"{public}/?match_id={mid}&startapp={start_param}{q_tab}"
        return {
            "inline_keyboard": [[{
                "text": "⚽ Открыть матч",
                "web_app": {"url": url},
            }]]
        }
    if mini_short:
        app_url = f"https://t.me/{BOT_USERNAME}/{mini_short}?startapp={start_param}"
    else:
        app_url = f"https://t.me/{BOT_USERNAME}?startapp={start_param}"
    return {
        "inline_keyboard": [[{
            "text": "⚽ Открыть матч",
            "url": app_url,
        }]]
    }


def _impulse_stars_text(val) -> str:
    """Текстовые звёзды силы: 77–82 ★, 83–89 ★★, 90+ ★★★."""
    try:
        v = float(val or 0)
    except (TypeError, ValueError):
        v = 0.0
    if v >= 90:
        return "★★★"
    if v >= 83:
        return "★★"
    if v >= 77:
        return "★"
    return ""


async def _impulse_notify(signal: dict):
    """Telegram-уведомление Premium-пользователям о импульсе + кнопка на матч."""
    if not BOT_TOKEN:
        logger.warning("[impulse] notify: BOT_TOKEN пуст — уведомления не отправятся")
        return
    minute = int(signal.get("minute") or 0)
    if not _impulse_signal_minute_allowed(minute, signal.get("period") or "", signal.get("status") or ""):
        logger.info("[impulse] notify skip: minute=%s (вне окна 11–43 / 2Т после 48′)", minute)
        return
    val_i = int(round(float(signal.get("value") or 0)))
    text = (
        f"⚡ Импульс {val_i}\n"
        f"{signal.get('flag') or ''} {signal.get('country') or ''} · {signal.get('league') or ''}\n"
        f"{signal.get('match_name') or ''}\n"
        f"{minute}' · {signal.get('team') or ''}\n"
        f"Счёт {signal.get('score') or '—'}"
    )
    match_id = int(signal.get("match_id") or 0)
    reply_markup = _tg_match_open_markup(match_id, tab="impulse") if match_id else None
    try:
        rows = db_fetchall(
            "SELECT user_id FROM users WHERE LOWER(COALESCE(subscription,'')) = 'premium' "
            "AND COALESCE(status,'active') != 'banned' LIMIT 500"
        )
    except Exception:
        try:
            rows = db_fetchall(
                "SELECT user_id FROM users WHERE subscription = 'Premium' AND COALESCE(status,'active') != 'banned' LIMIT 500"
            )
        except Exception:
            rows = []
    if not rows:
        logger.info("[impulse] notify: нет Premium-пользователей для рассылки")
        if ADMIN_ID:
            try:
                payload = {"chat_id": int(ADMIN_ID), "text": text + "\n\n(нет Premium для рассылки)"}
                if reply_markup:
                    payload["reply_markup"] = reply_markup
                await _tg_api("sendMessage", payload)
            except Exception:
                pass
        return
    ok_n, fail_n = 0, 0
    for r in rows:
        uid = r[0] if r else None
        if not uid:
            continue
        try:
            payload = {
                "chat_id": int(uid),
                "text": text,
                "disable_web_page_preview": True,
            }
            if reply_markup:
                payload["reply_markup"] = reply_markup
            resp = await _tg_api("sendMessage", payload)
            if resp and resp.get("ok"):
                ok_n += 1
            else:
                fail_n += 1
                if resp:
                    logger.warning("[impulse] notify fail uid=%s: %s", uid, resp.get("description") or resp)
            await asyncio.sleep(0.04)
        except Exception as e:
            fail_n += 1
            logger.warning("[impulse] notify exception uid=%s: %s", uid, e)
    logger.info("[impulse] notify done: ok=%s fail=%s value=%s match=%s",
                ok_n, fail_n, signal.get("value"), signal.get("match_name"))


def _live_event_fields(ev: dict) -> dict:
    """Поля из /events/live/ или detail: minute, score, teams, status."""
    live = _parse_event_live(ev if isinstance(ev, dict) else {})
    if not isinstance(ev, dict):
        return live
    # BSD live shape
    for k in ("current_minute", "minute"):
        if ev.get(k) is not None:
            try:
                m = int(ev.get(k))
                if m >= 0:
                    live["minute"] = m
                    break
            except (TypeError, ValueError):
                pass
    for hk, ak in (("home_score", "away_score"), ("score_home", "score_away")):
        if ev.get(hk) is not None and ev.get(ak) is not None:
            try:
                live["score_home"] = int(ev.get(hk))
                live["score_away"] = int(ev.get(ak))
                break
            except (TypeError, ValueError):
                pass
    period = str(ev.get("period") or ev.get("status") or ev.get("status_name") or "").lower()
    if period and period not in (live.get("status") or ""):
        live["status"] = period
    fin_or_break = (
        "ft", "finished", "ended", "after", "aet", "penalties", "postponed",
        "cancelled", "canceled", "abandoned", "ns", "not started", "scheduled",
        "full time", "fulltime", "match finished", "complete", "completed",
        "ht", "half time", "halftime", "half-time", "pause", "break",
    )
    if any(f in period for f in fin_or_break):
        live["is_live"] = False
    elif period in ("1h", "2h", "live", "1st", "2nd", "et") or "live" in period or "inplay" in period:
        live["is_live"] = True
    elif live["minute"] > 0 and not any(f in (live.get("status") or "") for f in fin_or_break):
        live["is_live"] = True
    # границы тайма: 45' на HT и 90'+ на FT часто приходят с «ложным» live — отсекаем
    m = int(live.get("minute") or 0)
    if m in (45, 46) and ("ht" in period or "half" in period and "1st" not in period and "2nd" not in period):
        live["is_live"] = False
    if m >= 90 and any(f in period for f in ("ft", "finished", "ended", "full")):
        live["is_live"] = False
    ht = ev.get("home_team") or ev.get("home") or live.get("home_team") or ""
    at = ev.get("away_team") or ev.get("away") or live.get("away_team") or ""
    if isinstance(ht, dict):
        ht = ht.get("name") or ""
    if isinstance(at, dict):
        at = at.get("name") or ""
    if ht:
        live["home_team"] = str(ht)
    if at:
        live["away_team"] = str(at)
    return live


async def _scan_impulse_once():
    """
    Скан импульса:
    1) Берём live с BSD /events/live/ (надёжнее, чем опрашивать все матчи)
    2) Сопоставляем с матчами в БД по event_id
    3) Тянем /stats/, считаем impulse, пишем сигналы ≥ 88 (с подтверждением)
    """
    if not BSD_API_KEY or not BSD_API_KEY.strip():
        logger.warning("[impulse] BSD_API_KEY пуст — скан пропущен")
        return

    # локальные матчи с event_id
    try:
        rows = db_fetchall(
            "SELECT id, name, country, league, event_id, COALESCE(logo_home,''), COALESCE(logo_away,'') "
            "FROM matches WHERE event_id IS NOT NULL AND event_id != 0"
        )
    except Exception:
        logger.exception("[impulse] db matches")
        return
    if not rows:
        logger.info("[impulse] нет матчей с event_id в БД")
        return

    by_eid = {}
    for r in rows:
        try:
            eid = int(r[4])
        except (TypeError, ValueError):
            continue
        by_eid[eid] = {
            "match_id": r[0], "name": r[1] or "", "country": r[2] or "", "league": r[3] or "",
            "event_id": eid, "logo_home": r[5] or "", "logo_away": r[6] or "",
        }

    async with aiohttp.ClientSession() as session:
        live_payload = await _bsd_get(session, "events/live/", params={"limit": 200})
        live_list = []
        if isinstance(live_payload, dict):
            live_list = live_payload.get("results") or live_payload.get("events") or live_payload.get("data") or []
        elif isinstance(live_payload, list):
            live_list = live_payload

        # fallback: если live endpoint пуст — точечно проверяем наши event_id (ограниченно)
        candidates = []
        if live_list:
            for ev in live_list:
                if not isinstance(ev, dict):
                    continue
                eid = ev.get("id") or ev.get("event_id")
                try:
                    eid = int(eid)
                except (TypeError, ValueError):
                    continue
                meta = by_eid.get(eid)
                if not meta:
                    continue
                lf = _live_event_fields(ev)
                if not lf.get("is_live"):
                    continue
                # окно минут: 11–43 (1Т); 2Т только после 48′
                _m = int(lf.get("minute") or 0)
                _per = str(lf.get("period") or lf.get("status") or "")
                if not _impulse_signal_minute_allowed(_m, _per, str(lf.get("status") or "")):
                    continue
                candidates.append((meta, lf, eid))
            logger.info("[impulse] live API: %s всего, пересечение с БД: %s", len(live_list), len(candidates))
        else:
            # нет live списка — проверяем до 25 матчей из БД
            logger.info("[impulse] events/live пуст — fallback по event_id (%s в БД)", len(by_eid))
            sem = asyncio.Semaphore(6)

            async def _probe(eid, meta):
                async with sem:
                    ev = await _bsd_get(session, f"events/{eid}/")
                    if not ev:
                        return None
                    lf = _live_event_fields(ev)
                    if not lf.get("is_live"):
                        return None
                    return (meta, lf, eid)

            probed = await asyncio.gather(
                *[_probe(eid, meta) for eid, meta in list(by_eid.items())[:40]],
                return_exceptions=True,
            )
            for p in probed:
                if isinstance(p, tuple):
                    candidates.append(p)

        if not candidates:
            # сброс is_live у всех, кто был live
            try:
                db_execute("UPDATE match_live SET is_live = 0 WHERE is_live = 1")
            except Exception:
                pass
            return

        sem = asyncio.Semaphore(6)

        async def _process(meta, lf, eid):
            async with sem:
                match_id = meta["match_id"]
                minute = int(lf.get("minute") or 0)
                st = str(lf.get("status") or "").lower()
                period = str(lf.get("period") or "").lower()
                blob = f"{st} {period}"
                is_live = bool(lf.get("is_live"))

                # всегда тянем stats и закрываем окна 20′ (даже HT / FT)
                stats = None
                try:
                    if eid:
                        stats = await _bsd_get(session, f"events/{eid}/stats/")
                except Exception:
                    stats = None
                try:
                    _impulse_check_goals_live(
                        match_id,
                        int(minute or 0),
                        int(lf.get("score_home", 0) or 0),
                        int(lf.get("score_away", 0) or 0),
                        is_live,
                        str(lf.get("status") or ""),
                        stats_raw=stats,
                    )
                except Exception:
                    logger.exception("[impulse] goal check early")

                # новые сигналы — только в активной игре основного времени
                if not is_live:
                    return
                if minute <= 10:
                    return
                stop_kw = (
                    "ht", "half time", "halftime", "half-time",
                    "ft", "finished", "ended", "full time", "fulltime",
                    "aet", "extra time", "extratime", "extra-time", "after et",
                    "penalty", "penalties", "penalt", "shootout", "shoot-out", "pso", "after pen",
                )
                if any(x in blob for x in stop_kw):
                    return
                if re.search(r"(?:^|[^a-z])(?:pen|pens|et|aet|pso)(?:[^a-z]|$)", blob):
                    return
                _per = str(lf.get("period") or lf.get("status") or "")
                if not _impulse_signal_minute_allowed(int(minute or 0), _per, str(lf.get("status") or "")):
                    return
                ih = compute_impulse_from_stats(stats, "home")
                ia = compute_impulse_from_stats(stats, "away")
                match_id = meta["match_id"]
                name = meta["name"]
                country = meta["country"]
                league = meta["league"]
                home_name = lf.get("home_team") or ""
                away_name = lf.get("away_team") or ""
                if not home_name or not away_name:
                    # только разделитель с пробелами: "A - B", не дефис в Al-Nasr
                    parts = None
                    for sep in (" — ", " – ", " - "):
                        if sep in (name or ""):
                            parts = [x.strip() for x in name.split(sep, 1)]
                            break
                    if parts and len(parts) == 2:
                        home_name = home_name or parts[0] or "Хозяева"
                        away_name = away_name or parts[1] or "Гости"
                    else:
                        home_name = home_name or "Хозяева"
                        away_name = away_name or "Гости"
                score_str = f"{lf.get('score_home', 0)}:{lf.get('score_away', 0)}"
                now_iso = datetime.now(tz).isoformat()
                da_h = _stats_side_num(stats, "home", "dangerous_attacks", "danger_attacks", "dangerous_attack")
                da_a = _stats_side_num(stats, "away", "dangerous_attacks", "danger_attacks", "dangerous_attack")
                sot_h = _stats_side_num(stats, "home", "shots_on_target", "shots_on", "on_target")
                sot_a = _stats_side_num(stats, "away", "shots_on_target", "shots_on", "on_target")
                sh_h = _stats_side_num(stats, "home", "total_shots", "shots", "shots_total")
                sh_a = _stats_side_num(stats, "away", "total_shots", "shots", "shots_total")
                cor_h = _stats_side_num(stats, "home", "corners", "corner_kicks", "corner")
                cor_a = _stats_side_num(stats, "away", "corners", "corner_kicks", "corner")
                ph, pa = _danger_last_10(stats, minute)
                if ph is None:
                    # fallback: доля по текущему импульсу
                    t = float(ih or 0) + float(ia or 0)
                    if t > 0:
                        ph, pa = round(float(ih) / t * 100.0, 1), round(float(ia) / t * 100.0, 1)
                    else:
                        ph, pa = 50.0, 50.0
                prev_da_h = prev_da_a = prev_sot_h = prev_sot_a = prev_sh_h = prev_sh_a = None
                prev_cor_h = prev_cor_a = None
                prev_score_h = prev_score_a = None
                prev_live_minute = None
                prev_last_goal_min = None
                try:
                    prev_row = db_fetchone(
                        "SELECT da_home, da_away, COALESCE(sot_home, 0), COALESCE(sot_away, 0), "
                        "COALESCE(shots_home, 0), COALESCE(shots_away, 0), "
                        "COALESCE(corners_home, 0), COALESCE(corners_away, 0), "
                        "COALESCE(score_home, 0), COALESCE(score_away, 0), COALESCE(minute, 0), "
                        "last_goal_minute "
                        "FROM match_live WHERE match_id = ?",
                        (match_id,),
                    )
                    if prev_row:
                        prev_da_h = float(prev_row[0]) if prev_row[0] is not None else None
                        prev_da_a = float(prev_row[1]) if prev_row[1] is not None else None
                        prev_sot_h = float(prev_row[2]) if prev_row[2] is not None else None
                        prev_sot_a = float(prev_row[3]) if prev_row[3] is not None else None
                        if len(prev_row) > 5:
                            prev_sh_h = float(prev_row[4]) if prev_row[4] is not None else None
                            prev_sh_a = float(prev_row[5]) if prev_row[5] is not None else None
                        if len(prev_row) > 7:
                            prev_cor_h = float(prev_row[6]) if prev_row[6] is not None else None
                            prev_cor_a = float(prev_row[7]) if prev_row[7] is not None else None
                        if len(prev_row) > 10:
                            prev_score_h = int(prev_row[8] or 0)
                            prev_score_a = int(prev_row[9] or 0)
                            prev_live_minute = int(prev_row[10] or 0)
                        prev_last_goal_min = None
                        if len(prev_row) > 11 and prev_row[11] is not None:
                            try:
                                prev_last_goal_min = int(prev_row[11])
                            except Exception:
                                prev_last_goal_min = None
                except Exception:
                    try:
                        prev_row = db_fetchone(
                            "SELECT da_home, da_away FROM match_live WHERE match_id = ?",
                            (match_id,),
                        )
                        if prev_row:
                            prev_da_h = float(prev_row[0]) if prev_row[0] is not None else None
                            prev_da_a = float(prev_row[1]) if prev_row[1] is not None else None
                    except Exception:
                        pass
                logger.info(
                    "[impulse] match=%s eid=%s min=%s score=%s ih=%.1f ia=%.1f da=%s/%s press=%s/%s",
                    name, eid, minute, score_str, ih, ia, da_h, da_a, ph, pa,
                )
                try:
                    db_execute(
                        "INSERT INTO match_live (match_id, event_id, is_live, minute, score_home, score_away, "
                        "impulse_home, impulse_away, home_team, away_team, updated_at, da_home, da_away, "
                        "press_home, press_away, sot_home, sot_away, shots_home, shots_away, "
                        "corners_home, corners_away) "
                        "VALUES (?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                        "ON CONFLICT(match_id) DO UPDATE SET is_live=1, minute=excluded.minute, "
                        "score_home=excluded.score_home, score_away=excluded.score_away, "
                        "impulse_home=excluded.impulse_home, impulse_away=excluded.impulse_away, "
                        "home_team=excluded.home_team, away_team=excluded.away_team, "
                        "updated_at=excluded.updated_at, da_home=excluded.da_home, da_away=excluded.da_away, "
                        "press_home=excluded.press_home, press_away=excluded.press_away, "
                        "sot_home=excluded.sot_home, sot_away=excluded.sot_away, "
                        "shots_home=excluded.shots_home, shots_away=excluded.shots_away, "
                        "corners_home=excluded.corners_home, corners_away=excluded.corners_away",
                        (match_id, eid, minute, lf.get("score_home", 0), lf.get("score_away", 0),
                         ih, ia, home_name, away_name, now_iso,
                         float(da_h or 0), float(da_a or 0), float(ph), float(pa),
                         float(sot_h or 0), float(sot_a or 0), float(sh_h or 0), float(sh_a or 0),
                         float(cor_h or 0), float(cor_a or 0)),
                    )
                except Exception as e:
                    # fallback без новых колонок
                    try:
                        db_execute(
                            "INSERT INTO match_live (match_id, event_id, is_live, minute, score_home, score_away, "
                            "impulse_home, impulse_away, home_team, away_team, updated_at) VALUES (?,?,1,?,?,?,?,?,?,?,?) "
                            "ON CONFLICT(match_id) DO UPDATE SET is_live=1, minute=excluded.minute, "
                            "score_home=excluded.score_home, score_away=excluded.score_away, "
                            "impulse_home=excluded.impulse_home, impulse_away=excluded.impulse_away, "
                            "home_team=excluded.home_team, away_team=excluded.away_team, updated_at=excluded.updated_at",
                            (match_id, eid, minute, lf.get("score_home", 0), lf.get("score_away", 0),
                             ih, ia, home_name, away_name, now_iso),
                        )
                    except Exception as e2:
                        logger.warning("[impulse] match_live upsert: %s / %s", e, e2)

                # Один сигнал на минуту (макс. значение); тишина 4′ после гола
                # Окно: 11–43 (1Т); с 44′ до конца 1Т — нет; 2Т только после 48′
                _per2 = str(lf.get("period") or lf.get("status") or "")
                if not _impulse_signal_minute_allowed(int(minute or 0), _per2, str(lf.get("status") or "")):
                    pass
                else:
                    cur_sh = int(lf.get("score_home", 0) or 0)
                    cur_sa = int(lf.get("score_away", 0) or 0)
                    post_goal_silence = False
                    try:
                        lg_m = prev_last_goal_min
                        if prev_score_h is not None and prev_score_a is not None:
                            if cur_sh > int(prev_score_h) or cur_sa > int(prev_score_a):
                                lg_m = int(minute)
                                try:
                                    db_execute(
                                        "UPDATE match_live SET last_goal_minute = ? WHERE match_id = ?",
                                        (lg_m, match_id),
                                    )
                                except Exception:
                                    pass
                        if lg_m is not None and 0 <= int(minute) - int(lg_m) <= IMPULSE_POST_GOAL_MIN:
                            post_goal_silence = True
                    except Exception:
                        post_goal_silence = False

                    candidates_sides = []
                    if not post_goal_silence:
                        for side, val, team in (("home", ih, home_name), ("away", ia, away_name)):
                            if val < IMPULSE_THRESHOLD:
                                continue
                            prev_da = prev_da_h if side == "home" else prev_da_a
                            prev_sot = prev_sot_h if side == "home" else prev_sot_a
                            prev_shots = prev_sh_h if side == "home" else prev_sh_a
                            prev_corners = prev_cor_h if side == "home" else prev_cor_a
                            press_side = ph if side == "home" else pa
                            if not _side_impulse_confirm(
                                stats, side, prev_da, prev_sot,
                                prev_shots=prev_shots,
                                impulse_val=float(val or 0),
                                press_side=press_side,
                                prev_corners=prev_corners,
                                minute=int(minute or 0),
                                score_home=cur_sh,
                                score_away=cur_sa,
                            ):
                                continue
                            candidates_sides.append((side, float(val), team))
                        # только самый высокий импульс в эту минуту
                        if len(candidates_sides) > 1:
                            candidates_sides.sort(key=lambda x: -x[1])
                            candidates_sides = candidates_sides[:1]

                    for side, val, team in candidates_sides:
                        skip = False
                        try:
                            cur_min = int(minute or 0)
                            # с 68′ до конца матча — не больше одного сигнала (первый по времени)
                            if cur_min >= IMPULSE_LATE_SINGLE_MINUTE:
                                late_cnt = db_fetchone(
                                    "SELECT COUNT(*) FROM impulse_signals "
                                    "WHERE match_id = ? AND COALESCE(minute, 0) >= ?",
                                    (match_id, IMPULSE_LATE_SINGLE_MINUTE),
                                )
                                if late_cnt and int(late_cnt[0] or 0) >= 1:
                                    skip = True
                            # лимит сигналов на сторону за матч
                            if not skip:
                                side_cnt = db_fetchone(
                                    "SELECT COUNT(*) FROM impulse_signals WHERE match_id = ? AND side = ?",
                                    (match_id, side),
                                )
                                n_side = int(side_cnt[0] or 0) if side_cnt else 0
                                if n_side >= IMPULSE_MAX_SIGNALS_PER_SIDE:
                                    skip = True
                            if not skip:
                                same_min = db_fetchone(
                                    "SELECT id FROM impulse_signals WHERE match_id = ? AND minute = ? LIMIT 1",
                                    (match_id, minute),
                                )
                                if same_min:
                                    skip = True
                            if not skip:
                                # эскалация кулдауна после 2+ сигналов по стороне
                                cd_min = IMPULSE_MATCH_COOLDOWN_ESCALATED if n_side >= 2 else IMPULSE_MATCH_COOLDOWN_MIN
                                last = db_fetchone(
                                    "SELECT value, created_at, minute FROM impulse_signals "
                                    "WHERE match_id = ? AND side = ? ORDER BY id DESC LIMIT 1",
                                    (match_id, side),
                                )
                                if last:
                                    last_val = float(last[0] or 0)
                                    last_at = last[1] or ""
                                    age_min = None
                                    try:
                                        last_dt = datetime.fromisoformat(str(last_at).replace("Z", "+00:00"))
                                        if last_dt.tzinfo is None:
                                            last_dt = last_dt.replace(tzinfo=tz)
                                        age_min = (datetime.now(tz) - last_dt).total_seconds() / 60.0
                                    except Exception:
                                        age_min = None
                                    if age_min is not None and age_min < cd_min:
                                        if float(val) < last_val + IMPULSE_PEAK_DELTA:
                                            skip = True
                                    elif age_min is None:
                                        recent = db_fetchone(
                                            "SELECT value FROM impulse_signals WHERE match_id = ? AND side = ? "
                                            "AND created_at > ? ORDER BY id DESC LIMIT 1",
                                            (match_id, side, (datetime.now(tz) - timedelta(minutes=cd_min)).isoformat()),
                                        )
                                        if recent and float(val) < float(recent[0] or 0) + IMPULSE_PEAK_DELTA:
                                            skip = True
                        except Exception:
                            skip = False
                        if skip:
                            continue
                        try:
                            val_i = int(round(float(val)))
                            db_execute(
                                "INSERT INTO impulse_signals (match_id, event_id, country, league, match_name, team, side, "
                                "value, minute, score, logo_home, logo_away, created_at, notified, goal_checked, goal_hit) "
                                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,NULL)",
                                (match_id, eid, country, league, name, team, side, val_i,
                                 minute, score_str, meta["logo_home"], meta["logo_away"], now_iso),
                            )
                        except Exception as e:
                            logger.warning("[impulse] insert signal: %s", e)
                            continue
                        sig = {
                            "match_id": match_id, "country": country, "league": league,
                            "match_name": name, "team": team, "value": val_i,
                            "minute": minute, "score": score_str, "flag": get_flag(country),
                        }
                        try:
                            await _impulse_notify(sig)
                            db_execute(
                                "UPDATE impulse_signals SET notified = 1 WHERE match_id = ? AND side = ? AND created_at = ?",
                                (match_id, side, now_iso),
                            )
                        except Exception:
                            logger.exception("[impulse] notify")

                    # зачёт голов: через 20′ включительно (после 70′ — после конца матча)
                    try:
                        _impulse_check_goals_live(
                            match_id,
                            int(minute or 0),
                            int(lf.get("score_home", 0) or 0),
                            int(lf.get("score_away", 0) or 0),
                            bool(lf.get("is_live")),
                            str(lf.get("status") or ""),
                            stats_raw=stats,
                        )
                    except Exception:
                        logger.exception("[impulse] goal check")


        await asyncio.gather(
            *[_process(meta, lf, eid) for meta, lf, eid in candidates],
            return_exceptions=True,
        )


async def _resolve_stale_impulse_goals():
    """Закрыть окна 20′ у сигналов, матчи которых уже не в live-списке.
    Тянем stats (shotmap), чтобы гол вне окна не стал плюсом по текущему счёту.
    """
    try:
        pending = db_fetchall(
            "SELECT DISTINCT match_id FROM impulse_signals "
            "WHERE COALESCE(goal_checked, 0) = 0 AND match_id IS NOT NULL LIMIT 40"
        ) or []
    except Exception:
        return
    if not pending:
        return
    if not BSD_API_KEY or not str(BSD_API_KEY).strip():
        # без API — только локальная проверка (осторожный режим)
        for (mid,) in pending:
            try:
                mid = int(mid)
            except (TypeError, ValueError):
                continue
            lr = None
            try:
                lr = db_fetchone(
                    "SELECT is_live, minute, score_home, score_away FROM match_live WHERE match_id = ?",
                    (mid,),
                )
            except Exception:
                lr = None
            is_live = bool(lr[0]) if lr else False
            minute = int(lr[1] or 0) if lr else 99
            sh = int(lr[2] or 0) if lr else 0
            sa = int(lr[3] or 0) if lr else 0
            if not is_live and minute < 1:
                minute = 99
            try:
                _impulse_check_goals_live(
                    mid, minute, sh, sa, is_live,
                    "" if is_live else "finished", stats_raw=None,
                )
            except Exception:
                logger.exception("[impulse] stale goal resolve match=%s", mid)
        return

    async with aiohttp.ClientSession() as session:
        for (mid,) in pending:
            try:
                mid = int(mid)
            except (TypeError, ValueError):
                continue
            lr = None
            eid = None
            try:
                lr = db_fetchone(
                    "SELECT is_live, minute, score_home, score_away, event_id "
                    "FROM match_live WHERE match_id = ?",
                    (mid,),
                )
            except Exception:
                try:
                    lr = db_fetchone(
                        "SELECT is_live, minute, score_home, score_away FROM match_live WHERE match_id = ?",
                        (mid,),
                    )
                except Exception:
                    lr = None
            if not eid and lr and len(lr) > 4 and lr[4]:
                try:
                    eid = int(lr[4])
                except (TypeError, ValueError):
                    eid = None
            if not eid:
                try:
                    er = db_fetchone(
                        "SELECT event_id FROM impulse_signals WHERE match_id = ? AND event_id IS NOT NULL LIMIT 1",
                        (mid,),
                    )
                    if er and er[0]:
                        eid = int(er[0])
                except Exception:
                    eid = None
            is_live = bool(lr[0]) if lr else False
            minute = int(lr[1] or 0) if lr else 99
            sh = int(lr[2] or 0) if lr else 0
            sa = int(lr[3] or 0) if lr else 0
            if not is_live and minute < 1:
                minute = 99
            stats = None
            if eid:
                try:
                    stats = await _bsd_get(session, f"events/{eid}/stats/")
                except Exception:
                    stats = None
            try:
                _impulse_check_goals_live(
                    mid, minute, sh, sa, is_live,
                    "" if is_live else "finished",
                    stats_raw=stats,
                )
            except Exception:
                logger.exception("[impulse] stale goal resolve match=%s", mid)


async def _impulse_loop():
    """Фон: каждые 40 сек сканирует live + закрывает окна 20′."""
    await asyncio.sleep(5)
    while True:
        try:
            await _scan_impulse_once()
        except Exception:
            logger.exception("[impulse] loop error")
        try:
            await _resolve_stale_impulse_goals()
        except Exception:
            logger.exception("[impulse] stale resolve error")
        await asyncio.sleep(40)


# ====================== API ENDPOINTS ======================

@app.on_event("startup")
async def startup():
    init_db()
    # авто-расписание 06:00/08:00 МСК (вкл/выкл в админке)
    asyncio.create_task(_auto_daily_loop())
    asyncio.create_task(_impulse_loop())
    # бот: webhook или long polling — чтобы /start всегда отвечал
    asyncio.create_task(_ensure_telegram_bot())
    logger.info("Koefii Mini App backend started (auto_daily=%s)", get_app_setting("auto_daily", "0"))

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

    banned, ban_label = is_user_banned(uid)
    if banned:
        return {
            "user": {
                "id": uid,
                "first_name": first_name,
                "username": username,
                "photo_url": photo_url or "",
                "subscription": "Free",
                "banned": True,
                "ban_label": ban_label,
                "is_admin": str(uid) in admin_users,
            },
            "stats": {},
            "banned": True,
            "ban_label": ban_label,
            "message": f"Вам закрыт доступ к приложению\n{ban_label}",
        }

    # Технические работы — для всех, кроме админов
    if get_app_setting("maintenance", "0") == "1" and str(uid) not in admin_users:
        return {
            "user": {
                "id": uid,
                "first_name": first_name,
                "username": username,
                "photo_url": photo_url or "",
                "subscription": "Free",
                "is_admin": False,
            },
            "stats": {},
            "maintenance": True,
            "message": "Приносим свои извинения, ведутся технические работы",
        }


    # Рефералка: start_param из initData (открытие по ссылке t.me/bot?start=ref_ID или startapp)
    start_param = user.get("_start_param") or ""
    if start_param and not db_user.get("referred_by"):
        apply_referral(uid, start_param)
        db_user = get_or_create_user(uid)

    # Test Drive один раз: Free, без end_date, notified_expire == 0
    bonus = None
    is_new = (
        db_user.get("subscription") == "Free"
        and not db_user.get("end_date")
        and int(db_user.get("notified_expire") or 0) == 0
    )
    if is_new:
        end_date = activate_premium(uid, 30)
        # бонус выдан; pending=1 пока баннер не показали
        try:
            db_execute(
                "UPDATE users SET notified_expire = 1, bonus_pending = 1 WHERE user_id = ?",
                (uid,),
            )
        except Exception:
            db_execute("UPDATE users SET notified_expire = 1 WHERE user_id = ?", (uid,))
        db_user = get_or_create_user(uid)
        bonus = {
            "text": "🎁 Добро пожаловать! Вам активирован Test Drive — 30 дней Premium бесплатно!",
            "end_date": end_date,
        }
    elif int(db_user.get("bonus_pending") or 0) == 1:
        # повторный /me до показа баннера — отдаём бонус снова
        bonus = {
            "text": "🎁 Добро пожаловать! Вам активирован Test Drive — 30 дней Premium бесплатно!",
            "end_date": db_user.get("end_date") or "",
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

    matches_left = "∞" if db_user["subscription"] == "Premium" else max(0, 5 - viewed_today)

    # Main Mini App: t.me/BOT?startapp=ref_ID — открывает приложение сразу
    # (в BotFather должен быть Menu Button / Main Mini App).
    # Если задан MINI_APP_SHORT_NAME — t.me/BOT/SHORT?startapp=ref_ID
    mini_short = (os.getenv("MINI_APP_SHORT_NAME") or "").strip()
    if mini_short:
        ref_link = f"https://t.me/{BOT_USERNAME}/{mini_short}?startapp=ref_{uid}"
    else:
        ref_link = f"https://t.me/{BOT_USERNAME}?startapp=ref_{uid}"

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
        "ref_link": ref_link,
    }


@app.post("/api/bonus/seen")
async def bonus_seen(user: dict = Depends(get_current_user)):
    uid = user["id"]
    try:
        db_execute("UPDATE users SET bonus_pending = 0 WHERE user_id = ?", (uid,))
    except Exception:
        pass
    return {"ok": True}


class ReferralClaim(BaseModel):
    start_param: str = ""


@app.post("/api/referral/claim")
async def api_referral_claim(body: ReferralClaim, user: dict = Depends(get_current_user)):
    """Явный claim реферала из Mini App (start_param / startapp).
    Начисляет пригласившему +3 дня Premium.
    """
    uid = user["id"]
    first_name = user.get("first_name", "") or ""
    username = user.get("username", "") or ""
    get_or_create_user(uid, first_name, username, user.get("photo_url") or "")
    # start_param из тела или из initData
    sp = (body.start_param or "").strip() or (user.get("_start_param") or "").strip()
    if not sp:
        return {"ok": False, "reason": "no_start_param"}
    db_user = get_or_create_user(uid)
    if db_user.get("referred_by"):
        return {"ok": False, "reason": "already_referred", "referred_by": db_user["referred_by"]}
    ok = apply_referral(uid, sp)
    return {"ok": ok, "start_param": sp}


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
    # live cache
    live_map = {}
    try:
        for lr in db_fetchall(
            "SELECT match_id, is_live, minute, score_home, score_away, impulse_home, impulse_away FROM match_live"
        ):
            live_map[lr[0]] = {
                "is_live": bool(lr[1]),
                "minute": lr[2] or 0,
                "score_home": lr[3] if lr[3] is not None else 0,
                "score_away": lr[4] if lr[4] is not None else 0,
                "score": f"{lr[3] if lr[3] is not None else 0}:{lr[4] if lr[4] is not None else 0}",
                "impulse_home": float(lr[5] or 0),
                "impulse_away": float(lr[6] or 0),
            }
    except Exception:
        pass
    groups = {}
    group_meta = {}  # key -> league_logo
    for r in rows:
        key = f"{r[2]}|{r[3]}"
        mid = r[0]
        live = live_map.get(mid) or {}
        groups.setdefault(key, []).append({
            "id": mid, "name": r[1], "country": r[2], "league": r[3], "match_time": r[4] or "",
            "logo_home": r[5] or "", "logo_away": r[6] or "",
            "is_live": bool(live.get("is_live")),
            "minute": live.get("minute") or 0,
            "score": live.get("score") or "",
            "score_home": live.get("score_home"),
            "score_away": live.get("score_away"),
        })
        if has_logo and len(r) > 8 and r[8] and key not in group_meta:
            group_meta[key] = r[8]
        elif has_logo and len(r) > 7 and r[7] and key not in group_meta:
            group_meta[key] = f"https://sports.bzzoiro.com/img/league/{r[7]}/"

    result = []
    for key, matches in sorted(groups.items(), key=lambda x: (
        get_league_priority(
            x[0].split("|")[1] if "|" in x[0] else x[0],
            x[0].split("|")[0] if "|" in x[0] else "",
        ),
        x[0]
    )):
        country, league = key.split("|", 1)
        any_live = any(m.get("is_live") for m in matches)
        result.append({
            "country": country,
            "league": league,
            "flag": get_flag(country),
            "league_logo": group_meta.get(key, ""),
            "count": len(matches),
            "matches": matches,
            "has_live": any_live,
            "live_summary": "",  # счёт и минуты — только в карточке матча
        })
    return {"groups": result}


@app.get("/api/impulse/recent")
async def api_impulse_recent(user: dict = Depends(get_current_user), limit: int = 3):
    """
    Сигналы импульса ≥ 88, минуты 11–43 (1Т) и 49–89 (2Т после 48′).
    limit=3 (главная): первые 3 сигнала за текущие сутки (МСК);
    если сегодня пусто — первые 3 за последний день, когда были сигналы.
    limit>3 (страница Импульс): последние N по времени (до 250).
    """
    lim = max(1, min(250, int(limit or 3)))
    today = datetime.now(tz).strftime("%Y-%m-%d")

    def _rows_to_signals(rows):
        out = []
        for r in rows:
            out.append({
                "id": r[0],
                "match_id": r[1],
                "country": r[2] or "",
                "league": r[3] or "",
                "match_name": r[4] or "",
                "team": r[5] or "",
                "side": r[6] or "",
                "value": float(r[7] or 0),
                "minute": int(r[8] or 0),
                "score": r[9] or "",
                "logo_home": r[10] or "",
                "logo_away": r[11] or "",
                "created_at": r[12] or "",
                "league_logo": (r[13] if len(r) > 13 else "") or "",
                "flag": get_flag(r[2] or ""),
            })
        return out

    cols = (
        "SELECT s.id, s.match_id, s.country, s.league, s.match_name, s.team, s.side, s.value, s.minute, s.score, "
        "s.logo_home, s.logo_away, s.created_at, COALESCE(m.league_logo, '') "
        "FROM impulse_signals s "
        "LEFT JOIN matches m ON m.id = s.match_id "
        "WHERE s.value >= ? AND COALESCE(s.minute, 0) > 10 "
        "AND COALESCE(s.minute, 0) NOT IN (44, 45, 46, 47, 48) AND COALESCE(s.minute, 0) <= 89 "
    )
    try:
        if lim <= 3:
            # первые 3 за сегодня (по времени появления)
            rows = db_fetchall(
                cols + "AND substr(s.created_at, 1, 10) = ? ORDER BY s.created_at ASC, s.id ASC LIMIT ?",
                (IMPULSE_THRESHOLD, today, lim),
            )
            if not rows:
                # fallback: последний день с сигналами
                day_row = db_fetchone(
                    "SELECT substr(created_at, 1, 10) FROM impulse_signals "
                    "WHERE value >= ? AND COALESCE(minute, 0) > 10 "
                    "AND COALESCE(minute, 0) NOT IN (44, 45, 46, 47, 48) AND COALESCE(minute, 0) <= 89 "
                    "ORDER BY created_at DESC LIMIT 1",
                    (IMPULSE_THRESHOLD,),
                )
                if day_row and day_row[0]:
                    rows = db_fetchall(
                        cols + "AND substr(s.created_at, 1, 10) = ? ORDER BY s.created_at ASC, s.id ASC LIMIT ?",
                        (IMPULSE_THRESHOLD, day_row[0], lim),
                    )
            signals = _rows_to_signals(rows or [])
        else:
            rows = db_fetchall(
                cols + "ORDER BY s.created_at DESC, s.id DESC LIMIT ?",
                (IMPULSE_THRESHOLD, lim),
            )
            signals = _rows_to_signals(rows or [])
    except Exception:
        signals = []
    return {"signals": signals, "threshold": IMPULSE_THRESHOLD}


@app.get("/api/impulse/match/{match_id}")
async def api_impulse_match(match_id: int, user: dict = Depends(get_current_user)):
    """Сигналы импульса ≥ 88 по матчу + текущие значения live."""
    try:
        rows = db_fetchall(
            "SELECT id, team, side, value, minute, score, created_at, "
            "goal_hit, COALESCE(goal_checked, 0) FROM impulse_signals "
            "WHERE match_id = ? AND value >= ? ORDER BY created_at DESC, id DESC LIMIT 40",
            (match_id, IMPULSE_THRESHOLD),
        )
    except Exception:
        try:
            rows = db_fetchall(
                "SELECT id, team, side, value, minute, score, created_at FROM impulse_signals "
                "WHERE match_id = ? AND value >= ? ORDER BY created_at DESC, id DESC LIMIT 40",
                (match_id, IMPULSE_THRESHOLD),
            )
        except Exception:
            rows = []
    signals = []
    for r in (rows or []):
        item = {
            "id": r[0], "team": r[1] or "", "side": r[2] or "",
            "value": float(r[3] or 0), "minute": int(r[4] or 0),
            "score": r[5] or "", "created_at": r[6] or "",
            "goal_hit": None, "goal_checked": 0,
        }
        if len(r) > 8:
            try:
                item["goal_hit"] = None if r[7] is None else int(r[7])
                item["goal_checked"] = int(r[8] or 0)
            except (TypeError, ValueError):
                pass
        signals.append(item)
    live = None
    try:
        lr = db_fetchone(
            "SELECT is_live, minute, score_home, score_away, impulse_home, impulse_away, home_team, away_team "
            "FROM match_live WHERE match_id = ?",
            (match_id,),
        )
        if lr:
            live = {
                "is_live": bool(lr[0]),
                "minute": lr[1] or 0,
                "score_home": lr[2] if lr[2] is not None else 0,
                "score_away": lr[3] if lr[3] is not None else 0,
                "score": f"{lr[2] if lr[2] is not None else 0}:{lr[3] if lr[3] is not None else 0}",
                "impulse_home": float(lr[4] or 0),
                "impulse_away": float(lr[5] or 0),
                "home_team": lr[6] or "",
                "away_team": lr[7] or "",
            }
    except Exception:
        pass
    return {"signals": signals, "live": live, "threshold": IMPULSE_THRESHOLD}


@app.get("/api/impulse/league-stats")
async def api_impulse_league_stats(
    country: str = "",
    league: str = "",
    days: int = 30,
    user: dict = Depends(get_current_user),
):
    """% проходимости импульса по стране/лиге (окно 20′)."""
    try:
        days = max(1, min(90, int(days or 30)))
    except (TypeError, ValueError):
        days = 30
    return _impulse_league_hit_stats(country or "", league or "", days=days)


def _score_match_for_best_league(stats: dict) -> tuple:
    """
    Оценка матча для «Лучшие матчи сегодня».

    Тип рынка НЕ важен: суммируются все доступные строки
    (ТБ 2.5, ТБ 1.5, ИТБ 1.5, ОЗ, Гол в 1Т) — чем выше % и второй фактор,
    тем выше score. Сортировка списка: score по убыванию.

    Возвращает (score: float, market_label: str) — label = сильнейшая строка.
    """
    if not isinstance(stats, dict) or not stats:
        return 0.0, ""

    form_h = (stats.get("form") or {}).get("home") or {}
    form_a = (stats.get("form") or {}).get("away") or {}
    form_h_home = form_h.get("home") if isinstance(form_h.get("home"), dict) else form_h
    form_a_away = form_a.get("away") if isinstance(form_a.get("away"), dict) else form_a
    ms_h = (stats.get("match_stats") or {}).get("home") or {}
    ms_a = (stats.get("match_stats") or {}).get("away") or {}
    bet_h = (stats.get("betting") or {}).get("home") or {}
    bet_a = (stats.get("betting") or {}).get("away") or {}
    halves_h = (stats.get("halves") or {}).get("home") or {}
    halves_a = (stats.get("halves") or {}).get("away") or {}

    def _pct(bag, *keys):
        for k in keys:
            v = bag.get(k) if isinstance(bag, dict) else None
            if v is None:
                continue
            try:
                f = float(v)
                if f <= 1.5:
                    f *= 100.0
                return f
            except (TypeError, ValueError):
                pass
        return None

    def _sf(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    o25_h = _pct(bet_h, "over_25_pct", "over_25")
    o25_a = _pct(bet_a, "over_25_pct", "over_25")
    o15_h = _pct(bet_h, "over_15_pct", "over_15")
    o15_a = _pct(bet_a, "over_15_pct", "over_15")
    btts_h = _pct(bet_h, "btts_pct", "btts")
    btts_a = _pct(bet_a, "btts_pct", "btts")
    itb_h = _pct(bet_h, "team_over_15_pct", "team_over_15", "indiv_over_15")
    itb_a = _pct(bet_a, "team_over_15_pct", "team_over_15", "indiv_over_15")
    ht_h = bet_h.get("ht") if isinstance(bet_h.get("ht"), dict) else {}
    ht_a = bet_a.get("ht") if isinstance(bet_a.get("ht"), dict) else {}
    ht05_h = _pct(ht_h, "ht_over_05_pct", "ht_over_05", "over_05_pct")
    ht05_a = _pct(ht_a, "ht_over_05_pct", "ht_over_05", "over_05_pct")

    gpg_h = _sf(form_h_home.get("gpg")) or _sf(form_h.get("gpg")) or _sf(ms_h.get("goals"))
    gpg_a = _sf(form_a_away.get("gpg")) or _sf(form_a.get("gpg")) or _sf(ms_a.get("goals"))
    goals_sum = (gpg_h or 0) + (gpg_a or 0)
    fh_goals_h = _sf((halves_h.get("first_half") or {}).get("goals"))
    fh_goals_a = _sf((halves_a.get("first_half") or {}).get("goals"))
    half_sec = max(fh_goals_h or 0, fh_goals_a or 0)

    try:
        base_h = _potential_power(form_h_home, form_h_home if form_h_home.get("shots_on") is not None else ms_h)
        base_a = _potential_power(form_a_away, form_a_away if form_a_away.get("shots_on") is not None else ms_a)
    except Exception:
        base_h, base_a = 50, 50
    pot_diff = abs(float(base_h) - float(base_a))

    def both_line(ph, pa, secondary, weight=1.0):
        """Вклад строки с двумя командами: min% важнее, + avg%, + secondary."""
        if ph is None or pa is None:
            return 0.0, 0.0
        mn = min(ph, pa)
        av = (ph + pa) / 2.0
        sc = (mn * 15.0 + av * 4.0 + (secondary or 0) * 8.0) * weight
        return sc, mn

    total = 0.0
    best_label = ""
    best_strength = -1.0

    # ТБ 2.5 — второй фактор: сумма ср. голов
    sc, strength = both_line(o25_h, o25_a, goals_sum, weight=1.0)
    if sc > 0:
        total += sc
        if strength > best_strength:
            best_strength, best_label = strength, "ТБ 2.5"

    # ТБ 1.5
    sc, strength = both_line(o15_h, o15_a, goals_sum, weight=1.0)
    if sc > 0:
        total += sc
        if strength > best_strength:
            best_strength, best_label = strength, "ТБ 1.5"

    # ОЗ
    sc, strength = both_line(btts_h, btts_a, goals_sum, weight=1.0)
    if sc > 0:
        total += sc
        if strength > best_strength:
            best_strength, best_label = strength, "ОЗ"

    # Гол в 1Т — второй фактор: голы 1-го тайма
    sc, strength = both_line(ht05_h, ht05_a, half_sec, weight=1.0)
    if sc > 0:
        total += sc
        if strength > best_strength:
            best_strength, best_label = strength, "Гол в 1Т"

    # ИТБ 1.5 — max из двух команд
    itb_sc = 0.0
    itb_strength = 0.0
    if itb_h is not None:
        s = itb_h * 15.0 + (gpg_h or 0) * 12.0
        if s > itb_sc:
            itb_sc, itb_strength = s, itb_h
    if itb_a is not None:
        s = itb_a * 15.0 + (gpg_a or 0) * 12.0
        if s > itb_sc:
            itb_sc, itb_strength = s, itb_a
    if itb_sc > 0:
        total += itb_sc
        if itb_strength > best_strength:
            best_strength, best_label = itb_strength, "ИТБ 1.5"

    if total > 0:
        total += pot_diff * 0.2
        return total, best_label or "Статистика"

    if pot_diff > 0:
        return 50.0 + pot_diff * 2.0, "Потенциал"

    return 0.0, ""


@app.get("/api/best-leagues")
async def api_best_leagues(user: dict = Depends(get_current_user)):
    """
    Лучшие матчи сегодня: сумма силы по всем строкам
    (ТБ 2.5, ТБ 1.5, ИТБ 1.5, ОЗ, Гол в 1Т) + вторые факторы (голы).
    Чем выше % и цифры — тем выше в списке. Тип рынка не приоритет.
    Ответ: { "matches": [ ... ] }.
    """
    try:
        rows = db_fetchall(
            "SELECT id, name, country, league, match_time, "
            "COALESCE(logo_home,''), COALESCE(logo_away,''), "
            "COALESCE(league_logo,''), COALESCE(stats_json,'') "
            "FROM matches ORDER BY match_time, country, league"
        )
    except Exception:
        rows = db_fetchall(
            "SELECT id, name, country, league, match_time, "
            "COALESCE(logo_home,''), COALESCE(logo_away,''), '', '' "
            "FROM matches ORDER BY match_time, country, league"
        )

    scored = []
    for r in rows:
        mid, name, country, league, mt, lh, la, llogo, sraw = (
            r[0], r[1] or "", r[2] or "", r[3] or "", r[4] or "",
            r[5] or "", r[6] or "", r[7] or "", r[8] or "",
        )
        if not name:
            continue
        stats = {}
        if sraw:
            try:
                stats = json.loads(sraw) if isinstance(sraw, str) else (sraw or {})
            except Exception:
                stats = {}
        score, market = _score_match_for_best_league(stats if isinstance(stats, dict) else {})
        if score <= 0:
            continue
        scored.append({
            "score": score,
            "market": market,
            "country": country,
            "league": league,
            "flag": get_flag(country),
            "league_logo": llogo,
            "match": {
                "id": mid,
                "name": name,
                "match_time": mt,
                "logo_home": lh,
                "logo_away": la,
            },
        })

    ranked = sorted(scored, key=lambda x: (-x["score"], x.get("match", {}).get("match_time") or "", x["country"], x["league"]))
    top = []
    for i, item in enumerate(ranked[:10], start=1):
        top.append({
            "rank": i,
            "country": item["country"],
            "league": item["league"],
            "flag": item["flag"],
            "league_logo": item["league_logo"],
            "match": item["match"],
        })
    return {"matches": top, "leagues": top}


@app.get("/api/matches/{match_id}")
async def api_match_detail(match_id: int, user: dict = Depends(get_current_user), refresh: int = 0):
    uid = user["id"]
    # Try full select; fallback if columns missing
    try:
        row = db_fetchone(
            "SELECT id, name, country, league, match_time, text, COALESCE(logo_home,''), COALESCE(logo_away,''), "
            "event_id, home_team_id, away_team_id, COALESCE(stats_json,''), league_id, COALESCE(league_logo,'') "
            "FROM matches WHERE id = ?",
            (match_id,)
        )
    except Exception:
        try:
            row = db_fetchone(
                "SELECT id, name, country, league, match_time, text, COALESCE(logo_home,''), COALESCE(logo_away,''), "
                "event_id, home_team_id, away_team_id, COALESCE(stats_json,'') FROM matches WHERE id = ?",
                (match_id,)
            )
            if row:
                row = tuple(list(row) + [None, ""])
        except Exception:
            row = db_fetchone(
                "SELECT id, name, country, league, match_time, text, COALESCE(logo_home,''), COALESCE(logo_away,'') "
                "FROM matches WHERE id = ?",
                (match_id,)
            )
            if row:
                row = tuple(list(row) + [None, None, None, "", None, ""])

    if not row:
        raise HTTPException(404, "Матч не найден")

    allowed = check_match_limit(uid, match_id)
    if not allowed:
        raise HTTPException(403, "⚠️ Лимит матчей исчерпан (5 в день). Купите ⭐ Premium.")

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

    # если forecast пуст — собрать на лету из уже загруженных stats
    if isinstance(stats, dict):
        fc = stats.get("forecast") or {}
        if not (isinstance(fc, dict) and (fc.get("singles") or fc.get("combos") or fc.get("analysis"))):
            try:
                # team names for analysis text
                if not stats.get("home_team") and row[1]:
                    parts = (row[1] or "").replace(" – ", " - ").split(" - ")
                    if len(parts) >= 2:
                        stats.setdefault("home_team", parts[0].strip())
                        stats.setdefault("away_team", parts[1].strip())
                stats["forecast"] = _build_forecast_variants(stats, odds_raw=None, pred_raw=stats.get("model") or {})
                # persist so next open is fast
                try:
                    db_execute(
                        "UPDATE matches SET stats_json = ? WHERE id = ?",
                        (json.dumps(stats, ensure_ascii=False), match_id),
                    )
                except Exception:
                    pass
            except Exception as e:
                logger.warning("[forecast] on-the-fly build failed: %s", e)

    # импульс ≥ 88 + live
    impulse_signals = []
    impulse_live = None
    try:
        for ir in db_fetchall(
            "SELECT team, side, value, minute, score, created_at, goal_hit, COALESCE(goal_checked,0) FROM impulse_signals "
            "WHERE match_id = ? AND value >= ? ORDER BY created_at DESC LIMIT 15",
            (match_id, IMPULSE_THRESHOLD),
        ):
            gh = None
            gc = 0
            if len(ir) > 7:
                try:
                    gh = None if ir[6] is None else int(ir[6])
                    gc = int(ir[7] or 0)
                except (TypeError, ValueError):
                    pass
            impulse_signals.append({
                "team": ir[0] or "", "side": ir[1] or "", "value": float(ir[2] or 0),
                "minute": int(ir[3] or 0), "score": ir[4] or "", "created_at": ir[5] or "",
                "goal_hit": gh, "goal_checked": gc,
            })
        lr = db_fetchone(
            "SELECT is_live, minute, score_home, score_away, impulse_home, impulse_away, home_team, away_team, "
            "COALESCE(press_home, 0), COALESCE(press_away, 0), "
            "COALESCE(sot_home, 0), COALESCE(sot_away, 0), "
            "COALESCE(shots_home, 0), COALESCE(shots_away, 0), "
            "COALESCE(corners_home, 0), COALESCE(corners_away, 0), "
            "COALESCE(da_home, 0), COALESCE(da_away, 0), "
            "COALESCE(last_goal_minute, 0) "
            "FROM match_live WHERE match_id = ?",
            (match_id,),
        )
        if lr:
            ph = float(lr[8] or 0) if len(lr) > 8 else 0.0
            pa = float(lr[9] or 0) if len(lr) > 9 else 0.0
            if ph <= 0 and pa <= 0:
                ihv = float(lr[4] or 0)
                iav = float(lr[5] or 0)
                tt = ihv + iav
                if tt > 0:
                    ph, pa = round(ihv / tt * 100.0, 1), round(iav / tt * 100.0, 1)
            impulse_live = {
                "is_live": bool(lr[0]),
                "minute": lr[1] or 0,
                "score_home": lr[2] if lr[2] is not None else 0,
                "score_away": lr[3] if lr[3] is not None else 0,
                "score": f"{lr[2] if lr[2] is not None else 0}:{lr[3] if lr[3] is not None else 0}",
                "impulse_home": float(lr[4] or 0),
                "impulse_away": float(lr[5] or 0),
                "home_team": lr[6] or "",
                "away_team": lr[7] or "",
                "press_home": ph,
                "press_away": pa,
                "sot_home": float(lr[10] or 0) if len(lr) > 10 else 0.0,
                "sot_away": float(lr[11] or 0) if len(lr) > 11 else 0.0,
                "shots_home": float(lr[12] or 0) if len(lr) > 12 else 0.0,
                "shots_away": float(lr[13] or 0) if len(lr) > 13 else 0.0,
                "corners_home": float(lr[14] or 0) if len(lr) > 14 else 0.0,
                "corners_away": float(lr[15] or 0) if len(lr) > 15 else 0.0,
                "da_home": float(lr[16] or 0) if len(lr) > 16 else 0.0,
                "da_away": float(lr[17] or 0) if len(lr) > 17 else 0.0,
                "last_goal_minute": int(lr[18] or 0) if len(lr) > 18 else 0,
            }
    except Exception:
        # fallback без press_* колонок
        try:
            lr = db_fetchone(
                "SELECT is_live, minute, score_home, score_away, impulse_home, impulse_away, home_team, away_team "
                "FROM match_live WHERE match_id = ?",
                (match_id,),
            )
            if lr:
                ihv = float(lr[4] or 0)
                iav = float(lr[5] or 0)
                tt = ihv + iav
                ph = round(ihv / tt * 100.0, 1) if tt > 0 else 50.0
                pa = round(100.0 - ph, 1) if tt > 0 else 50.0
                impulse_live = {
                    "is_live": bool(lr[0]),
                    "minute": lr[1] or 0,
                    "score_home": lr[2] if lr[2] is not None else 0,
                    "score_away": lr[3] if lr[3] is not None else 0,
                    "score": f"{lr[2] if lr[2] is not None else 0}:{lr[3] if lr[3] is not None else 0}",
                    "impulse_home": ihv,
                    "impulse_away": iav,
                    "home_team": lr[6] or "",
                    "away_team": lr[7] or "",
                    "press_home": ph,
                    "press_away": pa,
                }
        except Exception:
            pass

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
        "impulse_signals": impulse_signals,
        "impulse_live": impulse_live,
        "impulse_threshold": IMPULSE_THRESHOLD,
        "league_id": row[12] if len(row) > 12 else None,
        "league_logo": (row[13] if len(row) > 13 else "") or "",
        "impulse_league_stats": _impulse_league_hit_stats(
            row[2] or "",
            row[3] or "",
            days=30,
            league_id=(row[12] if len(row) > 12 else None),
        ),
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
async def api_create_payment(data: dict = None, user: dict = Depends(get_current_user)):
    uid = user["id"]
    data = data or {}
    plan_id = str(data.get("plan") or "1m").strip()
    plan = PREMIUM_PLANS.get(plan_id) or PREMIUM_PLANS["1m"]
    result = await create_yookassa_payment(
        uid, float(plan["amount"]), int(plan["days"]), str(plan["label"])
    )
    if not result:
        raise HTTPException(500, "Не удалось создать платёж. Попробуйте позже.")
    result["plan"] = plan_id
    result["label"] = plan["label"]
    return result

@app.post("/api/premium/check/{payment_id}")
async def api_check_payment(payment_id: str, user: dict = Depends(get_current_user)):
    status = await check_yookassa_payment(payment_id)
    if status == "succeeded":
        try:
            row = db_fetchone(
                "SELECT user_id, status, COALESCE(plan_days, 30) FROM pending_payments WHERE payment_id = ?",
                (payment_id,),
            )
        except Exception:
            row = db_fetchone(
                "SELECT user_id, status FROM pending_payments WHERE payment_id = ?",
                (payment_id,),
            )
            if row:
                row = (row[0], row[1], 30)
        if row and row[1] != "succeeded":
            days = int(row[2] if len(row) > 2 and row[2] else 30)
            end_date = activate_premium(row[0], days)
            db_execute("UPDATE pending_payments SET status = 'succeeded' WHERE payment_id = ?", (payment_id,))
            return {
                "status": "succeeded",
                "end_date": end_date,
                "days": days,
                "message": f"🎉 Оплата прошла успешно! Premium на {days} дн. до {end_date}.",
            }
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

    cat_label = SUPPORT_CATEGORIES.get(cat, cat)
    thread = db_fetchone("SELECT thread_id FROM support_threads WHERE thread_id = ?", (uid,))
    if not thread:
        db_execute(
            "INSERT INTO support_threads (thread_id, user_id, user_name, category, created_at) VALUES (?, ?, ?, ?, ?)",
            (uid, uid, user_name, cat, now)
        )
    else:
        try:
            db_execute(
                "UPDATE support_threads SET category = ?, user_name = ? WHERE thread_id = ?",
                (cat, user_name, uid),
            )
        except Exception:
            pass
    # текст с меткой темы — видно в переписке админу и пользователю
    body = (msg.text or "").strip()
    stored = f"[{cat_label}]\n{body}" if body else f"[{cat_label}]"
    db_execute(
        "INSERT INTO support_messages (thread_id, sender, text, created_at, admin_read, user_read) VALUES (?, 'user', ?, ?, 0, 1)",
        (uid, stored, now)
    )
    # Уведомление админу через бота (если токен есть) — упрощённо логируем
    logger.info("Support message from %s [%s]: %s", uid, cat, msg.text[:100])
    return {"ok": True, "message": "✅ Ваше сообщение отправлено Администрации!"}


@app.get("/api/support/thread")
async def api_support_thread(user: dict = Depends(get_current_user)):
    """Переписка пользователя с поддержкой + пометить ответы админа прочитанными."""
    uid = user["id"]
    try:
        db_execute(
            "UPDATE support_messages SET user_read = 1 WHERE thread_id = ? AND sender = 'admin'",
            (uid,),
        )
    except Exception:
        pass
    msgs = db_fetchall(
        "SELECT sender, text, created_at FROM support_messages WHERE thread_id = ? ORDER BY id",
        (uid,),
    )
    return {
        "messages": [
            {"sender": m[0], "text": m[1], "created_at": m[2] or ""}
            for m in msgs
        ]
    }


@app.get("/api/support/unread")
async def api_support_unread(user: dict = Depends(get_current_user)):
    uid = user["id"]
    try:
        row = db_fetchone(
            "SELECT COUNT(*) FROM support_messages WHERE thread_id = ? AND sender = 'admin' AND COALESCE(user_read, 0) = 0",
            (uid,),
        )
        n = int(row[0]) if row else 0
    except Exception:
        n = 0
    return {"unread": n}


# ====================== ADMIN ENDPOINTS ======================

def require_admin(user: dict = Depends(get_current_user)):
    if str(user["id"]) not in admin_users:
        raise HTTPException(403, "Нет доступа")
    return user


@app.get("/api/admin/users")
async def admin_users_list(page: int = 0, q: str = "", user: dict = Depends(require_admin)):
    """Список пользователей по 15, поиск по username, пагинация."""
    page = max(0, int(page or 0))
    per = 15
    q = (q or "").strip().lstrip("@")
    if q:
        like = f"%{q}%"
        total_row = db_fetchone(
            "SELECT COUNT(*) FROM users WHERE username LIKE ? COLLATE NOCASE OR CAST(user_id AS TEXT) = ? OR first_name LIKE ? COLLATE NOCASE",
            (like, q, like),
        )
        total = int(total_row[0]) if total_row else 0
        rows = db_fetchall(
            "SELECT user_id, COALESCE(username,''), COALESCE(first_name,''), subscription, "
            "COALESCE(banned_until,''), COALESCE(ban_label,'') "
            "FROM users WHERE username LIKE ? COLLATE NOCASE OR CAST(user_id AS TEXT) = ? OR first_name LIKE ? COLLATE NOCASE "
            "ORDER BY username COLLATE NOCASE, user_id LIMIT ? OFFSET ?",
            (like, q, like, per, page * per),
        )
    else:
        total = db_fetchone("SELECT COUNT(*) FROM users")[0]
        rows = db_fetchall(
            "SELECT user_id, COALESCE(username,''), COALESCE(first_name,''), subscription, "
            "COALESCE(banned_until,''), COALESCE(ban_label,'') "
            "FROM users ORDER BY username COLLATE NOCASE, user_id LIMIT ? OFFSET ?",
            (per, page * per),
        )
    return {
        "users": [
            {
                "user_id": r[0],
                "username": r[1] or "",
                "first_name": r[2] or "",
                "subscription": r[3] or "Free",
                "banned_until": r[4] or None,
                "ban_label": r[5] or "",
                "banned": bool(r[4]),
            }
            for r in rows
        ],
        "page": page,
        "per_page": per,
        "total": total,
        "total_pages": max(1, (int(total) + per - 1) // per),
        "q": q,
    }


@app.post("/api/admin/users/ban")
async def admin_ban_user(data: dict, user: dict = Depends(require_admin)):
    """Бан: duration = 1d|7d|30d|90d|180d|365d|forever"""
    uid = data.get("user_id")
    if not uid:
        raise HTTPException(400, "user_id обязателен")
    uid = int(uid)
    duration = str(data.get("duration") or "1d").lower().strip()
    now = datetime.now(tz)
    mapping = {
        "1d": (timedelta(days=1), "на сутки"),
        "7d": (timedelta(days=7), "на неделю"),
        "30d": (timedelta(days=30), "на месяц"),
        "90d": (timedelta(days=90), "на три месяца"),
        "180d": (timedelta(days=180), "на полгода"),
        "365d": (timedelta(days=365), "на год"),
        "forever": (None, "навсегда"),
    }
    if duration not in mapping:
        raise HTTPException(400, "Неизвестный срок бана")
    delta, label = mapping[duration]
    if delta is None:
        until = "forever"
        full_label = "навсегда"
    else:
        until = (now + delta).isoformat()
        full_label = label
    db_execute(
        "UPDATE users SET banned_until = ?, ban_label = ? WHERE user_id = ?",
        (until, full_label, uid),
    )
    log_admin(user["id"], "ban_user", f"{uid} {full_label}")
    return {"ok": True, "message": f"✅ Пользователь заблокирован {full_label}", "banned_until": until, "ban_label": full_label}


@app.post("/api/admin/users/unban")
async def admin_unban_user(data: dict, user: dict = Depends(require_admin)):
    uid = int(data.get("user_id") or 0)
    if not uid:
        raise HTTPException(400, "user_id обязателен")
    db_execute(
        "UPDATE users SET banned_until = NULL, ban_label = '' WHERE user_id = ?",
        (uid,),
    )
    log_admin(user["id"], "unban_user", str(uid))
    return {"ok": True, "message": "✅ Бан снят"}


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


def _clear_impulse_data(clear_stats: bool = False) -> None:
    """Удаляет сигналы импульса и обнуляет live-значения. clear_stats — сброс накопленной статистики."""
    try:
        db_execute("DELETE FROM impulse_signals")
    except Exception:
        pass
    try:
        db_execute(
            "UPDATE match_live SET impulse_home = 0, impulse_away = 0, "
            "press_home = 0, press_away = 0"
        )
    except Exception:
        try:
            db_execute(
                "UPDATE match_live SET impulse_home = 0, impulse_away = 0"
            )
        except Exception:
            pass
    if clear_stats:
        for tbl in ("impulse_stats_agg", "impulse_stats_weeks"):
            try:
                db_execute(f"DELETE FROM {tbl}")
            except Exception:
                pass
        try:
            set_app_setting("impulse_last_rollup_week", "")
        except Exception:
            pass


def _iso_week_key(dt: datetime) -> str:
    iso = dt.isocalendar()
    return f"{iso[0]}-W{int(iso[1]):02d}"


def _compute_live_impulse_hits() -> list:
    """
    Каждый сигнал — свой отрезок 20′ включительно.
    Пример: сигнал 54′ → гол до 74′ включительно (гол на 69′ = плюс).
    Сигнал ≥70′ — до конца матча.
    """
    window = IMPULSE_GOAL_WINDOW
    rows = db_fetchall(
        "SELECT s.id, s.match_id, s.team, s.side, s.value, s.minute, s.score, s.created_at, "
        "COALESCE(s.country,''), COALESCE(s.league,''), COALESCE(m.league_logo,''), m.league_id, "
        "s.goal_hit, COALESCE(s.goal_checked, 0) "
        "FROM impulse_signals s "
        "LEFT JOIN matches m ON m.id = s.match_id "
        "WHERE s.value >= ? AND COALESCE(s.minute, 0) > 10 "
        "AND COALESCE(s.minute, 0) NOT IN (44, 45, 46, 47, 48) AND COALESCE(s.minute, 0) <= 89 "
        "ORDER BY s.created_at ASC, s.id ASC LIMIT 5000",
        (IMPULSE_THRESHOLD,),
    ) or []

    def _parse_score(sc: str):
        m = re.match(r"^\s*(\d+)\s*[:\-]\s*(\d+)\s*$", str(sc or ""))
        if not m:
            return None, None
        return int(m.group(1)), int(m.group(2))

    # все счёта по матчу (по минутам сигналов) — для роста счёта в окне
    timeline = {}
    for r in rows:
        mid = r[1]
        if mid is None:
            continue
        timeline.setdefault(int(mid), []).append((int(r[5] or 0), str(r[6] or "")))
    for mid in timeline:
        timeline[mid].sort(key=lambda x: x[0])

    raw = []
    for r in rows:
        sid, match_id, team, side, value, minute, score = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
        side = (side or "").lower()
        if side not in ("home", "away"):
            side = "home"
        sig_min = int(minute or 0)
        if sig_min >= 70:
            window_end = 120
        else:
            window_end = sig_min + window  # включительно

        delay = goal_min = None
        base_h, base_a = _parse_score(score)
        if base_h is None:
            base_h, base_a = 0, 0
        tl = timeline.get(int(match_id) if match_id else -1) or []
        for mn, sc in tl:
            if mn <= sig_min:
                continue
            if mn > window_end:
                break
            h, a = _parse_score(sc)
            if h is None:
                continue
            # любой гол в окне 20′ включительно (как _impulse_check_goals_live)
            if h > base_h or a > base_a:
                delay, goal_min = mn - sig_min, mn
                break

        # goal_checked/goal_hit — источник правды после закрытия окна 20′
        g_hit = r[12] if len(r) > 12 else None
        g_chk = r[13] if len(r) > 13 else 0
        try:
            g_chk = int(g_chk or 0)
        except (TypeError, ValueError):
            g_chk = 0
        try:
            g_hit_i = None if g_hit is None else int(g_hit)
        except (TypeError, ValueError):
            g_hit_i = None

        if g_chk and g_hit_i is not None:
            if g_hit_i == 1:
                if delay is None:
                    # окно закрыто с голом, точной минуты нет → бакет 16–20
                    delay = max(1, min(window, window_end - sig_min if sig_min < 70 else window))
                    goal_min = min(window_end, sig_min + delay)
            else:
                # окно закрыто без гола — минус в статистике
                delay = None
                goal_min = None

        stars = _impulse_star_bucket(value)
        if stars not in (1, 2, 3):
            # порог 88+: всё равно учитываем как «сильный» сигнал
            try:
                v = float(value or 0)
            except (TypeError, ValueError):
                v = 0
            stars = 3 if v >= 90 else (2 if v >= 83 else (1 if v >= 77 else 1))

        lid = r[11] if len(r) > 11 else None
        try:
            lid = int(lid) if lid is not None else None
        except (TypeError, ValueError):
            lid = None

        # приоритет: закрытое окно → goal_hit; иначе таймлайн
        if g_chk and g_hit_i is not None:
            hit = 1 if g_hit_i == 1 else 0
        else:
            hit = 1 if delay is not None else 0
        raw.append({
            "row_id": sid, "match_id": match_id, "side": side, "stars": stars,
            "value": float(value or 0), "delay": delay if hit else None,
            "goal_min": goal_min if hit else None,
            "signal_min": sig_min, "hit": hit,
            "country": (r[8] or "").strip(), "league": (r[9] or "").strip(),
            "league_logo": (r[10] or "") if len(r) > 10 else "",
            "league_id": lid,
        })
    return raw



def _agg_upsert(scope: str, country: str, league: str, league_id, stars: int,
                signals: int, goals: int, delay_sum: float, delay_n: int,
                buckets: dict, league_logo: str = "") -> None:
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    country = country or ""
    league = league or ""
    try:
        lid = int(league_id) if league_id is not None else None
    except (TypeError, ValueError):
        lid = None
    row = db_fetchone(
        "SELECT id, signals_count, goals_count, delay_sum, delay_n, "
        "b_1_5, b_6_10, b_11_15, b_16_20, b_no_goal, COALESCE(league_logo,'') "
        "FROM impulse_stats_agg WHERE scope = ? AND country = ? AND league = ? "
        "AND COALESCE(league_id, -1) = COALESCE(?, -1) AND stars = ?",
        (scope, country, league, lid, stars),
    )
    b = {
        "1-5": buckets.get("1-5", 0),
        "6-10": buckets.get("6-10", 0),
        "11-15": buckets.get("11-15", 0),
        "16-20": buckets.get("16-20", 0),
        "no_goal": buckets.get("no_goal", 0),
    }
    if row:
        logo = league_logo or row[10] or ""
        db_execute(
            "UPDATE impulse_stats_agg SET signals_count = signals_count + ?, goals_count = goals_count + ?, "
            "delay_sum = delay_sum + ?, delay_n = delay_n + ?, "
            "b_1_5 = b_1_5 + ?, b_6_10 = b_6_10 + ?, b_11_15 = b_11_15 + ?, b_16_20 = b_16_20 + ?, "
            "b_no_goal = b_no_goal + ?, league_logo = CASE WHEN ? != '' THEN ? ELSE league_logo END, "
            "updated_at = ? WHERE id = ?",
            (signals, goals, delay_sum, delay_n,
             b["1-5"], b["6-10"], b["11-15"], b["16-20"], b["no_goal"],
             logo, logo, now, row[0]),
        )
    else:
        db_execute(
            "INSERT INTO impulse_stats_agg "
            "(scope, country, league, league_id, stars, signals_count, goals_count, delay_sum, delay_n, "
            "b_1_5, b_6_10, b_11_15, b_16_20, b_no_goal, league_logo, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (scope, country, league, lid, stars, signals, goals, delay_sum, delay_n,
             b["1-5"], b["6-10"], b["11-15"], b["16-20"], b["no_goal"], league_logo or "", now),
        )


def _impulse_weekly_rollup(force: bool = False) -> dict:
    """
    Сохраняет итоги текущих сигналов в накопленную статистику,
    пишет снимок недели и очищает impulse_signals.
    """
    now = datetime.now(tz)
    week_key = _iso_week_key(now)
    last = get_app_setting("impulse_last_rollup_week", "")
    if not force and last == week_key:
        return {"ok": True, "skipped": True, "week_key": week_key, "message": "Уже сохранён за эту неделю"}

    hits = _compute_live_impulse_hits()
    if not hits and not force:
        # неделя без сигналов — всё равно отметим, чтобы не долбить каждый цикл
        set_app_setting("impulse_last_rollup_week", week_key)
        return {"ok": True, "week_key": week_key, "signals": 0, "goals": 0, "cleared": False}

    def _empty_b():
        return {"1-5": 0, "6-10": 0, "11-15": 0, "16-20": 0, "no_goal": 0}

    # global / stars
    for stars_key in (0, 1, 2, 3):
        sub = hits if stars_key == 0 else [h for h in hits if h["stars"] == stars_key]
        if not sub and stars_key != 0:
            continue
        buckets = _empty_b()
        goals = 0
        delay_sum = 0.0
        delay_n = 0
        for h in sub:
            if h["hit"] and h["delay"] is not None:
                goals += 1
                delay_sum += h["delay"]
                delay_n += 1
                bk = _delay_bucket(h["delay"])
            else:
                bk = "no_goal"
            buckets[bk] = buckets.get(bk, 0) + 1
        _agg_upsert("global", "", "", None, stars_key, len(sub), goals, delay_sum, delay_n, buckets)

    # by country
    by_c = {}
    for h in hits:
        c = h["country"] or "—"
        by_c.setdefault(c, []).append(h)
    for c, sub in by_c.items():
        for stars_key in (0, 1, 2, 3):
            ss = sub if stars_key == 0 else [h for h in sub if h["stars"] == stars_key]
            if not ss:
                continue
            buckets = _empty_b()
            goals = delay_sum = delay_n = 0
            for h in ss:
                if h["hit"] and h["delay"] is not None:
                    goals += 1
                    delay_sum += h["delay"]
                    delay_n += 1
                    bk = _delay_bucket(h["delay"])
                else:
                    bk = "no_goal"
                buckets[bk] = buckets.get(bk, 0) + 1
            _agg_upsert("country", c, "", None, stars_key, len(ss), goals, delay_sum, delay_n, buckets)

    # by league
    by_l = {}
    for h in hits:
        key = (h["country"] or "—", h["league"] or "—", h.get("league_id"))
        by_l.setdefault(key, []).append(h)
    for (c, lg, lid), sub in by_l.items():
        logo = ""
        for h in sub:
            if h.get("league_logo"):
                logo = h["league_logo"]
                break
        for stars_key in (0, 1, 2, 3):
            ss = sub if stars_key == 0 else [h for h in sub if h["stars"] == stars_key]
            if not ss:
                continue
            buckets = _empty_b()
            goals = delay_sum = delay_n = 0
            for h in ss:
                if h["hit"] and h["delay"] is not None:
                    goals += 1
                    delay_sum += h["delay"]
                    delay_n += 1
                    bk = _delay_bucket(h["delay"])
                else:
                    bk = "no_goal"
                buckets[bk] = buckets.get(bk, 0) + 1
            _agg_upsert("league", c, lg, lid, stars_key, len(ss), goals, delay_sum, delay_n, buckets, logo)

    total_sig = len(hits)
    total_goals = sum(1 for h in hits if h["hit"])
    week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    week_end = now.strftime("%Y-%m-%d")
    payload = {
        "signals": total_sig,
        "goals": total_goals,
        "hit_pct": round(100.0 * total_goals / total_sig, 1) if total_sig else 0.0,
    }
    try:
        db_execute(
            "INSERT OR REPLACE INTO impulse_stats_weeks "
            "(week_key, week_start, week_end, signals_count, goals_count, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (week_key, week_start, week_end, total_sig, total_goals,
             json.dumps(payload, ensure_ascii=False), now.strftime("%Y-%m-%d %H:%M:%S")),
        )
    except Exception:
        logger.exception("[impulse] week snapshot")

    _clear_impulse_data()
    set_app_setting("impulse_last_rollup_week", week_key)
    set_app_setting("impulse_auto_last_clear", now.strftime("%Y-%m-%d"))
    logger.info("[impulse] weekly rollup %s signals=%s goals=%s", week_key, total_sig, total_goals)
    return {
        "ok": True,
        "week_key": week_key,
        "signals": total_sig,
        "goals": total_goals,
        "cleared": True,
        "message": f"✅ Неделя {week_key}: сохранено {total_sig} сигналов, {total_goals} голов → статистика обновлена, сигналы очищены",
    }


def _agg_row_to_stats(row) -> dict:
    """row from impulse_stats_agg stars=0."""
    if not row:
        return {
            "count": 0, "with_goal": 0, "hit_pct": 0.0,
            "by_stars": {str(k): {"count": 0, "with_goal": 0, "hit_pct": 0.0} for k in (1, 2, 3)},
            "buckets": {"1-5": 0, "6-10": 0, "11-15": 0, "16-20": 0, "no_goal": 0},
            "avg_delay": None,
        }
    c, g = int(row[0] or 0), int(row[1] or 0)
    ds, dn = float(row[2] or 0), int(row[3] or 0)
    return {
        "count": c,
        "with_goal": g,
        "hit_pct": round(100.0 * g / c, 1) if c else 0.0,
        "avg_delay": round(ds / dn, 1) if dn else None,
        "buckets": {
            "1-5": int(row[4] or 0),
            "6-10": int(row[5] or 0),
            "11-15": int(row[6] or 0),
            "16-20": int(row[7] or 0),
            "no_goal": int(row[8] or 0),
        },
    }


def _merge_stats(a: dict, b: dict) -> dict:
    """Складывает две сводки count/with_goal/buckets/by_stars."""
    out = {
        "count": (a.get("count") or 0) + (b.get("count") or 0),
        "with_goal": (a.get("with_goal") or 0) + (b.get("with_goal") or 0),
        "buckets": {},
        "by_stars": {},
    }
    for k in ("1-5", "6-10", "11-15", "16-20", "no_goal"):
        out["buckets"][k] = (a.get("buckets") or {}).get(k, 0) + (b.get("buckets") or {}).get(k, 0)
    for sk in ("1", "2", "3"):
        aa = (a.get("by_stars") or {}).get(sk) or {}
        bb = (b.get("by_stars") or {}).get(sk) or {}
        sc = (aa.get("count") or 0) + (bb.get("count") or 0)
        sw = (aa.get("with_goal") or 0) + (bb.get("with_goal") or 0)
        out["by_stars"][sk] = {
            "count": sc,
            "with_goal": sw,
            "hit_pct": round(100.0 * sw / sc, 1) if sc else 0.0,
        }
    out["hit_pct"] = round(100.0 * out["with_goal"] / out["count"], 1) if out["count"] else 0.0
    return out


@app.delete("/api/admin/impulse")
async def admin_clear_impulse(clear_stats: int = 0, user: dict = Depends(require_admin)):
    """Очистка сигналов. clear_stats=1 — также сброс накопленной недельной статистики."""
    do_stats = bool(int(clear_stats or 0))
    _clear_impulse_data(clear_stats=do_stats)
    try:
        log_admin(user["id"], "clear_impulse", f"stats={do_stats}")
    except Exception:
        pass
    if do_stats:
        return {"ok": True, "message": "✅ Сигналы и накопленная статистика сброшены"}
    return {"ok": True, "message": "✅ Сигналы очищены (накопленная статистика сохранена)"}


@app.get("/api/admin/impulse-clear")
async def admin_impulse_clear_get(user: dict = Depends(require_admin)):
    """Настройки автоочистки / недельного rollup Импульса."""
    return {
        "ok": True,
        "enabled": get_app_setting("impulse_auto_clear", "0") == "1",
        "time": get_app_setting("impulse_auto_clear_time", "06:00"),
        "last_clear": get_app_setting("impulse_auto_last_clear", "") or None,
        "weekly_rollup": get_app_setting("impulse_weekly_rollup", "1") == "1",
        "last_rollup_week": get_app_setting("impulse_last_rollup_week", "") or None,
    }


@app.post("/api/admin/impulse-clear")
async def admin_impulse_clear_set(data: dict, user: dict = Depends(require_admin)):
    """Вкл/выкл автоочистку / недельный rollup и время (МСК)."""
    if not isinstance(data, dict):
        data = {}
    if "enabled" in data:
        set_app_setting("impulse_auto_clear", "1" if data.get("enabled") else "0")
    if "weekly_rollup" in data:
        set_app_setting("impulse_weekly_rollup", "1" if data.get("weekly_rollup") else "0")
    if data.get("time"):
        t = str(data["time"]).strip()
        if re.match(r"^\d{1,2}:\d{2}$", t):
            hh, mm = t.split(":")
            t = f"{int(hh):02d}:{int(mm):02d}"
            set_app_setting("impulse_auto_clear_time", t)
    try:
        log_admin(
            user["id"],
            "impulse_auto_clear",
            f"enabled={get_app_setting('impulse_auto_clear', '0')} weekly={get_app_setting('impulse_weekly_rollup', '1')} time={get_app_setting('impulse_auto_clear_time', '06:00')}",
        )
    except Exception:
        pass
    return {
        "ok": True,
        "message": "✅ Настройки автоочистки Импульса сохранены",
        "enabled": get_app_setting("impulse_auto_clear", "0") == "1",
        "time": get_app_setting("impulse_auto_clear_time", "06:00"),
        "last_clear": get_app_setting("impulse_auto_last_clear", "") or None,
        "weekly_rollup": get_app_setting("impulse_weekly_rollup", "1") == "1",
        "last_rollup_week": get_app_setting("impulse_last_rollup_week", "") or None,
    }


@app.post("/api/admin/impulse-rollup")
async def admin_impulse_rollup(user: dict = Depends(require_admin)):
    """Ручной запуск: сохранить неделю в накопленную статистику и очистить сигналы."""
    res = _impulse_weekly_rollup(force=True)
    try:
        log_admin(user["id"], "impulse_rollup", res.get("week_key") or "")
    except Exception:
        pass
    return res


def _impulse_star_bucket(val) -> int:
    """77–82 → 1★, 83–89 → 2★, 90+ → 3★."""
    try:
        v = float(val or 0)
    except (TypeError, ValueError):
        return 0
    if v >= 90:
        return 3
    if v >= 83:
        return 2
    if v >= 77:
        return 1
    return 0


def _goals_minutes_from_stats(stats_raw) -> list:
    """Список голов: [{minute, side}] из shotmap BSD stats."""
    out = []
    for shot in _shotmap_list(stats_raw):
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
        if m_int is None or m_int < 0 or m_int > 130:
            continue
        side = None
        if "home" in shot and isinstance(shot.get("home"), bool):
            side = "home" if shot["home"] else "away"
        elif shot.get("is_home") is not None:
            side = "home" if shot.get("is_home") else "away"
        else:
            side = _incident_side(shot)
        if side not in ("home", "away"):
            continue
        out.append({"minute": m_int, "side": side})
    out.sort(key=lambda x: x["minute"])
    return out


# Окно «сигнал → гол» (минуты). Гол позже окна не засчитывается.

def _parse_score_pair(sc: str):
    m = re.match(r"^\s*(\d+)\s*[:\-]\s*(\d+)\s*$", str(sc or ""))
    if not m:
        return None, None
    return int(m.group(1)), int(m.group(2))


def _impulse_check_goals_live(match_id: int, cur_minute: int, cur_h: int, cur_a: int,
                              is_live: bool, status: str = "", stats_raw=None,
                              signal_created: Optional[dict] = None) -> None:
    """
    После окна 20′ (включительно) проставляем goal_hit / goal_checked.
    Сигнал 54′ → окно до 74′; любой гол в интервале = +; позже = −.
    Если матч ушёл из live до конца окна — всё равно закрываем проверку
    (по shotmap / счёту / wall-clock), иначе статистика «висит».
    """
    window = IMPULSE_GOAL_WINDOW
    st = (status or "").lower()
    finished = (not is_live) or any(
        x in st for x in ("ft", "finished", "ended", "full time", "fulltime", "aet", "pen")
    ) or int(cur_minute or 0) > 95

    rows = db_fetchall(
        "SELECT id, side, minute, score, created_at FROM impulse_signals "
        "WHERE match_id = ? AND COALESCE(goal_checked, 0) = 0",
        (match_id,),
    ) or []
    # fallback без created_at
    if not rows:
        try:
            rows = db_fetchall(
                "SELECT id, side, minute, score, NULL FROM impulse_signals "
                "WHERE match_id = ? AND COALESCE(goal_checked, 0) = 0",
                (match_id,),
            ) or []
        except Exception:
            return
    if not rows:
        return

    # минуты голов из shotmap
    goal_mins = {"home": [], "away": []}
    try:
        for g in _goals_minutes_from_stats(stats_raw) or []:
            side_g = (g.get("side") or "").lower()
            mn = int(g.get("minute") or 0)
            if side_g in goal_mins and mn > 0:
                goal_mins[side_g].append(mn)
    except Exception:
        pass

    score_rows = db_fetchall(
        "SELECT minute, score FROM impulse_signals WHERE match_id = ? ORDER BY minute ASC, id ASC",
        (match_id,),
    ) or []

    def _parse_sc(sc):
        m = re.match(r"^\s*(\d+)\s*[:\-]\s*(\d+)\s*$", str(sc or ""))
        if not m:
            return None, None
        return int(m.group(1)), int(m.group(2))

    now_dt = datetime.now(tz)

    for row in rows:
        rid, side, smin, sscore = row[0], row[1], row[2], row[3]
        created_at = row[4] if len(row) > 4 else None
        try:
            smin = int(smin or 0)
        except (TypeError, ValueError):
            smin = 0
        side = (side or "home").lower()
        if side not in ("home", "away"):
            side = "home"

        if smin >= 70:
            window_end = 120
            # ждём FT, либо wall-clock ≥ 25 мин с сигнала
            age_ok = False
            if created_at:
                try:
                    cdt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                    if cdt.tzinfo is None:
                        cdt = cdt.replace(tzinfo=tz)
                    age_ok = (now_dt - cdt).total_seconds() >= (window + 5) * 60
                except Exception:
                    age_ok = False
            if not finished and not age_ok:
                continue
        else:
            window_end = smin + window  # включительно
            cur_m_check = int(cur_minute or 0)
            # матч ушёл из live — не ждём минуту window_end вечно
            if not finished and cur_m_check < window_end:
                age_ok = False
                if created_at:
                    try:
                        cdt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                        if cdt.tzinfo is None:
                            cdt = cdt.replace(tzinfo=tz)
                        # реальное время ≥ окна + 2 мин
                        age_ok = (now_dt - cdt).total_seconds() >= (window + 2) * 60
                    except Exception:
                        age_ok = False
                if not age_ok:
                    continue

        hit = 0
        cur_m = int(cur_minute or 0)
        if finished and cur_m < window_end:
            # для расчёта near_close считаем окно закрытым
            cur_m = window_end

        # 1) shotmap: любой гол в [smin, window_end]
        for _side_g in ("home", "away"):
            for gm in goal_mins.get(_side_g, []):
                if smin <= gm <= window_end:
                    hit = 1
                    break
            if hit:
                break

        # 2) рост счёта по таймлайну сигналов внутри окна
        if hit == 0:
            base_h, base_a = _parse_sc(sscore)
            if base_h is None:
                base_h, base_a = 0, 0
            for mn, sc in score_rows:
                try:
                    mn = int(mn or 0)
                except (TypeError, ValueError):
                    continue
                if mn <= smin:
                    continue
                if mn > window_end:
                    break
                h, a = _parse_sc(sc)
                if h is None:
                    continue
                if h > base_h or a > base_a:
                    hit = 1
                    break

        # 3) текущий счёт: сразу после окна, после FT, или forced close
        if hit == 0:
            near_close = cur_m <= window_end + 5
            late_or_done = finished or smin >= 70
            if near_close or late_or_done:
                base_h, base_a = _parse_sc(sscore)
                if base_h is None:
                    base_h, base_a = 0, 0
                # при forced close без shotmap: рост счёта = плюс только если
                # матч ещё около окна или FT с late signal; иначе 0 (не гадаем)
                if int(cur_h) > int(base_h) or int(cur_a) > int(base_a):
                    if near_close or (finished and smin >= 70) or (finished and near_close):
                        hit = 1
                    elif finished and (goal_mins["home"] or goal_mins["away"]):
                        # shotmap был, но гол вне окна — hit остаётся 0
                        pass
                    elif finished and near_close:
                        hit = 1
                    elif finished:
                        # FT, нет минут голов — осторожно: плюс только если
                        # счёт вырос (любой гол мог быть в окне или после)
                        # Берём плюс: пользователь просил любой гол в интервале;
                        # без shotmap на FT лучше закрыть по счёту.
                        hit = 1

        try:
            db_execute(
                "UPDATE impulse_signals SET goal_checked = 1, goal_hit = ? WHERE id = ?",
                (hit, rid),
            )
        except Exception:
            try:
                db_execute("UPDATE impulse_signals SET goal_checked = 1 WHERE id = ?", (rid,))
            except Exception:
                pass



IMPULSE_GOAL_WINDOW = 20


def _delay_bucket(delay: Optional[int], window: int = IMPULSE_GOAL_WINDOW) -> str:
    """Интервалы только внутри окна; вне окна / нет гола → no_goal."""
    if delay is None or delay < 0 or delay > window:
        return "no_goal"
    if delay <= 5:
        return "1-5"
    if delay <= 10:
        return "6-10"
    if delay <= 15:
        return "11-15"
    return "16-20"


def _same_half(sig_min: int, goal_min: int) -> bool:
    """
    Гол засчитывается в окне 20′ включительно, в т.ч. через перерыв
    (сигнал 40′ → гол до 60′ включительно).
    """
    try:
        sig_min = int(sig_min or 0)
        goal_min = int(goal_min or 0)
    except (TypeError, ValueError):
        return False
    if goal_min < sig_min:
        return False
    if goal_min - sig_min <= IMPULSE_GOAL_WINDOW:
        return True
    if sig_min <= 45:
        return goal_min <= 45
    return goal_min > 45


@app.get("/api/admin/impulse-report")
async def admin_impulse_report(
    date: str = "",
    days: int = 1,
    period: str = "",
    user: dict = Depends(require_admin),
):
    """
    Отчёт: сигналы импульса по звёздам → через сколько минут гол.
    period: day | week | month | all.
    date=YYYY-MM-DD — конец периода (по умолчанию вчера МСК; для all — до сегодня).
    """
    period = (period or "").strip().lower()
    date = (date or "").strip()
    today_msk = datetime.now(tz).strftime("%Y-%m-%d")
    if date and re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        end_day = date
    else:
        end_day = (datetime.now(tz) - timedelta(days=1)).strftime("%Y-%m-%d")
    try:
        end_dt = datetime.strptime(end_day, "%Y-%m-%d")
    except ValueError:
        end_dt = datetime.now(tz) - timedelta(days=1)
        end_day = end_dt.strftime("%Y-%m-%d")

    if period == "all":
        start_day = None
        end_day_out = today_msk
        period_label = "Всё время"
    elif period == "month":
        start_day = (end_dt - timedelta(days=29)).strftime("%Y-%m-%d")
        end_day_out = end_day
        period_label = "Месяц"
    elif period == "week":
        start_day = (end_dt - timedelta(days=6)).strftime("%Y-%m-%d")
        end_day_out = end_day
        period_label = "Неделя"
    elif period == "day":
        start_day = end_day
        end_day_out = end_day
        period_label = "День"
    else:
        try:
            days = max(1, min(90, int(days or 1)))
        except (TypeError, ValueError):
            days = 1
        start_day = (end_dt - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        end_day_out = end_day
        period_label = f"{days} дн."

    base_sql = (
        "SELECT s.id, s.match_id, s.event_id, s.match_name, s.team, s.side, s.value, s.minute, s.score, "
        "s.created_at, COALESCE(s.country,''), COALESCE(s.league,''), COALESCE(m.league_logo,''), "
        "s.goal_hit, COALESCE(s.goal_checked, 0) "
        "FROM impulse_signals s "
        "LEFT JOIN matches m ON m.id = s.match_id "
        "WHERE s.value >= ? AND COALESCE(s.minute, 0) > 10 "
        "AND COALESCE(s.minute, 0) NOT IN (44, 45, 46, 47, 48) AND COALESCE(s.minute, 0) <= 89 "
    )
    if start_day is None:
        rows = db_fetchall(
            base_sql + "ORDER BY s.created_at ASC, s.id ASC LIMIT 5000",
            (IMPULSE_THRESHOLD,),
        ) or []
        if rows:
            start_day = (rows[0][9] or "")[:10] or "—"
        else:
            start_day = "—"
        end_day = end_day_out
    else:
        rows = db_fetchall(
            base_sql + "AND substr(s.created_at, 1, 10) >= ? AND substr(s.created_at, 1, 10) <= ? "
            "ORDER BY s.created_at ASC, s.id ASC LIMIT 5000",
            (IMPULSE_THRESHOLD, start_day, end_day_out),
        ) or []
        end_day = end_day_out

    goals_by_eid: dict = {}
    eids = sorted({int(r[2]) for r in rows if r[2]})
    if eids and BSD_API_KEY and BSD_API_KEY.strip():
        try:
            async with aiohttp.ClientSession() as session:
                sem = asyncio.Semaphore(6)

                async def _load(eid: int):
                    async with sem:
                        try:
                            st = await _bsd_get(session, f"events/{eid}/stats/")
                            goals_by_eid[eid] = _goals_minutes_from_stats(st)
                        except Exception:
                            goals_by_eid[eid] = []

                # до 150 матчей за период (день/неделя/месяц/всё время)
                await asyncio.gather(*[_load(e) for e in eids[:150]])
        except Exception:
            logger.exception("[impulse-report] BSD goals fetch")

    score_by_match_timeline = {}
    for r in rows:
        mid = r[1]
        mn = int(r[7] or 0)
        sc = str(r[8] or "")
        if mid is None:
            continue
        score_by_match_timeline.setdefault(int(mid), []).append((mn, sc))

    def _parse_score(sc: str):
        m = re.match(r"^\s*(\d+)\s*[:\-]\s*(\d+)\s*$", str(sc or ""))
        if not m:
            return None, None
        return int(m.group(1)), int(m.group(2))

    def _approx_goal_after(match_id, signal_min, side, window=IMPULSE_GOAL_WINDOW):
        """Любой гол (любая сторона) в окне = попадание."""
        tl = score_by_match_timeline.get(int(match_id) if match_id else -1) or []
        base_h, base_a = None, None
        for mn, sc in tl:
            if mn < signal_min:
                base_h, base_a = _parse_score(sc)
            elif mn == signal_min:
                h, a = _parse_score(sc)
                if h is not None:
                    base_h, base_a = h, a
        if base_h is None:
            return None
        for mn, sc in tl:
            if mn <= signal_min:
                continue
            if mn - signal_min > window:
                break
            h, a = _parse_score(sc)
            if h is None:
                continue
            if h > base_h or a > base_a:
                return mn - signal_min
        return None

    window = IMPULSE_GOAL_WINDOW
    buckets_def = ["1-5", "6-10", "11-15", "16-20", "no_goal"]
    by_star = {
        1: {"label": "★ 77–82", "count": 0, "with_goal": 0, "delays": [], "buckets": {b: 0 for b in buckets_def}, "samples": []},
        2: {"label": "★★ 83–89", "count": 0, "with_goal": 0, "delays": [], "buckets": {b: 0 for b in buckets_def}, "samples": []},
        3: {"label": "★★★ 90+", "count": 0, "with_goal": 0, "delays": [], "buckets": {b: 0 for b in buckets_def}, "samples": []},
    }
    details = []
    goals_source = "bsd_shotmap" if any(goals_by_eid.values()) else "score_fallback"

    # Сначала сырые пары сигнал→гол (в окне и том же тайме)
    raw_hits = []  # dicts with delay/goal for dedup
    for r in rows:
        sid = r[0]
        match_id, eid, match_name, team, side = r[1], r[2], r[3], r[4], r[5]
        value, minute, score, created_at = r[6], r[7], r[8], r[9]
        country = (r[10] if len(r) > 10 else "") or ""
        league = (r[11] if len(r) > 11 else "") or ""
        league_logo = (r[12] if len(r) > 12 else "") or ""
        stars = _impulse_star_bucket(value)
        if stars not in by_star:
            continue
        side = (side or "").lower()
        if side not in ("home", "away"):
            side = "home"
        sig_min = int(minute or 0)
        val = float(value or 0)
        delay = None
        goal_min = None
        source = None
        goals = goals_by_eid.get(int(eid), []) if eid else []
        if goals:
            source = "bsd"
            # любой гол в окне (своя сторона или соперник) = плюс
            for g in goals:
                gm = int(g["minute"])
                if gm <= sig_min:
                    continue
                d = gm - sig_min
                if d < 0:
                    continue
                if d > window and sig_min < 70:
                    continue
                if sig_min >= 70:
                    goal_min = gm
                    delay = d
                    break
                goal_min = gm
                delay = d
                break
        else:
            source = "approx"
            delay = _approx_goal_after(match_id, sig_min, side, window)
            if delay is not None and 0 <= delay <= window:
                goal_min = sig_min + delay
            elif delay is not None and sig_min >= 70:
                goal_min = sig_min + delay
            else:
                delay = None
                goal_min = None

        # goal_hit из live-проверки окна 20′ — главный источник для статистики
        g_hit_raw = r[13] if len(r) > 13 else None
        g_chk_raw = r[14] if len(r) > 14 else 0
        try:
            g_chk_i = int(g_chk_raw or 0)
        except (TypeError, ValueError):
            g_chk_i = 0
        try:
            g_hit_i = None if g_hit_raw is None else int(g_hit_raw)
        except (TypeError, ValueError):
            g_hit_i = None
        if g_chk_i and g_hit_i is not None:
            if g_hit_i == 1:
                if delay is None:
                    delay = window
                    goal_min = sig_min + window if sig_min < 70 else min(120, sig_min + window)
                source = source or "goal_hit"
            else:
                delay = None
                goal_min = None
                source = "goal_hit"

        raw_hits.append({
            "row_id": sid,
            "match_id": match_id,
            "match": match_name or "",
            "team": team or "",
            "side": side,
            "value": round(val, 1),
            "stars": stars,
            "signal_min": sig_min,
            "score": score or "",
            "goal_min": goal_min,
            "delay": delay,
            "source": source,
            "created_at": created_at or "",
            "half": 1 if sig_min <= 45 else 2,
            "country": country,
            "league": league,
            "league_logo": league_logo,
            "goal_checked": g_chk_i,
            "goal_hit": g_hit_i,
        })

    # Каждый сигнал независим: + если гол в [sig, sig+20] включительно
    # Приоритет: goal_checked/goal_hit (то же +/- что в карточке матча)
    credited_ids = set()
    absorbed_ids = set()  # больше не поглощаем
    for h in raw_hits:
        h["absorbed"] = False
        g_chk = int(h.get("goal_checked") or 0)
        g_hit = h.get("goal_hit")
        if g_chk and g_hit is not None:
            if int(g_hit) == 1:
                if h.get("delay") is None:
                    h["delay"] = window
                    h["goal_min"] = int(h.get("signal_min") or 0) + window
                credited_ids.add(h["row_id"])
            else:
                h["delay"] = None
                h["goal_min"] = None
            continue
        if h.get("delay") is not None and h.get("goal_min") is not None:
            # для ≥70′ окно до конца — delay может быть >20
            smin = int(h.get("signal_min") or 0)
            d = h.get("delay")
            if smin >= 70 or (d is not None and 0 <= int(d) <= window):
                credited_ids.add(h["row_id"])
            else:
                h["delay"] = None
                h["goal_min"] = None
        else:
            h["delay"] = None
            h["goal_min"] = None

    for h in raw_hits:
        if h["row_id"] in absorbed_ids:
            continue
        stars = h["stars"]
        if stars not in by_star:
            continue
        st = by_star[stars]
        st["count"] += 1
        delay = h["delay"] if h["row_id"] in credited_ids else None
        bkey = _delay_bucket(delay, window)
        st["buckets"][bkey] += 1
        if h["row_id"] in credited_ids and delay is not None:
            st["with_goal"] += 1
            st["delays"].append(delay)

    def _summary(st: dict) -> dict:
        delays = st["delays"]
        avg = round(sum(delays) / len(delays), 1) if delays else None
        med = None
        if delays:
            sd = sorted(delays)
            mid = len(sd) // 2
            med = float(sd[mid]) if len(sd) % 2 else round((sd[mid - 1] + sd[mid]) / 2, 1)
        hit = round(100.0 * st["with_goal"] / st["count"], 1) if st["count"] else 0.0
        return {
            "label": st["label"],
            "count": st["count"],
            "with_goal": st["with_goal"],
            "hit_pct": hit,
            "avg_delay": avg,
            "median_delay": med,
            "buckets": st["buckets"],
        }

    def _empty_star_half():
        return {
            1: {"count": 0, "with_goal": 0},
            2: {"count": 0, "with_goal": 0},
            3: {"count": 0, "with_goal": 0},
            "all": {"count": 0, "with_goal": 0},
        }

    by_country: dict = {}
    for h in raw_hits:
        if h["row_id"] in absorbed_ids:
            continue
        ctry = (h.get("country") or "—").strip() or "—"
        lg = (h.get("league") or "—").strip() or "—"
        stars = h.get("stars") or 0
        half = 1 if (h.get("half") or 1) == 1 else 2
        hit = 1 if h["row_id"] in credited_ids else 0
        if ctry not in by_country:
            by_country[ctry] = {
                "country": ctry,
                "flag": get_flag(ctry),
                "count": 0,
                "with_goal": 0,
                "halves": {1: _empty_star_half(), 2: _empty_star_half()},
                "by_stars": {1: {"count": 0, "with_goal": 0}, 2: {"count": 0, "with_goal": 0}, 3: {"count": 0, "with_goal": 0}},
                "leagues": {},
            }
        bc = by_country[ctry]
        bc["count"] += 1
        bc["with_goal"] += hit
        bc["halves"][half]["all"]["count"] += 1
        bc["halves"][half]["all"]["with_goal"] += hit
        if stars in (1, 2, 3):
            bc["by_stars"][stars]["count"] += 1
            bc["by_stars"][stars]["with_goal"] += hit
            bc["halves"][half][stars]["count"] += 1
            bc["halves"][half][stars]["with_goal"] += hit
        if lg not in bc["leagues"]:
            bc["leagues"][lg] = {
                "league": lg,
                "league_logo": h.get("league_logo") or "",
                "count": 0,
                "with_goal": 0,
                "halves": {1: _empty_star_half(), 2: _empty_star_half()},
                "by_stars": {1: {"count": 0, "with_goal": 0}, 2: {"count": 0, "with_goal": 0}, 3: {"count": 0, "with_goal": 0}},
            }
        bl = bc["leagues"][lg]
        if h.get("league_logo") and not bl["league_logo"]:
            bl["league_logo"] = h["league_logo"]
        bl["count"] += 1
        bl["with_goal"] += hit
        bl["halves"][half]["all"]["count"] += 1
        bl["halves"][half]["all"]["with_goal"] += hit
        if stars in (1, 2, 3):
            bl["by_stars"][stars]["count"] += 1
            bl["by_stars"][stars]["with_goal"] += hit
            bl["halves"][half][stars]["count"] += 1
            bl["halves"][half][stars]["with_goal"] += hit

    def _pct(w, c):
        return round(100.0 * w / c, 1) if c else 0.0

    def _fin_block(d: dict) -> dict:
        d["hit_pct"] = _pct(d["with_goal"], d["count"])
        for sk in (1, 2, 3):
            sc = d["by_stars"][sk]["count"]
            sw = d["by_stars"][sk]["with_goal"]
            d["by_stars"][sk]["hit_pct"] = _pct(sw, sc)
        for hf in (1, 2):
            for sk in (1, 2, 3, "all"):
                cell = d["halves"][hf][sk]
                cell["hit_pct"] = _pct(cell["with_goal"], cell["count"])
        return d

    countries_list = []
    leagues_list = []
    for ctry, bc in by_country.items():
        _fin_block(bc)
        leagues_out = []
        for lg, bl in bc["leagues"].items():
            _fin_block(bl)
            item = {
                "country": ctry,
                "flag": bc["flag"],
                "league": bl["league"],
                "league_logo": bl["league_logo"],
                "count": bl["count"],
                "with_goal": bl["with_goal"],
                "hit_pct": bl["hit_pct"],
                "by_stars": bl["by_stars"],
                "halves": bl["halves"],
            }
            leagues_out.append(item)
            leagues_list.append(item)
        leagues_out.sort(key=lambda x: (-x["count"], x["league"]))
        countries_list.append({
            "country": bc["country"],
            "flag": bc["flag"],
            "count": bc["count"],
            "with_goal": bc["with_goal"],
            "hit_pct": bc["hit_pct"],
            "by_stars": bc["by_stars"],
            "halves": bc["halves"],
            "leagues": leagues_out,
        })
    countries_list.sort(key=lambda x: (-x["count"], x["country"]))
    leagues_list.sort(key=lambda x: (-x["count"], x["country"], x["league"]))
    details = []

    # общая статистика по всем сигналам периода
    counted = [h for h in raw_hits if h["row_id"] not in absorbed_ids]
    sig_total = len(counted)
    goals_total = len(credited_ids)
    miss_total = max(0, sig_total - goals_total)
    overall_pct = round(100.0 * goals_total / sig_total, 1) if sig_total else 0.0
    overall = {
        "signals": sig_total,
        "with_goal": goals_total,
        "without_goal": miss_total,
        "hit_pct": overall_pct,
        "threshold": IMPULSE_THRESHOLD,
        "goal_window": window,
    }

    return {
        "ok": True,
        "period": period or "custom",
        "period_label": period_label,
        "start_day": start_day,
        "end_day": end_day,
        "threshold": IMPULSE_THRESHOLD,
        "goal_window": window,
        "signals_total": sig_total,
        "credited_goals": goals_total,
        "overall": overall,
        "absorbed": len(absorbed_ids),
        "events_with_goals": sum(1 for v in goals_by_eid.values() if v),
        "goals_source": goals_source,
        "rules": {
            "window_min": window,
            "by_stars": True,
            "same_half": True,
            "one_goal_one_signal": True,
            "note": "Гол засчитывается только в окне N минут после сигнала, в том же тайме; один гол — один ближайший сигнал. Статистика отдельно по ★ / ★★ / ★★★.",
        },
        "stars": {
            "1": _summary(by_star[1]),
            "2": _summary(by_star[2]),
            "3": _summary(by_star[3]),
        },
        "countries": countries_list,
        "leagues": leagues_list,
        "details": details[:200],
    }


def _impulse_league_hit_stats(country: str, league: str, days: int = 30, league_id=None) -> dict:
    """
    % проходимости импульса по этой лиге (окно 20′, таймы, звёзды).
    Накопленное + текущие сигналы. Без BSD.
    """
    country = (country or "").strip()
    league = (league or "").strip()
    window = IMPULSE_GOAL_WINDOW
    try:
        days = max(1, min(90, int(days or 30)))
    except (TypeError, ValueError):
        days = 30
    start_day = (datetime.now(tz) - timedelta(days=days)).strftime("%Y-%m-%d")
    lid = None
    try:
        if league_id is not None and str(league_id).strip() != "":
            lid = int(league_id)
    except (TypeError, ValueError):
        lid = None

    if lid is not None:
        rows = db_fetchall(
            "SELECT s.id, s.match_id, s.team, s.side, s.value, s.minute, s.score, s.created_at, "
            "COALESCE(s.country,''), COALESCE(s.league,''), COALESCE(m.league_logo,''), "
            "s.goal_hit, COALESCE(s.goal_checked, 0) "
            "FROM impulse_signals s "
            "LEFT JOIN matches m ON m.id = s.match_id "
            "WHERE s.value >= ? AND COALESCE(s.minute, 0) > 10 "
            "AND COALESCE(s.minute, 0) NOT IN (44, 45, 46, 47, 48) AND COALESCE(s.minute, 0) <= 89 "
            "AND substr(s.created_at, 1, 10) >= ? "
            "AND m.league_id = ? "
            "ORDER BY s.created_at ASC, s.id ASC LIMIT 2000",
            (IMPULSE_THRESHOLD, start_day, lid),
        ) or []
    else:
        rows = db_fetchall(
            "SELECT s.id, s.match_id, s.team, s.side, s.value, s.minute, s.score, s.created_at, "
            "COALESCE(s.country,''), COALESCE(s.league,''), COALESCE(m.league_logo,''), "
            "s.goal_hit, COALESCE(s.goal_checked, 0) "
            "FROM impulse_signals s "
            "LEFT JOIN matches m ON m.id = s.match_id "
            "WHERE s.value >= ? AND COALESCE(s.minute, 0) > 10 "
            "AND COALESCE(s.minute, 0) NOT IN (44, 45, 46, 47, 48) AND COALESCE(s.minute, 0) <= 89 "
            "AND substr(s.created_at, 1, 10) >= ? "
            "AND (? = '' OR LOWER(TRIM(s.country)) = LOWER(?)) "
            "AND (? != '' AND LOWER(TRIM(s.league)) = LOWER(?)) "
            "ORDER BY s.created_at ASC, s.id ASC LIMIT 2000",
            (IMPULSE_THRESHOLD, start_day, country, country, league, league),
        ) or []

    def _parse_score(sc: str):
        m = re.match(r"^\s*(\d+)\s*[:\-]\s*(\d+)\s*$", str(sc or ""))
        if not m:
            return None, None
        return int(m.group(1)), int(m.group(2))

    timeline = {}
    for r in rows:
        mid = r[1]
        if mid is None:
            continue
        timeline.setdefault(int(mid), []).append((int(r[5] or 0), str(r[6] or "")))

    raw = []
    for r in rows:
        sid, match_id, team, side, value, minute, score = r[0], r[1], r[2], r[3], r[4], r[5], r[6]
        stars = _impulse_star_bucket(value)
        if stars not in (1, 2, 3):
            try:
                v = float(value or 0)
            except (TypeError, ValueError):
                v = 0
            stars = 3 if v >= 90 else (2 if v >= 83 else (1 if v >= 77 else 2))
        side = (side or "").lower()
        if side not in ("home", "away"):
            side = "home"
        sig_min = int(minute or 0)
        if 44 <= sig_min <= 48:
            continue
        half = 1 if sig_min <= 45 else 2
        delay = goal_min = None
        base_h = base_a = None
        tl = timeline.get(int(match_id) if match_id else -1) or []
        for mn, sc in tl:
            if mn < sig_min:
                base_h, base_a = _parse_score(sc)
            elif mn == sig_min:
                h, a = _parse_score(sc)
                if h is not None:
                    base_h, base_a = h, a
        if base_h is not None:
            for mn, sc in tl:
                if mn <= sig_min:
                    continue
                # окно 20′ включительно: сигнал 54 → гол до 74 включительно
                if mn - sig_min > window and sig_min < 70:
                    break
                h, a = _parse_score(sc)
                if h is None:
                    continue
                # любой гол в окне (как в _impulse_check_goals_live)
                if h > base_h or a > base_a:
                    delay, goal_min = mn - sig_min, mn
                    break
        # goal_checked/goal_hit — то же +/- что в карточке матча (источник правды)
        g_hit = r[11] if len(r) > 11 else None
        g_chk = r[12] if len(r) > 12 else 0
        try:
            g_chk = int(g_chk or 0)
        except (TypeError, ValueError):
            g_chk = 0
        try:
            g_hit_i = None if g_hit is None else int(g_hit)
        except (TypeError, ValueError):
            g_hit_i = None
        if g_chk and g_hit_i is not None:
            if g_hit_i == 1:
                if delay is None:
                    delay = window
                    goal_min = sig_min + window if sig_min < 70 else min(120, sig_min + window)
            else:
                delay = goal_min = None
        raw.append({
            "row_id": sid, "match_id": match_id, "side": side, "stars": stars,
            "value": float(value or 0), "delay": delay, "goal_min": goal_min,
            "signal_min": sig_min, "half": half,
            "country": (r[8] if len(r) > 8 else country) or country,
            "league": (r[9] if len(r) > 9 else league) or league,
            "league_logo": (r[10] if len(r) > 10 else "") or "",
            "goal_checked": g_chk,
            "goal_hit": g_hit_i,
        })

    # Каждый сигнал — свой 20′ отрезок; +/- из goal_hit после закрытия окна
    credited = set()
    absorbed = set()
    for h in raw:
        g_chk = int(h.get("goal_checked") or 0)
        g_hit = h.get("goal_hit")
        if g_chk and g_hit is not None:
            if int(g_hit) == 1:
                credited.add(h["row_id"])
                if h.get("delay") is None:
                    h["delay"] = window
                    h["goal_min"] = int(h.get("signal_min") or 0) + window
            # goal_hit=0 → не в credited
        elif h.get("delay") is not None and h.get("goal_min") is not None:
            credited.add(h["row_id"])

    def _empty_h():
        return {
            1: {"count": 0, "with_goal": 0},
            2: {"count": 0, "with_goal": 0},
            3: {"count": 0, "with_goal": 0},
            "all": {"count": 0, "with_goal": 0},
        }

    live_count = live_goals = 0
    by_stars = {str(k): {"count": 0, "with_goal": 0} for k in (1, 2, 3)}
    halves = {1: _empty_h(), 2: _empty_h()}
    logo = ""
    for h in raw:
        if h["row_id"] in absorbed:
            continue
        if h.get("league_logo") and not logo:
            logo = h["league_logo"]
        hit = 1 if h["row_id"] in credited else 0
        live_count += 1
        live_goals += hit
        sk = str(h["stars"])
        by_stars[sk]["count"] += 1
        by_stars[sk]["with_goal"] += hit
        hf = h["half"]
        halves[hf]["all"]["count"] += 1
        halves[hf]["all"]["with_goal"] += hit
        halves[hf][h["stars"]]["count"] += 1
        halves[hf][h["stars"]]["with_goal"] += hit

    for sk in ("1", "2", "3"):
        sc, sw = by_stars[sk]["count"], by_stars[sk]["with_goal"]
        by_stars[sk]["hit_pct"] = round(100.0 * sw / sc, 1) if sc else 0.0
    for hf in (1, 2):
        for sk in (1, 2, 3, "all"):
            cell = halves[hf][sk]
            c, w = cell["count"], cell["with_goal"]
            cell["hit_pct"] = round(100.0 * w / c, 1) if c else 0.0

    live = {
        "count": live_count,
        "with_goal": live_goals,
        "hit_pct": round(100.0 * live_goals / live_count, 1) if live_count else 0.0,
        "by_stars": by_stars,
        "halves": halves,
    }

    # накопленное
    hist = {
        "count": 0, "with_goal": 0, "hit_pct": 0.0,
        "by_stars": {str(k): {"count": 0, "with_goal": 0, "hit_pct": 0.0} for k in (1, 2, 3)},
        "halves": {1: _empty_h(), 2: _empty_h()},
    }
    try:
        if lid is not None:
            agg0 = db_fetchone(
                "SELECT signals_count, goals_count, COALESCE(league_logo,'') "
                "FROM impulse_stats_agg WHERE scope = 'league' AND COALESCE(league_id, -1) = ? AND stars = 0",
                (lid,),
            )
        else:
            agg0 = db_fetchone(
                "SELECT signals_count, goals_count, COALESCE(league_logo,'') "
                "FROM impulse_stats_agg WHERE scope = 'league' AND LOWER(TRIM(country)) = LOWER(?) "
                "AND LOWER(TRIM(league)) = LOWER(?) AND stars = 0 LIMIT 1",
                (country or "", league or ""),
            )
        if agg0:
            hist["count"] = int(agg0[0] or 0)
            hist["with_goal"] = int(agg0[1] or 0)
            if agg0[2]:
                logo = logo or agg0[2]
        for sk in (1, 2, 3):
            if lid is not None:
                ar = db_fetchone(
                    "SELECT signals_count, goals_count FROM impulse_stats_agg "
                    "WHERE scope = 'league' AND COALESCE(league_id, -1) = ? AND stars = ?",
                    (lid, sk),
                )
            else:
                ar = db_fetchone(
                    "SELECT signals_count, goals_count FROM impulse_stats_agg "
                    "WHERE scope = 'league' AND LOWER(TRIM(country)) = LOWER(?) "
                    "AND LOWER(TRIM(league)) = LOWER(?) AND stars = ? LIMIT 1",
                    (country or "", league or "", sk),
                )
            sc = int(ar[0] or 0) if ar else 0
            sw = int(ar[1] or 0) if ar else 0
            hist["by_stars"][str(sk)] = {
                "count": sc, "with_goal": sw,
                "hit_pct": round(100.0 * sw / sc, 1) if sc else 0.0,
            }
        hist["hit_pct"] = round(100.0 * hist["with_goal"] / hist["count"], 1) if hist["count"] else 0.0
    except Exception:
        pass

    merged_stars = {}
    for sk in ("1", "2", "3"):
        sc = (hist["by_stars"].get(sk, {}).get("count") or 0) + (live["by_stars"].get(sk, {}).get("count") or 0)
        sw = (hist["by_stars"].get(sk, {}).get("with_goal") or 0) + (live["by_stars"].get(sk, {}).get("with_goal") or 0)
        merged_stars[sk] = {
            "count": sc, "with_goal": sw,
            "hit_pct": round(100.0 * sw / sc, 1) if sc else 0.0,
        }
    # таймы: накопленное по таймам в agg пока нет — берём live; hist all в общий %
    merged_halves = {1: _empty_h(), 2: _empty_h()}
    for hf in (1, 2):
        for sk in (1, 2, 3, "all"):
            lc = live["halves"][hf][sk]
            merged_halves[hf][sk] = {
                "count": lc["count"],
                "with_goal": lc["with_goal"],
                "hit_pct": lc["hit_pct"],
            }

    total = hist["count"] + live["count"]
    goals = hist["with_goal"] + live["with_goal"]
    ctry = country or (raw[0]["country"] if raw else "")
    lg = league or (raw[0]["league"] if raw else "")
    return {
        "country": ctry,
        "league": lg,
        "flag": get_flag(ctry),
        "league_logo": logo,
        "count": total,
        "with_goal": goals,
        "hit_pct": round(100.0 * goals / total, 1) if total else 0.0,
        "by_stars": merged_stars,
        "halves": merged_halves,
        "goal_window": window,
        "period_days": days,
        "threshold": IMPULSE_THRESHOLD,
        "cumulative": True,
        "hist_count": hist["count"],
        "live_count": live["count"],
    }



@app.get("/api/admin/auto-daily")
async def admin_auto_daily_get(user: dict = Depends(require_admin)):
    """Настройки авто: время очистки/парсера, ранги, ключевые лиги, лимит."""
    enabled = get_app_setting("auto_daily", "0") == "1"
    ranks = _auto_get_ranks()
    key_leagues = _auto_get_key_leagues()
    # варианты ключевых лиг из словаря приоритетов (ранг <= 7)
    # Уникальные лиги: без точных/префиксных дублей (EFL Championship ≈ Championship)
    league_options = []
    seen_cores = set()
    _PREFIXES = ("efl", "uefa", "isuzuute", "stoiximan", "cinch", "sseairtricity")

    def _core(name: str) -> str:
        s = re.sub(r"[^a-z0-9]+", "", name.lower())
        for p in _PREFIXES:
            if s.startswith(p) and len(s) > len(p) + 3:
                s = s[len(p):]
        return s

    for k, v in sorted(LEAGUE_PRIORITIES.items(), key=lambda x: (x[1], -len(x[0]), x[0])):
        if not isinstance(v, int) or v not in (7, 8):
            continue
        core = _core(k)
        if not core or core in seen_cores:
            continue
        # не схлопывать разные страны с общим хвостом (serie b vs serie bbrazil → разные core)
        seen_cores.add(core)
        country = LEAGUE_COUNTRY.get(k) or ""
        if not country:
            for lk, lc in LEAGUE_COUNTRY.items():
                if _core(lk) == core:
                    country = lc
                    break
        label = f"{country} · {k}" if country else k
        league_options.append({"name": k, "country": country, "label": label, "rank": v})
    return {
        "ok": True,
        "enabled": enabled,
        "clear_time": get_app_setting("auto_clear_time", "06:00"),
        "parse_time": get_app_setting("auto_parse_time", "08:00"),
        "ranks": ranks,
        "key_leagues": key_leagues,
        "league_options": league_options,
        "max_matches": _auto_get_max_matches(),
        "last_clear": get_app_setting("auto_last_clear", "") or None,
        "last_parse": get_app_setting("auto_last_parse", "") or None,
    }


@app.post("/api/admin/auto-daily")
async def admin_auto_daily_set(data: dict, user: dict = Depends(require_admin)):
    """Сохранить настройки авто-режима (вкл/выкл, время, ранги, лиги, лимит)."""
    data = data or {}
    if "enabled" in data:
        enabled = bool(data.get("enabled"))
        set_app_setting("auto_daily", "1" if enabled else "0")
    else:
        enabled = get_app_setting("auto_daily", "0") == "1"

    if data.get("clear_time"):
        ct = str(data["clear_time"]).strip()
        if re.match(r"^\d{1,2}:\d{2}$", ct):
            set_app_setting("auto_clear_time", ct if len(ct) == 5 else f"0{ct}" if len(ct) == 4 else ct)
    if data.get("parse_time"):
        pt = str(data["parse_time"]).strip()
        if re.match(r"^\d{1,2}:\d{2}$", pt):
            set_app_setting("auto_parse_time", pt if len(pt) == 5 else f"0{pt}" if len(pt) == 4 else pt)

    if "ranks" in data:
        ranks_in = data.get("ranks") or []
        if isinstance(ranks_in, str):
            ranks_in = [x.strip() for x in ranks_in.split(",") if x.strip()]
        ranks_clean = sorted({int(x) for x in ranks_in if str(x).isdigit() and 1 <= int(x) <= 99})
        if ranks_clean:
            set_app_setting("auto_ranks", ",".join(str(x) for x in ranks_clean))

    if "key_leagues" in data:
        kl = data.get("key_leagues") or []
        if isinstance(kl, str):
            kl = [x.strip() for x in kl.split(",") if x.strip()]
        set_app_setting("auto_key_leagues", json.dumps([str(x) for x in kl if str(x).strip()], ensure_ascii=False))

    if "max_matches" in data:
        try:
            mx = max(1, min(300, int(data.get("max_matches") or 80)))
            set_app_setting("auto_max_matches", str(mx))
        except Exception:
            pass

    log_admin(user["id"], "auto_daily", f"enabled={enabled}")
    return {
        "ok": True,
        "enabled": enabled,
        "clear_time": get_app_setting("auto_clear_time", "06:00"),
        "parse_time": get_app_setting("auto_parse_time", "08:00"),
        "ranks": _auto_get_ranks(),
        "key_leagues": _auto_get_key_leagues(),
        "max_matches": _auto_get_max_matches(),
        "message": "✅ Авто-режим включён" if enabled else "⏸ Авто-режим выключен",
    }


@app.get("/api/admin/maintenance")
async def admin_maintenance_get(user: dict = Depends(require_admin)):
    on = get_app_setting("maintenance", "0") == "1"
    return {"ok": True, "enabled": on}


@app.post("/api/admin/maintenance")
async def admin_maintenance_set(data: dict, user: dict = Depends(require_admin)):
    enabled = bool((data or {}).get("enabled"))
    set_app_setting("maintenance", "1" if enabled else "0")
    log_admin(user["id"], "maintenance", "ON" if enabled else "OFF")
    return {
        "ok": True,
        "enabled": enabled,
        "message": "🔧 Техработы включены" if enabled else "✅ Техработы выключены",
    }

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
    threads = []
    total_unread = 0
    for r in rows:
        tid = r[0]
        try:
            ur = db_fetchone(
                "SELECT COUNT(*) FROM support_messages WHERE thread_id = ? AND sender = 'user' AND COALESCE(admin_read, 0) = 0",
                (tid,),
            )
            unread = int(ur[0]) if ur else 0
        except Exception:
            unread = 0
        total_unread += unread
        threads.append({
            "thread_id": tid, "user_name": r[1],
            "category": r[2], "category_label": SUPPORT_CATEGORIES.get(r[2], "❓"),
            "created_at": r[3] or "",
            "unread": unread,
        })
    return {"threads": threads, "unread_total": total_unread}

@app.get("/api/admin/support/{thread_id}")
async def admin_support_thread(thread_id: int, user: dict = Depends(require_admin)):
    # пометить сообщения пользователя как прочитанные
    try:
        db_execute(
            "UPDATE support_messages SET admin_read = 1 WHERE thread_id = ? AND sender = 'user'",
            (thread_id,),
        )
    except Exception:
        pass
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
        "INSERT INTO support_messages (thread_id, sender, text, created_at, admin_read, user_read) VALUES (?, 'admin', ?, ?, 1, 0)",
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



def is_user_banned(user_id: int) -> tuple:
    """Возвращает (banned: bool, label: str). Авто-снимает истёкший бан."""
    try:
        row = db_fetchone(
            "SELECT banned_until, COALESCE(ban_label,'') FROM users WHERE user_id = ?",
            (user_id,),
        )
    except Exception:
        return False, ""
    if not row or not row[0]:
        return False, ""
    until = str(row[0]).strip()
    label = str(row[1] or "").strip()
    if until in ("forever", "permanent", "1"):
        return True, label or "навсегда"
    try:
        # ISO or YYYY-MM-DD HH:MM
        dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        if datetime.now(tz) < dt:
            return True, label or until
        # expired
        db_execute(
            "UPDATE users SET banned_until = NULL, ban_label = '' WHERE user_id = ?",
            (user_id,),
        )
    except Exception:
        return True, label or until
    return False, ""


def get_app_setting(key: str, default: str = "") -> str:
    try:
        row = db_fetchone("SELECT value FROM app_settings WHERE key = ?", (key,))
        if row and row[0] is not None:
            return str(row[0])
    except Exception:
        pass
    return default


def set_app_setting(key: str, value: str) -> None:
    db_execute(
        "INSERT INTO app_settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )



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
    return out  # полный список отсутствующих, без обрезания


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


def _norm_p(v):
    """Normalize probability to 0..1 float or None."""
    if v is None:
        return None
    try:
        p = float(v)
    except (TypeError, ValueError):
        return None
    if p > 1.5:
        p = p / 100.0
    if p < 0 or p > 1:
        return None
    return p


def _coef_from_prob(p: float, lo=1.60, hi=2.50) -> Optional[float]:
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
    "home_dc": "1X (хозяева не проиграют)",
    "away_dc": "X2 (гости не проиграют)",
    "over_15": "ТБ 1.5",
    "under_15": "ТМ 1.5",
    "over_25": "ТБ 2.5",
    "under_25": "ТМ 2.5",
    "over_35": "ТБ 3.5",
    "under_35": "ТМ 3.5",
    "btts_yes": "Обе забьют — да",
    "btts_no": "Обе забьют — нет",
    "ht_goal": "Гол в 1-м тайме",
    "home_over_15": "ИТБ 1.5 (хозяева)",
    "away_over_15": "ИТБ 1.5 (гости)",
    "home_clean": "П1 сухая",
    "away_clean": "П2 сухая",
}


def _extract_final_third(bag: dict) -> Optional[float]:
    """
    Активность в чужой трети, %.
    BSD не отдаёт pure possession_final_third — берём:
      1) dangerous_attack_pct / attack_pct (live + stats)
      2) dangerous_attacks / attacks * 100
      3) прокси от владения + ударов (если атак нет)
    """
    if not isinstance(bag, dict):
        return None

    def _num(v):
        if isinstance(v, dict):
            v = v.get("average") or v.get("avg") or v.get("value") or v.get("pct") or v.get("total")
        return _safe_float(v)

    # 1) прямые % от BSD
    for k in (
        "dangerous_attack_pct", "dangerous_attacks_pct", "danger_attack_pct",
        "possession_final_third", "final_third_possession", "ball_possession_final_third",
        "attacking_third_possession", "final_third_pct", "opp_third_possession",
        "dangerous_possession",
    ):
        fv = _num(bag.get(k))
        if fv is not None:
            if fv <= 1.5:
                fv *= 100.0
            return round(min(100.0, max(0.0, fv)), 1)

    # 2) отношение опасных атак к обычным (реальная метрика BSD)
    da = _num(bag.get("dangerous_attacks") or bag.get("danger_attacks") or bag.get("dangerous_attack"))
    at = _num(bag.get("attacks") or bag.get("total_attacks") or bag.get("attack"))
    if da is not None and at is not None and at > 0:
        return round(min(100.0, max(0.0, da / at * 100.0)), 1)
    # иногда attack_pct рядом
    ap = _num(bag.get("attack_pct") or bag.get("attacks_pct"))
    if da is not None and ap is not None and ap > 0:
        # da за матч при attack_pct — грубая нормализация не нужна, лучше da как интенсивность
        pass
    if da is not None:
        # типичный диапазон dangerous attacks за матч ~20–70 → в %
        return round(min(100.0, max(0.0, da / 55.0 * 100.0)), 1)

    # 3) прокси только если нет атак: владение × точность ударов
    pos = _num(bag.get("possession") or bag.get("ball_possession"))
    sot = _num(bag.get("shots_on") or bag.get("shots_on_target"))
    shots = _num(bag.get("shots") or bag.get("total_shots") or bag.get("shots_total"))
    if pos is not None and sot is not None and shots is not None and shots > 0:
        accuracy = min(1.0, sot / shots)
        est = pos * (0.35 + 0.45 * accuracy)
        return round(min(100.0, max(0.0, est)), 1)
    if pos is not None and sot is not None:
        est = pos * min(1.0, sot / 5.5) * 0.85
        return round(min(100.0, max(0.0, est)), 1)
    return None


def _potential_power(form_side: dict, match_side: dict = None) -> int:
    """Рейтинг потенциала 0–100 (как в UI блока Потенциал)."""
    f = form_side if isinstance(form_side, dict) else {}
    m = match_side if isinstance(match_side, dict) else {}
    parts = []
    xg = _safe_float(f.get("xg_avg"))
    xga = _safe_float(f.get("xga_avg"))
    pos = _safe_float(f.get("possession"))
    sot = _safe_float(m.get("shots_on") if m.get("shots_on") is not None else f.get("shots_on"))
    gpg = _safe_float(f.get("gpg"))
    gapg = _safe_float(f.get("gapg"))
    ft = _safe_float(f.get("possession_final_third"))
    if ft is None:
        ft = _extract_final_third(f) or _extract_final_third(m)
    if xg is not None:
        parts.append(min(100.0, xg / 2.2 * 100))
    if xga is not None:
        parts.append(min(100.0, max(0.0, (2.2 - xga) / 2.2 * 100)))
    if pos is not None:
        parts.append(min(100.0, pos / 60.0 * 100))
    if ft is not None:
        parts.append(min(100.0, ft / 40.0 * 100))
    if sot is not None:
        parts.append(min(100.0, sot / 6.0 * 100))
    if gpg is not None:
        parts.append(min(100.0, gpg / 2.5 * 100))
    if gapg is not None:
        parts.append(min(100.0, max(0.0, (2.5 - gapg) / 2.5 * 100)))
    if not parts:
        if gpg is not None or gapg is not None:
            g = gpg or 1.0
            ga = gapg or 1.0
            return int(round(min(100, max(20, (g / max(ga, 0.3)) * 40 + g * 20))))
        return 50
    return int(round(sum(parts) / len(parts)))


def _detect_league_context(league_name: str = "") -> dict:
    """
    tier: top | second | lower | cup
    region: europe | conmebol | afc | concacaf | generic
    Подбирает тексты зон под реальные турниры региона (не везде есть ЛЧ УЕФА).
    """
    ln = (league_name or "").lower().strip()
    if not ln:
        return {"tier": "second", "region": "generic"}

    # Кубки (осторожно: «copa» есть и в названиях лиг ЮА — проверяем кубковые маркеры)
    cup_markers = (
        "fa cup", "efl cup", "league cup", "carabao", "pokal", "coupe de",
        "coppa italia", "taça de", "super cup", "supercup", "playoff", "play-off",
        "плей-офф", "кубок россии", "кубок ", " cup",
    )
    if any(k in ln for k in cup_markers) and not any(
        k in ln for k in ("serie a", "serie b", "brasileirão", "brasileirao", "primera división", "primera division")
    ):
        # «Copa Libertadores» / «Copa Sudamericana» — континентальные кубки
        if "libertadores" in ln or "sudamericana" in ln or "recopa" in ln:
            return {"tier": "cup", "region": "conmebol"}
        return {"tier": "cup", "region": "generic"}

    lower_markers = (
        "serie c", "serie d", "league two", "league 2", "ligue 3", "3. liga", "3 liga",
        "national league", "regionalliga", "segunda federacion", "tercera",
        "j3", "третья лига", "вторая лига", "liga 3", "division 2", "division 3",
        "non-league", "amateur", "reserve", "série c", "serie c",
    )
    second_markers = (
        "championship", "serie b", "série b", "2. bundesliga", "2 bundesliga", "bundesliga 2",
        "ligue 2", "segunda división", "segunda division", "segunda liga",
        "efl championship", "liga 2", "liga ii", "2. liga", "2 liga",
        "challenger pro", "eerste divisie", "keuken kampioen",
        "j2 league", "j.league division 2", "фнл", "первая лига",
        "league one", "efl league one", "1. division", "division 1",
        "segunda", "primera b", "primera nacional", "série b",
    )

    # --- регионы топ-лиг ---
    europe_top = (
        "premier league", "la liga", "laliga ea", "laliga", "serie a",
        "bundesliga", "ligue 1", "eredivisie", "primeira liga",
        "super lig", "süper lig", "scottish premiership",
        "belgian pro league", "jupiler", "superliga", "allsvenskan",
        "eliteserien", "russian premier", "рпл", "премьер-лига",
        "ekstraklasa", "fortuna liga", "austrian bundesliga",
        "swiss super", "cypriot", "greek super", "ukrainian premier",
    )
    conmebol_top = (
        "brasileirão", "brasileirao", "serie a brazil", "série a",
        "brazilian serie a", "campeonato brasileiro",
        "liga profesional", "liga profesional de fútbol", "liga profesional de futbol",
        "primera división", "primera division", "primera divisi",
        "liga 1 peru",
        "categoría primera", "categoria primera", "liga betplay",
        "primera a", "chilean primera", "uruguayan primera",
        "paraguayan primera", "bolivian", "ecuafútbol", "liga pro ecuador",
        "venezuelan primera",
    )
    # Brazil often just "Serie A" — ambiguous with Italy; prefer country hints
    conmebol_hints = (
        "brazil", "brasil", "argentina", "chile", "uruguay", "paraguay",
        "colombia", "peru", "ecuador", "bolivia", "venezuela",
        "brasileir", "libertadores", "sudamericana",
    )
    afc_top = (
        "j1 league", "j.league", "k league", "k-league", "chinese super",
        "saudi pro league", "uae pro", "qatar stars", "a-league",
        "indian super", "thai league", "persian gulf",
    )
    concacaf_top = (
        "major league soccer", "mls", "liga mx", "liga expansion",
        "canadian premier",
    )

    if any(k in ln for k in lower_markers):
        return {"tier": "lower", "region": "generic"}
    if any(k in ln for k in second_markers):
        return {"tier": "second", "region": "generic"}
    if any(x in ln for x in (" 2 ", " 2.", " ii", "2nd", "second division")):
        if "bundesliga" in ln and "2" in ln:
            return {"tier": "second", "region": "europe"}
        return {"tier": "second", "region": "generic"}

    if any(k in ln for k in europe_top):
        if "2." in ln and "bundesliga" in ln:
            return {"tier": "second", "region": "europe"}
        return {"tier": "top", "region": "europe"}
    if any(k in ln for k in conmebol_top) or any(k in ln for k in conmebol_hints):
        # Serie A без brazil/brasil может быть Италия — если только "serie a"
        if ln.strip() in ("serie a", "série a") and not any(k in ln for k in conmebol_hints):
            return {"tier": "top", "region": "europe"}
        return {"tier": "top", "region": "conmebol"}
    if any(k in ln for k in afc_top):
        return {"tier": "top", "region": "afc"}
    if any(k in ln for k in concacaf_top):
        return {"tier": "top", "region": "concacaf"}

    # неизвестная — без ЛЧ УЕФА
    return {"tier": "second", "region": "generic"}


def _zone_labels(tier: str, region: str) -> dict:
    """
    Тексты зон: учитывают уровень лиги (высшая / 2-я / 3+) и регион
    (Европа — ЛЧ УЕФА; ЮА — Либертадорес; Азия — ЛЧ АФК; и т.д.).
    """
    # --- 2-й дивизион (Championship, Serie B, Ligue 2…) ---
    if tier == "second":
        return {
            "title": "борьба за чемпионство и прямое повышение",
            "top": "зона прямого повышения в высший дивизион",
            "upper": "зона плей-офф за повышение",
            "near_top": "зоны прямого повышения",
            "near_upper": "плей-офф за повышение",
            "rel": "зона вылета в третий дивизион",
            "tier_note": "второй дивизион",
        }
    # --- 3+ дивизион ---
    if tier == "lower":
        return {
            "title": "борьба за чемпионство и выход выше",
            "top": "зона повышения дивизионом выше",
            "upper": "зона плей-офф за повышение",
            "near_top": "зоны повышения",
            "near_upper": "плей-офф за повышение",
            "rel": "зона вылета ещё ниже",
            "tier_note": "третий и ниже дивизион",
        }
    # --- высший дивизион по регионам ---
    if region == "europe":
        return {
            "title": "гонка за чемпионство",
            "top": "зона Лиги чемпионов УЕФА",
            "upper": "зона Лиги Европы / конференции УЕФА",
            "near_top": "зоны Лиги чемпионов",
            "near_upper": "зоны еврокубков",
            "rel": "зона вылета из высшего дивизиона",
            "tier_note": "высший дивизион",
        }
    if region == "conmebol":
        return {
            "title": "гонка за чемпионство",
            "top": "зона Кубка Либертадорес",
            "upper": "зона Южноамериканского кубка (Судамерикана)",
            "near_top": "зоны Либертадорес",
            "near_upper": "зоны Судамериканы",
            "rel": "зона вылета из высшего дивизиона",
            "tier_note": "высший дивизион",
        }
    if region == "afc":
        return {
            "title": "гонка за чемпионство",
            "top": "зона Лиги чемпионов АФК",
            "upper": "зона азиатских клубных кубков",
            "near_top": "зоны ЛЧ АФК",
            "near_upper": "азиатских кубков",
            "rel": "зона вылета из высшего дивизиона",
            "tier_note": "высший дивизион",
        }
    if region == "concacaf":
        return {
            "title": "гонка за чемпионство и плей-офф",
            "top": "зона плей-офф / Лиги чемпионов КОНКАКАФ",
            "upper": "зона плей-офф сезона",
            "near_top": "зоны плей-офф",
            "near_upper": "плей-офф",
            "rel": "зона вне плей-офф",
            "tier_note": "высший дивизион",
        }
    # неизвестный регион, но высшая лига
    return {
        "title": "гонка за чемпионство",
        "top": "верхняя часть таблицы (топ)",
        "upper": "верхняя половина таблицы",
        "near_top": "топа таблицы",
        "near_upper": "верхней части таблицы",
        "rel": "зона вылета",
        "tier_note": "высший дивизион",
    }


def _league_zones(n: int, league_name: str = "") -> dict:
    """Зоны турнирной таблицы: tier + region + границы мест."""
    ctx = _detect_league_context(league_name)
    tier = ctx["tier"]
    region = ctx["region"]

    if tier == "cup":
        return {"type": "cup", "tier": "cup", "region": region, "top": 0, "upper": 0, "rel": 0}

    if n <= 0:
        n = 20

    if tier == "top":
        if region == "conmebol":
            # Бразилия Serie A ~20: Либертадорес ~1–6, Судамерикана ~7–12, вылет 17–20
            if n >= 18:
                return {"type": "league", "tier": "top", "region": region, "top": 6, "upper": 12, "rel": 4}
            if n >= 14:
                return {"type": "league", "tier": "top", "region": region, "top": 4, "upper": 8, "rel": 3}
            return {"type": "league", "tier": "top", "region": region, "top": 3, "upper": 6, "rel": 2}
        if region == "concacaf":
            # MLS — плей-офф широкая зона
            if n >= 24:
                return {"type": "league", "tier": "top", "region": region, "top": 3, "upper": 9, "rel": 0}
            return {"type": "league", "tier": "top", "region": region, "top": 2, "upper": 8, "rel": 0}
        if region == "afc":
            if n >= 16:
                return {"type": "league", "tier": "top", "region": region, "top": 3, "upper": 6, "rel": 3}
            return {"type": "league", "tier": "top", "region": region, "top": 2, "upper": 4, "rel": 2}
        # europe
        if n >= 18:
            return {"type": "league", "tier": "top", "region": region, "top": 4, "upper": 6, "rel": 3}
        if n >= 16:
            return {"type": "league", "tier": "top", "region": region, "top": 3, "upper": 5, "rel": 3}
        if n >= 12:
            return {"type": "league", "tier": "top", "region": region, "top": 2, "upper": 4, "rel": 2}
        return {"type": "league", "tier": "top", "region": region, "top": 1, "upper": 2, "rel": 2}

    if tier == "lower":
        if n >= 20:
            return {"type": "league", "tier": "lower", "region": region, "top": 3, "upper": 7, "rel": 4}
        if n >= 16:
            return {"type": "league", "tier": "lower", "region": region, "top": 2, "upper": 6, "rel": 3}
        return {"type": "league", "tier": "lower", "region": region, "top": 2, "upper": 4, "rel": 2}

    # second
    if n >= 16:
        return {"type": "league", "tier": "second", "region": region, "top": 2, "upper": 6, "rel": 3}
    if n >= 12:
        return {"type": "league", "tier": "second", "region": region, "top": 2, "upper": 4, "rel": 2}
    return {"type": "league", "tier": "second", "region": region, "top": 1, "upper": 3, "rel": 2}


def _motivation_level(pos: int, n: int, pts: int, table: list, zones: dict) -> tuple:
    """
    Возвращает (level_key, label, detail).
    detail — развёрнутый текст с местом, очками и корректными зонами
    (регион + уровень дивизиона: высший / второй / третий+).
    """
    if zones.get("type") == "cup" or zones.get("tier") == "cup":
        return (
            "high",
            "Высокая",
            "Кубковый турнир: один матч может решить выход дальше — мотивация высокая у обеих сторон.",
        )
    if not pos or n <= 0:
        return "mid", "Средняя", "По турнирной таблице данных недостаточно — оцениваем нейтрально."

    tier = zones.get("tier") or "second"
    region = zones.get("region") or "generic"
    top_n = int(zones.get("top") or 2)
    upper_n = int(zones.get("upper") or 4)
    rel = int(zones.get("rel") or 0)

    labels = _zone_labels(tier, region)
    z_title = labels["title"]
    z_top = labels["top"]
    z_upper = labels["upper"]
    z_near_top = labels["near_top"]
    z_near_upper = labels["near_upper"]
    z_rel = labels["rel"]
    tier_note = labels.get("tier_note") or ""

    def _pts_at(p):
        for r in table:
            try:
                if int(r.get("pos") or 0) == p:
                    return int(r.get("pts") or 0)
            except (TypeError, ValueError):
                continue
        return None

    def _place(p):
        return f"{p}-е место из {n}" if n else f"{p}-е место"

    leader_pts = _pts_at(1) or pts
    top_pts = _pts_at(top_n) if top_n else None
    upper_pts = _pts_at(upper_n) if upper_n else None
    rel_start = (n - rel + 1) if rel else (n + 1)
    safe_pts = _pts_at(rel_start - 1) if rel and rel_start > 1 else None
    rel_pts = _pts_at(rel_start) if rel else None
    second_pts = _pts_at(2)

    # —— Лидер ——
    if pos == 1:
        gap = (pts - (second_pts if second_pts is not None else pts))
        if gap >= 8:
            return (
                "low",
                "Низкая",
                f"{_place(1)}, отрыв от 2-го места {gap} очк. — титул почти в кармане, "
                f"турнирной остроты мало ({tier_note}).",
            )
        return (
            "max",
            "Максимальная",
            f"{_place(1)}, отрыв от 2-го всего {gap} очк. — идёт {z_title} ({tier_note}).",
        )

    # —— Верхняя зона (ЛЧ / повышение / топ) ——
    if top_n and pos <= top_n:
        gap_to_1 = (leader_pts - pts) if leader_pts is not None else 0
        if gap_to_1 <= 6:
            return (
                "max",
                "Максимальная",
                f"{_place(pos)}, до лидера {gap_to_1} очк. — реальная {z_title} ({tier_note}).",
            )
        return (
            "high",
            "Высокая",
            f"{_place(pos)} — {z_top}. До лидера {gap_to_1} очк.; каждый матч важен ({tier_note}).",
        )

    # —— Второй эшелон сверху (евро / плей-офф) ——
    if upper_n and pos <= upper_n:
        gap = (top_pts - pts) if top_pts is not None else None
        if gap is not None and 0 <= gap <= 5:
            return (
                "high",
                "Высокая",
                f"{_place(pos)} — {z_upper}; до {z_near_top} не хватает {gap} очк. "
                f"Мотивация держать темп высокая ({tier_note}).",
            )
        return (
            "high",
            "Высокая",
            f"{_place(pos)} — {z_upper}. Матч влияет на итоговую позицию в зоне ({tier_note}).",
        )

    # —— Зона вылета ——
    if rel and pos >= rel_start:
        return (
            "max",
            "Максимальная",
            f"{_place(pos)} — {z_rel}. Очки критичны: поражение усугубляет положение ({tier_note}).",
        )

    # —— Чуть над вылетом ——
    if rel and safe_pts is not None and pos < rel_start:
        gap = pts - (rel_pts if rel_pts is not None else safe_pts)
        if 0 <= gap <= 4:
            return (
                "high",
                "Высокая",
                f"{_place(pos)}, всего {gap} очк. над {z_rel} — нельзя терять очки ({tier_note}).",
            )

    # —— Чуть под верхней зоной ——
    if upper_n and upper_pts is not None and pos > upper_n:
        gap = upper_pts - pts
        if 0 < gap <= 5:
            return (
                "high",
                "Высокая",
                f"{_place(pos)}, {gap} очк. до {z_near_upper} — есть шанс подняться выше ({tier_note}).",
            )

    # —— Середина / низ без угроз ——
    if pos <= max(n // 2, upper_n + 1):
        return (
            "mid",
            "Средняя",
            f"{_place(pos)} — середина таблицы: до топа далеко, до вылета запас. "
            f"Мотивация умеренная ({tier_note}).",
        )
    return (
        "low",
        "Низкая",
        f"{_place(pos)} — нижняя часть без прямой угрозы вылета и без борьбы за верх. "
        f"Турнирной мотивации мало ({tier_note}).",
    )


def _compute_motivation(pack: dict) -> dict:
    """
    Мотивация обеих команд по турнирной таблице и типу лиги.
    Возвращает {home: {...}, away: {...}, note: str, available: bool}.
    """
    standings = pack.get("standings") or {}
    table = standings.get("table") or []
    context = pack.get("context") or {}
    is_derby = bool(context.get("is_derby"))

    def _tname(v, fallback):
        if isinstance(v, dict):
            return (v.get("name") or v.get("short_name") or fallback).strip()
        return (str(v or "").strip() or fallback)

    home_name = _tname(pack.get("home_team"), "Хозяева")
    away_name = _tname(pack.get("away_team"), "Гости")
    league_name = (
        pack.get("league")
        or (pack.get("event") or {}).get("league_name")
        or (pack.get("event") or {}).get("league")
        or ""
    )
    if isinstance(league_name, dict):
        league_name = league_name.get("name") or ""

    empty = {
        "level": "mid",
        "label": "Средняя",
        "detail": "Нет данных таблицы",
        "pos": None,
        "pts": None,
    }
    if not table:
        note = "Мотивация по таблице недоступна — нет данных стендингов."
        if is_derby:
            note = "Дерби — мотивация высокая независимо от места."
        return {
            "home": {**empty, "name": home_name},
            "away": {**empty, "name": away_name},
            "note": note,
            "available": False,
            "is_derby": is_derby,
        }

    n = len(table)
    zones = _league_zones(n, str(league_name))

    def _find_row(name: str, side_highlight: bool = False):
        name_l = (name or "").lower().strip()
        # 1) highlight rows from parser
        for r in table:
            if r.get("highlight") and name_l and name_l[:4] in (r.get("team") or "").lower():
                return r
        for r in table:
            t = (r.get("team") or "").lower()
            if name_l and (name_l in t or t in name_l or name_l[:5] in t):
                return r
        return None

    def _side(name: str):
        row = _find_row(name)
        if not row:
            return {**empty, "name": name}
        try:
            pos = int(row.get("pos") or 0) or None
        except (TypeError, ValueError):
            pos = None
        try:
            pts = int(row.get("pts") or 0)
        except (TypeError, ValueError):
            pts = 0
        level, label, detail = _motivation_level(pos or 0, n, pts, table, zones)
        if is_derby and level in ("mid", "low"):
            level, label = "high", "Высокая"
            detail = f"Дерби · {detail}"
        return {
            "name": name,
            "level": level,
            "label": label,
            "detail": detail,
            "pos": pos,
            "pts": pts,
            "played": row.get("played"),
        }

    home = _side(home_name)
    away = _side(away_name)

    # Короткий note ≤ 5–6 строк смысла — одна фраза
    notes = []
    if is_derby:
        notes.append("Дерби повышает мотивацию обеих сторон.")
    if zones.get("type") == "cup":
        notes.append("Кубковая стадия — результат важнее места в таблице.")
    else:
        for side in (home, away):
            if side.get("level") in ("max", "high") and side.get("detail"):
                notes.append(f"{side['name']}: {side['detail']}.")
    if not notes:
        notes.append("Ориентир по турнирной мотивации на основе текущей таблицы.")
    note = " ".join(notes[:2])

    return {
        "home": home,
        "away": away,
        "note": note,
        "available": True,
        "is_derby": is_derby,
        "teams_in_league": n,
        "zones": zones,
    }


def _class_diff_info(diff: float, stronger: str) -> tuple:
    """Оценка разницы потенциала: (текст, число звёзд 0–5).
    ≤7   Соперники равны
    8–15  чуть сильнее           ★
    16–22 сильнее                ★★
    23–28 на много сильнее       ★★★
    29–33 выше классом           ★★★★
    ≥34   гораздо выше классом   ★★★★★
    """
    d = abs(float(diff or 0))
    if d <= 7:
        return "Соперники равны", 0
    if d <= 15:
        return f"{stronger} чуть сильнее", 1
    if d <= 22:
        return f"{stronger} сильнее", 2
    if d <= 28:
        return f"{stronger} на много сильнее", 3
    if d <= 33:
        return f"{stronger} выше классом", 4
    return f"{stronger} гораздо выше классом", 5


def _class_diff_text(diff: float, stronger: str) -> str:
    return _class_diff_info(diff, stronger)[0]


def _build_forecast_variants(pack: dict, odds_raw=None, pred_raw=None) -> dict:
    """
    Прогноз по схеме:
    — потенциал дом/выезд → финальный рейтинг (+бонус разницы общей);
    — тренды ТБ/ОЗ + матч (голы) + таймы + травмы;
    — 2 ординара + 2 комбо, кф 1.60–2.50, % и пояснение 3–5 строк.
    """
    COEF_MIN, COEF_MAX = 1.60, 2.50
    MIN_PROB = 0.60
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
    # дом хозяев / выезд гостей — контекст площадки
    form_h_home = form_h.get("home") if isinstance(form_h.get("home"), dict) else {}
    form_a_away = form_a.get("away") if isinstance(form_a.get("away"), dict) else {}
    if not form_h_home:
        form_h_home = form_h
    if not form_a_away:
        form_a_away = form_a

    ms_h = (pack.get("match_stats") or {}).get("home") or {}
    ms_a = (pack.get("match_stats") or {}).get("away") or {}
    bet_h = (pack.get("betting") or {}).get("home") or {}
    bet_a = (pack.get("betting") or {}).get("away") or {}
    halves_h = (pack.get("halves") or {}).get("home") or {}
    halves_a = (pack.get("halves") or {}).get("away") or {}
    inj_h = (pack.get("injuries") or {}).get("home") or []
    inj_a = (pack.get("injuries") or {}).get("away") or []

    def_h_n, def_h_names = _count_missing_defenders(inj_h)
    def_a_n, def_a_names = _count_missing_defenders(inj_a)
    att_h_n, att_h_names = _count_missing_attackers(inj_h)
    att_a_n, att_a_names = _count_missing_attackers(inj_a)

    # --- 1. Рейтинг потенциала: ТОЛЬКО дом хозяев / выезд гостей (без прибавки общего) ---
    base_h = _potential_power(form_h_home, form_h_home if form_h_home.get("shots_on") is not None else ms_h)
    base_a = _potential_power(form_a_away, form_a_away if form_a_away.get("shots_on") is not None else ms_a)
    overall_h = _potential_power(form_h, ms_h)  # только для справки в signals, не в итоговый
    overall_a = _potential_power(form_a, ms_a)
    final_h = base_h
    final_a = base_a
    pot_diff = abs(final_h - final_a)
    if final_h > final_a:
        stronger = home_name
        stronger_side = "home"
    elif final_a > final_h:
        stronger = away_name
        stronger_side = "away"
    else:
        stronger = home_name
        stronger_side = "home"
    class_txt, class_stars = _class_diff_info(pot_diff, stronger)

    # --- 2. Тренды % ---
    def _pct(bag, *keys):
        for k in keys:
            v = bag.get(k) if isinstance(bag, dict) else None
            if v is None:
                continue
            try:
                f = float(v)
                if f <= 1.5:
                    f *= 100.0
                return f
            except (TypeError, ValueError):
                pass
        return None

    o25_h = _pct(bet_h, "over_25_pct", "over_25")
    o25_a = _pct(bet_a, "over_25_pct", "over_25")
    o15_h = _pct(bet_h, "over_15_pct", "over_15")
    o15_a = _pct(bet_a, "over_15_pct", "over_15")
    btts_h = _pct(bet_h, "btts_pct", "btts")
    btts_a = _pct(bet_a, "btts_pct", "btts")

    gpg_h = _safe_float(form_h_home.get("gpg")) or _safe_float(form_h.get("gpg")) or _safe_float(ms_h.get("goals"))
    gpg_a = _safe_float(form_a_away.get("gpg")) or _safe_float(form_a.get("gpg")) or _safe_float(ms_a.get("goals"))
    gapg_h = _safe_float(form_h_home.get("gapg")) or _safe_float(form_h.get("gapg"))
    gapg_a = _safe_float(form_a_away.get("gapg")) or _safe_float(form_a.get("gapg"))
    goals_sum = (gpg_h or 0) + (gpg_a or 0)

    fh_goals_h = _safe_float((halves_h.get("first_half") or {}).get("goals"))
    fh_goals_a = _safe_float((halves_a.get("first_half") or {}).get("goals"))
    # % гола в 1Т из трендов (ht_over_05)
    ht_h = bet_h.get("ht") if isinstance(bet_h.get("ht"), dict) else {}
    ht_a = bet_a.get("ht") if isinstance(bet_a.get("ht"), dict) else {}
    ht05_h = _pct(ht_h, "ht_over_05_pct", "ht_over_05", "over_05_pct")
    ht05_a = _pct(ht_a, "ht_over_05_pct", "ht_over_05", "over_05_pct")

    # --- травмы: только количество нападающих/защитников, без имён ---
    _ABSENCE_VERBS = ("отсутствуют", "не сыграют", "пропускают", "не выйдут на поле")
    _inj_phrase_i = [0]

    def _absence_verb() -> str:
        v = _ABSENCE_VERBS[_inj_phrase_i[0] % len(_ABSENCE_VERBS)]
        _inj_phrase_i[0] += 1
        return v

    def _role_word(kind: str, n: int) -> str:
        if kind == "att":
            return "нападающий" if n == 1 else "нападающих"
        return "защитник" if n == 1 else ("защитника" if 2 <= n <= 4 else "защитников")

    def _inj_line(team: str, n: int, kind: str, tail: str) -> str:
        verb = _absence_verb()
        role = _role_word(kind, n)
        return f"{team}: {verb} {n} {role} — {tail}"

    inj_notes = []
    att_tail = "в связи с этим команда может забить меньше ожидаемого количества мячей."
    def_tail = "в связи с этим команда может пропустить больше мячей, чем обычно."
    if att_h_n >= 2:
        inj_notes.append(_inj_line(home_name, att_h_n, "att", att_tail))
    if att_a_n >= 2:
        inj_notes.append(_inj_line(away_name, att_a_n, "att", att_tail))
    if def_h_n >= 2:
        inj_notes.append(_inj_line(home_name, def_h_n, "def", def_tail))
    if def_a_n >= 2:
        inj_notes.append(_inj_line(away_name, def_a_n, "def", def_tail))

    # --- кандидаты ---
    candidates = []

    def add_cand(key, score, why_lines, est_prob=None):
        p = est_prob
        if p is not None:
            try:
                p = float(p)
                if p > 1.5:
                    p = p / 100.0
            except (TypeError, ValueError):
                p = None
        coef = odds.get(key)
        estimated = False
        try:
            coef_f = float(coef) if coef is not None else None
        except (TypeError, ValueError):
            coef_f = None
        if coef_f is not None and not (COEF_MIN <= coef_f <= COEF_MAX):
            coef_f = None
        if coef_f is None and p is not None:
            coef_f = _coef_from_prob(p, COEF_MIN, COEF_MAX)
            estimated = True
        if coef_f is None and p is not None:
            raw = 1.0 / max(min(p, 0.9), 0.35) * 0.95
            coef_f = max(COEF_MIN, min(COEF_MAX, round(raw, 2)))
            estimated = True
        if coef_f is None:
            coef_f = 1.85
            estimated = True
            if p is None:
                p = 0.62
        if p is None:
            try:
                p = min(0.88, max(0.40, 1.0 / float(coef_f) * 0.95))
            except ZeroDivisionError:
                p = 0.60
        if p < MIN_PROB:
            p = MIN_PROB
        why = [w for w in (why_lines or []) if w]
        candidates.append({
            "key": key,
            "label": MARKET_LABELS.get(key, key),
            "coef": round(float(coef_f), 2),
            "score": score,
            "why": why[:5],
            "estimated": estimated,
            "est_prob": p,
        })

    pot_line = (
        f"Потенциал (дом/выезд): {home_name} {final_h}/100, {away_name} {final_a}/100 "
        f"(разница {pot_diff:.0f} — {class_txt})."
    )

    # === ТБ 2.5: обе >75% по трендам И сумма голов в блоке Матч > 2.5 ===
    signal_o25 = (
        o25_h is not None and o25_a is not None
        and o25_h > 75 and o25_a > 75
        and goals_sum > 2.5
    )
    if signal_o25:
        why = [
            pot_line,
            f"Тренды ТБ 2.5: {home_name} {o25_h:.0f}% / {away_name} {o25_a:.0f}% (оба >75%).",
            f"Блок Матч: ср. голы {gpg_h or 0:.2f} + {gpg_a or 0:.2f} = {goals_sum:.2f} (>2.5).",
        ]
        add_cand("over_25", 92, why, est_prob=0.68 if att_h_n + att_a_n < 3 else 0.62)

    # === ТБ 1.5: обе ТБ1.5 ≥75% и голы ≥1 у каждой (второй фактор без изменений) ===
    signal_o15 = (
        not signal_o25
        and o15_h is not None and o15_a is not None
        and o15_h >= 75 and o15_a >= 75
        and (gpg_h or 0) >= 1.0 and (gpg_a or 0) >= 1.0
    )
    if signal_o15:
        why = [
            pot_line,
            f"ТБ 2.5 не дотягивает ( {o25_h or '—'}% / {o25_a or '—'}% ), смотрим ТБ 1.5.",
            f"Тренды ТБ 1.5: {home_name} {o15_h:.0f}% / {away_name} {o15_a:.0f}% (оба ≥75%).",
            f"Матч: обе забивают в среднем ≥1 ({gpg_h or 0:.2f} / {gpg_a or 0:.2f}).",
        ]
        add_cand("over_15", 88, why, est_prob=0.74)

    # === ТМ 3.5: обе ТБ 2.5 ≤ 57% ===
    signal_u35 = (
        o25_h is not None and o25_a is not None
        and o25_h <= 57 and o25_a <= 57
    )
    if signal_u35:
        why = [
            pot_line,
            f"Тренды ТБ 2.5: {home_name} {o25_h:.0f}% / {away_name} {o25_a:.0f}% (обе ≤57%).",
            "Низкий процент тотала больше 2.5 → вероятен ТМ 3.5 на матч.",
        ]
        add_cand("under_35", 80, why, est_prob=0.66)

    # === Гол в 1Т: одна комбинация ===
    # Тренды ТБ 0.5 (1 тайм) обе ≥73% + в «Таймы» у одной команды голы 1Т ≥1
    half_ok = (
        (fh_goals_h is not None and fh_goals_h >= 1)
        or (fh_goals_a is not None and fh_goals_a >= 1)
    )
    ht_pct_ok = (
        ht05_h is not None and ht05_a is not None
        and ht05_h >= 73 and ht05_a >= 73
    )
    signal_ht = bool(ht_pct_ok and half_ok)
    if signal_ht:
        why = [
            pot_line,
            f"Тренды ТБ 0.5 (1 тайм): {home_name} {ht05_h:.0f}% / {away_name} {ht05_a:.0f}% (обе ≥73%).",
            f"Блок «Таймы», 1-й тайм (голы): {home_name} {fh_goals_h if fh_goals_h is not None else '—'} / "
            f"{away_name} {fh_goals_a if fh_goals_a is not None else '—'} — у одной из команд ≥1.",
        ]
        add_cand("ht_goal", 95, why, est_prob=0.66)

    # === ОЗ: тренды обе ≥70% И в блоке Матч голы ≥1 у обеих ===
    signal_btts = (
        btts_h is not None and btts_a is not None
        and btts_h >= 70 and btts_a >= 70
        and (gpg_h or 0) >= 1.0 and (gpg_a or 0) >= 1.0
    )
    if signal_btts:
        why = [
            f"Критерий ОЗ: в «Трендах» обе забьют ≥70% — {home_name} {btts_h:.0f}% / {away_name} {btts_a:.0f}%.",
            f"Критерий «Матч»: ср. голы ≥1 у обеих — {gpg_h:.2f} / {gpg_a:.2f} (дом/выезд).",
            "Оба условия выполнены → публикуем «Обе забьют — да».",
        ]
        add_cand("btts_yes", 88, why, est_prob=0.66)

    # === ИТБ 1.5: тренд team_over_15 ≥60% И в блоке Матч голы команды ≥2.0 ===
    # Если подходят обе — берём команду с лучшими показателями
    itb_h = _pct(bet_h, "team_over_15_pct", "team_over_15", "indiv_over_15")
    itb_a = _pct(bet_a, "team_over_15_pct", "team_over_15", "indiv_over_15")
    home_itb_ok = itb_h is not None and itb_h >= 60 and (gpg_h or 0) >= 2.0
    away_itb_ok = itb_a is not None and itb_a >= 60 and (gpg_a or 0) >= 2.0
    signal_itb = False
    if home_itb_ok or away_itb_ok:
        if home_itb_ok and away_itb_ok:
            # лучше: выше % тренда, при равенстве — выше ср. голы
            score_h = (itb_h or 0) * 10 + (gpg_h or 0)
            score_a = (itb_a or 0) * 10 + (gpg_a or 0)
            pick_home = score_h >= score_a
        else:
            pick_home = home_itb_ok
        if pick_home:
            key_itb = "home_over_15"
            nm_itb, pct_itb, gpg_itb = home_name, itb_h, gpg_h
        else:
            key_itb = "away_over_15"
            nm_itb, pct_itb, gpg_itb = away_name, itb_a, gpg_a
        signal_itb = True
        why = [
            pot_line,
            f"Тренды ИТБ 1.5: {nm_itb} {pct_itb:.0f}% (≥60%) — команда забивает 2+ в большинстве матчей.",
            f"Блок «Матч»: ср. голы {nm_itb} {gpg_itb:.2f} (≥2.0).",
            "Оба условия выполнены → индивидуальный тотал больше 1.5.",
        ]
        add_cand(key_itb, 86, why, est_prob=min(0.78, 0.55 + (pct_itb or 60) / 100.0 * 0.25))

    # === Исход по классу потенциала ===
    if pot_diff > 20:
        key = "home_win" if stronger_side == "home" else "away_win"
        why = [
            pot_line,
            f"Разница потенциала дом/выезд >20 — {class_txt}, победа наиболее вероятна.",
            f"Рейтинг площадки: хозяева {final_h}/100, гости {final_a}/100.",
        ]
        add_cand(key, 90, why, est_prob=0.66)
    elif pot_diff > 15:
        key = "home_win" if stronger_side == "home" else "away_win"
        why = [
            pot_line,
            f"Разница 15–20 — {stronger} значительно сильнее, победа рабочий вариант.",
            f"Дом/выезд: хозяева {final_h}/100, гости {final_a}/100.",
        ]
        add_cand(key, 84, why, est_prob=0.63)
    elif pot_diff <= 10:
        # Равны / близки: «не проиграет» тому, у кого рейтинг (дом/выезд) выше;
        # если рейтинги одинаковы — смотрим общий потенциал.
        if final_h > final_a:
            dc_side, dc_key, dc_name = "home", "home_dc", home_name
        elif final_a > final_h:
            dc_side, dc_key, dc_name = "away", "away_dc", away_name
        else:
            # рейтинги дом/выезд равны → общий потенциал
            if overall_h > overall_a:
                dc_side, dc_key, dc_name = "home", "home_dc", home_name
            elif overall_a > overall_h:
                dc_side, dc_key, dc_name = "away", "away_dc", away_name
            else:
                dc_side, dc_key, dc_name = "home", "home_dc", home_name  # полный паритет → хозяева
        if dc_key == "home_dc":
            why = [
                pot_line,
                f"Потенциал близок — надёжнее 1X ({dc_name} не проиграют), чем чистая победа.",
                f"Рейтинг площадки: хозяева {final_h}/100, гости {final_a}/100"
                + (f"; общий {overall_h}/{overall_a}." if final_h == final_a else "."),
            ]
            add_cand("home_dc", 82, why, est_prob=0.70)
        else:
            why = [
                pot_line,
                f"Потенциал близок — надёжнее X2 ({dc_name} не проиграют), чем чистая победа.",
                f"Рейтинг площадки: хозяева {final_h}/100, гости {final_a}/100"
                + (f"; общий {overall_h}/{overall_a}." if final_h == final_a else "."),
            ]
            add_cand("away_dc", 80, why, est_prob=0.68)
    elif 10 < pot_diff <= 15:
        key = "home_win" if stronger_side == "home" else "away_win"
        dc = "home_dc" if stronger_side == "home" else "away_dc"
        why = [
            pot_line,
            f"{stronger} сильнее (разница 10–15) — победа возможна, 1X/X2 надёжнее для комбо.",
        ]
        add_cand(key, 76, why, est_prob=0.61)
        add_cand(dc, 79, why + ["Двойной шанс снижает риск ничьей."], est_prob=0.68)

    # Без fallback: если критерий не прошёл — исход не публикуем

    # Ординары 3–4: только прошедшие критерии (ТБ 2.5 / ТМ 3.5 / гол 1Т / победа|1X)
    preferred_order = (
        "over_25", "under_35", "ht_goal", "btts_yes",
        "home_over_15", "away_over_15",
        "home_win", "away_win", "home_dc", "away_dc",
        "over_15",
    )
    by_key = {c["key"]: c for c in candidates}
    singles = []
    used_keys = set()
    conflict = {
        "over_25": {"under_25", "under_35", "over_15"},
        "under_35": {"over_25", "over_35", "over_15"},
        "over_15": {"under_15", "under_35", "over_25"},
        "home_win": {"away_win", "away_dc", "home_dc", "away_clean", "home_clean"},
        "away_win": {"home_win", "home_dc", "away_dc", "home_clean", "away_clean"},
        "home_dc": {"away_win", "away_dc", "home_win", "away_clean"},
        "away_dc": {"home_win", "home_dc", "away_win", "home_clean"},
        "home_clean": {"away_win", "away_dc", "away_clean", "home_dc", "home_win", "btts_yes"},
        "away_clean": {"home_win", "home_dc", "home_clean", "away_dc", "away_win", "btts_yes"},
        "btts_yes": {"btts_no", "home_clean", "away_clean"},
    }

    def _try_add_single(c):
        if not c or c["key"] in used_keys:
            return False
        if any(c["key"] in conflict.get(u, set()) or u in conflict.get(c["key"], set()) for u in used_keys):
            return False
        used_keys.add(c["key"])
        singles.append({
            "pick": c["label"],
            "key": c["key"],
            "coef": c["coef"],
            "prob": int(round(float(c["est_prob"]) * 100)),
            "why": "\n".join(c["why"][:5]),
        })
        return True

    for key in preferred_order:
        if len(singles) >= 4:
            break
        _try_add_single(by_key.get(key))
    # добор по score, если < 3
    rest = sorted(candidates, key=lambda c: (-c["score"], -float(c.get("est_prob") or 0)))
    for c in rest:
        if len(singles) >= 4:
            break
        _try_add_single(c)

    # combos: тотал/ОЗ/1Т + исход/1X; кф комбо из совместной вероятности в коридоре 1.60–2.50
    COMPAT = {
        "over_15": {"btts_yes", "home_win", "away_win", "home_dc", "away_dc", "ht_goal", "home_clean", "away_clean"},
        "over_25": {"btts_yes", "home_win", "away_win", "home_dc", "away_dc", "ht_goal", "home_clean", "away_clean"},
        "under_35": {"home_dc", "away_dc", "home_win", "away_win", "btts_no", "home_clean", "away_clean"},
        "under_25": {"home_dc", "away_dc", "btts_no", "home_clean", "away_clean"},
        "btts_yes": {"over_15", "over_25", "home_win", "away_win", "home_dc", "away_dc", "ht_goal"},
        "ht_goal": {"over_15", "over_25", "btts_yes", "home_dc", "away_dc", "home_win", "away_win", "home_clean", "away_clean"},
        "home_win": {"over_15", "over_25", "btts_yes", "under_35", "ht_goal"},
        "away_win": {"over_15", "over_25", "btts_yes", "under_35", "ht_goal"},
        "home_dc": {"over_15", "over_25", "btts_yes", "under_35", "ht_goal"},
        "away_dc": {"over_15", "over_25", "btts_yes", "under_35", "ht_goal"},
        "home_clean": {"over_15", "under_35", "ht_goal"},
        "away_clean": {"over_15", "under_35", "ht_goal"},
    }
    score_map = {c["key"]: c for c in candidates}
    combo_cands = []
    keys = list(score_map.keys())
    for i, k1 in enumerate(keys):
        for k2 in keys[i + 1:]:
            if k2 not in COMPAT.get(k1, set()) and k1 not in COMPAT.get(k2, set()):
                continue
            if k1.startswith("over") and k2.startswith("over"):
                continue
            if k1.startswith("under") and k2.startswith("under"):
                continue
            if {k1, k2} <= {"home_win", "away_win", "home_dc", "away_dc", "draw"}:
                continue
            p1 = float(score_map[k1].get("est_prob") or 0)
            p2 = float(score_map[k2].get("est_prob") or 0)
            cprob = p1 * p2
            if cprob < 0.38:
                continue
            # кф комбо: из совместной вероятности, всегда в 1.60–2.50
            coef_combo = _coef_from_prob(cprob, COEF_MIN, COEF_MAX)
            if coef_combo is None:
                raw = 1.0 / max(min(cprob, 0.88), 0.40) * 0.92
                coef_combo = max(COEF_MIN, min(COEF_MAX, round(raw, 2)))
            # если произведение реальных кэфов попадает в коридор — берём его
            c1 = score_map[k1].get("coef")
            c2 = score_map[k2].get("coef")
            try:
                prod = round(float(c1) * float(c2), 2)
                if COEF_MIN <= prod <= COEF_MAX:
                    coef_combo = prod
            except (TypeError, ValueError):
                pass
            s = score_map[k1]["score"] + score_map[k2]["score"]
            if p1 >= MIN_PROB and p2 >= MIN_PROB:
                s += 8
            # бонус за «тотал + исход» и «гол 1Т + тотал»
            pair = {k1, k2}
            if pair & {"over_25", "over_15", "under_35"} and pair & {"home_win", "away_win", "home_dc", "away_dc"}:
                s += 12
            if "ht_goal" in pair and pair & {"over_15", "over_25", "btts_yes", "home_dc", "away_dc"}:
                s += 10
            why_lines = [
                f"Комбо: {MARKET_LABELS.get(k1, k1)} + {MARKET_LABELS.get(k2, k2)}.",
                pot_line,
            ]
            for line in (score_map[k1]["why"][:2] + score_map[k2]["why"][:2]):
                if line not in why_lines and line != pot_line:
                    why_lines.append(line)
            combo_cands.append({
                "pick": f"{MARKET_LABELS.get(k1, k1)} + {MARKET_LABELS.get(k2, k2)}",
                "keys": [k1, k2],
                "coef": round(float(coef_combo), 2),
                "score": s,
                "why": "\n".join(why_lines[:5]),
                "est_prob": cprob,
            })
    combo_cands.sort(key=lambda c: (-c["score"], -float(c.get("est_prob") or 0), c["coef"]))
    combos = []
    used_pairs = set()
    for c in combo_cands:
        pair = tuple(sorted(c["keys"]))
        if pair in used_pairs:
            continue
        used_pairs.add(pair)
        pr = c.get("est_prob")
        # для UI показываем совместную вероятность, но не ниже 45% визуально если сигнал сильный
        show_p = int(round(float(pr) * 100)) if pr is not None else None
        combos.append({
            "pick": c["pick"],
            "keys": c["keys"],
            "coef": c["coef"],
            "prob": show_p,
            "why": c["why"],
        })
        if len(combos) >= 2:
            break

    # Аналитика: таблица строк убрана; травмы — отдельным списком
    analysis_rows = []
    injury_rows = list(inj_notes)
    analysis = ""

    motivation = _compute_motivation(pack)

    return {
        "analysis": analysis,
        "analysis_rows": analysis_rows,
        "injury_rows": injury_rows,
        "singles": singles,
        "combos": combos,
        "coef_range": [COEF_MIN, COEF_MAX],
        "min_prob": int(MIN_PROB * 100),
        "odds": odds,
        "odds_count": len(odds),
        "model": {
            k: pred[k]
            for k in pred
            if k.startswith("prob_") or k.startswith("xg_") or k in ("predicted_1x2", "most_likely_score")
        },
        "potential": {
            "home_venue": base_h,
            "away_venue": base_a,
            "home_overall": overall_h,
            "away_overall": overall_a,
            "home_final": final_h,
            "away_final": final_a,
            "diff": pot_diff,
            "class": class_txt,
            "stars": class_stars,
            "stronger": stronger,
        },
        "motivation": motivation,
        "defenders_out": {
            "home": {"count": def_h_n, "names": def_h_names},
            "away": {"count": def_a_n, "names": def_a_names},
        },
        "attackers_out": {
            "home": {"count": att_h_n, "names": att_h_names},
            "away": {"count": att_a_n, "names": att_a_names},
        },
        "signals": {
            "over_25": signal_o25,
            "over_15": signal_o15,
            "under_35": signal_u35,
            "ht_goal": signal_ht,
            "btts_yes": signal_btts,
            "team_over_15": signal_itb,
            "goals_sum": round(goals_sum, 2),
            "o25_home": o25_h,
            "o25_away": o25_a,
        },
    }


def _extract_metric_avg(stats_obj, keys) -> dict:
    """Pull average/total/matches for first matching key in stats bag."""
    if not stats_obj or not isinstance(stats_obj, dict):
        return {}
    for key in keys:
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


def _extract_xg_from_stats_block(stats_obj) -> dict:
    """Pull xG total/avg from form.overall.stats or similar bag."""
    return _extract_metric_avg(stats_obj, ("xg", "expected_goals", "xG", "xg_for", "xg_scored"))


def _extract_xga_from_stats_block(stats_obj) -> dict:
    """Pull xGA (expected goals against) from many possible BSD shapes."""
    if not stats_obj or not isinstance(stats_obj, dict):
        return {}
    # nested: expected_goals / xg -> against / conceded
    for root in ("xg", "expected_goals", "xG", "expectedGoals"):
        block = stats_obj.get(root)
        if isinstance(block, dict):
            for ak in ("against", "conceded", "opp", "opponent", "xga", "against_average"):
                sub = block.get(ak)
                if isinstance(sub, dict):
                    avg = sub.get("average") or sub.get("avg") or sub.get("actual")
                    if avg is not None:
                        try:
                            return {"avg": float(avg), "total": float(sub["total"]) if sub.get("total") is not None else None, "matches": sub.get("matches")}
                        except (TypeError, ValueError):
                            pass
                elif isinstance(sub, (int, float)):
                    return {"avg": float(sub)}
            # xg.for / xg.against style
            if "for" in block and "against" not in block:
                pass
    direct = _extract_metric_avg(
        stats_obj,
        ("xga", "xGA", "xg_against", "expected_goals_against", "xg_conceded", "xg_opp", "xg_against_avg",
         "avg_xga", "xga_avg", "expected_goals_conceded"),
    )
    if direct.get("avg") is not None:
        return direct
    # deep scan one level of nested stats
    for v in stats_obj.values():
        if not isinstance(v, dict):
            continue
        got = _extract_metric_avg(
            v,
            ("xga", "xGA", "xg_against", "expected_goals_against", "xg_conceded", "avg_xga"),
        )
        if got.get("avg") is not None:
            return got
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
            ("possession_final_third", (
                "possession_final_third", "final_third_possession", "ball_possession_final_third",
                "attacking_third_possession", "possession_attacking_third", "final_third_pct",
                "opp_third_possession", "dangerous_possession",
            )),
            ("dangerous_attacks", ("dangerous_attacks", "danger_attacks")),
            ("attacks", ("attacks", "total_attacks")),
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
    xga_info = _extract_xga_from_stats_block(raw_stats if isinstance(raw_stats, dict) else {})
    if not xga_info:
        for k in ("xga", "xGA", "xg_against", "expected_goals_against", "avg_xga"):
            v = side_data.get(k)
            if isinstance(v, (int, float)):
                xga_info = {"avg": float(v)}
                break
            if isinstance(v, dict) and (v.get("average") is not None or v.get("avg") is not None):
                xga_info = {"avg": float(v.get("average") or v.get("avg"))}
                break
    # flatten common averages for UI
    possession = stats_avg.get("possession")
    shots = stats_avg.get("shots")
    shots_on = stats_avg.get("shots_on")
    corners = stats_avg.get("corners")
    yellow = stats_avg.get("yellow")
    fouls = stats_avg.get("fouls")
    # чужая треть: из API или прокси
    ft_bag = {
        "possession": possession,
        "shots": shots,
        "shots_on": shots_on,
        "possession_final_third": stats_avg.get("possession_final_third"),
        "dangerous_attacks": stats_avg.get("dangerous_attacks"),
        "attacks": stats_avg.get("attacks"),
    }
    for k in ("possession_final_third", "dangerous_attacks", "attacks", "possession", "shots", "shots_on"):
        if side_data.get(k) is not None and ft_bag.get(k) is None:
            ft_bag[k] = side_data.get(k)
    possession_final_third = _extract_final_third(ft_bag)
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
        "xga_avg": xga_info.get("avg"),
        "possession": possession,
        "possession_final_third": possession_final_third,
        "shots": shots,
        "shots_on": shots_on,
        "corners": corners,
        "yellow": yellow,
        "fouls": fouls,
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


def _compute_form_sides_from_matches(matches: list, team_id: int = None) -> dict:
    """
    Build overall / home / away form buckets from finished matches.
    Used when BSD /form/ does not return home/away splits (common for season scope).
    Returns dict compatible with _parse_form output structure.
    """
    if not matches or not team_id:
        return {}
    try:
        tid = int(team_id)
    except (TypeError, ValueError):
        return {}

    def _empty_bucket():
        return {
            "form": "",
            "won": 0, "drawn": 0, "lost": 0,
            "goals_for": 0, "goals_against": 0,
            "matches_count": 0,
            "gpg": None, "gapg": None,
        }

    overall = _empty_bucket()
    home_b = _empty_bucket()
    away_b = _empty_bucket()
    form_chars = []       # chronological (API usually newest first)
    form_chars_home = []
    form_chars_away = []

    for m in matches:
        if not isinstance(m, dict):
            continue
        try:
            hs = int(m.get("home_score"))
            aws = int(m.get("away_score"))
        except (TypeError, ValueError):
            continue
        hid = m.get("home_team_id")
        aid = m.get("away_team_id")
        try:
            is_home = hid is not None and int(hid) == tid
            is_away = aid is not None and int(aid) == tid
        except (TypeError, ValueError):
            continue
        if not is_home and not is_away:
            continue

        if is_home:
            gf, ga = hs, aws
            bucket = home_b
            if hs > aws:
                letter = "W"
            elif hs == aws:
                letter = "D"
            else:
                letter = "L"
            form_chars_home.append(letter)
        else:
            gf, ga = aws, hs
            bucket = away_b
            if aws > hs:
                letter = "W"
            elif aws == hs:
                letter = "D"
            else:
                letter = "L"
            form_chars_away.append(letter)

        for b in (overall, bucket):
            b["matches_count"] += 1
            b["goals_for"] += gf
            b["goals_against"] += ga
            if letter == "W":
                b["won"] += 1
            elif letter == "D":
                b["drawn"] += 1
            else:
                b["lost"] += 1

        form_chars.append(letter)

    def _finalize(b, chars=None):
        n = b["matches_count"]
        if n <= 0:
            return {}
        b["gpg"] = round(b["goals_for"] / n, 2)
        b["gapg"] = round(b["goals_against"] / n, 2)
        if chars is not None:
            b["form"] = "".join(chars[:10])
        else:
            b["form"] = ("W" * b["won"] + "D" * b["drawn"] + "L" * b["lost"])[:10]
        return b

    out = _finalize(overall, form_chars)
    if not out:
        return {}
    h = _finalize(home_b, form_chars_home)
    a = _finalize(away_b, form_chars_away)
    if h:
        out["home"] = h
    if a:
        out["away"] = a
    return out


def _merge_form_with_computed(form: dict, computed: dict) -> dict:
    """Fill missing overall metrics and home/away splits from computed match-based form."""
    if not computed:
        return form if isinstance(form, dict) else {}
    base = form if isinstance(form, dict) else {}
    for key in ("form", "won", "drawn", "lost", "goals_for", "goals_against",
                "matches_count", "gpg", "gapg"):
        if base.get(key) in (None, "", 0) and computed.get(key) not in (None, "", 0):
            if key == "form" and base.get("form"):
                continue
            if key in ("won", "drawn", "lost", "goals_for", "goals_against", "matches_count") and base.get(key):
                continue
            base[key] = computed[key]
    if base.get("gpg") is None and computed.get("gpg") is not None:
        base["gpg"] = computed["gpg"]
    if base.get("gapg") is None and computed.get("gapg") is not None:
        base["gapg"] = computed["gapg"]
    for split in ("home", "away"):
        api_side = base.get(split) if isinstance(base.get(split), dict) else {}
        comp_side = computed.get(split) if isinstance(computed.get(split), dict) else {}
        if not comp_side:
            continue
        needs = (
            not api_side
            or not api_side.get("matches_count")
            or (api_side.get("gpg") is None and api_side.get("gapg") is None)
        )
        if needs:
            merged = dict(api_side) if api_side else {}
            for k, v in comp_side.items():
                if merged.get(k) in (None, "", 0) and v not in (None, ""):
                    merged[k] = v
            for k in ("form", "won", "drawn", "lost", "goals_for", "goals_against",
                      "matches_count", "gpg", "gapg"):
                if comp_side.get(k) is not None:
                    if k == "form" and merged.get("form"):
                        continue
                    if merged.get(k) in (None, "") or (k in ("gpg", "gapg") and merged.get(k) is None):
                        merged[k] = comp_side[k]
            base[split] = merged
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
    team_over15 = 0  # эта команда забила 2+ (ИТБ 1.5)
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
        if gf >= 2:
            team_over15 += 1
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
        "team_over_15_pct": pct(team_over15),  # ИТБ 1.5 — % матчей, где команда забила 2+
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


def _is_cup_match(m: dict, league_id: int = None) -> bool:
    """
    Detect domestic / continental cups so they can be excluded from season stats.
    Prefer league_id match when available; otherwise name heuristics.
    """
    if not isinstance(m, dict):
        return False
    if league_id is not None:
        mid = m.get("league_id")
        try:
            if mid is not None and int(mid) != int(league_id):
                return True  # different competition → treat as non-league (cup etc.)
            if mid is not None and int(mid) == int(league_id):
                return False
        except (TypeError, ValueError):
            pass
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
    cup_keys = (
        "cup", "кубок", "copa", "coupe", "pokal", "taça", "taca",
        "supercup", "super cup", "суперкубок", "fa cup", "efl",
        "league cup", "carabao", "community shield", "charity shield",
        "champions league", "europa league", "conference league",
        "uefa", "libertadores", "sudamericana", "afc champions",
        "club world", "межсезон",
    )
    return any(k in name for k in cup_keys)


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
        # no hard cap — take all finished competitive matches returned (season window)
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
    """Pull goals/corners/yellow/fouls/shots/sot/xg/possession from a team stats block."""
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
        "possession": ("ball_possession", "possession", "possession_pct", "ball_possession_percentage"),
        "xg": ("xg", "expected_goals", "xG", "xg_for", "expected_goals_for", "xg_scored"),
        "xga": ("xga", "xGA", "xg_against", "expected_goals_against", "xg_conceded"),
        # BSD: атаки / опасные атаки — ближайшее к «чужой трети»
        "attacks": ("attacks", "total_attacks", "attack"),
        "dangerous_attacks": ("dangerous_attacks", "danger_attacks", "dangerous_attack"),
        "dangerous_attack_pct": ("dangerous_attack_pct", "dangerous_attacks_pct", "danger_attack_pct"),
        "attack_pct": ("attack_pct", "attacks_pct"),
    }
    for key, aliases in mapping.items():
        v = _side_stat(team_block, *aliases)
        if v is not None:
            try:
                out[key] = float(v)
            except (TypeError, ValueError):
                pass
    # чужая треть из атак BSD
    ft = _extract_final_third(out)
    if ft is not None:
        out["possession_final_third"] = ft
    return out


def _xg_from_shotmap(st, side: str) -> tuple:
    """
    Sum xG for/against from shotmap rows for one side.
    Returns (xg_for, xg_against) or (None, None) if no xG fields found.
    """
    shots = _shotmap_list(st)
    if not shots:
        return None, None
    xf = xa = 0.0
    found = False
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        xg_val = shot.get("xg")
        if xg_val is None:
            xg_val = shot.get("expected_goals") or shot.get("xG") or shot.get("xg_value")
        if xg_val is None:
            continue
        try:
            xg_f = float(xg_val)
        except (TypeError, ValueError):
            continue
        is_home = shot.get("home")
        if is_home is None:
            team = str(shot.get("team") or shot.get("side") or "").lower()
            is_home = team in ("home", "h", "1")
        scorer = "home" if is_home else "away"
        found = True
        if scorer == side:
            xf += xg_f
        else:
            xa += xg_f
    if not found:
        return None, None
    return round(xf, 3), round(xa, 3)


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
    """Trends from current-season finished matches (W/D/L, goals, BTTS, O2.5, clean sheets)."""
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
    for m in (last_matches or []):
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
        sot = _side_stat_num(st, our_side, "shots_on", "shots_on_target", "shots_on_goal", "on_target", "sot")
        for wkey, start_m, end_m, _label in windows_def:
            gf0, ga0 = _score_at_minute(timeline, start_m)
            sk = _score_key(gf0, ga0)
            # только счета до 3-3 включительно
            if gf0 > 3 or ga0 > 3:
                continue
            gf_a, ga_a = _events_between(timeline, start_m, end_m)
            bucket = out["windows"][wkey]["by_score"].setdefault(sk, {
                "n": 0, "gf": 0, "ga": 0, "yellow": 0.0, "corners": 0.0, "fouls": 0.0, "sot": 0.0,
                "n_scored": 0, "n_conceded": 0, "n_yellow": 0, "n_corners": 0, "n_sot": 0,
            })
            bucket["n"] += 1
            bucket["gf"] += gf_a
            bucket["ga"] += ga_a
            if gf_a > 0:
                bucket["n_scored"] += 1
            if ga_a > 0:
                bucket["n_conceded"] += 1
            if yellow >= 1:
                bucket["n_yellow"] += 1
            if corners >= 1:
                bucket["n_corners"] += 1
            if sot >= 1:
                bucket["n_sot"] += 1
            bucket["yellow"] += yellow
            bucket["corners"] += corners
            bucket["fouls"] += fouls
            bucket["sot"] += sot
    out["matches"] = used
    for w in out["windows"].values():
        for sk, b in list(w["by_score"].items()):
            n = max(1, int(b.get("n") or 1))
            b["yellow_avg"] = round(float(b.get("yellow") or 0) / n, 1)
            b["corners_avg"] = round(float(b.get("corners") or 0) / n, 1)
            b["fouls_avg"] = round(float(b.get("fouls") or 0) / n, 1)
            b["sot_avg"] = round(float(b.get("sot") or 0) / n, 1)
            b["gf_avg"] = round(float(b.get("gf") or 0) / n, 2)
            b["ga_avg"] = round(float(b.get("ga") or 0) / n, 2)
            b["p_score"] = round(100.0 * int(b.get("n_scored") or 0) / n)
            b["p_concede"] = round(100.0 * int(b.get("n_conceded") or 0) / n)
            b["p_yellow"] = round(100.0 * int(b.get("n_yellow") or 0) / n)
            b["p_corners"] = round(100.0 * int(b.get("n_corners") or 0) / n)
            b["p_sot"] = round(100.0 * int(b.get("n_sot") or 0) / n)
            # ranked actions for UI
            actions = [
                {"key": "score", "label": "Забьют", "p": b["p_score"]},
                {"key": "concede", "label": "Пропустят", "p": b["p_concede"]},
                {"key": "yellow", "label": "Жёлтые", "p": b["p_yellow"]},
                {"key": "corners", "label": "Угловые", "p": b["p_corners"]},
                {"key": "sot", "label": "Уд. в створ", "p": b["p_sot"]},
            ]
            actions.sort(key=lambda x: (-x["p"], x["label"]))
            b["actions"] = actions
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



def _xga_from_match_stats_list(stats_list: list, match_sides: list) -> Optional[float]:
    """Average xGA from per-match stats: our side xga OR opponent side xg."""
    vals = []
    for st, our_side in zip(stats_list or [], match_sides or []):
        if not st or not our_side:
            continue
        bag = st.get("stats") if isinstance(st.get("stats"), dict) else st
        if not isinstance(bag, dict):
            continue
        opp = "away" if our_side == "home" else "home"
        # try our xga
        for side_key in (our_side, "team"):
            side = bag.get(side_key) if isinstance(bag.get(side_key), dict) else None
            if not side:
                continue
            for k in ("xga", "xg_against", "expected_goals_against", "xGA"):
                v = side.get(k)
                if isinstance(v, (int, float)):
                    vals.append(float(v))
                    break
                if isinstance(v, dict) and (v.get("average") is not None or v.get("avg") is not None):
                    try:
                        vals.append(float(v.get("average") or v.get("avg")))
                    except (TypeError, ValueError):
                        pass
                    break
        # opponent xg ≈ our xga
        side = bag.get(opp) if isinstance(bag.get(opp), dict) else None
        if side:
            for k in ("xg", "expected_goals", "xG", "xg_for"):
                v = side.get(k)
                if isinstance(v, (int, float)):
                    vals.append(float(v))
                    break
                if isinstance(v, dict) and (v.get("average") is not None or v.get("avg") is not None):
                    try:
                        vals.append(float(v.get("average") or v.get("avg")))
                    except (TypeError, ValueError):
                        pass
                    break
        # full/match block
        for fk in ("full", "match", "ft", "full_time"):
            full = bag.get(fk)
            if not isinstance(full, dict):
                continue
            for sk, keys in ((our_side, ("xga", "xg_against")), (opp, ("xg", "expected_goals", "xG"))):
                sb = full.get(sk) if isinstance(full.get(sk), dict) else None
                if not sb:
                    continue
                for k in keys:
                    v = sb.get(k)
                    if isinstance(v, (int, float)):
                        vals.append(float(v))
    if not vals:
        return None
    return round(sum(vals) / len(vals), 2)


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
        lg = event.get("league") or event.get("league_name") or event.get("competition") or ""
        if isinstance(lg, dict):
            lg = lg.get("name") or lg.get("title") or ""
        pack["league"] = str(lg or "").strip()
        pack["league_id"] = league_id

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

    # Form + season results (league + cup), squad, halves, goal segments, betting rates
    kickoff_iso = None
    if event and isinstance(event, dict):
        kickoff_iso = event.get("event_date") or event.get("date") or event.get("kickoff")

    async def _team_pack(tid):
        if not tid:
            return {}, [], [], {}, {}, {}, {}
        # Form + events: ONLY current league (no domestic/continental cups)
        form_params = {}
        if season_id:
            form_params["season_id"] = season_id
        else:
            form_params["last"] = 25
        if league_id:
            form_params["league_id"] = league_id
        form10_p = _bsd_get(session, f"teams/{tid}/form/", form_params)
        # League-only finished matches (exclude cups via league_id filter)
        ev_params = {"team_id": tid, "status": "finished", "limit": 40}
        if season_id:
            ev_params["season_id"] = season_id
        if league_id:
            ev_params["league_id"] = league_id
        ev_p = _bsd_get(session, "events/", ev_params)
        squad_p = _bsd_get(session, f"teams/{tid}/squad/")
        form10_d, ev_d, squad_d = await asyncio.gather(form10_p, ev_p, squad_p)
        form = _parse_form(form10_d)
        last5 = _parse_recent_fixtures(ev_d)
        # Hard-filter: drop cups / other competitions / friendlies
        if last5 and league_id:
            last5 = [
                m for m in last5
                if not _is_friendly_match(m) and not _is_cup_match(m, league_id)
            ]
        elif last5:
            last5 = [
                m for m in last5
                if not _is_friendly_match(m) and not _is_cup_match(m, None)
            ]
        if not last5:
            # fallback without league filter only if league-scoped returned empty
            fix_params = {"status": "finished", "limit": 30}
            if season_id:
                fix_params["season_id"] = season_id
            if league_id:
                fix_params["league_id"] = league_id
            fix_d = await _bsd_get(session, f"teams/{tid}/fixtures/", fix_params)
            last5 = _parse_recent_fixtures(fix_d)
            if last5 and league_id:
                last5 = [
                    m for m in last5
                    if not _is_friendly_match(m) and not _is_cup_match(m, league_id)
                ]
        # Enrich form with home/away splits from league matches only
        tid_int = int(tid) if tid else None
        computed_form = _compute_form_sides_from_matches(last5, team_id=tid_int)
        form = _merge_form_with_computed(form if isinstance(form, dict) else {}, computed_form)
        if not last5:
            form = form if isinstance(form, dict) else {}
            form["season_empty"] = True
            form["season_message"] = "Статистики ещё нет — сезон не стартовал"
        injuries = _parse_injuries(squad_d)
        betting_rates = _compute_betting_rates(last5, team_id=tid_int)
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

        # Halves + goal segments + match stats — limited sample for speed
        half_stats = {}
        match_avg = {}
        goal_seg = _empty_goal_segments()
        character = {"matches": 0, "windows": {}}
        SEASON_SAMPLE_CAP = 14  # was 40 — main cause of slow load after parser
        event_ids = []
        match_meta = []
        for m in (last5 or []):
            if league_id and _is_cup_match(m, league_id):
                continue
            if _is_friendly_match(m):
                continue
            eid = m.get("event_id") or m.get("id")
            if eid:
                event_ids.append(int(eid))
                match_meta.append(m if isinstance(m, dict) else {})
                if len(event_ids) >= SEASON_SAMPLE_CAP:
                    break
        event_ids = event_ids[:SEASON_SAMPLE_CAP]
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
            our_name = (form.get("team_name") or "").lower().strip()
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
            # Bounded concurrency — avoids hammering API (was unlimited parallel ×40)
            _sem = asyncio.Semaphore(6)

            async def _stats_one(eid):
                async with _sem:
                    return await _bsd_get(session, f"events/{eid}/stats/")

            stats_raw = await asyncio.gather(*[_stats_one(eid) for eid in event_ids])

            sides_for_goals = []
            stats_aligned = []
            for st, m in zip(stats_raw, match_meta):
                side = _resolve_side(m)
                sides_for_goals.append(side)
                stats_aligned.append(st)

            normalized = []
            ms_keys = [
                "goals", "corners", "yellow", "fouls", "shots", "shots_on", "possession",
                "xg", "xga", "attacks", "dangerous_attacks", "dangerous_attack_pct",
                "attack_pct", "possession_final_third",
            ]
            ms_acc = {k: [] for k in ms_keys}
            # venue splits for Potential «Дома» / «Выезд»
            ms_acc_home = {k: [] for k in ms_keys}
            ms_acc_away = {k: [] for k in ms_keys}
            games_with_metrics = 0

            def _ms_push(acc, k, v):
                if v is None:
                    return
                try:
                    acc[k].append(float(v))
                except (TypeError, ValueError):
                    pass

            for st, side, m in zip(stats_aligned, sides_for_goals, match_meta):
                if not st or not isinstance(st, dict):
                    continue
                bag = st.get("stats") if isinstance(st.get("stats"), dict) else st
                if not isinstance(bag, dict):
                    continue
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
                    out_key = "first_half" if "first" in half_key or half_key.startswith("1") else "second_half"
                    if _side_stat(team_half, "goals", "score") is None:
                        if out_key == "first_half" and sm_g1 is not None:
                            team_half["goals"] = sm_g1
                        elif out_key == "second_half" and sm_g2 is not None:
                            team_half["goals"] = sm_g2
                    norm["stats"][out_key] = {"home": team_half}
                if norm["stats"]:
                    normalized.append(norm)

                team = _team_block_from_stats_bag(bag, side)
                metrics = _pull_match_metrics(team) if team else {}
                fh = bag.get("first_half") or bag.get("1st_half") or {}
                sh = bag.get("second_half") or bag.get("2nd_half") or {}
                fh_t = fh.get(side) if isinstance(fh.get(side), dict) else {}
                sh_t = sh.get(side) if isinstance(sh.get(side), dict) else {}
                if not metrics:
                    m1 = _pull_match_metrics(fh_t)
                    m2 = _pull_match_metrics(sh_t)
                    for key in ms_keys:
                        if key in ("possession",):
                            # possession is % — average halves, don't sum
                            vals = [v for v in (m1.get(key), m2.get(key)) if v is not None]
                            if vals:
                                metrics[key] = sum(vals) / len(vals)
                        elif key in m1 or key in m2:
                            metrics[key] = float(m1.get(key) or 0) + float(m2.get(key) or 0)
                else:
                    # fill missing possession from halves average
                    if metrics.get("possession") is None:
                        m1 = _pull_match_metrics(fh_t)
                        m2 = _pull_match_metrics(sh_t)
                        vals = [v for v in (m1.get("possession"), m2.get("possession")) if v is not None]
                        if vals:
                            metrics["possession"] = sum(vals) / len(vals)
                # xG / xGA from shotmap when not in team block
                if metrics.get("xg") is None or metrics.get("xga") is None:
                    xf, xa = _xg_from_shotmap(st, side)
                    if metrics.get("xg") is None and xf is not None:
                        metrics["xg"] = xf
                    if metrics.get("xga") is None and xa is not None:
                        metrics["xga"] = xa
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
                venue_acc = ms_acc_home if side == "home" else ms_acc_away
                for key in ms_keys:
                    if key in metrics:
                        _ms_push(ms_acc, key, metrics[key])
                        _ms_push(venue_acc, key, metrics[key])

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
            if match_avg.get("possession_final_third") is None:
                ft_avg = _extract_final_third(match_avg)
                if ft_avg is not None:
                    match_avg["possession_final_third"] = ft_avg

            def _avg_bucket(acc):
                out = {}
                n = 0
                for k, vals in acc.items():
                    if vals:
                        out[k] = round(sum(vals) / len(vals), 2)
                        n = max(n, len(vals))
                    else:
                        out[k] = None
                out["matches"] = n
                if out.get("possession_final_third") is None:
                    ft_b = _extract_final_third(out)
                    if ft_b is not None:
                        out["possession_final_third"] = ft_b
                return out if n else {}

            venue_home_avg = _avg_bucket(ms_acc_home)
            venue_away_avg = _avg_bucket(ms_acc_away)

            logger.info(
                "[team_pack] tid=%s match_stats.matches=%s home=%s away=%s samples=%s",
                tid, match_avg["matches"],
                venue_home_avg.get("matches"), venue_away_avg.get("matches"),
                len([1 for x in stats_aligned if x]),
            )

            goal_seg = _aggregate_goal_segments_from_stats(stats_aligned, sides_for_goals)
            # incidents only for up to 4 games if shotmap gave zero goals (speed)
            total_goals = sum((goal_seg.get("scored") or {}).values()) + sum((goal_seg.get("conceded") or {}).values())
            if total_goals == 0 and event_ids:
                sample = list(zip(event_ids, sides_for_goals))[:4]
                inc_raw = await asyncio.gather(*[
                    _bsd_get(session, f"events/{eid}/incidents/") for eid, _ in sample
                ])
                sides_sample = [s for _, s in sample]
                inc_seg = _aggregate_goal_segments(inc_raw, sides_sample)
                if sum((inc_seg.get("scored") or {}).values()) + sum((inc_seg.get("conceded") or {}).values()) > 0:
                    goal_seg = inc_seg
            if not goal_seg.get("matches"):
                goal_seg["matches"] = len([1 for st in stats_aligned if st]) or len(event_ids)

            character = _compute_character_from_stats(stats_aligned, sides_for_goals)
            if isinstance(form, dict) and form.get("xga_avg") is None:
                xga_m = _xga_from_match_stats_list(stats_aligned, sides_for_goals)
                if xga_m is not None:
                    form["xga_avg"] = xga_m
            # overall → form (xG / possession critical for Potential)
            # Prefer league-sample averages over form API (may mix cups)
            if isinstance(form, dict) and match_avg:
                for mk in ("shots", "shots_on", "corners", "fouls", "yellow", "possession"):
                    if match_avg.get(mk) is not None:
                        form[mk] = match_avg[mk]
                    elif form.get(mk) is None and match_avg.get(mk) is not None:
                        form[mk] = match_avg[mk]
                if match_avg.get("goals") is not None:
                    form["gpg"] = match_avg["goals"]
                if match_avg.get("xg") is not None:
                    form["xg_avg"] = match_avg["xg"]
                if match_avg.get("xga") is not None:
                    form["xga_avg"] = match_avg["xga"]
                # чужая треть: API или прокси от владения + ударов
                ft = _extract_final_third({**form, **{k: match_avg.get(k) for k in ("possession", "shots", "shots_on")}})
                if ft is not None:
                    form["possession_final_third"] = ft
            # venue splits → form.home / form.away so Potential «Дома/Выезд» is full
            if isinstance(form, dict):
                for split_key, vavg in (("home", venue_home_avg), ("away", venue_away_avg)):
                    if not vavg:
                        continue
                    side_form = form.get(split_key) if isinstance(form.get(split_key), dict) else {}
                    side_form = dict(side_form)
                    for mk in ("shots", "shots_on", "corners", "fouls", "yellow", "possession"):
                        if vavg.get(mk) is not None:
                            side_form[mk] = vavg[mk]
                    if vavg.get("goals") is not None:
                        side_form["gpg"] = vavg["goals"]
                    if vavg.get("xg") is not None:
                        side_form["xg_avg"] = vavg["xg"]
                    if vavg.get("xga") is not None:
                        side_form["xga_avg"] = vavg["xga"]
                    if vavg.get("matches"):
                        side_form.setdefault("matches_count", vavg["matches"])
                    ft_side = _extract_final_third(side_form)
                    if ft_side is not None:
                        side_form["possession_final_third"] = ft_side
                    form[split_key] = side_form

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

def get_league_priority(league_name: str, country: str = "") -> int:
    if not league_name:
        return 99
    name = str(league_name).strip()
    if name in LEAGUE_PRIORITIES:
        return LEAGUE_PRIORITIES[name]
    low = name.lower()
    for k, v in LEAGUE_PRIORITIES.items():
        if k.lower() == low:
            return v
    ctry = (country or "").strip().lower()
    # 2-я лига Бельгии и аналоги — всегда 99 (до эвристик по «pro league»)
    if "challenger pro" in low or "challenger league" in low:
        return 99
    # По стране + короткому названию (API часто шлёт «Super League», «Pro League», «Superliga»)
    if ctry in ("switzerland", "швейцария") and ("super league" in low or low in ("super league", "ssl")):
        return 5
    if ctry in ("belgium", "бельгия") and "challenger" not in low and (
        "jupiler" in low or low in ("pro league", "first division a", "belgian pro league", "belgium pro league")
        or (low == "pro league") or (low.endswith(" pro league") and "challenger" not in low)
    ):
        return 5
    if ctry in ("romania", "румыния") and ("liga" in low or "superliga" in low or "super liga" in low):
        return 5
    # Африканская ЛЧ / CAF — не ранг 2
    if any(m in low for m in _AFRICA_CUP_MARKERS):
        return 99
    # Явные UEFA-кубки → 2
    for frag in _EURO_CUP_PRIORITY_2:
        if frag in low:
            return 2
    # «Champions League» без Africa/CAF → 2 (Европа)
    if any(frag in low for frag in _CL_GENERIC):
        if not any(m in low for m in _AFRICA_CUP_MARKERS):
            return 2
    if "europa league" in low or "conference league" in low:
        return 2
    # эвристики по фрагментам названия
    if any(f in low for f in ("usl championship", "usa championship", "usl league", "challenger pro")):
        return 99
    if any(f in low for f in (
        "championship", "league one", "league two", "efl league",
        "2. bundesliga", "2 bundesliga", "segunda", "ligue 2",
        "liga portugal 2", "segunda liga", "serie b", "série b", "fnl",
    )):
        return 8
    if any(f in low for f in (
        "npfl", "nigeria", "betplay", "primera a", "botola",
        "j1 league", "j.league", "k league", "k-league", "girabola",
        "veikkausliiga", "super league greece", "chinese super",
    )):
        return 7
    if any(f in low for f in (
        "liga mx", "saudi pro", "efbet", "bulgarian", "parva liga",
        "austrian bundesliga", "czech first", "fortuna liga", "a-league",
    )):
        return 6
    if any(f in low for f in (
        "scottish premiership", "cinch premiership", "swiss super",
        "jupiler", "belgian pro", "belgium pro",
        "superliga romania", "liga i", "romanian liga",
        "allsvenskan", "eliteserien", "danish superliga",
    )):
        return 5
    if any(f in low for f in (
        "brasileir", "brasileirao", "liga profesional", "major league soccer",
        "süper lig", "super lig", "primeira liga", "liga portugal",
    )):
        return 4
    # Ранг 9 — национальные / клубные кубки стран (не UEFA CL/EL)
    domestic_cup = (
        "fa cup", "efl cup", "league cup", "carabao", "dfb pokal", "pokal",
        "coupe de france", "coupe de la ligue", "coppa italia", "copa del rey",
        "copa del rey", "taça de portugal", "taca de portugal", "knvb",
        "scottish cup", "belgian cup", "croky cup", "swiss cup",
        "turkish cup", "turkish cup", "russian cup", "кубок россии",
        "кубок ", " кубок", "national cup", "domestic cup",
        "supercup", "super cup", "суперкубок", "community shield",
        "copa argentina", "copa do brasil", "us open cup",
    )
    # не трогаем уже обработанные UEFA / Africa
    if any(k in low for k in domestic_cup) or (
        ("cup" in low or "кубок" in low or "copa" in low or "coupe" in low or "pokal" in low or "taça" in low or "taca" in low)
        and "champions" not in low
        and "europa" not in low
        and "conference" not in low
        and "uefa" not in low
        and not any(m in low for m in _AFRICA_CUP_MARKERS)
    ):
        return 9
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
        priority = get_league_priority(league_name, country_en)
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
    to_enrich = []  # list of (match_id, name, match_time, event_id, home_tid, away_tid, league_id, logo_home, logo_away, league_logo)

    def _quick_insert(row_full):
        """Insert match without waiting for BSD enrichment."""
        nonlocal count, errors
        name, country, league, match_time, logo_home, logo_away = (
            row_full[0], row_full[1], row_full[2], row_full[3], row_full[4], row_full[5]
        )
        event_id = row_full[6] if len(row_full) > 6 else None
        home_tid = row_full[7] if len(row_full) > 7 else None
        away_tid = row_full[8] if len(row_full) > 8 else None
        league_id = row_full[9] if len(row_full) > 9 else None
        league_logo = f"https://sports.bzzoiro.com/img/league/{league_id}/" if league_id else ""

        existing = db_fetchone("SELECT id FROM matches WHERE name = ? AND league = ?", (name, league))
        match_id = existing[0] if existing else None
        if not match_id:
            try:
                db_execute(
                    "INSERT INTO matches (name, country, league, match_time, text, created_at, logo_home, logo_away, event_id, home_team_id, away_team_id, stats_json, league_id, league_logo) "
                    "VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, '', ?, ?)",
                    (name, country, league, match_time or "", now, logo_home or "", logo_away or "",
                     event_id, home_tid, away_tid, league_id, league_logo)
                )
                row_id = db_fetchone(
                    "SELECT id FROM matches WHERE name = ? AND league = ? ORDER BY id DESC LIMIT 1",
                    (name, league),
                )
                match_id = row_id[0] if row_id else None
                count += 1
            except Exception as e:
                logger.warning("[parser insert] %s: %s", name, e)
                errors += 1
                return
        else:
            count += 1
        if match_id and BSD_API_KEY:
            to_enrich.append((
                match_id, name, match_time or "", event_id, home_tid, away_tid,
                league_id, logo_home or "", logo_away or "", league_logo,
            ))

    select_cols = (
        "name, country, league, match_time, COALESCE(logo_home,''), COALESCE(logo_away,''), "
        "event_id, home_team_id, away_team_id, league_id"
    )

    if mode == "single":
        parsed_id = data.get("parsed_id")
        row = db_fetchone(f"SELECT {select_cols} FROM parsed_matches WHERE id = ?", (parsed_id,))
        if not row:
            raise HTTPException(404, "Матч не найден")
        _quick_insert(row)
        log_admin(user["id"], "parser_add_match", row[0])
    elif mode == "league":
        league_id = data.get("league_id")
        rows = db_fetchall(f"SELECT {select_cols} FROM parsed_matches WHERE league_id = ?", (league_id,))
        for r in rows:
            _quick_insert(r)
        league_row = db_fetchone("SELECT league FROM parsed_matches WHERE league_id = ?", (league_id,))
        log_admin(user["id"], "parser_add_league", f"{league_row[0] if league_row else ''} ({count})")
    elif mode == "priority":
        # Пакет по рангам: priority=3 или priorities=[1,2,3] или from_rank/to_rank
        ranks = data.get("priorities") or data.get("ranks") or []
        if not ranks:
            one = data.get("priority") or data.get("rank")
            if one is not None:
                ranks = [int(one)]
            else:
                fr = data.get("from_rank", data.get("from_priority"))
                to = data.get("to_rank", data.get("to_priority"))
                if fr is not None and to is not None:
                    ranks = list(range(int(fr), int(to) + 1))
        ranks = sorted({int(x) for x in ranks if str(x).isdigit() or isinstance(x, int)})
        if not ranks:
            raise HTTPException(400, "Укажите rank / priorities / from_rank–to_rank")
        placeholders = ",".join("?" * len(ranks))
        rows = db_fetchall(
            f"SELECT {select_cols} FROM parsed_matches WHERE priority IN ({placeholders}) ORDER BY priority ASC, match_time ASC",
            tuple(ranks),
        )
        for r in rows:
            _quick_insert(r)
        log_admin(user["id"], "parser_add_priority", f"ranks={ranks} ({count})")
    else:
        limit = int(data.get("limit", 80))
        rows = db_fetchall(
            f"SELECT {select_cols} FROM parsed_matches ORDER BY priority ASC LIMIT ?",
            (limit,),
        )
        for r in rows:
            _quick_insert(r)
        log_admin(user["id"], "parser_add_all", f"{count} матчей")

    # Enrich in parallel (max 3 matches at once) — much faster than sequential
    if to_enrich:
        timeout = aiohttp.ClientTimeout(total=None, sock_connect=12, sock_read=35)
        enrich_sem = asyncio.Semaphore(3)

        async def _enrich_one(session, item):
            nonlocal errors
            match_id, name, match_time, event_id, home_tid, away_tid, league_id, logo_home, logo_away, league_logo = item
            async with enrich_sem:
                try:
                    if not event_id:
                        eid, hid, aid = await resolve_event_id(session, name, match_time, league_id)
                        if eid:
                            event_id = eid
                            home_tid = home_tid or hid
                            away_tid = away_tid or aid
                    stats_json = ""
                    if event_id:
                        pack = await fetch_match_enrichment(
                            session, int(event_id), home_tid, away_tid, league_id
                        )
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
                             logo_home, logo_home, logo_away, logo_away,
                             league_id, league_logo, league_logo, match_id)
                        )
                except Exception as e:
                    logger.warning("[parser enrich] %s: %s", name, e)
                    errors += 1

        async with aiohttp.ClientSession(timeout=timeout) as session:
            await asyncio.gather(*[_enrich_one(session, item) for item in to_enrich])

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

# ====================== TELEGRAM BOT WEBHOOK (рефералка /start) ======================

async def _tg_api(method: str, payload: dict):
    if not BOT_TOKEN:
        logger.warning("tg_api %s: BOT_TOKEN пуст", method)
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = await resp.json(content_type=None)
                if not data.get("ok"):
                    logger.warning("tg_api %s not ok: %s", method, data)
                return data
    except Exception as e:
        logger.warning("tg_api %s failed: %s", method, e)
        return None


async def _handle_bot_message(message: dict):
    """Ответ на /start и простые команды. Используется webhook + long polling."""
    if not message:
        return
    text = (message.get("text") or "").strip()
    chat = message.get("chat") or {}
    from_user = message.get("from") or {}
    chat_id = chat.get("id") or from_user.get("id")
    if not chat_id:
        return

    if not text.startswith("/start"):
        # любое другое сообщение — подсказка
        if text:
            await _tg_api("sendMessage", {
                "chat_id": chat_id,
                "text": "Нажмите /start чтобы открыть приложение.",
            })
        return

    parts = text.split(maxsplit=1)
    start_arg = parts[1].strip() if len(parts) > 1 else ""
    uid = int(from_user.get("id") or chat_id)
    first_name = from_user.get("first_name") or ""
    username = from_user.get("username") or ""
    get_or_create_user(uid, first_name, username, "")
    if start_arg:
        apply_referral(uid, start_arg)

    public = (os.getenv("PUBLIC_URL") or "").rstrip("/")
    mini_short = (os.getenv("MINI_APP_SHORT_NAME") or "").strip()
    if public:
        reply_markup = {
            "inline_keyboard": [[{
                "text": "⚽ Открыть приложение",
                "web_app": {"url": public},
            }]]
        }
    else:
        if mini_short:
            app_url = f"https://t.me/{BOT_USERNAME}/{mini_short}"
        else:
            app_url = f"https://t.me/{BOT_USERNAME}"
        app_url += f"?startapp={start_arg}" if start_arg else "?startapp"
        reply_markup = {
            "inline_keyboard": [[{
                "text": "⚽ Открыть приложение",
                "url": app_url,
            }]]
        }
    caption = (
        "👋 Добро пожаловать в КОЭФФИЦИЕНТ!\n\n"
        "📡 Доступ к различной и большой статистике матча, а также уведомления о сигналах импульса!\n"
        "👇 Переходи ниже по кнопке в приложение."
    )
    # картинка логотипа (уменьшенная) — если файл есть
    # фото выше текста приветствия
    _assets = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "assets"))
    logo_path = None
    for name in ("koef-welcome-bot.jpg", "koef-welcome.jpg", "koef-logo-bot.png", "koef-logo.png"):
        cand = os.path.join(_assets, name)
        if os.path.isfile(cand):
            logo_path = cand
            break
    resp = None
    if logo_path:
        try:
            import httpx
            token = BOT_TOKEN
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            ext = os.path.splitext(logo_path)[1].lower()
            mime = "image/jpeg" if ext in (".jpg", ".jpeg") else "image/png"
            fname = os.path.basename(logo_path)
            with open(logo_path, "rb") as f:
                files = {"photo": (fname, f, mime)}
                data = {
                    "chat_id": str(chat_id),
                    "caption": caption,
                    "reply_markup": json.dumps(reply_markup, ensure_ascii=False),
                }
                async with httpx.AsyncClient(timeout=30.0) as client:
                    r = await client.post(url, data=data, files=files)
                    resp = r.json() if r.status_code == 200 else None
                    if not resp or not resp.get("ok"):
                        logger.warning("[tg] sendPhoto bad resp: %s", resp)
        except Exception as e:
            logger.warning("[tg] sendPhoto failed: %s", e)
            resp = None
    if not resp or not resp.get("ok"):
        resp = await _tg_api("sendMessage", {
            "chat_id": chat_id,
            "text": caption,
            "reply_markup": reply_markup,
        })
    if not resp or not resp.get("ok"):
        logger.warning("[tg] /start reply failed chat_id=%s: %s", chat_id, resp)
    else:
        logger.info("[tg] /start ok chat_id=%s", chat_id)


async def _ensure_telegram_bot():
    """
    Webhook если есть PUBLIC_URL; иначе long polling (getUpdates).
    Так бот отвечает на /start даже без PUBLIC_URL / при сбое webhook.
    """
    if not BOT_TOKEN:
        logger.warning("[tg] BOT_TOKEN пуст — бот не запущен")
        return

    public = (os.getenv("PUBLIC_URL") or "").rstrip("/")
    use_webhook = False
    if public and public.startswith("https://"):
        webhook_url = f"{public}/api/telegram/webhook"
        try:
            res = await _tg_api("setWebhook", {
                "url": webhook_url,
                "allowed_updates": ["message"],
                "drop_pending_updates": True,
            })
            if res and res.get("ok"):
                logger.info("[tg] webhook OK: %s", webhook_url)
                use_webhook = True
            else:
                logger.warning("[tg] setWebhook failed: %s — fallback polling", res)
        except Exception:
            logger.exception("[tg] setWebhook error — fallback polling")
    else:
        logger.info("[tg] PUBLIC_URL не задан или не https — long polling")

    if use_webhook:
        # проверяем, что Telegram видит webhook
        info = await _tg_api("getWebhookInfo", {})
        result = (info or {}).get("result") or {}
        logger.info("[tg] webhook info url=%s pending=%s last_error=%s",
                    result.get("url"), result.get("pending_update_count"),
                    result.get("last_error_message"))
        return

    # --- long polling ---
    await _tg_api("deleteWebhook", {"drop_pending_updates": False})
    logger.info("[tg] long polling started")
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            params = {"timeout": 25, "offset": offset, "allowed_updates": '["message"]'}
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=35)) as resp:
                    data = await resp.json(content_type=None)
            if not data.get("ok"):
                logger.warning("[tg] getUpdates not ok: %s", data)
                await asyncio.sleep(3)
                continue
            for upd in data.get("result") or []:
                offset = max(offset, int(upd.get("update_id", 0)) + 1)
                msg = upd.get("message") or upd.get("edited_message")
                if msg:
                    try:
                        await _handle_bot_message(msg)
                    except Exception:
                        logger.exception("[tg] handle message")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[tg] polling error: %s", e)
            await asyncio.sleep(3)


@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request):
    """Webhook Telegram. Также работает long polling, если webhook не поднялся."""
    try:
        data = await request.json()
    except Exception:
        return {"ok": True}
    message = data.get("message") or data.get("edited_message") or {}
    try:
        await _handle_bot_message(message)
    except Exception:
        logger.exception("[tg] webhook handle")
    return {"ok": True}


@app.post("/api/admin/bot-test")
async def admin_bot_test(user: dict = Depends(require_admin)):
    """Проверка: отправить тестовое сообщение админу + статус webhook."""
    info = await _tg_api("getWebhookInfo", {})
    result = (info or {}).get("result") or {}
    send = None
    if ADMIN_ID:
        send = await _tg_api("sendMessage", {
            "chat_id": int(ADMIN_ID),
            "text": "✅ Тест бота КОЭФФИЦИЕНТ: сообщения работают.",
        })
    return {
        "ok": True,
        "bot_token_set": bool(BOT_TOKEN),
        "public_url": (os.getenv("PUBLIC_URL") or "") or None,
        "webhook_url": result.get("url"),
        "pending": result.get("pending_update_count"),
        "last_error": result.get("last_error_message"),
        "test_send_ok": bool(send and send.get("ok")),
        "test_send": send,
    }


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
