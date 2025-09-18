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
from urllib.parse import quote

# ==== Интеграция модуля recruit_bot (рекрут/очки/магазин) ====
try:
    from recruit_bot.ui_components import PersistentEventSubmitView, UnifiedEventView
    from recruit_bot import bot as recruit_bot_module
    from recruit_bot.bot import RecruitCog, init_db as recruit_init_db, PersistentApplyButtonView, PersistentPointsRequestView, ApplyModal
    from unified_settings import unified_settings as _unified_settings
    RECRUIT_AVAILABLE = True
except ImportError as _recruit_err:
    print(f"Recruit modules not available: {_recruit_err}")
    RECRUIT_AVAILABLE = False

"""Единый импорт системы настроек.
Главная логика дефолтов теперь в web.get_complete_guild_settings.
Здесь оставляем тонкие обёртки для совместимости бота.
"""
try:
    # Абсолютный импорт через пакет Bigbot.party_bot.* чтобы работать при запуске bot_main.py
    from party_bot.simple_settings_db import (
        get_settings_db as _get_settings_db,
        get_guild_setting as _db_get_setting,
        set_guild_setting as _db_set_setting,
        get_guild_settings as _db_get_settings
    )
    USING_DATABASE = True
    USING_FAST_DB = True
    print("✅ Настройки: быстрый simple_settings_db")
except ImportError as e_simple:
    print(f"⚠️ simple_settings_db недоступен: {e_simple}")
    try:
        from party_bot.settings_db import (
            get_guild_settings as _db_get_settings,
            set_guild_setting as _db_set_setting,
            get_guild_setting as _db_get_setting
        )
        USING_DATABASE = True
        USING_FAST_DB = False
        print("✅ Настройки: settings_db (fallback)")
    except ImportError as e_legacy:
        print(f"❌ Нет БД настроек: {e_legacy} -> fallback к файлу settings.json")
        USING_DATABASE = False
        USING_FAST_DB = False

# Обёртки (API бота остаётся прежним)
def get_guild_settings(guild_id: int):
    if USING_DATABASE:
        return _db_get_settings(guild_id)
    # Файловый fallback ниже использует глобальный SETTINGS
    guild_id_str = str(guild_id)
    if "guilds" not in SETTINGS:
        SETTINGS["guilds"] = {}
    return SETTINGS["guilds"].setdefault(guild_id_str, {})

def set_guild_setting(guild_id: int, key: str, value):
    if USING_DATABASE:
        return _db_set_setting(guild_id, key, value)
    guild_id_str = str(guild_id)
    if "guilds" not in SETTINGS:
        SETTINGS["guilds"] = {}
    SETTINGS["guilds"].setdefault(guild_id_str, {})[key] = value
    save_all_data()

def get_guild_setting(guild_id: int, key: str, default=None):
    if USING_DATABASE:
        return _db_get_setting(guild_id, key, default)
    return get_guild_settings(guild_id).get(key, default)

# === Оценка состояния настройки сервера ===
REQUIRED_BASE_KEYS = [
    'event_creator_role', 'moderator_role', 'ping_role'
]
REQUIRED_RECRUIT_KEYS = [
    'default_role', 'recruit_role', 'recruit_panel_channel'
]

def evaluate_guild_setup(guild_id: int):
    """Возвращает dict c состоянием настройки сервера.
    status: 'missing' (ничего нет), 'partial', 'complete'
    missing: список недостающих логических полей
    """
    settings = get_guild_settings(guild_id) or {}
    recruit = settings.get('recruit_settings') or {}
    missing = []
    for k in REQUIRED_BASE_KEYS:
        if settings.get(k) in (None, '', [], {}):
            missing.append(k)
    for k in REQUIRED_RECRUIT_KEYS:
        if recruit.get(k) in (None, '', [], {}):
            missing.append(f"recruit:{k}")
    if len(missing) == len(REQUIRED_BASE_KEYS) + len(REQUIRED_RECRUIT_KEYS):
        status = 'missing'
    elif missing:
        status = 'partial'
    else:
        status = 'complete'
    return {'status': status, 'missing': missing, 'settings': settings}

# Словарь для отслеживания времени последней отправки сообщений настройки
last_setup_message_time = {}

async def send_setup_message(guild: discord.Guild, channel: Optional[discord.TextChannel] = None, force: bool=False):
    """Отправка embed с инструкцией настройки.
    Если force=True отправляем независимо от статуса.
    """
    try:
        # Проверяем, не отправляли ли мы сообщение недавно (предотвращение спама)
        now = datetime.now()
        last_sent = last_setup_message_time.get(guild.id)
        if not force and last_sent and (now - last_sent).total_seconds() < 300:  # 5 минут
            return
        
        state = evaluate_guild_setup(guild.id)
        if not force and state['status'] == 'complete':
            return
        # Выбираем канал: заданный, либо системный, либо первый текстовый
        if channel is None:
            channel = guild.system_channel
            if channel is None:
                for ch in guild.text_channels:
                    if ch.permissions_for(guild.me).send_messages:
                        channel = ch
                        break
        if channel is None:
            return
        status = state['status']
        missing = state['missing']
        url = f"{WEB_BASE_URL}/guild/{guild.id}"
        title_map = {
            'missing': '🚀 Добро пожаловать! Требуется настройка бота',
            'partial': '⚠️ Настройка не завершена — нужно дополнить параметры',
            'complete': '✅ Бот настроен и готов к работе'
        }
        color_map = {
            'missing': 0xE74C3C,  # Красный
            'partial': 0xF39C12,  # Оранжевый
            'complete': 0x2ECC71  # Зеленый
        }
        desc_lines = []
        if status == 'missing':
            desc_lines.append('🔧 **Что нужно настроить:**')
            desc_lines.append('• Роли (администратор, модератор, пинг-роль)')
            desc_lines.append('• Каналы для событий и рекрутинга')
            desc_lines.append('• Настройки рекрутинга и очков')
            desc_lines.append('')
            desc_lines.append('💡 **Зачем это нужно:**')
            desc_lines.append('• Управление мероприятиями и событиями')
            desc_lines.append('• Система рекрутинга новых участников')
            desc_lines.append('• Начисление и управление очками за активность')
            desc_lines.append('• Магазин с наградами за очки')
        elif status == 'partial':
            desc_lines.append('🔧 **Осталось настроить:**')
            for m in missing[:8]:
                desc_lines.append(f"• {m}")
            if len(missing) > 8:
                desc_lines.append(f"• … и ещё {len(missing)-8} параметров")
        else:
            desc_lines.append('🎉 Все ключевые параметры настроены!')
            desc_lines.append('📝 Изменения и дополнительные настройки доступны через веб-интерфейс.')
        
        desc_lines.append('')
        desc_lines.append(f"🌐 **[Открыть панель настроек]({url})**")
        
        embed = discord.Embed(
            title=title_map[status], 
            description='\n'.join(desc_lines), 
            color=color_map[status]
        )
        embed.add_field(
            name="📋 Доступные функции после настройки",
            value="• 🎉 Организация мероприятий\n• 👥 Система рекрутинга\n• 🏆 Очки за активность\n• 🛒 Магазин наград",
            inline=False
        )
        embed.set_footer(text=f"Bot Setup • Сервер: {guild.name}")
        await channel.send(embed=embed)
        
        # Сохраняем время отправки
        last_setup_message_time[guild.id] = now
        
    except Exception as e:
        print(f"[SETUP NOTICE] Ошибка отправки сообщения: {e}")

# Пути к файлам (относительно папки проекта)
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
DB_FILE = os.path.join(SCRIPT_DIR, "events.db")
SESSIONS_FILE = os.path.join(SCRIPT_DIR, "sessions.json")
STATS_FILE = os.path.join(SCRIPT_DIR, "party_stats.json")
SETTINGS_FILE = os.path.join(SCRIPT_DIR, "settings.json")

# Функция для загрузки конфигурации с поддержкой .env и config.json
def load_config():
    """Загружает конфигурацию из переменных окружения или config.json"""
    config = {}
    
    # Сначала пытаемся загрузить из config.json (если существует)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                config = json.load(f)
            print("✅ Конфигурация загружена из config.json")
        except Exception as e:
            print(f"⚠️ Ошибка чтения config.json: {e}")
    
    # Переменные окружения имеют приоритет над config.json
    env_vars = {
        'BOT_TOKEN': os.getenv('BOT_TOKEN'),
        'WEB_BASE_URL': os.getenv('WEB_BASE_URL'),
        'DISCORD_CLIENT_ID': os.getenv('DISCORD_CLIENT_ID'),
        'DISCORD_CLIENT_SECRET': os.getenv('DISCORD_CLIENT_SECRET'),
        'DISCORD_REDIRECT_URI': os.getenv('DISCORD_REDIRECT_URI'),
        'FLASK_SECRET_KEY': os.getenv('FLASK_SECRET_KEY'),
        'ADMIN_USERS': os.getenv('ADMIN_USERS'),
        'DEBUG': os.getenv('DEBUG'),
        'PORT': os.getenv('PORT')
    }
    
    # Обновляем конфигурацию переменными окружения (если они установлены)
    for key, value in env_vars.items():
        if value is not None:
            config[key] = value
    
    return config

# Load config
CONFIG = load_config()

# Поддержка разных названий ключей в config.json и обязательная проверка BOT_TOKEN
BOT_TOKEN = CONFIG.get("BOT_TOKEN") or CONFIG.get("bot_token") or os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ Ошибка: BOT_TOKEN не найден!")
    print("   Установите переменную окружения BOT_TOKEN или добавьте в config.json")
    print("   Пример: export BOT_TOKEN=your_token_here")
    sys.exit(1)

# Настройка URL веб-интерфейса
WEB_BASE_URL = CONFIG.get('WEB_BASE_URL', 'http://localhost:8082')

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

ALL_SESSIONS = {}
raw_stats = {}
SETTINGS = {"guilds": {}}

# Загружаем только служебные файлы (sessions, stats). settings.json будем мигрировать один раз.
for file, default in [(SESSIONS_FILE, {}), (STATS_FILE, {})]:
    if not os.path.exists(file):
        with open(file, "w", encoding="utf-8") as f:
            json.dump(default, f)
try:
    with open(SESSIONS_FILE, "r", encoding="utf-8") as f:
        ALL_SESSIONS = json.load(f)
except Exception:
    ALL_SESSIONS = {}
try:
    with open(STATS_FILE, "r", encoding="utf-8") as f:
        raw_stats = json.load(f)
except Exception:
    raw_stats = {}

def _migrate_settings_json_to_db():
    """Одноразовая миграция settings.json -> simple_settings_db.
    Переносим только если используется база и существует файл с данными.
    """
    if not USING_DATABASE:
        return
    if not os.path.exists(SETTINGS_FILE):
        return
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    if not isinstance(data, dict):
        return
    guilds = data.get("guilds") or {}
    if not isinstance(guilds, dict) or not guilds:
        return
    print(f"🛠️ Миграция settings.json -> settings.db (guilds: {len(guilds)})")
    for gid, gsettings in guilds.items():
        try:
            gid_int = int(gid)
        except Exception:
            continue
        if not isinstance(gsettings, dict):
            continue
        # Пакетная запись через внутренний db API (если доступен)
        try:
            if USING_FAST_DB:
                db = _get_settings_db()
                db.batch_set_settings(gid_int, gsettings)
            else:
                # медленная по ключу
                for k, v in gsettings.items():
                    set_guild_setting(gid_int, k, v)
        except Exception as mig_err:
            print(f"⚠️ Ошибка миграции guild {gid}: {mig_err}")
    # Переименуем файл чтобы не мигрировать снова
    try:
        os.rename(SETTINGS_FILE, SETTINGS_FILE + ".migrated")
        print("✅ Миграция завершена, исходный settings.json переименован")
    except Exception:
        pass

_migrate_settings_json_to_db()

PARTY_STATS = {int(uid): set(events) for uid, events in raw_stats.items()}

def reload_settings_from_disk():
    # При использовании БД больше не поддерживаем live‑reload JSON настроек
    if USING_DATABASE:
        return
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            SETTINGS.update(loaded)
    except Exception:
        pass

def _deep_merge_dicts(base: dict, incoming: dict) -> dict:
    """Глубокое слияние словарей. Значения из incoming имеют приоритет.
    Не модифицирует исходники, возвращает новый словарь."""
    result = dict(base or {})
    for k, v in (incoming or {}).items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge_dicts(result[k], v)
        else:
            result[k] = v
    return result

def save_all_data():
    # Настройки сами сохраняются в БД; здесь сохраняем только служебные структуры
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(ALL_SESSIONS, f, indent=2, ensure_ascii=False)
    with open(STATS_FILE, "w", encoding="utf-8") as f:
        json.dump({str(uid): list(events) for uid, events in PARTY_STATS.items()}, f, indent=2, ensure_ascii=False)


def get_guild_templates(guild_id: int) -> dict:
    """Получить шаблоны для конкретного сервера из отдельного файла"""
    templates_file = f"templates_data/guild_{guild_id}_templates.json"
    
    if not os.path.exists("templates_data"):
        os.makedirs("templates_data")
    
    if os.path.exists(templates_file):
        try:
            with open(templates_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"Ошибка загрузки шаблонов для гильдии {guild_id}: {e}")
            return {}
    return {}

def save_guild_templates(guild_id: int, templates: dict):
    """Сохранить шаблоны для конкретного сервера"""
    if not os.path.exists("templates_data"):
        os.makedirs("templates_data")
    
    templates_file = f"templates_data/guild_{guild_id}_templates.json"
    try:
        with open(templates_file, "w", encoding="utf-8") as f:
            json.dump(templates, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Ошибка сохранения шаблонов для гильдии {guild_id}: {e}")

def set_guild_template(guild_id: int, template_name: str, template_data: dict):
    """Установить шаблон для конкретного сервера"""
    templates = get_guild_templates(guild_id)
    templates[template_name] = template_data
    save_guild_templates(guild_id, templates)

def delete_guild_template(guild_id: int, template_name: str):
    """Удалить шаблон для конкретного сервера"""
    templates = get_guild_templates(guild_id)
    if template_name in templates:
        del templates[template_name]
        save_guild_templates(guild_id, templates)
        return True
    return False

def get_guild_template(guild_id: int, template_name: str):
    """Получить конкретный шаблон для сервера"""
    templates = get_guild_templates(guild_id)
    return templates.get(template_name)
    return False

def get_guild_template(guild_id: int, template_name: str):
    """Получить конкретный шаблон для сервера"""
    guild_templates = get_guild_templates(guild_id)
    return guild_templates.get(template_name)

def save_event(event_id: int, data: dict):
    cursor.execute("""
        INSERT OR REPLACE INTO events (id, guild_id, channel_id, main_msg_id, thread_id, title, description, time, party_roles, creator_id, stopped)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        event_id,
        data["guild_id"],
        data["channel_id"],
        data["main_msg_id"],
        data["thread_id"],
        data["title"],
        data["description"],
        data.get("time", ""),
        json.dumps(data["party_roles"], ensure_ascii=False),
        data["creator_id"],
        int(data.get("stopped", False))
    ))
    conn.commit()

def load_events_from_db():
    cursor.execute("SELECT * FROM events")
    rows = cursor.fetchall()
    events = {}
    for row in rows:
        event_id = row[0]
        events[event_id] = {
            "guild_id": row[1],
            "channel_id": row[2],
            "main_msg_id": row[3],
            "thread_id": row[4],
            "title": row[5],
            "description": row[6],
            "time": row[7],
            "party_roles": json.loads(row[8]),
            "creator_id": row[9],
            "stopped": bool(row[10]),
        }
    return events

ALL_EVENTS = load_events_from_db()
ALL_SESSIONS = {str(k): v for k, v in ALL_EVENTS.items()}

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="/", intents=intents)

# Устанавливаем экземпляр бота для веб-интерфейса
try:
    from party_bot.web import set_bot_instance
    set_bot_instance(bot)
except ImportError:
    pass  # Веб-модуль может быть недоступен

# Группы команд - убираем для замены на команды с подкомандами
# settings_group = app_commands.Group(name="settings", description="Настройки бота для сервера")
# templates_group = app_commands.Group(name="templates", description="Управление шаблонами ивентов") 
# events_group = app_commands.Group(name="events", description="Управление ивентами")

# --- Views and UI elements ---

class PartySelectView(ui.View):
    def __init__(self, session_id, user_id):
        super().__init__(timeout=None)
        self.session_id = session_id
        self.user_id = user_id
        self.custom_id = f"party_select_{session_id}"

        session = ALL_SESSIONS.get(str(session_id))
        if not session:
            return

        # Добавляем селекторы для записи (разделяем на группы по 25)
        if not session.get("stopped"):
            roles = session["party_roles"]
            
            # Разделяем роли на группы по 25 (лимит Discord)
            for group_index in range(0, len(roles), 25):
                group_roles = roles[group_index:group_index + 25]
                options = []
                
                for i, role in enumerate(group_roles):
                    actual_index = group_index + i
                    user = role.get("user_id")
                    label = f"{role['name']}"
                    if user:
                        label += f" (Занято: <@{user}>)"
                    options.append(discord.SelectOption(
                        label=label[:100],  # Discord ограничивает длину метки
                        value=str(actual_index)
                    ))
                
                # Создаем селектор для каждой группы
                group_number = (group_index // 25) + 1
                total_groups = (len(roles) + 24) // 25  # Округляем вверх
                
                if total_groups > 1:
                    placeholder = f"Роли {group_index + 1}-{min(group_index + 25, len(roles))} (стр. {group_number}/{total_groups})"
                else:
                    placeholder = "Выберите роль для записи"
                
                self.add_item(PartySignupSelect(options, session_id, user_id, placeholder))
            
            self.add_item(PartyUnsubscribeButton(session_id, user_id))

        # Добавляем кнопки управления (только для создателя)
        if session.get("creator_id") == user_id:
            self.add_item(EditButton(session_id))
            self.add_item(StopEventButton(session_id))
            self.add_item(RemindButton(session_id))

        # Кнопки быстрых действий (только для администраторов и создателя)
        # Проверка будет выполняться в самих кнопках для получения актуальной информации
        self.add_item(PartyCheckButton(session_id))
        self.add_item(CloneButton(session_id))
        self.add_item(RefreshButton(session_id))

class PartySignupSelect(ui.Select):
    def __init__(self, options, session_id, user_id, placeholder="Выберите роль для записи"):
        super().__init__(
            placeholder=placeholder, 
            options=options, 
            max_values=1,
            custom_id=f"signup_select_{session_id}_{len(options)}"  # Добавляем уникальность
        )
        self.session_id = session_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        session = ALL_SESSIONS.get(str(self.session_id))
        if not session:
            await interaction.response.send_message("Ошибка: сессия не найдена.", ephemeral=True)
            return
        roles = session["party_roles"]
        user_id = interaction.user.id

        if session.get("stopped"):
            await interaction.response.send_message("❌ Ивент остановлен, запись закрыта.", ephemeral=True)
            return

        index = int(self.values[0])
        selected_role = roles[index]

        if selected_role.get("user_id") and selected_role["user_id"] != user_id:
            await interaction.response.send_message("❌ Этот слот уже занят другим участником.", ephemeral=True)
            return

        if selected_role.get("user_id") == user_id:
            await interaction.response.send_message("❗ Вы уже записаны на этот слот.", ephemeral=True)
            return

        # Снять запись только с других слотов пользователя, если есть
        for i, r in enumerate(roles):
            if r.get("user_id") == user_id and i != index:
                r["user_id"] = None

        selected_role["user_id"] = user_id

        register_signup(user_id, self.session_id)
        ALL_SESSIONS[str(self.session_id)] = session
        save_all_data()
        save_event(int(self.session_id), session)
        await interaction.response.defer()
        await update_party_message(self.session_id, interacting_user_id=user_id)
        await interaction.followup.send("✅ Вы успешно записались!", ephemeral=True)

class PartyUnsubscribeButton(ui.Button):
    def __init__(self, session_id, user_id):
        super().__init__(
            label="🚪 Выписаться", 
            style=discord.ButtonStyle.danger,
            custom_id=f"unsubscribe_button_{session_id}"  # Добавляем custom_id
        )
        self.session_id = session_id

    async def callback(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        session = ALL_SESSIONS.get(str(self.session_id))
        if not session:
            await interaction.response.defer()
            await update_party_message(self.session_id, interacting_user_id=user_id)
            await interaction.followup.send("❌ Ошибка: сессия не найдена.", ephemeral=True)
            return

        roles = session["party_roles"]
        found = False

        for role in roles:
            if role.get("user_id") == user_id:
                role["user_id"] = None
                found = True
                break

        if found:
            ALL_SESSIONS[str(self.session_id)] = session
            save_all_data()
            save_event(int(self.session_id), session)
            await update_party_message(self.session_id, interacting_user_id=user_id)
            await interaction.response.send_message("✅ Вы выписались со своего слота.", ephemeral=True)

        else:
            await interaction.response.send_message("❌ Вы не записаны ни на один слот, ничего не изменено.", ephemeral=True)
            

class PartyUnsubscribeSelect(ui.Select):
    def __init__(self, options, session_id, user_id):
        super().__init__(placeholder="Выберите участника для выписки", options=options, max_values=1)
        self.session_id = session_id
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        session = ALL_SESSIONS.get(str(self.session_id))
        if not session:
            await interaction.response.send_message("Ошибка: сессия не найдена.", ephemeral=True)
            return
        roles = session["party_roles"]
        index = int(self.values[0])
        if roles[index].get("user_id"):
            roles[index]["user_id"] = None
            ALL_SESSIONS[str(self.session_id)] = session
            save_all_data()
            save_event(int(self.session_id), session)
            await update_party_message(self.session_id, interacting_user_id=interaction.user.id)
            await interaction.response.send_message("✅ Участник выписан.", ephemeral=True)
        else:
            await interaction.response.send_message("Этот слот уже свободен.", ephemeral=True)

class CloneButton(ui.Button):
    def __init__(self, session_id):
        super().__init__(
            label="📄 Скопировать", 
            style=discord.ButtonStyle.gray,
            custom_id=f"clone_button_{session_id}"  # Добавляем custom_id
        )
        self.session_id = session_id

    async def callback(self, interaction: discord.Interaction):
        session = ALL_SESSIONS.get(str(self.session_id))
        if not session:
            await interaction.response.send_message("Ошибка: сессия не найдена.", ephemeral=True)
            return
        
        # Проверка прав (только создатель события или администратор)
        moderator_role_id = get_guild_setting(interaction.guild.id, "moderator_role")
        is_moderator = moderator_role_id and any(r.id == moderator_role_id for r in interaction.user.roles)
        is_creator = interaction.user.id == session["creator_id"]
        is_admin = interaction.user.guild_permissions.administrator
        
        if not (is_creator or is_moderator or is_admin):
            await interaction.response.send_message("❌ Только создатель события, администратор или модератор может клонировать события.", ephemeral=True)
            return
        
        role_list = [r["name"] for r in session["party_roles"]]
        embed = discord.Embed(
            title=session["title"] + " (копия)",
            description=f"Создал: <@{interaction.user.id}>\n\n{session['description']}",
            color=0x00ff00
        )
        if session.get("time"):
            embed.description += f"\n\n🕒 Время: {session['time']}"
        embed.add_field(name="Участники", value="\n".join([f"{i+1}. {r} - Свободно" for i, r in enumerate(role_list)]))
        msg = await interaction.channel.send(embed=embed)
        thread = await msg.create_thread(name=session["title"] + " (копия)")
        await thread.send("📌 Нажмите в меню, чтобы занять слот.\nИли нажмите кнопку \"🚪 Выписаться\".")
        new_session_id = msg.id
        
        # Используем настройки конкретного сервера
        event_creator_role_id = get_guild_setting(interaction.guild.id, "event_creator_role")
        moderator_role_id = get_guild_setting(interaction.guild.id, "moderator_role")
        creator_id = bot.user.id  # по умолчанию

        # Ищем первого пользователя с нужной ролью
        for member in interaction.guild.members:
            if event_creator_role_id and any(r.id == event_creator_role_id for r in member.roles):
                creator_id = member.id
                break
            if moderator_role_id and any(r.id == moderator_role_id for r in member.roles):
                creator_id = member.id
                break

        ALL_SESSIONS[str(new_session_id)] = {
            "guild_id": session["guild_id"],
            "channel_id": session["channel_id"],
            "main_msg_id": msg.id,
            "thread_id": thread.id,
            "title": session["title"] + " (копия)",
            "description": session["description"],
            "time": session.get("time", ""),
            "party_roles": [{"name": r, "user_id": None} for r in role_list],
            "creator_id": creator_id,
            "stopped": False,
            "last_reminder_time": 0
        }
        save_all_data()
        save_event(new_session_id, ALL_SESSIONS[str(new_session_id)])
        await update_party_message(new_session_id, interacting_user_id=interaction.user.id)
        await interaction.response.send_message(f"✅ Копия создана: {msg.jump_url}", ephemeral=True)

class StopEventButton(ui.Button):
    def __init__(self, session_id):
        super().__init__(
            label="⏹ Остановить ивент", 
            style=discord.ButtonStyle.red,
            custom_id=f"stop_button_{session_id}"
        )
        self.session_id = session_id

    async def callback(self, interaction: discord.Interaction):
        session = ALL_SESSIONS.get(str(self.session_id))
        if not session:
            await interaction.response.send_message("Ошибка: сессия не найдена.", ephemeral=True)
            return
        guild = interaction.guild
        moderator_role_id = get_guild_setting(interaction.guild.id, "moderator_role")
        is_moderator = moderator_role_id and any(r.id == moderator_role_id for r in interaction.user.roles)
        if interaction.user.id != session["creator_id"] and not is_moderator:
            await interaction.response.send_message("Только создатель ивента или модератор может редактировать.", ephemeral=True)
            return
        if session.get("stopped"):
            await interaction.response.send_message("Ивент уже остановлен.", ephemeral=True)
            return
        session["stopped"] = True
        ALL_SESSIONS[str(self.session_id)] = session
        save_all_data()
        save_event(int(self.session_id), session)
        await update_party_message(self.session_id, interacting_user_id=interaction.user.id)
        await interaction.response.send_message("Ивент остановлен, запись закрыта.", ephemeral=True)

class RemindButton(ui.Button):
    def __init__(self, session_id):
        super().__init__(
            label="📢 Напомнить", 
            style=discord.ButtonStyle.primary,
            custom_id=f"remind_button_{session_id}"  # Добавляем custom_id
        )
        self.session_id = session_id

    async def callback(self, interaction: discord.Interaction):
        session = ALL_SESSIONS.get(str(self.session_id))
        if not session:
            await interaction.response.send_message("Ошибка: сессия не найдена.", ephemeral=True)
            return
        mentions = []
        for role in session["party_roles"]:
            user_id = role.get("user_id")
            if user_id:
                mentions.append(f"<@{user_id}>")
        if mentions:
            await interaction.response.send_message(
                "Напоминание для: " + ", ".join(mentions),
                allowed_mentions=discord.AllowedMentions(users=True)
            )
        else:
            await interaction.response.send_message("Нет записавшихся участников для напоминания.", ephemeral=True)

class PartyCheckButton(ui.Button):
    def __init__(self, session_id):
        super().__init__(
            label="📋 Party Check", 
            style=discord.ButtonStyle.green,
            custom_id=f"check_button_{session_id}"  # Добавляем custom_id
        )
        self.session_id = session_id

    async def callback(self, interaction: discord.Interaction):
        session = ALL_SESSIONS.get(str(self.session_id))
        if not session:
            await interaction.response.send_message("Ошибка: сессия не найдена.", ephemeral=True)
            return
        
        # Проверка прав (только создатель события или администратор)
        moderator_role_id = get_guild_setting(interaction.guild.id, "moderator_role")
        is_moderator = moderator_role_id and any(r.id == moderator_role_id for r in interaction.user.roles)
        is_creator = interaction.user.id == session["creator_id"]
        is_admin = interaction.user.guild_permissions.administrator
        
        if not (is_creator or is_moderator or is_admin):
            await interaction.response.send_message("❌ Только создатель события, администратор или модератор может использовать эту функцию.", ephemeral=True)
            return
        
        guild = bot.get_guild(session["guild_id"])
        if not guild:
            await interaction.response.send_message("Не могу получить сервер.", ephemeral=True)
            return
        member = guild.get_member(interaction.user.id)
        if not member:
            await interaction.response.send_message("Ошибка получения информации о вас.", ephemeral=True)
            return
        voice_channel = None
        if member.voice and member.voice.channel:
            voice_channel = member.voice.channel
        else:
            await interaction.response.send_message("❌ Вы должны находиться в голосовом канале этого сервера.", ephemeral=True)
            return
        voice_member_ids = {m.id for m in voice_channel.members}
        absent = []
        for role in session["party_roles"]:
            user_id = role.get("user_id")
            if user_id and user_id not in voice_member_ids:
                absent.append(f"<@{user_id}> ({role['name']})")
        if absent:
            await interaction.response.send_message(
                f"❗ Следующие участники отсутствуют в голосовом канале **{voice_channel.name}**:\n" + "\n".join(absent),
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"Все записавшиеся участники присутствуют в голосовом канале **{voice_channel.name}**.",
                ephemeral=True
            )

class RefreshButton(ui.Button):
    def __init__(self, session_id):
        super().__init__(
            label="🔄 Обновить", 
            style=discord.ButtonStyle.secondary,
            custom_id=f"refresh_button_{session_id}"  # Добавляем custom_id
        )
        self.session_id = session_id

    async def callback(self, interaction: discord.Interaction):
        await update_party_message(self.session_id, interacting_user_id=interaction.user.id)
        await interaction.response.send_message("Обновлено", ephemeral=True)

class EditModal(ui.Modal, title="Редактирование ивента"):
    def __init__(self, session_id: int):
        super().__init__()
        self.session_id = session_id
        session = ALL_SESSIONS.get(str(session_id))
        self.name = ui.TextInput(label="Название", default=session.get("title", ""), required=True)
        self.desc = ui.TextInput(label="Описание", style=discord.TextStyle.paragraph, default=session.get("description", ""), required=False)
        self.time = ui.TextInput(label="Время ивента", default=session.get("time", ""), required=False)
        roles_str = "\n".join([r["name"] for r in session.get("party_roles", [])])
        self.roles = ui.TextInput(label="Роли (по строкам)", style=discord.TextStyle.paragraph, default=roles_str, required=True)
        self.add_item(self.name)
        self.add_item(self.desc)
        self.add_item(self.time)
        self.add_item(self.roles)

    async def on_submit(self, interaction: discord.Interaction):
        session = ALL_SESSIONS.get(str(self.session_id))
        if not session:
            await interaction.response.send_message("Ошибка: сессия не найдена.", ephemeral=True)
            return
        role_list = [r.strip() for r in self.roles.value.strip().splitlines() if r.strip()]
        if not role_list:
            await interaction.response.send_message("❌ Укажите хотя бы одну роль", ephemeral=True)
            return
        
        # Проверка на максимальное количество ролей
        if len(role_list) > 50:
            await interaction.response.send_message("❌ Слишком много ролей (максимум 50). Уменьшите количество ролей.", ephemeral=True)
            return

        # Сохраняем новые данные
        session["title"] = self.name.value.strip()
        session["description"] = self.desc.value.strip()
        session["time"] = self.time.value.strip()

        # Если количество ролей изменилось, то обновляем party_roles с сохранением занятости по совпадению имени
        old_roles = session["party_roles"]
        new_roles = []
        for role_name in role_list:
            # Попытаемся найти прежний user_id по имени роли
            user_id = None
            for r in old_roles:
                if r["name"] == role_name:
                    user_id = r.get("user_id")
                    break
            new_roles.append({"name": role_name, "user_id": user_id})
        session["party_roles"] = new_roles

        ALL_SESSIONS[str(self.session_id)] = session
        save_all_data()
        save_event(self.session_id, session)
        await update_party_message(self.session_id, interacting_user_id=interaction.user.id)
        await interaction.response.send_message("Ивент обновлён", ephemeral=True)

class EditButton(ui.Button):
    def __init__(self, session_id):
        super().__init__(
            label="✏️ Редактировать", 
            style=discord.ButtonStyle.secondary,
            custom_id=f"edit_button_{session_id}"  # Добавляем custom_id
        )
        self.session_id = session_id

    async def callback(self, interaction: discord.Interaction):
        session = ALL_SESSIONS.get(str(self.session_id))
        if not session:
            await interaction.response.send_message("Ошибка: ивент не найден.", ephemeral=True)
            return
        if interaction.user.id != session["creator_id"]:
            await interaction.response.send_message("Только создатель ивента может редактировать.", ephemeral=True)
            return
        modal = EditModal(self.session_id)
        await interaction.response.send_modal(modal)

class ViewWithStorage(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.selected_channel = None

class ChannelSelect(discord.ui.Select):
    def __init__(self, guild, parent_view):
        options = [
            discord.SelectOption(
                label=f"#{channel.name}",
                value=str(channel.id),
                description=f"ID: {channel.id}"
            ) for channel in guild.text_channels
        ]
        super().__init__(
            placeholder="Выберите канал для мониторинга",
            min_values=1,
            max_values=1,
            options=options
        )
        self.parent_view = parent_view  # используем другое имя

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_channel = int(self.values[0])
        await interaction.response.send_message(
            f"Выбран канал: <#{self.parent_view.selected_channel}>", ephemeral=True
        )

class SaveButton(discord.ui.Button):
    def __init__(self, parent_view, event_creator_role, moderator_role):
        super().__init__(label="Сохранить", style=discord.ButtonStyle.green)
        self.parent_view = parent_view
        self.event_creator_role = event_creator_role
        self.moderator_role = moderator_role

    async def callback(self, interaction: discord.Interaction):
        if not self.parent_view.selected_channel:
            await interaction.response.send_message("❌ Сначала выберите канал!", ephemeral=True)
            return
        
        # Сохраняем настройки для конкретного сервера
        set_guild_setting(interaction.guild.id, "monitored_channels", [self.parent_view.selected_channel])
        set_guild_setting(interaction.guild.id, "event_creator_role", self.event_creator_role.id)
        set_guild_setting(interaction.guild.id, "moderator_role", self.moderator_role.id)
        
        channel = interaction.guild.get_channel(self.parent_view.selected_channel)
        await interaction.response.send_message(
            f"✅ Настройки сохранены\n"
            f"Роль создателя ивентов: {self.event_creator_role.mention}\n"
            f"Роль модератора: {self.moderator_role.mention}\n"
            f"Отслеживаемый канал: {channel.mention}",
            ephemeral=True
        )

# --- Functions ---

async def update_party_message(event_id: int, interacting_user_id=None):
    session = ALL_SESSIONS.get(str(event_id))
    if not session:
        return
    guild = bot.get_guild(session["guild_id"])
    if not guild:
        return
    channel = guild.get_channel(session["channel_id"])
    if not channel:
        return
    try:
        message = await channel.fetch_message(session["main_msg_id"])
    except discord.NotFound:
        # Сообщение удалено - помечаем ивент как остановленный
        print(f"Сообщение ивента {event_id} не найдено, автоматически останавливаем")
        session["stopped"] = True
        ALL_SESSIONS[str(event_id)] = session
        save_all_data()
        save_event(event_id, session)
        return
    except Exception:
        return

    # Получаем кого пингуем
    ping_val = get_guild_setting(session["guild_id"], "ping_role", "everyone")
    if ping_val == "everyone":
        ping_text = "@everyone"
        allowed_mentions = discord.AllowedMentions(everyone=True)
    else:
        role = guild.get_role(int(ping_val))
        if role and role.mentionable:
            ping_text = role.mention
            allowed_mentions = discord.AllowedMentions(roles=True)
        else:
            ping_text = "@everyone"
            allowed_mentions = discord.AllowedMentions(everyone=True)

    roles_text = ""
    for idx, role in enumerate(session["party_roles"], 1):
        user_id = role.get("user_id")
        if user_id:
            roles_text += f"{idx}. {role['name']} — <@{user_id}>\n"
        else:
            roles_text += f"{idx}. {role['name']} — Свободно\n"

    text = (
        f"{ping_text}\n"
        f"**{session['title']}**\n"
        f"{session['description']}\n\n"
    )
    
    if session.get("time"):
        text += f"**Время:** {session['time']}\n\n"
    
    text += f"**Роли:**\n{roles_text}\n"
    
    # Добавляем информацию о множественных селекторах если ролей много
    total_roles = len(session["party_roles"])
    if total_roles > 25:
        text += f"\n💡 *Ролей много ({total_roles}), они разделены на несколько селекторов*\n"
    
    if session.get("stopped"):
        text += "\n*Ивент остановлен, запись закрыта*"
    else:
        text += "\n*Ивент активен*"

    view = PartySelectView(event_id, interacting_user_id or 0)
    view.timeout = None
    try:
        await message.edit(content=text, view=view, embed=None, allowed_mentions=allowed_mentions)
    except Exception as e:
        print(f"Ошибка при обновлении сообщения: {e}")

def register_signup(user_id: int, session_id: int):
    if user_id not in PARTY_STATS:
        PARTY_STATS[user_id] = set()
    PARTY_STATS[user_id].add(session_id)
    save_all_data()


# --- Commands ---

# Новые компактные команды с подкомандами

@bot.tree.command(name="settings", description="Показать настройки сервера")
@app_commands.describe(
    show_web_links="Показать ссылки на веб-панели (только для админов)"
)
async def settings_command(
    interaction: discord.Interaction, 
    show_web_links: bool = False
):
    # Показываем настройки всем, но веб-ссылки только админам
    if show_web_links and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Только администраторы могут видеть веб-ссылки", ephemeral=True)
        return
    
    # Показываем текущие настройки
    await show_settings_info(interaction)
    
    # Если запрошены веб-ссылки и пользователь админ, отправляем дополнительные кнопки
    if show_web_links and interaction.user.guild_permissions.administrator:
        base_url = "http://localhost:8082"
        guild_id = interaction.guild.id
        
        view = ui.View(timeout=None)
        
        # Кнопка админ-панели
        admin_button = ui.Button(
            label="�️ Админ-панель",
            style=discord.ButtonStyle.primary,
            url=f"{base_url}/guild/{guild_id}"
        )
        view.add_item(admin_button)
        
        # Кнопка настройки ролей
        roles_button = ui.Button(
            label="🎭 Настройка ролей",
            style=discord.ButtonStyle.secondary,
            url=f"{base_url}/guild/{guild_id}/role-settings"
        )
        view.add_item(roles_button)
        
        # Кнопка шаблонов
        templates_button = ui.Button(
            label="📝 Шаблоны",
            style=discord.ButtonStyle.secondary,
            url=f"{base_url}/guild/{guild_id}/templates"
        )
        view.add_item(templates_button)
        
        embed = discord.Embed(
            title="🌐 Веб-управление",
            description="Используйте веб-интерфейс для детальной настройки бота:",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="🛠️ Основные функции",
            value=(
                "• Настройка ролей и каналов\n"
                "• Управление шаблонами событий\n"
                "• Просмотр статистики и логов"
            ),
            inline=False
        )
        
        embed.add_field(
            name="🔧 Быстрые переключатели",
            value=(
                "• Используйте `/settings` для просмотра\n"
                "• Веб-панель для всех изменений\n"
                "• Автосохранение настроек"
            ),
            inline=False
        )
        
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

async def show_settings_info(interaction: discord.Interaction):
    import time as _t
    started = _t.time()
    guild_id = interaction.guild.id
    try:
        # Централизованная функция с дефолтами (импорт ленивый чтобы избежать циклов)
        from party_bot.web import get_complete_guild_settings, DEFAULT_SETTINGS  # type: ignore
        full_settings = get_complete_guild_settings(guild_id)
        source = "🗃️ settings.db"
    except Exception as e:
        # Fallback если веб модуль не прогружен
        print(f"[SETTINGS CMD] fallback direct DB: {e}")
        full_settings = get_guild_settings(guild_id) or {}
        # Минимальные дефолты
        full_settings.setdefault('reminder_time', [0,15])
        full_settings.setdefault('monitored_channels', [])
        source = "(fallback)"

    monitoring_enabled = bool(full_settings.get('monitoring_enabled', False))
    cleanup_enabled = bool(full_settings.get('cleanup_enabled', False))
    reminders_enabled = bool(full_settings.get('reminders_enabled', False))
    reminder_time = full_settings.get('reminder_time', [0,15])

    # Роли (одиночные / множественные)
    event_creator_role_id = full_settings.get('event_creator_role')
    event_creator_role = interaction.guild.get_role(event_creator_role_id) if event_creator_role_id else None
    moderator_role_id = full_settings.get('moderator_role')
    moderator_role = interaction.guild.get_role(moderator_role_id) if moderator_role_id else None
    event_creator_roles = full_settings.get('event_creator_roles', [])
    role_mentions = []
    for rid in event_creator_roles:
        r = interaction.guild.get_role(rid)
        if r:
            role_mentions.append(r.mention)

    # Каналы
    monitored_channel_ids = full_settings.get('monitored_channels', [])
    monitored_channels = []
    for ch_id in monitored_channel_ids:
        ch = interaction.guild.get_channel(ch_id)
        if ch:
            monitored_channels.append(ch.mention)

    monitoring_time = full_settings.get('monitoring_time', [10,20])
    cleanup_time = full_settings.get('cleanup_time', [0,0])
    monitoring_time_str = f"{monitoring_time[0]:02d}:00–{monitoring_time[1]:02d}:00 UTC" if monitoring_time else "По умолчанию"
    cleanup_time_str = f"{cleanup_time[0]:02d}:{cleanup_time[1]:02d} UTC" if cleanup_time else "По умолчанию"

    ping_val = full_settings.get('ping_role', 'everyone')
    if ping_val == 'everyone':
        ping_text = '@everyone'
    else:
        role_obj = interaction.guild.get_role(ping_val)
        ping_text = role_obj.mention if role_obj else f"<@&{ping_val}>"

    data_source = source

    embed = discord.Embed(
        title="⚙️ Настройки сервера", 
        description=f"Текущие настройки для **{interaction.guild.name}**\n📍 **Источник данных:** {data_source}",
        color=0x00FF00
    )
    
    # Роли (старая и новая система)
    roles_value = ""
    if event_creator_role:
        roles_value += f"🎮 **Создатель событий (старая):** {event_creator_role.mention}\n"
    if role_mentions:
        roles_value += f"🎭 **Роли для событий (новая):** {', '.join(role_mentions)}\n"
    if moderator_role:
        roles_value += f"🛡️ **Модератор:** {moderator_role.mention}\n"
    
    if not roles_value:
        roles_value = "❌ Роли не настроены"
    
    embed.add_field(
        name="👥 Роли и права",
        value=roles_value,
        inline=False
    )
    
    embed.add_field(
        name="📺 Отслеживаемые каналы",
        value="\n".join(monitored_channels) if monitored_channels else "❌ Не установлены",
        inline=False
    )
    
    embed.add_field(
        name="⏰ Расписание",
        value=f"🔍 **Мониторинг:** {monitoring_time_str}\n🧹 **Очистка:** {cleanup_time_str}",
        inline=False
    )
    
    embed.add_field(
        name="🔔 Уведомления",
        value=f"📢 **Пинг при событиях:** {ping_text}\n"
              f"⏰ **Напоминания:** {'✅ Включены' if reminders_enabled else '❌ Отключены'} "
              f"(за {reminder_time[0]}ч {reminder_time[1]}м)",
        inline=False
    )
    
    embed.add_field(
        name="🔧 Статус функций",
        value=f"🔍 **Мониторинг каналов:** {'✅ Включен' if monitoring_enabled else '❌ Отключен'}\n"
              f"🧹 **Автоочистка:** {'✅ Включена' if cleanup_enabled else '❌ Отключена'}",
        inline=False
    )
    
    # Добавляем ссылки на веб-панель
    base_url = "http://localhost:8082"  # TODO: получать из конфига
    embed.add_field(
        name="🌐 Веб-управление",
        value=f"[📋 Админ-панель]({base_url}/guild/{guild_id})\n"
              f"[🎭 Настройка ролей]({base_url}/guild/{guild_id}/role-settings)\n"
              f"[📝 Шаблоны событий]({base_url}/guild/{guild_id}/templates)",
        inline=False
    )
    
    embed.set_footer(text="💡 Используйте веб-панель для детальной настройки")
    
    embed.set_footer(text=f"Загрузка {int((_t.time()-started)*1000)} мс")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="party", description="Управление ивентами")
@app_commands.describe(
    action="Выберите действие",
    template="Название шаблона (для создания из шаблона)",
    days="Количество дней для истории (по умолчанию 30)"
)
@app_commands.choices(action=[
    app_commands.Choice(name="📝 Создать новый ивент", value="create"),
    app_commands.Choice(name="📊 Показать статистику", value="stats"),
    app_commands.Choice(name="📋 Создать из шаблона", value="from_template"),
    app_commands.Choice(name="📄 Выгрузить историю", value="history")
])
async def party_command(
    interaction: discord.Interaction, 
    action: str,
    template: str = None,
    days: int = 30
):
    if action == "create":
        await create_party_modal(interaction)
    elif action == "stats":
        await show_stats(interaction)
    elif action == "from_template":
        if not template:
            await interaction.response.send_message("❌ Укажите название шаблона", ephemeral=True)
            return
        await use_template_action(interaction, template)
    elif action == "history":
        await export_history_action(interaction, days)

async def create_party_modal(interaction: discord.Interaction):
    class PartyModal(ui.Modal, title="Создание ивента"):
        name = ui.TextInput(label="Название ивента", required=True)
        desc = ui.TextInput(label="Описание", style=discord.TextStyle.paragraph, required=False)
        time = ui.TextInput(label="Время ивента", required=False)
        roles = ui.TextInput(label="Роли (по строкам)", style=discord.TextStyle.paragraph, required=True)

        async def on_submit(self, interaction: discord.Interaction):
            role_list = [r.strip() for r in self.roles.value.strip().splitlines() if r.strip()]
            if not role_list:
                await interaction.response.send_message("❌ Укажите хотя бы одну роль", ephemeral=True)
                return
            
            # Предупреждение о большом количестве ролей
            if len(role_list) > 50:
                await interaction.response.send_message("❌ Слишком много ролей (максимум 50). Уменьшите количество ролей.", ephemeral=True)
                return
            elif len(role_list) > 25:
                # Информируем пользователя что роли будут разделены
                pass  # Продолжаем создание
            
            event_id = interaction.message.id if interaction.message else interaction.id

            embed = discord.Embed(
                title=self.name.value.strip(),
                description=self.desc.value.strip(),
                color=0x00FF00
            )
            if self.time.value.strip():
                embed.add_field(name="Время", value=self.time.value.strip(), inline=False)
            embed.add_field(name="Роли", value="\n".join([f"{i+1}. {r} — Свободно" for i, r in enumerate(role_list)]), inline=False)

            guild_id = str(interaction.guild_id)
            ping_val = get_guild_setting(interaction.guild.id, "ping_role", "everyone")
            if ping_val == "everyone":
                ping_text = "@everyone"
                allowed_mentions = discord.AllowedMentions(everyone=True)
            else:
                # Проверяем, что роль существует
                role = interaction.guild.get_role(int(ping_val))
                ping_text = role.mention if role and role.mentionable else "@everyone"
                allowed_mentions = discord.AllowedMentions(everyone=True) if ping_text == "@everyone" else discord.AllowedMentions(roles=True)

            msg = await interaction.channel.send(
                f"{ping_text}\n"
                f"**{self.name.value.strip()}**\n"
                f"{self.desc.value.strip()}\n\n"
                f"**Время:** {self.time.value.strip() or 'Не указано'}\n\n"
                f"**Роли:**\n" + 
                "\n".join([f"{i+1}. {r} — Свободно" for i, r in enumerate(role_list)]),
                allowed_mentions=allowed_mentions
            )
            thread = await msg.create_thread(name=self.name.value.strip())

            ALL_SESSIONS[str(msg.id)] = {
                "guild_id": interaction.guild.id,
                "channel_id": interaction.channel.id,
                "main_msg_id": msg.id,
                "thread_id": thread.id,
                "title": self.name.value.strip(),
                "description": self.desc.value.strip(),
                "time": self.time.value.strip(),
                "party_roles": [{"name": r, "user_id": None} for r in role_list],
                "creator_id": interaction.user.id,
                "stopped": False,
                "last_reminder_time": 0
            }
            save_all_data()
            save_event(msg.id, ALL_SESSIONS[str(msg.id)])
            await update_party_message(msg.id, interacting_user_id=interaction.user.id)
            
            # Информируем о создании ивента
            response_text = f"Ивент создан: {msg.jump_url}"
            if len(role_list) > 25:
                response_text += f"\n💡 Ролей много ({len(role_list)}), они разделены на несколько селекторов для удобства"
            
            await interaction.response.send_message(response_text, ephemeral=True)

    modal = PartyModal()
    await interaction.response.send_modal(modal)

async def show_stats(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Ошибка: команда должна использоваться на сервере.", ephemeral=True)
        return
    if not PARTY_STATS:
        await interaction.response.send_message("Статистика пуста.", ephemeral=True)
        return

    lines = []
    for user_id, sessions in PARTY_STATS.items():
        member = guild.get_member(user_id)
        name = member.display_name if member else f"User ID: {user_id}"
        lines.append(f"**{name}**: {len(sessions)} посещений")
    text = "\n".join(lines)
    await interaction.response.send_message(f"📊 **Статистика посещений:**\n{text}", ephemeral=True)

async def use_template_action(interaction: discord.Interaction, template: str):
    # Используем шаблоны конкретного сервера
    template_data = get_guild_template(interaction.guild.id, template)
    if not template_data:
        await interaction.response.send_message("❌ Шаблон не найден", ephemeral=True)
        return
    guild_id = str(interaction.guild_id)
    ping_val = get_guild_setting(interaction.guild.id, "ping_role", "everyone")
    if ping_val == "everyone":
        ping_text = "@everyone"
        allowed_mentions = discord.AllowedMentions(everyone=True)
    else:
        role = interaction.guild.get_role(int(ping_val))
        ping_text = role.mention if role and role.mentionable else "@everyone"
        allowed_mentions = discord.AllowedMentions(everyone=True) if ping_text == "@everyone" else discord.AllowedMentions(roles=True)

    # Создаем текст сообщения
    text = (
        f"{ping_text}\n"
        f"**{template_data['title']}**\n"
        f"{template_data['description']}\n\n"
        "**Роли:**\n" + "\n".join([f"{i+1}. {r} — Свободно" for i, r in enumerate(template_data["roles"])])
    )

    msg = await interaction.channel.send(text, allowed_mentions=allowed_mentions)
    thread = await msg.create_thread(name=template_data["title"])

    ALL_SESSIONS[str(msg.id)] = {
        "guild_id": interaction.guild.id,
        "channel_id": interaction.channel.id,
        "main_msg_id": msg.id,
        "thread_id": thread.id,
        "title": template_data["title"],
        "description": template_data["description"],
        "time": "",
        "party_roles": [{"name": r, "user_id": None} for r in template_data["roles"]],
        "creator_id": interaction.user.id,
        "stopped": False,
        "last_reminder_time": 0
    }
    save_all_data()
    save_event(msg.id, ALL_SESSIONS[str(msg.id)])
    await update_party_message(msg.id, interacting_user_id=interaction.user.id)
    
    await interaction.response.send_message(f"✅ Ивент создан из шаблона '{template}'", ephemeral=True)

async def export_history_action(interaction: discord.Interaction, days: int = 30):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Только администраторы могут использовать эту команду", ephemeral=True)
        return
        
    await interaction.response.defer(ephemeral=True)
    
    guild = interaction.guild
    history = []
    current_time = datetime.now()
    cutoff = current_time - timedelta(days=days)

    for session_id, session in ALL_SESSIONS.items():
        if session["guild_id"] != guild.id:
            continue

        # Получаем дату создания из session_id (если это snowflake)
        try:
            timestamp = int(session_id) / 1000
            created_at = datetime.fromtimestamp(timestamp)
        except Exception:
            created_at = None

        # Фильтруем по дате
        if created_at and created_at < cutoff:
            continue

        try:
            event_info = [
                f"=== Ивент: {session['title']} ===",
                f"ID: {session_id}",
                f"Дата создания: {created_at.strftime('%d.%m.%Y %H:%M') if created_at else 'Неизвестно'}",
                f"Создатель: {guild.get_member(session['creator_id']).display_name if guild.get_member(session['creator_id']) else session['creator_id']}",
                f"Описание: {session['description']}",
                f"Время: {session.get('time', 'Не указано')}",
                f"Статус: {'Остановлен' if session.get('stopped') else 'Активен'}",
                "\nУчастники:",
            ]
            
            for role in session["party_roles"]:
                user_id = role.get("user_id")
                if user_id:
                    member = guild.get_member(user_id)
                    event_info.append(f"  {role['name']}: {member.display_name if member else user_id}")
                else:
                    event_info.append(f"  {role['name']}: Свободно")
            
            history.append("\n".join(event_info) + "\n")
        except Exception as e:
            print(f"Ошибка при обработке ивента {session_id}: {e}")
            continue
    
    if not history:
        await interaction.followup.send("Ивентов не найдено за указанный период.", ephemeral=True)
        return
        
    filename = f"events_history_{guild.name}_{current_time.strftime('%Y%m%d_%H%M')}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n\n".join(history))
        
    await interaction.followup.send(
        f"📄 История ивентов за {days} дней:",
        file=discord.File(filename),
        ephemeral=True
    )
    
    os.remove(filename)


async def stats(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("Ошибка: команда должна использоваться на сервере.", ephemeral=True)
        return
    if not PARTY_STATS:
        await interaction.response.send_message("Статистика пуста.", ephemeral=True)
        return

    lines = []
    for user_id, sessions in PARTY_STATS.items():
        member = guild.get_member(user_id)
        name = member.display_name if member else f"User ID: {user_id}"
        lines.append(f"**{name}**: {len(sessions)} посещений")
    text = "\n".join(lines)
    await interaction.response.send_message(f"📊 **Статистика посещений:**\n{text}", ephemeral=True)

# КОМАНДА SETUP ОТКЛЮЧЕНА В ПОЛЬЗУ ВЕБ-ИНТЕРФЕЙСА
# Команда setup удалена - используйте веб-панель для настройки ролей


@app_commands.describe(days="За сколько последних дней (по умолчанию 30)")
async def export_history(interaction: discord.Interaction, days: int = 30):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Только администраторы могут использовать эту команду", ephemeral=True)
        return
        
    await interaction.response.defer(ephemeral=True)
    
    guild = interaction.guild
    history = []
    current_time = datetime.now()
    cutoff = current_time - timedelta(days=days)

    for session_id, session in ALL_SESSIONS.items():
        if session["guild_id"] != guild.id:
            continue

        # Получаем дату создания из session_id (если это snowflake)
        try:
            timestamp = int(session_id) / 1000
            created_at = datetime.fromtimestamp(timestamp)
        except Exception:
            created_at = None

        # Фильтруем по дате
        if created_at and created_at < cutoff:
            continue

        try:
            event_info = [
                f"=== Ивент: {session['title']} ===",
                f"ID: {session_id}",
                f"Дата создания: {created_at.strftime('%d.%m.%Y %H:%M') if created_at else 'Неизвестно'}",
                f"Создатель: {guild.get_member(session['creator_id']).display_name if guild.get_member(session['creator_id']) else session['creator_id']}",
                f"Описание: {session['description']}",
                f"Время: {session.get('time', 'Не указано')}",
                f"Статус: {'Остановлен' if session.get('stopped') else 'Активен'}",
                "\nУчастники:",
            ]
            
            for role in session["party_roles"]:
                user_id = role.get("user_id")
                if user_id:
                    member = guild.get_member(user_id)
                    user_name = member.display_name if member else f"ID: {user_id}"
                    event_info.append(f"- {role['name']}: {user_name}")
                else:
                    event_info.append(f"- {role['name']}: Свободно")
            
            history.append("\n".join(event_info) + "\n")
        except Exception as e:
            print(f"Ошибка при обработке ивента {session_id}: {e}")
            continue
    
    if not history:
        await interaction.followup.send("Ивентов не найдено за указанный период.", ephemeral=True)
        return
        
    filename = f"events_history_{guild.name}_{current_time.strftime('%Y%m%d_%H%M')}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n\n".join(history))
        
    await interaction.followup.send(
        f"📄 История ивентов за {days} дней:",
        file=discord.File(filename),
        ephemeral=True
    )
    
    os.remove(filename)

@bot.tree.command(name="templates", description="Управление шаблонами")
@app_commands.describe(
    action="Выберите действие",
    template="Название шаблона (для показа/редактирования/удаления)"
)
@app_commands.choices(action=[
    app_commands.Choice(name="📝 Создать новый шаблон", value="create"),
    app_commands.Choice(name="📋 Список шаблонов", value="list"),
    app_commands.Choice(name="👁️ Показать подробности", value="show"),
    app_commands.Choice(name="✏️ Редактировать", value="edit"),
    app_commands.Choice(name="🗑️ Удалить", value="delete")
])
async def templates_command(
    interaction: discord.Interaction, 
    action: str,
    template: str = None
):
    # Проверяем права администратора
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Только администраторы могут управлять шаблонами", ephemeral=True)
        return
    
    try:
        if action == "create":
            await create_template_modal(interaction)
        elif action == "list":
            await interaction.response.defer(ephemeral=True)
            await list_templates_action(interaction)
        elif action == "show":
            if not template:
                await interaction.response.send_message("❌ Укажите название шаблона", ephemeral=True)
                return
            await show_template_details(interaction, template)
        elif action == "edit":
            if not template:
                await interaction.response.send_message("❌ Укажите название шаблона", ephemeral=True)
                return
            await edit_template_modal(interaction, template)
        elif action == "delete":
            if not template:
                await interaction.response.send_message("❌ Укажите название шаблона", ephemeral=True)
                return
            await delete_template_action(interaction, template)
    except Exception as e:
        print(f"Ошибка в templates_command: {e}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Произошла ошибка при выполнении команды", ephemeral=True)
            else:
                await interaction.followup.send("❌ Произошла ошибка при выполнении команды", ephemeral=True)
        except Exception as followup_error:
            print(f"Ошибка при отправке сообщения об ошибке: {followup_error}")
            pass

@bot.tree.command(name="links", description="Получить ссылки для участников")
@app_commands.describe(
    show_in_channel="Показать ссылки в канале (иначе только вам)"
)
async def links_command(
    interaction: discord.Interaction,
    show_in_channel: bool = False
):
    """Команда для получения полезных ссылок сервера"""
    try:
        if not interaction.guild:
            await interaction.response.send_message("❌ Эта команда доступна только на серверах", ephemeral=True)
            return
        
        # Проверяем права - только администраторы могут показывать ссылки в канале
        if show_in_channel and not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Только администраторы могут показывать ссылки в канале", ephemeral=True)
            return
        
        guild_id = interaction.guild.id
        base_url = "http://localhost:8082"  # TODO: получать из конфига
        
        embed = discord.Embed(
            title="🔗 Полезные ссылки сервера",
            description=f"Веб-интерфейс бота для сервера **{interaction.guild.name}**",
            color=discord.Color.blue()
        )
        
        # Админские ссылки
        if interaction.user.guild_permissions.administrator:
            embed.add_field(
                name="👑 Для администраторов",
                value=(
                    f"🛠️ [Админ-панель]({base_url}/guild/{guild_id})\n"
                    f"⚙️ [Настройки бота]({base_url}/guild/{guild_id}/settings)\n"
                    f"📊 [Статистика]({base_url}/guild/{guild_id}/stats)\n"
                    f"📝 [Шаблоны событий]({base_url}/guild/{guild_id}/templates)"
                ),
                inline=False
            )
        
        # Ссылки для всех участников
        embed.add_field(
            name="👥 Для всех участников",
            value=(
                f"🎮 [Создать событие]({base_url}/guild/{guild_id}/events/guest)\n"
                f"📋 [Просмотр событий]({base_url}/guild/{guild_id}/events)\n"
                f"🏆 [Рейтинг участников]({base_url}/guild/{guild_id}/stats)"
            ),
            inline=False
        )
        
        embed.add_field(
            name="📚 Справка",
            value=(
                f"❓ Откройте веб-панель для полной настройки\n"
                f"🆔 [Узнать свой ID]({base_url}/my-id)"
            ),
            inline=False
        )
        
        embed.set_footer(text="💡 Сохраните эти ссылки в закладки для быстрого доступа!")
        
        # Добавляем кнопку для быстрого создания события
        view = ui.View(timeout=None)
        create_button = ui.Button(
            label="🎮 Создать событие",
            style=discord.ButtonStyle.primary,
            url=f"{base_url}/guild/{guild_id}/events/guest"
        )
        view.add_item(create_button)
        
        # Если админ, добавляем кнопку админки
        if interaction.user.guild_permissions.administrator:
            admin_button = ui.Button(
                label="🛠️ Админ-панель",
                style=discord.ButtonStyle.secondary,
                url=f"{base_url}/guild/{guild_id}"
            )
            view.add_item(admin_button)
        
        await interaction.response.send_message(
            embed=embed, 
            view=view, 
            ephemeral=not show_in_channel
        )
        
    except Exception as e:
        print(f"Ошибка в links_command: {e}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Произошла ошибка при выполнении команды", ephemeral=True)
            else:
                await interaction.followup.send("❌ Произошла ошибка при выполнении команды", ephemeral=True)
        except Exception as followup_error:
            print(f"Ошибка при отправке сообщения об ошибке: {followup_error}")
            pass

@bot.tree.command(name="role-links", description="Управление ролевыми ссылками для событий")
@app_commands.describe(
    action="Выберите действие",
    role="Роль для создания ссылки"
)
@app_commands.choices(action=[
    app_commands.Choice(name="⚙️ Настроить роли", value="settings"),
    app_commands.Choice(name="🔗 Показать ссылки", value="show"),
    app_commands.Choice(name="📋 Ссылка для роли", value="role_link")
])
async def role_links_command(
    interaction: discord.Interaction,
    action: str,
    role: discord.Role = None
):
    """Команда для управления ролевыми ссылками"""
    try:
        if not interaction.guild:
            await interaction.response.send_message("❌ Эта команда доступна только на серверах", ephemeral=True)
            return
        
        # Проверяем права - только администраторы могут управлять ролевыми ссылками
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Только администраторы могут управлять ролевыми ссылками", ephemeral=True)
            return
        
        guild_id = interaction.guild.id
        base_url = "http://localhost:8082"  # TODO: получать из конфига
        
        if action == "settings":
            embed = discord.Embed(
                title="⚙️ Настройка ролевых ссылок",
                description=f"Откройте веб-панель для настройки ролей, которые могут создавать события:",
                color=discord.Color.blue()
            )
            
            embed.add_field(
                name="🛠️ Панель настроек",
                value=f"[Настроить роли]({base_url}/guild/{guild_id}/role-settings)",
                inline=False
            )
            
            embed.add_field(
                name="📚 Возможности",
                value=(
                    "• Выбор ролей с правами создания событий\n"
                    "• Генерация персональных ссылок\n"
                    "• Управление доступом участников"
                ),
                inline=False
            )
            
            view = ui.View(timeout=None)
            settings_button = ui.Button(
                label="⚙️ Открыть настройки",
                style=discord.ButtonStyle.primary,
                url=f"{base_url}/guild/{guild_id}/role-settings"
            )
            view.add_item(settings_button)
            
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            
        elif action == "show":
            # Получаем настроенные роли
            try:
                from unified_settings import get_guild_setting
                event_roles = get_guild_setting(guild_id, "event_creator_roles", [])
                if not event_roles:
                    # Проверяем старую настройку
                    old_role = get_guild_setting(guild_id, "event_creator_role")
                    if old_role:
                        event_roles = [old_role]
            except:
                event_roles = []
            
            if not event_roles:
                embed = discord.Embed(
                    title="❌ Ролевые ссылки не настроены",
                    description=f"Сначала настройте роли в [веб-панели]({base_url}/guild/{guild_id}/role-settings)",
                    color=discord.Color.orange()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            
            embed = discord.Embed(
                title="🔗 Ролевые ссылки для событий",
                description="Ссылки для участников с особыми ролями:",
                color=discord.Color.green()
            )
            
            for role_id in event_roles:
                role_obj = interaction.guild.get_role(role_id)
                if role_obj:
                    link = f"{base_url}/guild/{guild_id}/events/role/{role_id}"
                    embed.add_field(
                        name=f"🎭 {role_obj.name}",
                        value=f"[Ссылка для создания событий]({link})",
                        inline=False
                    )
            
            embed.set_footer(text="💡 Поделитесь этими ссылками с участниками соответствующих ролей")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        elif action == "role_link":
            if not role:
                await interaction.response.send_message("❌ Укажите роль для создания ссылки", ephemeral=True)
                return
            
            # Проверяем, настроена ли эта роль
            try:
                from unified_settings import get_guild_setting
                event_roles = get_guild_setting(guild_id, "event_creator_roles", [])
                if not event_roles:
                    old_role = get_guild_setting(guild_id, "event_creator_role")
                    if old_role:
                        event_roles = [old_role]
            except:
                event_roles = []
            
            if role.id not in event_roles:
                embed = discord.Embed(
                    title="❌ Роль не настроена",
                    description=(
                        f"Роль **{role.name}** не настроена для создания событий.\n"
                        f"Настройте её в [веб-панели]({base_url}/guild/{guild_id}/role-settings)"
                    ),
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            
            link = f"{base_url}/guild/{guild_id}/events/role/{role.id}"
            
            embed = discord.Embed(
                title=f"🔗 Ссылка для роли {role.name}",
                description=(
                    f"Участники с ролью **{role.name}** могут использовать эту ссылку "
                    f"для создания и управления своими событиями:"
                ),
                color=role.color if role.color != discord.Color.default() else discord.Color.blue()
            )
            
            embed.add_field(
                name="🎮 Ссылка для создания событий",
                value=f"[Открыть страницу событий]({link})",
                inline=False
            )
            
            embed.add_field(
                name="📋 Прямая ссылка",
                value=f"`{link}`",
                inline=False
            )
            
            embed.set_footer(text="💡 Сохраните ссылку и поделитесь с участниками этой роли")
            
            view = ui.View(timeout=None)
            open_button = ui.Button(
                label="🎮 Открыть страницу",
                style=discord.ButtonStyle.link,
                url=link
            )
            view.add_item(open_button)
            
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        
    except Exception as e:
        print(f"Ошибка в role_links_command: {e}")
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Произошла ошибка при выполнении команды", ephemeral=True)
            else:
                await interaction.followup.send("❌ Произошла ошибка при выполнении команды", ephemeral=True)
        except Exception as followup_error:
            print(f"Ошибка при отправке сообщения об ошибке: {followup_error}")
            pass

async def create_template_modal(interaction: discord.Interaction):
    try:
        if not interaction.guild:
            await interaction.response.send_message("❌ Эта команда доступна только на серверах", ephemeral=True)
            return
            
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Только администраторы могут создавать шаблоны", ephemeral=True)
            return
            
        class TemplateModal(ui.Modal, title="Создание шаблона"):
            template_name = ui.TextInput(
                label="Название шаблона",
                placeholder="Введите название шаблона",
                required=True,
                max_length=100
            )
            
            template_title = ui.TextInput(
                label="Название ивента",
                placeholder="Введите название ивента",
                required=True,
                max_length=100
            )
            
            template_desc = ui.TextInput(
                label="Описание",
                placeholder="Введите описание ивента",
                style=discord.TextStyle.paragraph,
                required=False,
                max_length=1000
            )
            
            template_roles = ui.TextInput(
                label="Роли (каждая с новой строки)",
                placeholder="Танк\nХил\nДД",
                style=discord.TextStyle.paragraph,
                required=True,
                max_length=1000
            )

            async def on_submit(self, interaction: discord.Interaction):
                name = self.template_name.value.strip()
                
                # Проверяем наличие ролей
                roles = [r.strip() for r in self.template_roles.value.strip().splitlines() if r.strip()]
                if not roles:
                    await interaction.response.send_message("❌ Укажите хотя бы одну роль", ephemeral=True)
                    return

                # Создаем шаблон для конкретного сервера
                set_guild_template(interaction.guild.id, name, {
                    "title": self.template_title.value.strip(),
                    "description": self.template_desc.value.strip(),
                    "roles": roles
                })
                
                await interaction.response.send_message(
                    f"✅ Шаблон '{name}' создан для этого сервера!\n"
                    f"Используйте команду `/events_template {name}` чтобы создать ивент", 
                    ephemeral=True
                )

        await interaction.response.send_modal(TemplateModal())
        
    except Exception as e:
        print(f"Ошибка в create_template_modal: {e}")
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ Произошла ошибка при создании шаблона", ephemeral=True)
        else:
            await interaction.followup.send("❌ Произошла ошибка при создании шаблона", ephemeral=True)

async def list_templates_action(interaction: discord.Interaction):
    try:
        # Проверяем, что команда вызвана в гильдии
        if not interaction.guild:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ Эта команда доступна только на серверах", ephemeral=True)
            else:
                await interaction.followup.send("❌ Эта команда доступна только на серверах", ephemeral=True)
            return
            
        # Используем шаблоны конкретного сервера
        guild_templates = get_guild_templates(interaction.guild.id)
        if not guild_templates:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ На этом сервере нет доступных шаблонов", ephemeral=True)
            else:
                await interaction.followup.send("❌ На этом сервере нет доступных шаблонов", ephemeral=True)
            return

        names = "\n".join([f"• {name}" for name in guild_templates.keys()])
        message = (
            f"📋 **Доступные шаблоны на сервере:**\n{names}\n\n"
            f"Чтобы посмотреть подробности, используйте `/templates_show <название шаблона>`"
        )
        
        if not interaction.response.is_done():
            await interaction.response.send_message(message, ephemeral=True)
        else:
            await interaction.followup.send(message, ephemeral=True)
            
    except Exception as e:
        print(f"Ошибка в list_templates_action: {e}")
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ Произошла ошибка при получении списка шаблонов", ephemeral=True)
        else:
            await interaction.followup.send("❌ Произошла ошибка при получении списка шаблонов", ephemeral=True)

async def show_template_details(interaction: discord.Interaction, template: str):
    # Используем шаблоны конкретного сервера
    template_data = get_guild_template(interaction.guild.id, template)
    if not template_data:
        await interaction.response.send_message("❌ Шаблон не найден на этом сервере", ephemeral=True)
        return
    
    roles = "\n".join([f"• {role}" for role in template_data["roles"]])
    await interaction.response.send_message(
        f"**Название шаблона:** {template}\n"
        f"**Название ивента:** {template_data['title']}\n"
        f"**Описание:** {template_data['description']}\n"
        f"**Роли:**\n{roles}",
        ephemeral=True
    )

async def edit_template_modal(interaction: discord.Interaction, template: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Только администраторы могут редактировать шаблоны", ephemeral=True)
        return
    
    # Используем шаблоны конкретного сервера
    template_data = get_guild_template(interaction.guild.id, template)
    if not template_data:
        await interaction.response.send_message("❌ Шаблон не найден на этом сервере", ephemeral=True)
        return

    class EditTemplateModal(ui.Modal, title=f"Редактирование шаблона '{template}'"):
        template_title = ui.TextInput(
            label="Название ивента",
            placeholder="Введите название ивента",
            required=True,
            max_length=100,
            default=template_data["title"]
        )
        
        template_desc = ui.TextInput(
            label="Описание",
            placeholder="Введите описание ивента",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
            default=template_data["description"]
        )
        
        template_roles = ui.TextInput(
            label="Роли (каждая с новой строки)",
            placeholder="Танк\nХил\nДД",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000,
            default="\n".join(template_data["roles"])
        )

        async def on_submit(self, interaction: discord.Interaction):
            roles = [r.strip() for r in self.template_roles.value.strip().splitlines() if r.strip()]
            if not roles:
                await interaction.response.send_message("❌ Укажите хотя бы одну роль", ephemeral=True)
                return

            # Сохраняем в шаблоны сервера
            set_guild_template(interaction.guild.id, template, {
                "title": self.template_title.value.strip(),
                "description": self.template_desc.value.strip(),
                "roles": roles
            })
            
            await interaction.response.send_message(
                f"✅ Шаблон '{template}' обновлен!", 
                ephemeral=True
            )

    try:
        await interaction.response.send_modal(EditTemplateModal())
    except Exception as e:
        print(f"Ошибка при редактировании шаблона: {e}")
        await interaction.response.send_message(
            "❌ Произошла ошибка при редактировании шаблона", 
            ephemeral=True
        )

async def delete_template_action(interaction: discord.Interaction, template: str):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Только администраторы могут удалять шаблоны", ephemeral=True)
        return
    
    # Используем шаблоны конкретного сервера
    if not delete_guild_template(interaction.guild.id, template):
        await interaction.response.send_message("❌ Шаблон не найден на этом сервере", ephemeral=True)
        return
    
    await interaction.response.send_message(f"✅ Шаблон '{template}' удален", ephemeral=True)


async def create_template(interaction: discord.Interaction):
    class TemplateModal(ui.Modal, title="Создание шаблона"):
        template_name = ui.TextInput(
            label="Название шаблона",
            placeholder="Введите название шаблона",
            required=True,
            max_length=100
        )
        
        template_title = ui.TextInput(
            label="Название ивента",
            placeholder="Введите название ивента",
            required=True,
            max_length=100
        )
        
        template_desc = ui.TextInput(
            label="Описание",
            placeholder="Введите описание ивента",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000
        )
        
        template_roles = ui.TextInput(
            label="Роли (каждая с новой строки)",
            placeholder="Танк\nХил\nДД",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )

        async def on_submit(self, interaction: discord.Interaction):
            name = self.template_name.value.strip()
            
            # Проверяем наличие ролей
            roles = [r.strip() for r in self.template_roles.value.strip().splitlines() if r.strip()]
            if not roles:
                await interaction.response.send_message("❌ Укажите хотя бы одну роль", ephemeral=True)
                return

            # Создаем шаблон для конкретного сервера
            set_guild_template(interaction.guild.id, name, {
                "title": self.template_title.value.strip(),
                "description": self.template_desc.value.strip(),
                "roles": roles
            })
            
            await interaction.response.send_message(
                f"✅ Шаблон '{name}' создан для этого сервера!\n"
                f"Используйте команду `/events_template {name}` чтобы создать ивент", 
                ephemeral=True
            )

    try:
        await interaction.response.send_modal(TemplateModal())
    except Exception as e:
        print(f"Ошибка при создании шаблона: {e}")
        await interaction.response.send_message(
            "❌ Произошла ошибка при создании шаблона", 
            ephemeral=True
        )


@app_commands.describe(template="Название шаблона", time="Время ивента (необязательно)")
async def use_template(interaction: discord.Interaction, template: str, time: str = None):
    # Используем шаблоны конкретного сервера
    template_data = get_guild_template(interaction.guild.id, template)
    if not template_data:
        await interaction.response.send_message("❌ Шаблон не найден на этом сервере", ephemeral=True)
        return
    guild_id = str(interaction.guild_id)
    ping_val = get_guild_setting(interaction.guild.id, "ping_role", "everyone")
    if ping_val == "everyone":
        ping_text = "@everyone"
        allowed_mentions = discord.AllowedMentions(everyone=True)
    else:
        role = interaction.guild.get_role(int(ping_val))
        ping_text = role.mention if role and role.mentionable else "@everyone"
        allowed_mentions = discord.AllowedMentions(everyone=True) if ping_text == "@everyone" else discord.AllowedMentions(roles=True)

    # Создаем текст сообщения
    text = (
        f"{ping_text}\n"
        f"**{template_data['title']}**\n"
        f"{template_data['description']}\n\n"
    )
    if time:
        text += f"**Время:** {time}\n\n"
    role_list = template_data["roles"]
    text += "**Роли:**\n" + "\n".join([f"{i+1}. {r} — Свободно" for i, r in enumerate(role_list)])

    msg = await interaction.channel.send(text, allowed_mentions=allowed_mentions)
    thread = await msg.create_thread(name=template_data["title"])

    ALL_SESSIONS[str(msg.id)] = {
        "guild_id": interaction.guild.id,
        "channel_id": interaction.channel.id,
        "main_msg_id": msg.id,
        "thread_id": thread.id,
        "title": template_data["title"],
        "description": template_data["description"],
        "time": time or "",
        "party_roles": [{"name": r, "user_id": None} for r in role_list],
        "creator_id": interaction.user.id,
        "stopped": False,
        "last_reminder_time": 0
    }
    save_all_data()
    save_event(msg.id, ALL_SESSIONS[str(msg.id)])
    await update_party_message(msg.id, interacting_user_id=interaction.user.id)
    await interaction.response.send_message(f"✅ Ивент создан из шаблона '{template}'", ephemeral=True)


@bot.event
async def on_ready():
    print("=" * 60)
    print(f"🤖 Бот успешно запущен!")
    print(f"📛 Имя бота: {bot.user.name}")
    print(f"🆔 ID бота: {bot.user.id}")
    print(f"🔗 Дискриминатор: #{bot.user.discriminator}")
    print(f"⏰ Время запуска: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"🌐 Серверов подключено: {len(bot.guilds)}")
    print(f"👥 Пользователей видно: {len(set(bot.get_all_members()))}")
    print(f"📊 Версия Discord.py: {discord.__version__}")
    print(f"🐍 Версия Python: {sys.version.split()[0]}")
    print("=" * 60)
    
    # Показываем информацию о серверах
    if bot.guilds:
        print("📋 Подключенные серверы:")
        for guild in bot.guilds:
            print(f"  • {guild.name} (ID: {guild.id}) - {guild.member_count} участников")
    
    print("=" * 60)
    
    try:
        await setup_persistent_views()
        print("✅ Persistent views зарегистрированы")
        
        # Подключаем RecruitPot (если доступен) до синхронизации команд
        if RECRUIT_AVAILABLE:
            try:
                await recruit_init_db()
                await bot.add_cog(RecruitCog(bot))
                print("✅ RecruitCog подключен (ReqrutPot)")
            except Exception as e:
                print(f"⚠️ Не удалось подключить RecruitCog: {e}")

        synced = await bot.tree.sync()
        print(f"✅ Команды синхронизированы ({len(synced)} команд)")
        
        # Показываем список синхронизированных команд
        if synced:
            print("📝 Доступные команды:")
            for cmd in synced:
                print(f"  • /{cmd.name} - {cmd.description}")
        
        print("=" * 60)
        
        # Запускаем фоновые задачи
        bot.loop.create_task(monitor_channel_activity())
        print("🔍 Задача мониторинга каналов запущена")
        
        bot.loop.create_task(cleanup_channels())
        print("🧹 Задача очистки каналов запущена")
        
        bot.loop.create_task(connection_monitor())
        print("📡 Монитор подключения к интернету запущен")
        
        bot.loop.create_task(process_command_queue())
        print("⚡ Обработчик очереди команд запущен")
        
        bot.loop.create_task(update_bot_stats())
        print("📊 Обновление статистики запущено")
        
        print("=" * 60)
        print(f"🟢 Бот полностью готов к работе!")
        print("=" * 60)
        
    except Exception as e:
        print(f"❌ Ошибка инициализации: {e}")
        import traceback
        traceback.print_exc()

    # Онбординг для серверов без настроек (один раз)
    if RECRUIT_AVAILABLE:
        try:
            # Доступ к unified_settings, если импортировался
            from ..unified_settings import unified_settings as _unified_settings_local  # type: ignore
        except Exception:
            _unified_settings_local = None
        if _unified_settings_local is not None:
            for guild in bot.guilds:
                try:
                    rs = _unified_settings_local.get_recruit_settings(guild.id)
                    onboarding_sent = bool(rs.get("onboarding_sent", False))
                    forum = rs.get("forum_channel")
                    recruit_panel = rs.get("recruit_panel_channel")
                    points_panel = rs.get("points_panel_channel")
                    if not onboarding_sent and not (forum and (recruit_panel or points_panel)):
                        await _send_onboarding(guild)
                        rs["onboarding_sent"] = True
                        _unified_settings_local.update_recruit_settings(guild.id, rs)
                except Exception:
                    continue

async def setup_persistent_views():
    for session_id in ALL_SESSIONS.keys():
        view = PartySelectView(int(session_id), 0)
        bot.add_view(view)
    # Регистрируем постоянные UI из ReqrutPot, если доступны
    if RECRUIT_AVAILABLE:
        try:
            # Кнопка подачи заявки
            apply_view = PersistentApplyButtonView(bot)
            bot.add_view(apply_view)
            # Присваиваем глобальной переменной модуля, чтобы /apply использовал её
            try:
                recruit_bot_module.persistent_view = apply_view
            except Exception:
                pass
            
            bot.add_view(PersistentEventSubmitView())
            bot.add_view(UnifiedEventView())
        except Exception as e:
            print(f"Ошибка регистрации RecruitPot views: {e}")

# ── Онбординг при первом запуске/добавлении ─────────────────────────────────
def _find_announce_channel(guild: discord.Guild) -> Optional[discord.abc.Messageable]:
    if guild.system_channel and guild.system_channel.permissions_for(guild.me).send_messages:
        return guild.system_channel
    for ch in guild.text_channels:
        try:
            if ch.permissions_for(guild.me).send_messages:
                return ch
        except Exception:
            continue
    return None

async def _send_onboarding(guild: discord.Guild):
    url = "http://localhost:8082"
    try:
        channel = _find_announce_channel(guild)
        embed = discord.Embed(
            title="🎉 Бот PartyBot успешно подключён!",
            description=(
                f"**Админская панель:** {url}/guild/{guild.id}\n"
                f"**Создание событий:** {url}/guild/{guild.id}/events/guest\n\n"
                "**Что можно настроить в админке:**\n"
                "• Панели рекрута и очков участников\n"
                "• Форум-каналы и роли рекрутеров\n"
                "• Шаблоны событий для быстрого создания\n\n"
                "**Участники могут:**\n"
                "• Создавать события по ссылке выше\n"
                "• Регистрироваться на мероприятия\n"
                "• Получать очки за активность"
            ),
            color=discord.Color.green()
        )
        embed.add_field(
            name="🔗 Полезные ссылки", 
            value=(
                f"[Админка]({url}/guild/{guild.id}) • "
                f"[Создать событие]({url}/guild/{guild.id}/events/guest) • "
                f"[Веб-панель]({url}/guild/{guild.id})"
            ), 
            inline=False
        )
        embed.set_footer(text="Все настройки теперь в веб-интерфейсе!")
        
        if channel:
            await channel.send(embed=embed)
        else:
            owner = guild.owner or await guild.fetch_owner()
            try:
                await owner.send(embed=embed)
            except Exception:
                pass
    except Exception:
        pass

@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.component:
        custom_id = interaction.data.get("custom_id", "")
        if custom_id.startswith("party_select_"):
            session_id = int(custom_id.split("_")[-1])
            view = PartySelectView(session_id, interaction.user.id)
            # Обновляем компоненты для текущего пользователя
            await interaction.response.edit_message(view=view)


async def monitor_channel_activity():
    await bot.wait_until_ready()
    while not bot.is_closed():
        now_utc = datetime.now(timezone.utc)
        for guild in bot.guilds:
            guild_id = str(guild.id)
            start, end = get_guild_setting(guild.id, "monitoring_time", [10, 20])
            if start <= now_utc.hour < end:  # Проверяем только в дневное время
                monitoring_enabled = get_guild_setting(guild.id, "monitoring_enabled", True)
                if not monitoring_enabled:
                    continue
                channels = get_guild_setting(guild.id, "monitored_channels", [])
                for channel_id in channels:
                    channel = guild.get_channel(channel_id)
                    if not channel:
                        continue
                    # Получаем последнее сообщение
                    messages = [msg async for msg in channel.history(limit=1)]
                    if not messages:
                        continue
                    last_message = messages[0]
                    # Если не было сообщений 1 час
                    now = datetime.now(last_message.created_at.tzinfo)
                    if (now - last_message.created_at).total_seconds() > 4800:  # 1 час
                        # Проверяем, есть ли уже активный опрос
                        recent = [msg async for msg in channel.history(limit=3)]
                        poll_exists = any(
                            msg.author == bot.user and msg.embeds and msg.embeds[0].title == "📊 Чем займемся?"
                            for msg in recent
                        )
                        if poll_exists:
                            continue
                        # Получаем варианты опроса из шаблонов сервера
                        guild_templates = get_guild_templates(guild.id)
                        options = list(guild_templates.keys())[:8]
                        text = (
                            "📊 **Чем займемся?**\n"
                            "Голосуем за активность! (15 минут)\n\n" +
                            "\n".join([f"{i+1}. {opt}" for i, opt in enumerate(options)])
                        )
                        poll_msg = await channel.send(
                            f"@everyone\n{text}",
                            allowed_mentions=discord.AllowedMentions(everyone=True)
                        )
                        reactions = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣"]
                        for i in range(len(options)):
                            await poll_msg.add_reaction(reactions[i])
                        # Ждем 15 минут
                        await asyncio.sleep(900)
                        poll_msg = await channel.fetch_message(poll_msg.id)
                        votes = []
                        for i in range(len(options)):
                            reaction_obj = discord.utils.get(poll_msg.reactions, emoji=reactions[i])
                            count = reaction_obj.count - 1 if reaction_obj else 0
                            template_name = options[i]
                            # Используем шаблоны конкретного сервера
                            template_data = get_guild_template(guild.id, template_name)
                            roles_count = len(template_data["roles"]) if template_data else 0
                            # "Потенциал сбора" — сколько процентов от полного состава проголосовало
                            fill_ratio = count / roles_count if roles_count else 0
                            votes.append((template_name, count, fill_ratio))

                        # Сортируем сначала по fill_ratio, потом по количеству голосов
                        votes.sort(key=lambda x: (x[2], x[1]), reverse=True)
                        winner = votes[0][0] if votes and votes[0][1] > 0 else None
                        template_data = get_guild_template(guild.id, winner) if winner else None
                        if winner and template_data:
                            # template_data уже получен выше
                            guild_id = str(guild.id)
                            ping_val = get_guild_setting(guild.id, "ping_role", "everyone")
                            if ping_val == "everyone":
                                ping_text = "@everyone"
                                allowed_mentions = discord.AllowedMentions(everyone=True)
                            else:
                                role = guild.get_role(int(ping_val))
                                ping_text = role.mention if role and role.mentionable else "@everyone"
                                allowed_mentions = discord.AllowedMentions(everyone=True) if ping_text == "@everyone" else discord.AllowedMentions(roles=True)

                            text = (
                                f"{ping_text}\n"
                                f"**{template_data['title']}**\n"
                                f"{template_data['description']}\n\n"
                                "**Роли:**\n" +
                                "\n".join([f"{i+1}. {r} — Свободно" for i, r in enumerate(template_data["roles"])])
                            )
                            msg = await channel.send(text, allowed_mentions=allowed_mentions)
                            thread = await msg.create_thread(name=template_data["title"])
                            event_creator_role_id = get_guild_setting(guild.id, "event_creator_role")
                            moderator_role_id = get_guild_setting(guild.id, "moderator_role")
                            creator_id = bot.user.id  # по умолчанию

                            # Ищем первого пользователя с нужной ролью
                            for member in guild.members:
                                if event_creator_role_id and any(r.id == event_creator_role_id for r in member.roles):
                                    creator_id = member.id
                                    break
                                if moderator_role_id and any(r.id == moderator_role_id for r in member.roles):
                                    creator_id = member.id
                                    break

                            ALL_SESSIONS[str(msg.id)] = {
                                "guild_id": guild.id,
                                "channel_id": channel.id,
                                "main_msg_id": msg.id,
                                "thread_id": thread.id,
                                "title": template_data["title"],
                                "description": template_data["description"],
                                "time": "",
                                "party_roles": [{"name": r, "user_id": None} for r in template_data["roles"]],
                                "creator_id": creator_id,
                                "stopped": False,
                                "last_reminder_time": 0
                            }
                            save_all_data()
                            save_event(msg.id, ALL_SESSIONS[str(msg.id)])
                            await update_party_message(msg.id)
                            await poll_msg.delete()
                        else:
                            await channel.send("❌ Никто не проголосовал🛌.")
                            await poll_msg.delete()
        await asyncio.sleep(300)  # Проверять каждые 5 минут

        # Проверяем активные ивенты для напоминаний каждые 15 минут
        for guild in bot.guilds:
            monitored_channels = get_guild_setting(guild.id, "monitored_channels", [])
            for channel_id in monitored_channels:
                channel = guild.get_channel(channel_id)
                if not channel:
                    continue
                for session_id, session in ALL_SESSIONS.items():
                    if session["channel_id"] != channel_id or session.get("stopped"):
                        continue
                    
                    empty_roles = [r for r in session["party_roles"] if not r.get("user_id")]
                    filled_roles = [r for r in session["party_roles"] if r.get("user_id")]
                    
                    try:
                        msg = await channel.fetch_message(session["main_msg_id"])
                        event_age = (datetime.now(msg.created_at.tzinfo) - msg.created_at).total_seconds()
                        
                        # Логика напоминаний каждые 15 минут (900 секунд)
                        # Напоминаем только если есть записавшиеся, но не все роли заняты
                        reminders_enabled = get_guild_setting(guild.id, "reminders_enabled", True)
                        if reminders_enabled and filled_roles and empty_roles and event_age > 900:
                            # Проверяем, прошло ли 15 минут с последнего напоминания
                            last_reminder = session.get("last_reminder_time", 0)
                            time_since_reminder = event_age - last_reminder
                            
                            if time_since_reminder >= 900:  # 15 минут = 900 секунд
                                # Отправляем напоминание записавшимся участникам
                                mentions = [f"<@{role['user_id']}>" for role in filled_roles]
                                empty_role_names = [role['name'] for role in empty_roles]
                                
                                reminder_text = (
                                    f"📢 **Напоминание об ивенте:** {session['title']}\n"
                                    f"@everyone\n"
                                    f"Свободные роли: {', '.join(empty_role_names)}\n"
                                    f"ЗАПОЛНИТЕ РОЛИ ЧТОБЫ КОНТЕНТ СОСТОЯЛСЯ 🎮"
                                )
                                
                                await channel.send(
                                    reminder_text,
                                    allowed_mentions=discord.AllowedMentions(users=True)
                                )
                                
                                # Обновляем время последнего напоминания
                                session["last_reminder_time"] = event_age
                                ALL_SESSIONS[session_id] = session
                                save_all_data()
                        
                        # Автоматическое закрытие если прошло больше часа и есть незаполненные роли
                        elif event_age > 3600 and empty_roles:
                            session["stopped"] = True
                            ALL_SESSIONS[session_id] = session
                            save_all_data()
                            save_event(int(session_id), session)
                            await update_party_message(int(session_id))
                            await channel.send(f"🔴 Сбор **{session['title']}** завершён из-за нехватки участников.")
                            
                    except discord.NotFound:
                        # Сообщение не найдено (404) - автоматически закрываем ивент
                        print(f"Сообщение ивента {session_id} не найдено, автоматически закрываем")
                        session["stopped"] = True
                        ALL_SESSIONS[session_id] = session
                        save_all_data()
                        save_event(int(session_id), session)
                        try:
                            await channel.send(f"🔴 Ивент **{session['title']}** автоматически закрыт (сообщение удалено).")
                        except Exception:
                            pass  # Если не можем отправить уведомление, просто игнорируем
                    except Exception as e:
                        print(f"Ошибка при завершении ивента: {e}")
                        continue

# Добавьте новую функцию для очистки канала
async def cleanup_channels():
    await bot.wait_until_ready()
    while not bot.is_closed():
        now = datetime.now(timezone.utc)
        for guild in bot.guilds:
            guild_id = str(guild.id)
            hour, minute = get_guild_setting(guild.id, "cleanup_time", [0, 0])
            cleanup_enabled = get_guild_setting(guild.id, "cleanup_enabled", True)
            if not cleanup_enabled:
                continue
            if now.hour == hour and now.minute == minute:
                cleanup_channel_id = get_guild_setting(guild.id, "cleanup_channels")
                if cleanup_channel_id:
                    channel = guild.get_channel(cleanup_channel_id)
                    if channel:
                        try:
                            messages = [msg async for msg in channel.history(limit=100)]
                            if len(messages) > 1:
                                await channel.delete_messages(messages[1:])
                                await channel.send("🧹 Канал очищен, новый день — новый контент!")
                        except Exception as e:
                            print(f"Ошибка при очистке канала: {e}")
        await asyncio.sleep(60)

async def check_internet_connection():
    """Проверяет подключение к интернету"""
    try:
        # Пробуем подключиться к DNS Google
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex(("8.8.8.8", 53))
        sock.close()
        return result == 0
    except Exception:
        return False

async def check_discord_api():
    """Проверяет доступность Discord API"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://discord.com/api/v10/gateway", timeout=5) as response:
                return response.status == 200
    except Exception:
        return False

async def connection_monitor():
    """Мониторит подключение к интернету и переподключается при необходимости"""
    await bot.wait_until_ready()
    print("🔍 Монитор подключения активирован")
    
    connection_lost_time = None
    last_check_time = datetime.now()
    
    while not bot.is_closed():
        try:
            current_time = datetime.now()
            
            # Проверяем интернет соединение
            internet_ok = await check_internet_connection()
            discord_api_ok = await check_discord_api()
            
            if not internet_ok or not discord_api_ok:
                if connection_lost_time is None:
                    connection_lost_time = current_time
                    print(f"⚠️  Потеря подключения обнаружена в {current_time.strftime('%H:%M:%S')}")
                    print(f"   Интернет: {'✅' if internet_ok else '❌'}")
                    print(f"   Discord API: {'✅' if discord_api_ok else '❌'}")
                
                # Показываем время без подключения
                lost_duration = (current_time - connection_lost_time).total_seconds()
                if lost_duration > 60:  # Показываем только если больше минуты
                    minutes = int(lost_duration // 60)
                    seconds = int(lost_duration % 60)
                    print(f"🔴 Нет подключения уже {minutes}м {seconds}с")
                
                # Если бот не подключен к Discord, пытаемся переподключиться
                if bot.is_closed() or not discord_api_ok:
                    print("🔄 Попытка переподключения к Discord...")
                    try:
                        if not bot.is_closed():
                            await bot.close()
                        await asyncio.sleep(5)
                        # Здесь нужно было бы перезапустить бота, но в рамках одного процесса это сложно
                        # Вместо этого просто ждем восстановления соединения
                    except Exception as e:
                        print(f"❌ Ошибка при переподключении: {e}")
            
            else:
                # Подключение восстановлено
                if connection_lost_time is not None:
                    lost_duration = (current_time - connection_lost_time).total_seconds()
                    minutes = int(lost_duration // 60)
                    seconds = int(lost_duration % 60)
                    print(f"✅ Подключение восстановлено после {minutes}м {seconds}с")
                    connection_lost_time = None
                
                # Показываем статус каждые 10 минут при нормальной работе
                if (current_time - last_check_time).total_seconds() >= 600:  # 10 минут
                    guilds_count = len(bot.guilds)
                    users_count = len(set(bot.get_all_members()))
                    print(f"📊 Статус: {guilds_count} серверов, {users_count} пользователей | {current_time.strftime('%H:%M:%S')}")
                    last_check_time = current_time
            
            await asyncio.sleep(30)  # Проверяем каждые 30 секунд
            
        except Exception as e:
            print(f"❌ Ошибка в мониторе подключения: {e}")
            await asyncio.sleep(30)

@bot.event
async def on_disconnect():
    print(f"⚠️  Бот отключился в {datetime.now().strftime('%H:%M:%S')}")

@bot.event
async def on_resumed():
    print(f"✅ Подключение к Discord восстановлено в {datetime.now().strftime('%H:%M:%S')}")
    # Не отправляем массовые сообщения при восстановлении подключения
    # Это может вызвать спам, особенно при нестабильном соединении

@bot.event 
async def on_guild_join(guild):
    print(f"➕ Бот добавлен на сервер: {guild.name} (ID: {guild.id}) - {guild.member_count} участников")
    # Отправляем сообщение с инструкцией настройки
    await send_setup_message(guild, force=True)

@bot.event
async def on_guild_remove(guild):
    print(f"➖ Бот удален с сервера: {guild.name} (ID: {guild.id})")

async def process_command_queue():
    """Обрабатывает очередь команд от веб-интерфейса"""
    await bot.wait_until_ready()
    print("⚡ Обработчик очереди команд активирован")
    
    queue_file = 'command_queue.json'
    
    while not bot.is_closed():
        try:
            # Проверяем наличие файла очереди
            if not os.path.exists(queue_file):
                await asyncio.sleep(5)
                continue
            
            # Читаем команды
            try:
                with open(queue_file, 'r', encoding='utf-8') as f:
                    commands = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                await asyncio.sleep(5)
                continue
            
            if not commands:
                await asyncio.sleep(5)
                continue
            
            # Обрабатываем команды
            processed_commands = []
            
            for command in commands:
                try:
                    if command['type'] == 'create_event':
                        result = await process_create_event_command(command)
                        print(f"📝 Обработана команда создания события: {result}")
                    else:
                        print(f"⚠️  Неизвестный тип команды: {command['type']}")
                    
                    processed_commands.append(command)
                    
                except Exception as e:
                    print(f"❌ Ошибка обработки команды {command.get('type', 'unknown')}: {e}")
                    # Удаляем команду, которая вызвала ошибку, чтобы избежать бесконечного цикла
                    processed_commands.append(command)
            
            # Удаляем обработанные команды
            remaining_commands = [cmd for cmd in commands if cmd not in processed_commands]
            
            if remaining_commands != commands:
                with open(queue_file, 'w', encoding='utf-8') as f:
                    json.dump(remaining_commands, f, ensure_ascii=False, indent=2)
            
            await asyncio.sleep(2)  # Проверяем каждые 2 секунды
            
        except Exception as e:
            print(f"❌ Ошибка в обработчике очереди команд: {e}")
            await asyncio.sleep(10)

async def process_create_event_command(command):
    """Обрабатывает команду создания события"""
    try:
        guild_id = command['guild_id']
        channel_id = command['channel_id']
        title = command['title']
        description = command['description']
        time_str = command['time']
        roles = command['roles']
        creator_id = command['creator_id']
        
        # Получаем гильдию и канал
        guild = bot.get_guild(guild_id)
        if not guild:
            return f"Ошибка: Сервер {guild_id} не найден"
        
        channel = guild.get_channel(channel_id)
        if not channel:
            return f"Ошибка: Канал {channel_id} не найден на сервере {guild.name}"
        
        # Получаем настройки пинга
        ping_val = get_guild_setting(guild_id, "ping_role", "everyone")
        if ping_val == "everyone":
            ping_text = "@everyone"
            allowed_mentions = discord.AllowedMentions(everyone=True)
        else:
            role = guild.get_role(int(ping_val))
            ping_text = role.mention if role and role.mentionable else "@everyone"
            allowed_mentions = discord.AllowedMentions(everyone=True) if ping_text == "@everyone" else discord.AllowedMentions(roles=True)
        
        # Создаем текст сообщения
        text = (
            f"{ping_text}\n"
            f"**{title}**\n"
            f"{description}\n\n"
        )
        
        if time_str:
            text += f"**Время:** {time_str}\n\n"
        
        text += "**Роли:**\n" + "\n".join([f"{i+1}. {r} — Свободно" for i, r in enumerate(roles)])
        
        # Отправляем сообщение
        msg = await channel.send(text, allowed_mentions=allowed_mentions)
        thread = await msg.create_thread(name=title)
        
        # Сохраняем в базу
        ALL_SESSIONS[str(msg.id)] = {
            "guild_id": guild_id,
            "channel_id": channel_id,
            "main_msg_id": msg.id,
            "thread_id": thread.id,
            "title": title,
            "description": description,
            "time": time_str,
            "party_roles": [{"name": r, "user_id": None} for r in roles],
            "creator_id": creator_id,
            "stopped": False,
            "last_reminder_time": 0
        }
        save_all_data()
        save_event(msg.id, ALL_SESSIONS[str(msg.id)])
        await update_party_message(msg.id)
        
        return f"Успешно создано событие '{title}' в канале #{channel.name}"
        
    except Exception as e:
        return f"Ошибка создания события: {str(e)}"

async def update_bot_stats():
    """Обновляет статистику бота для веб-интерфейса"""
    await bot.wait_until_ready()
    print("📊 Обновление статистики активировано")
    
    while not bot.is_closed():
        try:
            total_members = 0
            online_members = 0
            guilds_count = len(bot.guilds)
            active_events = len([s for s in ALL_SESSIONS.values() if not s.get('stopped', False)])
            
            # Подсчитываем участников
            for guild in bot.guilds:
                total_members += guild.member_count or 0
                
                # Подсчитываем онлайн участников (только для небольших серверов)
                if guild.member_count and guild.member_count < 1000:
                    try:
                        online_count = len([m for m in guild.members if m.status != discord.Status.offline])
                        online_members += online_count
                    except:
                        # Если нет доступа к участникам, пропускаем
                        pass
            
            # Формируем статистику
            stats = {
                'guilds_count': guilds_count,
                'total_members': total_members,
                'online_members': online_members,
                'active_events': active_events,
                'last_updated': datetime.now().isoformat()
            }
            
            # Сохраняем в файл для веб-интерфейса
            try:
                with open('bot_stats.json', 'w', encoding='utf-8') as f:
                    json.dump(stats, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"❌ Ошибка сохранения статистики: {e}")
            
            # Обновляем каждые 5 минут
            await asyncio.sleep(300)
            
        except Exception as e:
            print(f"❌ Ошибка обновления статистики: {e}")
            await asyncio.sleep(60)  # При ошибке ждём меньше

@bot.event
async def on_error(event, *args, **kwargs):
    """Глобальный обработчик ошибок бота"""
    import traceback
    print(f"❌ Ошибка в событии {event}: {traceback.format_exc()}")

@bot.event
@bot.event
async def on_command_error(ctx, error):
    """Обработчик ошибок команд"""
    if isinstance(error, commands.CommandNotFound):
        return  # Игнорируем неизвестные команды
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ У вас недостаточно прав для выполнения этой команды")
    elif isinstance(error, commands.BotMissingPermissions):
        await ctx.send("❌ У бота недостаточно прав для выполнения этой команды")
    else:
        print(f"❌ Ошибка команды {ctx.command}: {error}")
        await ctx.send("❌ Произошла ошибка при выполнении команды")

# Функция для безопасного запуска бота с переподключением
async def start_bot_with_reconnect():
    """Запускает бота с автоматическим переподключением при ошибках"""
    retry_count = 0
    max_retries = 5
    
    while retry_count < max_retries:
        try:
            print("=" * 60)
            print(f"🚀 Попытка запуска бота #{retry_count + 1}")
            print("=" * 60)
            
            await bot.start(BOT_TOKEN)
            
        except discord.LoginFailure:
            print("❌ КРИТИЧЕСКАЯ ОШИБКА: Неверный токен бота!")
            print("🔧 Проверьте config.json и убедитесь, что токен корректный")
            break
            
        except discord.HTTPException as e:
            print(f"❌ Ошибка HTTP: {e}")
            if e.status == 429:  # Rate limit
                print("⏳ Превышен лимит запросов, ждем...")
                await asyncio.sleep(60)
            retry_count += 1
            
        except discord.ConnectionClosed as e:
            print(f"🔌 Соединение закрыто: {e}")
            retry_count += 1
            
        except Exception as e:
            print(f"❌ Неожиданная ошибка: {e}")
            import traceback
            traceback.print_exc()
            retry_count += 1
        
        if retry_count < max_retries:
            wait_time = min(2 ** retry_count, 60)  # Экспоненциальная задержка, максимум 60 секунд
            print(f"⏳ Ожидание {wait_time} секунд перед повторной попыткой...")
            await asyncio.sleep(wait_time)
        else:
            print("❌ Превышено максимальное количество попыток подключения")
            break

def start_web_server():
    """Запуск веб-сервера в отдельном потоке"""
    try:
        print("🌐 Запуск веб-интерфейса...")
        from web import app
        app.run(host='localhost', port=8082, debug=False, use_reloader=False, threaded=True)
    except Exception as e:
        print(f"❌ Ошибка веб-сервера: {e}")

def start_both():
    """Запуск бота и веб-сервера"""
    print("🚀 Запуск Discord бота с веб-интерфейсом...")
    print("=" * 60)
    print("📋 Компоненты:")
    print("  🤖 Discord бот")
    print("  🌐 Веб-интерфейс (http://localhost:8082)")
    print("  🌍 Внешний доступ: https://8bf681c15819.ngrok-free.app")
    print("=" * 60)
    
    # Запускаем веб-сервер в отдельном потоке
    web_thread = threading.Thread(target=start_web_server, daemon=True)
    web_thread.start()
    
    # Даем веб-серверу время запуститься
    time.sleep(2)
    print("✅ Веб-интерфейс запущен")
    
    # Запускаем бота
    try:
        asyncio.run(start_bot_with_reconnect())
    except KeyboardInterrupt:
        print("\n🛑 Получен сигнал остановки (Ctrl+C)")
        print("👋 Бот завершает работу...")
    except Exception as e:
        print(f"💥 Критическая ошибка при запуске: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("🏁 Работа бота завершена")

if __name__ == "__main__":
    # Проверяем аргументы командной строки
    if len(sys.argv) > 1 and sys.argv[1] == "--bot-only":
        # Запуск только бота
        print("🎯 Запуск Discord бота для управления ивентами")
        print(f"📅 Дата запуска: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)
        
        try:
            asyncio.run(start_bot_with_reconnect())
        except KeyboardInterrupt:
            print("\n🛑 Получен сигнал остановки (Ctrl+C)")
            print("👋 Бот завершает работу...")
        except Exception as e:
            print(f"💥 Критическая ошибка при запуске: {e}")
            import traceback
            traceback.print_exc()
        finally:
            print("🏁 Работа бота завершена")
    else:
        # Запуск бота + веб-интерфейса
        start_both()