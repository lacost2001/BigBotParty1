import discord
from discord.ext import commands
from discord import app_commands, ui
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
import asyncio
import sys
import socket
import aiohttp
import threading
import time
from typing import Optional

# ==== Интеграция модуля recruit_bot (рекрут/очки/магазин) ====
try:
    from ..recruit_bot.ui_components import PersistentEventSubmitView, UnifiedEventView
    from ..recruit_bot import bot as recruit_bot_module
    from ..recruit_bot.bot import RecruitCog, init_db as recruit_init_db, PersistentApplyButtonView
    RECRUIT_AVAILABLE = True
except Exception as _recruit_err:
    RECRUIT_AVAILABLE = False

# Пути к файлам (относительно папки проекта)
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
DB_FILE = os.path.join(SCRIPT_DIR, "events.db")
SESSIONS_FILE = os.path.join(SCRIPT_DIR, "sessions.json")
STATS_FILE = os.path.join(SCRIPT_DIR, "party_stats.json")
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "settings.json")

# Load config
with open(CONFIG_FILE, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

# Поддержка разных названий ключей в config.json
BOT_TOKEN = CONFIG.get("BOT_TOKEN") or CONFIG.get("bot_token")
if not BOT_TOKEN:
    print("❌ Ошибка: BOT_TOKEN не найден в config.json")
    sys.exit(1)

# Setup DB
conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    guild_id INTEGER,
    channel_id INTEGER,
    main_msg_id INTEGER,
    thread_id INTEGER,
    title TEXT,
    description TEXT,
    time TEXT,
    party_roles TEXT,
    creator_id INTEGER,
    stopped INTEGER DEFAULT 0
)
""")
conn.commit()
