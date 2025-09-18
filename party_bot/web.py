from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_session import Session
import requests
import json
import os
import time
import discord
from datetime import datetime, timedelta
import asyncio
import threading
import sys
import traceback
import logging
from copy import deepcopy

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
    ENV_LOADED = True
except ImportError:
    ENV_LOADED = False
    print("python-dotenv not installed, using config.json only")

# Добавляем в sys.path корень проекта (папку Bigbot)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

# Импорт unified_settings с резервным вариантом
try:
    from unified_settings import UnifiedSettings
except ImportError as e:
    print(f"Ошибка импорта unified_settings: {e}")
    UnifiedSettings = None

# Глобальная переменная для экземпляра бота
bot_instance = None

# Очередь задач для асинхронных операций
task_queue = []

def set_bot_instance(bot):
    """Устанавливает глобальный экземпляр бота"""
    global bot_instance
    bot_instance = bot

def get_bot_instance():
    """Получает экземпляр бота"""
    global bot_instance
    if bot_instance is not None:
        return bot_instance
    
    # Попробуем получить из импорта
    try:
        from party_bot.main import bot
        bot_instance = bot
        return bot_instance
    except Exception as e:
        print(f"Ошибка получения бота: {e}")
        return None

def queue_async_task(coro):
    """Добавляет асинхронную задачу в очередь"""
    global task_queue
    task_queue.append(coro)

def execute_async_task(coro):
    """Выполняет асинхронную задачу в цикле бота"""
    bot = get_bot_instance()
    if not bot or not bot.loop:
        raise Exception("Бот или его event loop недоступен")
    
    # Создаем новую задачу в цикле бота
    task = bot.loop.create_task(coro)
    
    # Ждем выполнения в другом потоке
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(lambda: bot.loop.run_until_complete(task))
        return future.result(timeout=10)

# Импорт бэкэнда recruit_bot (очки/магазин) для веб-интеграции
try:
    import sys
    import os
    # Добавляем путь к корню проекта
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from recruit_bot.database import EventDatabase
    RECRUIT_DB_AVAILABLE = True
except Exception as e:
    print(f"Ошибка импорта EventDatabase: {e}")
    RECRUIT_DB_AVAILABLE = False

# Импортируем единую систему настроек
try:
    from unified_settings import unified_settings, get_recruit_config, update_recruit_config
    # Принудительно отключаем unified_settings для синхронизации с Discord ботом
    USE_UNIFIED_SETTINGS = False  # Принудительно используем main.py функции
except ImportError as e:
    print(f"Ошибка импорта unified_settings: {e}")
    USE_UNIFIED_SETTINGS = False
    unified_settings = None
    def get_recruit_config(guild_id): return {}
    def update_recruit_config(guild_id, **kwargs): return False

# Импорт новой простой системы настроек (абсолютный пакетный)
try:
    from party_bot.simple_settings_db import get_settings_db, get_guild_setting, set_guild_setting, get_guild_settings, save_all_data, reload_settings_from_disk
    print("✅ Используется новая простая система настроек (abs import)")
    USING_DATABASE = True
    USING_FAST_DB = True
except ImportError as e:
    print(f"⚠️ Простая система недоступна (abs): {e}")
    USING_FAST_DB = False
    # Fallback: пробуем через main (который уже настраивает импорты)
    try:
        import party_bot.main as main_module
        get_guild_settings = main_module.get_guild_settings
        set_guild_setting = main_module.set_guild_setting
        get_guild_setting = main_module.get_guild_setting
        save_all_data = main_module.save_all_data
        reload_settings_from_disk = main_module.reload_settings_from_disk
        print("✅ Fallback через main_module успешен")
        USING_DATABASE = getattr(main_module, 'USING_DATABASE', False)
    except Exception as e2:
        print(f"❌ Fallback через main_module провалился: {e2}")
        def get_guild_settings(guild_id): return {}
        def set_guild_setting(guild_id, key, value): pass
        def get_guild_setting(guild_id, key, default=None): return default
        def save_all_data(): pass
        def reload_settings_from_disk(): pass
        USING_DATABASE = False

# Импортируем остальные функции из main.py (пакет party_bot)
try:
    from party_bot import main as main_module
except ImportError:
    try:
        from . import main as main_module  # относительный fallback
    except ImportError as e:
        print(f"❌ Ошибка импорта дополнительных функций из main.py: {e}")
        main_module = None

if main_module:
    get_guild_templates = main_module.get_guild_templates
    set_guild_template = main_module.set_guild_template
    delete_guild_template = main_module.delete_guild_template
    get_guild_template = main_module.get_guild_template
    save_guild_templates = main_module.save_guild_templates
    ALL_SESSIONS = main_module.ALL_SESSIONS
    save_event = main_module.save_event
    bot = main_module.bot
else:
    # Создаем заглушки
    def get_guild_templates(guild_id): return {}
    def set_guild_template(guild_id, name, data): pass
    def delete_guild_template(guild_id, name): return False
    def get_guild_template(guild_id, name): return None
    def save_guild_templates(guild_id, templates): pass
    ALL_SESSIONS = {}
    def save_event(event_id, data): pass
    bot = None
    # Заглушка оставляем, но ниже добавим ленивый прокси
    async def update_party_message(event_id, interacting_user_id=None):
        pass

# Ленивый прокси для избежания циклического импорта и использования реальной функции после старта бота
async def update_party_message_web(event_id, interacting_user_id=None):
    try:
        # Импортируем при вызове, когда main уже инициализирован
        import main
        return await main.update_party_message(event_id, interacting_user_id=interacting_user_id)
    except Exception as e:
        print(f"update_party_message_web fallback: {e}")
        # Мягкий фоллбек: не роняем веб, просто логируем
        return None

# Функции-обёртки больше не нужны, используем прямые импорты из БД или main.py
# get_guild_settings, set_guild_setting, get_guild_setting уже импортированы выше

# Настройка путей для Flask
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(SCRIPT_DIR, "templates")

app = Flask(__name__, template_folder=TEMPLATES_DIR)

# Загружаем конфигурацию из .env или config.json
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET", "")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI", "http://localhost:8082/callback")
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "your-secret-key-here")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_USERS = os.getenv("ADMIN_USERS", "").split(",") if os.getenv("ADMIN_USERS") else []

# Fallback к config.json если .env не загружен
if not ENV_LOADED or not DISCORD_CLIENT_ID:
    try:
        CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
            if "web" in config:
                DISCORD_CLIENT_ID = DISCORD_CLIENT_ID or config["web"].get("client_id", "")
                DISCORD_CLIENT_SECRET = DISCORD_CLIENT_SECRET or config["web"].get("client_secret", "")
            BOT_TOKEN = BOT_TOKEN or config.get("bot_token", "")
            if not ADMIN_USERS:
                ADMIN_USERS = config.get("admin_users", [])
            SUPPORT_SERVER = config.get("support_server", {})
    except Exception as e:
        logging.error(f"Ошибка загрузки конфигурации: {e}")
        BOT_TOKEN = BOT_TOKEN or ""
        ADMIN_USERS = ADMIN_USERS or []
        SUPPORT_SERVER = {}

app.secret_key = FLASK_SECRET_KEY
app.config['SESSION_TYPE'] = 'filesystem'
Session(app)

@app.context_processor
def inject_globals():
    """Добавляет глобальные переменные в шаблоны"""
    return {
        'support_server': SUPPORT_SERVER if 'SUPPORT_SERVER' in globals() else {},
        'bot_name': 'Potatos Party Bot',
        'admin_users': ADMIN_USERS
    }

DISCORD_API_BASE_URL = "https://discord.com/api/v10"

# ===================== DEFAULT SETTINGS LAYER =====================
# Базовые дефолты для всех серверов. Любой отсутствующий ключ будет подставлен
# чтобы шаблон не падал KeyError / AttributeError. Для вложенных структур используем merge.
DEFAULT_SETTINGS = {
    'monitoring_enabled': False,
    'cleanup_enabled': False,
    'reminders_enabled': False,
    'reminder_time': [0, 15],  # [hours, minutes]
    'event_creator_role': None,
    'moderator_role': None,
    'ping_role': 'everyone',
    'monitored_channels': [],
    # Вложенный блок рекрутинга (соответствует обращениям в шаблоне)
    'recruit_settings': {
        'default_role': None,
        'recruit_role': None,
        'guild_name': '',
        'cooldown_hours': 1,
        'points_moderator_roles': '',  # строка через запятую
        'recruiter_roles': '',         # строка через запятую
        'events_channel': None,
        'shop_channel': None,
        'forum_channel': None,
        'points_panel_channel': None,
        'recruit_panel_channel': None,
        'points_start_date': '',
        'points_end_date': ''
    }
}

def deep_merge(defaults: dict, actual: dict) -> dict:
    """Глубокое объединение: значения из actual перекрывают defaults, но структуры сохраняются.
    Оба словаря не модифицируются (copy)."""
    result = deepcopy(defaults)
    if not isinstance(actual, dict):
        return result
    for k, v in actual.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result

def get_complete_guild_settings(guild_id: int) -> dict:
    """Возвращает полные настройки с применением дефолтов и безопасным блоком recruit_settings.
    Никогда не возвращает None. Гарантирует наличие всех ключей, ожидаемых шаблоном.
    """
    try:
        raw = get_guild_settings(guild_id) or {}
    except Exception as e:
        print(f"[SETTINGS] Ошибка загрузки настроек guild {guild_id}: {e}")
        raw = {}

    # Миграция: если в settings.db нет recruit_settings, но можно получить из legacy источников
    if 'recruit_settings' not in raw or not raw.get('recruit_settings'):
        migrated = None
        # unified_settings как источник
        if USE_UNIFIED_SETTINGS:
            try:
                unified = get_recruit_config(guild_id)
                if isinstance(unified, dict) and unified:
                    migrated = unified
            except Exception:
                pass
        # EventDatabase fallback
        if migrated is None and RECRUIT_DB_AVAILABLE:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                migrated = loop.run_until_complete(get_recruit_settings(guild_id))
                loop.close()
            except Exception as e:
                print(f"[Recruit Migration] fallback load failed: {e}")
                migrated = None
        if isinstance(migrated, dict) and migrated:
            try:
                set_guild_setting(guild_id, 'recruit_settings', migrated)
                raw['recruit_settings'] = migrated
                print(f"[Recruit Migration] guild {guild_id}: migrated legacy recruit settings into settings.db")
            except Exception as e:
                print(f"[Recruit Migration] save failed guild {guild_id}: {e}")

    # recruit_settings может прийти как None / строка / др. Приводим к dict перед merge.
    rs = raw.get('recruit_settings')
    if not isinstance(rs, dict):
        # Попытка распарсить если строка JSON
        if isinstance(rs, str):
            try:
                parsed = json.loads(rs)
                if isinstance(parsed, dict):
                    rs = parsed
                else:
                    rs = {}
            except Exception:
                rs = {}
        else:
            rs = {}
    raw['recruit_settings'] = rs

    complete = deep_merge(DEFAULT_SETTINGS, raw)
    return complete

# ===================== ERROR HANDLERS =====================
@app.errorhandler(500)
def internal_error(e):
    import traceback, uuid
    err_id = uuid.uuid4().hex[:8]
    tb = traceback.format_exc()
    print(f"[ERROR 500][{err_id}] {e}\n{tb}")
    logging.error(f"500 {err_id}: {e}\n{tb}")
    # Возвращаем простой JSON если AJAX, иначе страницу
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify({'success': False, 'error': 'Internal Server Error', 'error_id': err_id}), 500
    return render_template('error.html' if os.path.exists(os.path.join(TEMPLATES_DIR,'error.html')) else 'base.html',
                           error_id=err_id, error=str(e)), 500

def is_bot_admin(user_id):
    """Проверить, является ли пользователь администратором бота"""
    return str(user_id) in ADMIN_USERS

@app.errorhandler(404)
def not_found_error(error):
    return render_template('404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    return render_template('500.html'), 500

@app.errorhandler(Exception)
def handle_exception(e):
    # Логируем ошибку
    logging.error(f"Unhandled exception: {e}", exc_info=True)
    
    # Если это HTTP ошибка, возвращаем её код
    if hasattr(e, 'code'):
        return render_template('500.html'), e.code
    
    # Иначе возвращаем 500
    return render_template('500.html'), 500

# Функции для работы с настройками recruitment
async def get_recruit_settings(guild_id):
    """Получить настройки recruitment для гильдии"""
    # 1. Основной источник теперь settings.db (ключ 'recruit_settings')
    try:
        base = get_guild_settings(int(guild_id)) or {}
        rs = base.get('recruit_settings')
        if isinstance(rs, str):
            try:
                import json as _json
                parsed = _json.loads(rs)
                if isinstance(parsed, dict):
                    rs = parsed
            except Exception:
                rs = None
        if isinstance(rs, dict):
            return rs
    except Exception as e:
        print(f"[Recruit] read from settings.db failed: {e}")

    # 2. Unified settings (если включено)
    if USE_UNIFIED_SETTINGS:
        try:
            return get_recruit_config(guild_id) or {}
        except Exception:
            pass

    # 3. Старый EventDatabase как последний fallback
    if RECRUIT_DB_AVAILABLE:
        try:
            config = await EventDatabase.get_guild_config(guild_id)
            return config or {}
        except Exception as e:
            print(f"Ошибка получения настроек recruitment (fallback DB) {guild_id}: {e}")
    return {}

async def update_recruit_settings(guild_id, settings):
    """Обновить настройки recruitment для гильдии"""
    guild_id_int = int(guild_id)
    # 1. Пишем в settings.db под ключом 'recruit_settings'
    try:
        current = get_guild_settings(guild_id_int) or {}
        existing = current.get('recruit_settings')
        if isinstance(existing, str):
            try:
                import json as _json
                parsed = _json.loads(existing)
                if isinstance(parsed, dict):
                    existing = parsed
            except Exception:
                existing = {}
        if not isinstance(existing, dict):
            existing = {}
        merged = {**existing, **settings}
        set_guild_setting(guild_id_int, 'recruit_settings', merged)
        success_primary = True
    except Exception as e:
        print(f"[Recruit] primary save to settings.db failed: {e}")
        success_primary = False

    # 2. Синхронно обновляем unified_settings если включено (не критично)
    if USE_UNIFIED_SETTINGS:
        try:
            update_recruit_config(guild_id_int, **settings)
        except Exception as e:
            print(f"[Recruit] unified_settings update failed: {e}")

    # 3. Обновляем старую EventDatabase для обратной совместимости (best effort)
    if RECRUIT_DB_AVAILABLE:
        try:
            # Асинхронная операция — выполняем реально
            await EventDatabase.update_guild_config(guild_id_int, **settings)
        except Exception as e:
            print(f"[Recruit] legacy EventDatabase update failed: {e}")

    return success_primary

async def get_event_submissions(guild_id, limit=50):
    """Получить заявки на события для гильдии"""
    if not RECRUIT_DB_AVAILABLE:
        return []
    
    try:
        import aiosqlite
        db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "potatos_recruit.db")
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("""
                SELECT id, submitter_id, event_type, action, group_size, base_points, 
                       status, created_at, description
                FROM event_submissions 
                WHERE guild_id = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            """, (guild_id, limit))
            
            rows = await cursor.fetchall()
            submissions = []
            for row in rows:
                submissions.append({
                    'id': row[0],
                    'submitter_id': row[1],
                    'submitter_name': f'User {row[1]}',  # Здесь можно добавить получение имени пользователя
                    'event_type': row[2],
                    'action': row[3],
                    'group_size': row[4],
                    'base_points': row[5],
                    'status': row[6],
                    'created_at': row[7],
                    'description': row[8]
                })
            return submissions
    except Exception as e:
        print(f"Ошибка получения заявок для гильдии {guild_id}: {e}")
        return []

async def get_shop_purchases(guild_id, limit=50):
    """Получить покупки в магазине для гильдии"""
    if not RECRUIT_DB_AVAILABLE:
        return []
    
    try:
        import aiosqlite
        db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "potatos_recruit.db")
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("""
                SELECT id, user_id, item_name, points_cost, status, created_at
                FROM shop_purchases 
                WHERE guild_id = ? 
                ORDER BY created_at DESC 
                LIMIT ?
            """, (guild_id, limit))
            
            rows = await cursor.fetchall()
            purchases = []
            for row in rows:
                purchases.append({
                    'id': row[0],
                    'user_id': row[1],
                    'user_name': f'User {row[1]}',  # Здесь можно добавить получение имени пользователя
                    'item_name': row[2],
                    'points_cost': row[3],
                    'status': row[4],
                    'created_at': row[5]
                })
            return purchases
    except Exception as e:
        print(f"Ошибка получения покупок для гильдии {guild_id}: {e}")
        return []

async def get_user_points_leaderboard(guild_id, limit=20):
    """Получить таблицу лидеров по очкам для гильдии"""
    if not RECRUIT_DB_AVAILABLE:
        return []
    
    try:
        import aiosqlite
        db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "potatos_recruit.db")
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("""
                SELECT user_id, total_points, events_participated, last_updated
                FROM user_points 
                WHERE guild_id = ? 
                ORDER BY total_points DESC 
                LIMIT ?
            """, (guild_id, limit))
            
            rows = await cursor.fetchall()
            leaderboard = []
            for row in rows:
                leaderboard.append({
                    'user_id': row[0],
                    'user_name': f'User {row[0]}',  # Здесь можно добавить получение имени пользователя
                    'total_points': row[1],
                    'events_participated': row[2],
                    'last_updated': row[3]
                })
            return leaderboard
    except Exception as e:
        print(f"Ошибка получения таблицы лидеров для гильдии {guild_id}: {e}")
        return []

def user_has_permissions_session(user_guilds, bot_guilds, guild_id):
    """Проверить права с учетом сессии пользователя"""
    user_id = session.get('user', {}).get('id')
    if user_id and is_bot_admin(user_id):
        return True
    return user_has_permissions(user_guilds, bot_guilds, guild_id, user_id)

def get_user_guilds(access_token):
    """Получить список серверов пользователя"""
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(f"{DISCORD_API_BASE_URL}/users/@me/guilds", headers=headers)
    if response.status_code == 200:
        return response.json()
    return []

def get_user_guild_member(guild_id, access_token):
    """Получить информацию о участнике сервера с ролями"""
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(f"{DISCORD_API_BASE_URL}/users/@me/guilds/{guild_id}/member", headers=headers)
    if response.status_code == 200:
        return response.json()
    return None

def user_has_role(guild_id, user_id, role_id, access_token):
    """Проверить, есть ли у пользователя определенная роль"""
    try:
        # Получаем информацию о пользователе на сервере через Discord API
        member_info = get_user_guild_member(guild_id, access_token)
        if member_info and 'roles' in member_info:
            return str(role_id) in member_info['roles']
        
        # Альтернативный способ через бота, если API недоступен
        bot = get_bot_instance()
        if bot:
            guild = bot.get_guild(int(guild_id))
            if guild:
                member = guild.get_member(int(user_id))
                if member:
                    return any(role.id == int(role_id) for role in member.roles)
        
        return False
    except Exception as e:
        print(f"Error checking role {role_id} for user {user_id} in guild {guild_id}: {e}")
        return False

def get_guild_event_creator_roles(guild_id):
    """Получить роли, которые могут создавать события"""
    try:
        # Ищем в настройках сервера
        guild_settings = get_guild_setting(int(guild_id), "event_creator_roles", [])
        if guild_settings:
            return guild_settings
        
        # Возвращаем роль из старых настроек, если есть
        event_role = get_guild_setting(int(guild_id), "event_creator_role")
        if event_role:
            return [event_role]
        
        return []
    except Exception as e:
        print(f"Error getting event creator roles for guild {guild_id}: {e}")
        return []

def set_guild_event_creator_roles(guild_id, role_ids):
    """Установить роли, которые могут создавать события"""
    try:
        set_guild_setting(int(guild_id), "event_creator_roles", role_ids)
        return True
    except Exception as e:
        print(f"Error setting event creator roles for guild {guild_id}: {e}")
        return False

# ======== API для очков/магазина (ReqrutPot) ========

def get_bot_stats():
    """Получить статистику бота"""
    try:
        # Проверяем, есть ли файл со статистикой от бота
        stats_file = 'bot_stats.json'
        if os.path.exists(stats_file):
            with open(stats_file, 'r', encoding='utf-8') as f:
                stats = json.load(f)
                return stats
        
        # Если файла нет, возвращаем базовую статистику
        bot_guilds = get_bot_guilds()
        return {
            'guilds_count': len(bot_guilds),
            'total_members': 0,
            'online_members': 0,
            'last_updated': None
        }
    except Exception as e:
        print(f"Error getting bot stats: {e}")
        return {
            'guilds_count': 0,
            'total_members': 0,
            'online_members': 0,
            'last_updated': None
        }

# ======== API для очков/магазина/заявок (recruit_bot) ========

@app.route('/guild/<guild_id>/stats')
def guild_stats(guild_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        flash('У вас нет прав для управления ботом на этом сервере', 'error')
        return redirect(url_for('dashboard'))

    guild_info = next((g for g in user_guilds if g['id'] == guild_id), None)
    # Лидерборд, покупки, заявки
    leaderboard = asyncio.run(get_user_points_leaderboard(int(guild_id), limit=20)) if RECRUIT_DB_AVAILABLE else []
    submissions = asyncio.run(get_event_submissions(int(guild_id), limit=20)) if RECRUIT_DB_AVAILABLE else []
    purchases = asyncio.run(get_shop_purchases(int(guild_id), limit=20)) if RECRUIT_DB_AVAILABLE else []

    # Активные события
    try:
        import main
        _SESS = main.ALL_SESSIONS
        active_cnt = sum(1 for ev in _SESS.values() if ev.get('guild_id') == int(guild_id) and not ev.get('stopped'))
    except Exception:
        active_cnt = 0

    return render_template('guild_stats.html', guild=guild_info, leaderboard=leaderboard,
                           submissions=submissions, purchases=purchases, active_events=active_cnt)

@app.route('/api/<guild_id>/leaderboard')
def api_leaderboard(guild_id):
    if not RECRUIT_DB_AVAILABLE:
        return jsonify({'error': 'Recruit module unavailable'}), 501
    try:
        limit = int(request.args.get('limit', 10))
        import asyncio
        data = asyncio.run(EventDatabase.get_leaderboard(int(guild_id), limit))
        items = [
            {
                'user_id': user_id,
                'points': points,
                'events': events
            } for (user_id, points, events) in data
        ]
        return jsonify({'leaderboard': items})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/<guild_id>/balance/<user_id>')
def api_user_balance(guild_id, user_id):
    if not RECRUIT_DB_AVAILABLE:
        return jsonify({'error': 'Recruit module unavailable'}), 501
    try:
        import asyncio
        points, events = asyncio.run(EventDatabase.get_user_points(int(guild_id), int(user_id)))
        return jsonify({'user_id': int(user_id), 'points': points, 'events': events})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/<guild_id>/submissions/pending')
def api_pending_submissions(guild_id):
    if not RECRUIT_DB_AVAILABLE:
        return jsonify({'error': 'Recruit module unavailable'}), 501
    try:
        import asyncio
        items = asyncio.run(EventDatabase.get_pending_submissions(int(guild_id)))
        return jsonify({'pending': items})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/<guild_id>/submissions/approve', methods=['POST'])
def api_approve_submission(guild_id):
    if not RECRUIT_DB_AVAILABLE:
        return jsonify({'error': 'Recruit module unavailable'}), 501
    try:
        payload = request.get_json(force=True)
        submission_id = int(payload.get('submission_id'))
        multiplier = float(payload.get('multiplier', 1.0))
        reviewer_id = int(session.get('user', {}).get('id', 0) or 0)

        import asyncio
        ok = asyncio.run(EventDatabase.approve_event_submission(
            submission_id=submission_id,
            reviewer_id=reviewer_id,
            final_multiplier=multiplier
        ))
        if not ok:
            return jsonify({'success': False, 'error': 'Cannot approve'}), 400
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/<guild_id>/submissions/reject', methods=['POST'])
def api_reject_submission(guild_id):
    if not RECRUIT_DB_AVAILABLE:
        return jsonify({'error': 'Recruit module unavailable'}), 501
    try:
        payload = request.get_json(force=True)
        submission_id = int(payload.get('submission_id'))
        reason = payload.get('reason')
        reviewer_id = int(session.get('user', {}).get('id', 0) or 0)

        import asyncio
        ok = asyncio.run(EventDatabase.reject_event_submission(
            submission_id=submission_id,
            reviewer_id=reviewer_id,
            reason=reason
        ))
        if not ok:
            return jsonify({'success': False, 'error': 'Cannot reject'}), 400
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/<guild_id>/submissions/<int:submission_id>')
def api_get_submission_details(guild_id, submission_id):
    if not RECRUIT_DB_AVAILABLE:
        return jsonify({'error': 'Recruit module unavailable'}), 501
    try:
        import asyncio
        submission = asyncio.run(EventDatabase.get_submission_details(submission_id))
        if not submission:
            return jsonify({'error': 'Submission not found'}), 404
        return jsonify({'success': True, 'submission': submission})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/<guild_id>/users/names', methods=['POST'])
def api_get_user_names(guild_id):
    """API для получения имен пользователей Discord"""
    try:
        payload = request.get_json(force=True)
        user_ids = payload.get('user_ids', [])
        
        if not user_ids:
            return jsonify({'success': True, 'users': []})
        
        # Получаем бота из импорта
        if not bot:
            return jsonify({'error': 'Bot not available'}), 503
        
        guild = bot.get_guild(int(guild_id))
        if not guild:
            return jsonify({'error': 'Guild not found'}), 404
        
        users = []
        for user_id in user_ids:
            try:
                member = guild.get_member(int(user_id))
                if member:
                    users.append({
                        'id': member.id,
                        'username': member.name,
                        'display_name': member.display_name,
                        'avatar_url': str(member.avatar.url) if member.avatar else None
                    })
                else:
                    # Пытаемся получить пользователя через fetch
                    try:
                        user = bot.get_user(int(user_id))
                        if user:
                            users.append({
                                'id': user.id,
                                'username': user.name,
                                'display_name': user.display_name,
                                'avatar_url': str(user.avatar.url) if user.avatar else None
                            })
                        else:
                            users.append({
                                'id': user_id,
                                'username': f'Пользователь {user_id}',
                                'display_name': f'Пользователь {user_id}',
                                'avatar_url': None
                            })
                    except:
                        users.append({
                            'id': user_id,
                            'username': f'Пользователь {user_id}',
                            'display_name': f'Пользователь {user_id}',
                            'avatar_url': None
                        })
            except Exception as e:
                users.append({
                    'id': user_id,
                    'username': f'Пользователь {user_id}',
                    'display_name': f'Пользователь {user_id}',
                    'avatar_url': None
                })
        
        return jsonify({'success': True, 'users': users})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/<guild_id>/shop/pending')
def api_pending_shop(guild_id):
    if not RECRUIT_DB_AVAILABLE:
        return jsonify({'error': 'Recruit module unavailable'}), 501
    try:
        import asyncio
        items = asyncio.run(EventDatabase.get_pending_purchases(int(guild_id)))
        return jsonify({'pending': items})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/<guild_id>/submissions/all')
def api_all_submissions(guild_id):
    """API для получения всех заявок с фильтрацией"""
    if not RECRUIT_DB_AVAILABLE:
        return jsonify({'error': 'Recruit module unavailable'}), 501
    try:
        status = request.args.get('status')  # pending, approved, rejected
        limit = int(request.args.get('limit', 50))
        
        import asyncio
        items = asyncio.run(EventDatabase.get_all_submissions(int(guild_id), status, limit))
        return jsonify({'submissions': items, 'total': len(items)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/<guild_id>/shop/process', methods=['POST'])
def api_process_shop(guild_id):
    if not RECRUIT_DB_AVAILABLE:
        return jsonify({'error': 'Recruit module unavailable'}), 501
    try:
        payload = request.get_json(force=True)
        purchase_id = int(payload.get('purchase_id'))
        action = payload.get('action', 'give')
        reason = payload.get('reason')
        completed = action.lower() in ('give', 'выдать', 'complete', 'ok', 'approve')
        admin_id = int(session.get('user', {}).get('id', 0) or 0)

        import asyncio
        ok = asyncio.run(EventDatabase.process_shop_purchase(
            purchase_id=purchase_id,
            admin_id=admin_id,
            completed=completed,
            admin_notes=reason
        ))
        if not ok:
            return jsonify({'success': False, 'error': 'Already processed or not found'}), 400
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    """Получить статистику бота"""
    try:
        # Проверяем, есть ли файл со статистикой от бота
        stats_file = 'bot_stats.json'
        if os.path.exists(stats_file):
            with open(stats_file, 'r', encoding='utf-8') as f:
                stats = json.load(f)
                return stats
        
        # Если файла нет, возвращаем базовую статистику
        bot_guilds = get_bot_guilds()
        return {
            'guilds_count': len(bot_guilds),
            'total_members': 0,
            'online_members': 0,
            'last_updated': None
        }
    except:
        return {
            'guilds_count': 0,
            'total_members': 0,
            'online_members': 0,
            'last_updated': None
        }

def get_bot_guilds():
    """Получить список серверов бота"""
    headers = {"Authorization": f"Bot {BOT_TOKEN}"}
    response = requests.get(f"{DISCORD_API_BASE_URL}/users/@me/guilds", headers=headers)
    if response.status_code == 200:
        return response.json()
    return []

@app.route('/api/guild/<guild_id>/recruit-config')
def api_guild_recruit_config(guild_id):
    """Диагностика: вернуть текущую конфигурацию рекрутинга из БД"""
    if not RECRUIT_DB_AVAILABLE:
        return jsonify({'error': 'Recruit module unavailable'}), 501
    try:
        cfg = asyncio.run(EventDatabase.get_guild_config(int(guild_id)))
        return jsonify({'guild_id': int(guild_id), 'config': cfg or {}})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def generate_bot_invite_url():
    """Генерировать ссылку для добавления бота"""
    permissions = 8  # Administrator permission
    # Можно использовать более специфичные права:
    # permissions = 268435456 | 2048 | 8192 | 16384 | 274877906944  # Send Messages, Embed Links, Attach Files, Read Message History, Use Slash Commands
    
    # Используем фиксированный Client ID из предоставленной ссылки
    client_id = "1416183475798540368"
    
    return (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={client_id}"
        f"&permissions={permissions}"
        f"&scope=bot+applications.commands"
    )

def generate_bot_invite_url_for_guild(guild_id: str):
    """Собрать ссылку приглашения с предустановленным сервером (если у пользователя есть права)."""
    base = generate_bot_invite_url()
    return f"{base}&guild_id={guild_id}&disable_guild_select=true"

@app.route('/guild/<guild_id>/invite')
def invite_guild(guild_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    # Проверяем права добавления бота на этот сервер
    user_guilds = get_user_guilds(session['access_token'])
    g = next((x for x in user_guilds if x.get('id') == guild_id), None)
    if not g:
        flash('Сервер не найден в вашем списке', 'error')
        return redirect(url_for('dashboard'))
    perms = int(g.get('permissions', 0))
    is_owner = bool(g.get('owner'))
    is_admin = (perms & 0x8) != 0
    can_manage_guild = (perms & 0x20) != 0
    if not (is_owner or is_admin or can_manage_guild):
        flash('Недостаточно прав для добавления бота на этот сервер', 'error')
        return redirect(url_for('dashboard'))
    return redirect(generate_bot_invite_url_for_guild(guild_id))

def categorize_guilds(user_guilds, bot_guilds):
    """Разделить серверы на категории"""
    available_guilds = []  # Серверы где есть бот и права
    user_only_guilds = []  # Серверы где нет бота, но есть права
    
    # Проверяем, является ли пользователь администратором бота
    user_id = session.get('user', {}).get('id')
    is_admin = user_id and is_bot_admin(user_id)

    # Хелпер: может ли текущий пользователь добавить бота на этот сервер
    def can_user_add_bot_to_guild(g):
        try:
            perms = int(g.get('permissions', 0) or 0)
        except Exception:
            perms = 0
        is_owner = bool(g.get('owner'))
        has_admin = (perms & 0x8) != 0  # ADMINISTRATOR
        can_manage_guild = (perms & 0x20) != 0  # MANAGE_GUILD
        return is_owner or has_admin or can_manage_guild

    if is_admin:
        # Администраторы видят все серверы где есть бот
        for guild in bot_guilds:
            available_guilds.append(guild)
        # Но в "Серверы без бота" — только те, куда пользователь реально может добавить бота
        for guild in user_guilds:
            if not any(bg['id'] == guild['id'] for bg in bot_guilds):
                if can_user_add_bot_to_guild(guild):
                    user_only_guilds.append(guild)
    else:
        # Обычная логика для пользователей
        for guild in user_guilds:
            guild_id = guild['id']
            # Права пользователя на сервере (для добавления бота нужен Manage Guild или быть владельцем/админом)
            can_add_bot = can_user_add_bot_to_guild(guild)

            # Для доступных серверов (где бот уже есть) оставим прежнюю широкую проверку
            has_permissions_manage = user_has_permissions(user_guilds, bot_guilds, guild_id)

            if any(bg['id'] == guild_id for bg in bot_guilds):
                if has_permissions_manage:
                    available_guilds.append(guild)
            else:
                # В список "Серверы без бота" помещаем только те, где пользователь может добавить бота
                if can_add_bot:
                    user_only_guilds.append(guild)
    
    return available_guilds, user_only_guilds

def user_has_permissions(user_guilds, bot_guilds, guild_id, user_id=None):
    """Проверить, может ли пользователь управлять ботом на сервере"""
    debug_prefix = f"[PERMS guild={guild_id} user={user_id}]"
    # 1. Админ бота всегда True
    if user_id and is_bot_admin(user_id):
        print(f"{debug_prefix} ALLOW: bot admin")
        return True

    user_guild = next((g for g in user_guilds if str(g.get("id")) == str(guild_id)), None)
    bot_guild = next((g for g in bot_guilds if str(g.get("id")) == str(guild_id)), None)

    if not user_guild:
        print(f"{debug_prefix} DENY: user_guild not found in OAuth list")
        return False
    if not bot_guild:
        print(f"{debug_prefix} WARN: bot_guild not found in bot_guilds list, will still attempt fallback checks")

    # Fallback: если owner флаг не пришёл в OAuth (бывает при устаревшем токене) — сверяем через объект бота
    if user_guild.get("owner") is not True and user_id:
        try:
            bot = get_bot_instance()
            if bot:
                g_obj = bot.get_guild(int(guild_id))
                if g_obj and getattr(g_obj, 'owner_id', None) and str(g_obj.owner_id) == str(user_id):
                    print(f"{debug_prefix} ALLOW: matched owner via bot guild object owner_id={g_obj.owner_id}")
                    return True
        except Exception as e:
            print(f"{debug_prefix} fallback owner check error: {e}")

    # 2. Владелец сервера
    if user_guild.get("owner") is True:
        print(f"{debug_prefix} ALLOW: owner flag True")
        return True

    # 3. Права (битовая маска) — приводим безопасно
    try:
        permissions_raw = user_guild.get("permissions", 0)
        permissions = int(permissions_raw)
    except Exception:
        permissions_raw = user_guild.get("permissions", 0)
        try:
            permissions = int(str(permissions_raw) or 0)
        except Exception:
            permissions = 0

    has_admin = (permissions & 0x8) != 0      # ADMINISTRATOR
    has_manage = (permissions & 0x20) != 0     # MANAGE_GUILD
    has_manage_messages = (permissions & 0x2000) != 0  # MANAGE_MESSAGES

    decision = has_admin or has_manage or has_manage_messages
    print(f"{debug_prefix} perms_raw={permissions_raw} int={permissions} admin={has_admin} manage_guild={has_manage} manage_messages={has_manage_messages} => {decision}")
    return decision

def get_guild_channels(guild_id, access_token):
    """Вернуть только те текстовые каналы, где у текущего пользователя есть права писать (VIEW_CHANNEL+SEND_MESSAGES).
    Реализация:
    - Берём список серверов пользователя для грубой проверки прав (owner/admin/manage guild → все text-каналы)
    - Иначе считаем права на основе ролей участника и оверрайдов канала
    Примечание: если недоступны данные участника/ролей (нет прав/интентов), вернём все текстовые каналы как деградацию.
    """
    # Константы прав
    PERM_ADMIN = 0x8
    PERM_MANAGE_GUILD = 0x20
    PERM_VIEW_CHANNEL = 0x400
    PERM_SEND_MESSAGES = 0x800

    # Права на уровне сервера (по OAuth пользователя)
    user_guilds = get_user_guilds(access_token)
    user_guild = next((g for g in user_guilds if g.get("id") == guild_id), None)
    user_perms = int(user_guild.get("permissions", 0)) if user_guild else 0
    is_owner = bool(user_guild and user_guild.get("owner"))
    is_admin = (user_perms & PERM_ADMIN) != 0
    can_manage_guild = (user_perms & PERM_MANAGE_GUILD) != 0

    headers = {"Authorization": f"Bot {BOT_TOKEN}"}
    # Список каналов
    resp_ch = requests.get(f"{DISCORD_API_BASE_URL}/guilds/{guild_id}/channels", headers=headers)
    if resp_ch.status_code != 200:
        return []
    channels = resp_ch.json()
    text_channels = [ch for ch in channels if ch.get("type") == 0]

    # Если владелец/админ/управление сервером → все текстовые каналы
    if is_owner or is_admin or can_manage_guild:
        return text_channels

    # Иначе пробуем точный расчёт прав пользователя в каналах
    try:
        # Получим участника и роли сервера
        user = session.get('user', {})
        user_id = user.get('id')
        if not user_id:
            return text_channels

        # Роли сервера
        roles_resp = requests.get(f"{DISCORD_API_BASE_URL}/guilds/{guild_id}/roles", headers=headers)
        if roles_resp.status_code != 200:
            return text_channels
        roles = roles_resp.json()
        roles_map = {str(r['id']): int(r.get('permissions', 0)) for r in roles}

        # Участник сервера
        mem_resp = requests.get(f"{DISCORD_API_BASE_URL}/guilds/{guild_id}/members/{user_id}", headers=headers)
        if mem_resp.status_code != 200:
            return text_channels
        member = mem_resp.json()
        member_role_ids = [str(rid) for rid in member.get('roles', [])]

        guild_id_str = str(guild_id)
        everyone_perms = int(roles_map.get(guild_id_str, 0))  # @everyone id == guild_id

        def compute_base_perms():
            perms = everyone_perms
            for rid in member_role_ids:
                perms |= int(roles_map.get(rid, 0))
            return perms

        def apply_overwrites(perms: int, overwrites: list):
            # Применяем по правилам Discord:
            # 1) @everyone overwrite
            # 2) role overwrites (суммарные deny, затем allow)
            # 3) member overwrite
            def _parse(bitstr):
                try:
                    return int(bitstr)
                except Exception:
                    return 0

            # @everyone
            for ow in overwrites:
                if str(ow.get('id')) == guild_id_str:
                    deny = _parse(ow.get('deny', '0'))
                    allow = _parse(ow.get('allow', '0'))
                    perms = (perms & ~deny) | allow

            # roles
            deny_sum = 0
            allow_sum = 0
            for ow in overwrites:
                if ow.get('type') == 0 and str(ow.get('id')) in member_role_ids:
                    deny_sum |= _parse(ow.get('deny', '0'))
                    allow_sum |= _parse(ow.get('allow', '0'))
            perms = (perms & ~deny_sum) | allow_sum

            # member
            for ow in overwrites:
                if ow.get('type') == 1 and str(ow.get('id')) == str(user_id):
                    deny = _parse(ow.get('deny', '0'))
                    allow = _parse(ow.get('allow', '0'))
                    perms = (perms & ~deny) | allow
            return perms

        allowed = []
        for ch in text_channels:
            perms = compute_base_perms()
            perms = apply_overwrites(perms, ch.get('permission_overwrites', []) or [])
            if (perms & PERM_VIEW_CHANNEL) and (perms & PERM_SEND_MESSAGES):
                allowed.append(ch)
        return allowed
    except Exception as e:
        # На случай отсутствия интентов/прав — возвращаем все текстовые каналы
        print(f"Permission calc fallback for guild {guild_id}: {e}")
        return text_channels

def get_guild_forum_channels(guild_id, access_token):
    """Получить форум-каналы сервера"""
    headers = {"Authorization": f"Bot {BOT_TOKEN}"}
    response = requests.get(f"{DISCORD_API_BASE_URL}/guilds/{guild_id}/channels", headers=headers)
    if response.status_code == 200:
        channels = response.json()
        # Фильтруем только форум-каналы (type == 15)
        return [ch for ch in channels if ch["type"] == 15]
    return []

def get_guild_roles(guild_id, access_token=None):
    """Получить роли сервера"""
    headers = {"Authorization": f"Bot {BOT_TOKEN}"}
    response = requests.get(f"{DISCORD_API_BASE_URL}/guilds/{guild_id}/roles", headers=headers)
    if response.status_code == 200:
        roles = response.json()
        # Фильтруем системные роли (@everyone и боты)
        return [role for role in roles if not role.get('managed') and role['name'] != '@everyone']
    return []

@app.route('/')
def index():
    if 'user' not in session:
        return render_template('login.html')
    return redirect(url_for('dashboard'))

@app.route('/setup')
def setup_instructions():
    """Страница с инструкциями по настройке Discord Developer Portal"""
    return render_template('setup_instructions.html')

@app.route('/debug')
def debug_info():
    """Страница с диагностической информацией"""
    debug_data = {
        'client_id': DISCORD_CLIENT_ID,
        'redirect_uri': DISCORD_REDIRECT_URI,
        'has_client_secret': bool(DISCORD_CLIENT_SECRET),
        'api_base': DISCORD_API_BASE_URL
    }
    return f"""
    <h1>🔧 Диагностика настроек</h1>
    <h2>Discord OAuth2 конфигурация:</h2>
    <ul>
        <li><strong>Client ID:</strong> {debug_data['client_id']}</li>
        <li><strong>Redirect URI:</strong> {debug_data['redirect_uri']}</li>
        <li><strong>Client Secret настроен:</strong> {'✅ Да' if debug_data['has_client_secret'] else '❌ Нет'}</li>
        <li><strong>API Base URL:</strong> {debug_data['api_base']}</li>
    </ul>
    
    <h2>✅ Что нужно проверить в Discord Developer Portal:</h2>
    <ol>
        <li>Откройте: <a href="https://discord.com/developers/applications" target="_blank">Discord Developer Portal</a></li>
        <li>Выберите приложение с ID: <strong>{debug_data['client_id']}</strong></li>
        <li>Перейдите в OAuth2 → General</li>
        <li>В разделе "Redirects" должен быть: <strong>{debug_data['redirect_uri']}</strong></li>
        <li>Нажмите "Save Changes" если добавили новый URI</li>
    </ol>
    
    <h2>🔗 Полезные ссылки:</h2>
    <ul>
        <li><a href="/">Главная страница</a></li>
        <li><a href="/setup">Подробная инструкция</a></li>
        <li><a href="/login">Попробовать авторизацию</a></li>
    </ul>
    """

@app.route('/login')
def login():
    discord_oauth_url = (
        f"https://discord.com/api/oauth2/authorize"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={DISCORD_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope=identify+guilds"
    )
    return redirect(discord_oauth_url)

@app.route('/callback')
def callback():
    code = request.args.get('code')
    error = request.args.get('error')
    
    if error:
        if error == 'access_denied':
            flash('Авторизация отменена пользователем', 'warning')
        else:
            flash(f'Ошибка OAuth2: {error}', 'error')
        return redirect(url_for('index'))
    
    if not code:
        flash('Ошибка авторизации: не получен код', 'error')
        return redirect(url_for('index'))
    
    # Обмен кода на токен
    data = {
        'client_id': DISCORD_CLIENT_ID,
        'client_secret': DISCORD_CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': DISCORD_REDIRECT_URI
    }
    
    try:
        response = requests.post(f"{DISCORD_API_BASE_URL}/oauth2/token", data=data)
        if response.status_code != 200:
            error_data = response.json() if response.headers.get('content-type') == 'application/json' else {}
            error_msg = error_data.get('error_description', f'HTTP {response.status_code}')
            flash(f'Ошибка получения токена: {error_msg}', 'error')
            return redirect(url_for('index'))
    except Exception as e:
        flash(f'Ошибка соединения с Discord: {str(e)}', 'error')
        return redirect(url_for('index'))
    
    token_data = response.json()
    access_token = token_data['access_token']
    
    # Получение информации о пользователе
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        user_response = requests.get(f"{DISCORD_API_BASE_URL}/users/@me", headers=headers)
        if user_response.status_code != 200:
            flash('Ошибка получения данных пользователя', 'error')
            return redirect(url_for('index'))
    except Exception as e:
        flash(f'Ошибка получения данных пользователя: {str(e)}', 'error')
        return redirect(url_for('index'))
    
    user_data = user_response.json()
    
    # Формируем правильный URL аватарки
    if user_data.get('avatar'):
        user_data['avatar'] = f"https://cdn.discordapp.com/avatars/{user_data['id']}/{user_data['avatar']}.png"
    else:
        # Дефолтная аватарка Discord
        discriminator = int(user_data.get('discriminator', '0'))
        default_avatar = discriminator % 5
        user_data['avatar'] = f"https://cdn.discordapp.com/embed/avatars/{default_avatar}.png"
    
    session['user'] = user_data
    # Сохраняем список гильдий пользователя сразу (для последующих прав)
    try:
        ug = get_user_guilds(access_token)
        session['user_guilds'] = ug
        print(f"[OAUTH] Fetched and stored {len(ug)} user guilds")
    except Exception as e:
        print(f"[OAUTH] Failed to fetch user guilds: {e}")
    session['access_token'] = access_token
    
    flash('Успешная авторизация!', 'success')
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('index'))

@app.route('/my-id')
def show_my_id():
    """Показать ID текущего пользователя для отладки"""
    if 'user' not in session:
        return f"Не авторизован. <a href='{url_for('login')}'>Войти</a>"
    
    user_id = session.get('user', {}).get('id')
    is_admin = is_bot_admin(user_id)
    
    return f"""
    <h1>Информация о пользователе</h1>
    <p><strong>ID:</strong> {user_id}</p>
    <p><strong>Имя:</strong> {session.get('user', {}).get('username', 'Неизвестно')}</p>
    <p><strong>Администратор бота:</strong> {'Да' if is_admin else 'Нет'}</p>
    <p><strong>Список админов в конфиге:</strong> {ADMIN_USERS}</p>
    <a href="{url_for('dashboard')}">Назад к панели</a>
    """

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    
    # Разделяем серверы на категории
    available_guilds, user_only_guilds = categorize_guilds(user_guilds, bot_guilds)
    
    # Генерируем ссылку для добавления бота
    bot_invite_url = generate_bot_invite_url()
    
    # Получаем статистику бота
    bot_stats = get_bot_stats()
    
    # Для обратной совместимости: needs_bot_guilds = user_only_guilds
    return render_template('dashboard.html', 
                         user=session['user'], 
                         available_guilds=available_guilds,
                         user_only_guilds=user_only_guilds,
                         needs_bot_guilds=user_only_guilds,
                         bot_invite_url=bot_invite_url,
                         bot_stats=bot_stats)

@app.route('/api/guilds')
def api_guilds():
    """API endpoint для получения списка серверов"""
    if 'user' not in session:
        return jsonify({'error': 'Не авторизован'}), 401

    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()

    # Разделяем серверы на категории
    available_guilds, user_only_guilds = categorize_guilds(user_guilds, bot_guilds)

    # Генерируем ссылку для добавления бота
    bot_invite_url = generate_bot_invite_url()

    return jsonify({
        'available_guilds': available_guilds,
        'user_only_guilds': user_only_guilds,
        'bot_invite_url': bot_invite_url
    })

@app.route('/api/guilds_debug')
def api_guilds_debug():
    """Диагностика категорий серверов и прав пользователя для отладки фильтрации.
    Требует авторизации через сессию.
    """
    if 'user' not in session:
        return jsonify({'error': 'Не авторизован'}), 401

    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()

    available_guilds, user_only_guilds = categorize_guilds(user_guilds, bot_guilds)
    needs_ids = {g['id'] for g in user_only_guilds}
    bot_ids = {g['id'] for g in bot_guilds}

    def _flags(g):
        try:
            perms = int(g.get('permissions', 0) or 0)
        except Exception:
            perms = 0
        return {
            'is_owner': bool(g.get('owner')),
            'has_admin': (perms & 0x8) != 0,            # ADMINISTRATOR
            'can_manage_guild': (perms & 0x20) != 0,    # MANAGE_GUILD
        }

    details = []
    for g in user_guilds:
        flags = _flags(g)
        details.append({
            'id': g.get('id'),
            'name': g.get('name'),
            'in_bot_guilds': g.get('id') in bot_ids,
            **flags,
            'included_in_needs_bot': g.get('id') in needs_ids,
        })

    return jsonify({
        'count': len(details),
        'details': details
    })

@app.route('/admin')
def admin_panel():
    """Панель администратора для управления системой"""
    if 'user' not in session:
        flash('Требуется авторизация для доступа к админ-панели', 'error')
        return redirect(url_for('login'))
    
    # Простая проверка - можно расширить логику проверки администратора
    # Пока что разрешаем доступ всем авторизованным пользователям
    # В будущем можно добавить проверку по ID пользователя или роли
    
    # Проверяем, есть ли шаблоны для миграции
    templates_to_migrate = 0
    migrated_guilds = {}
    
    try:
        # Получаем информацию о серверах пользователя
        user_guilds = get_user_guilds(session['access_token'])
        guild_names = {guild['id']: guild['name'] for guild in user_guilds}
        
        # Проверяем наличие старых шаблонов в settings.json
        if os.path.exists("settings.json"):
            with open("settings.json", "r", encoding="utf-8") as f:
                settings = json.load(f)
                
            if "guilds" in settings:
                for guild_id, guild_data in settings["guilds"].items():
                    if "templates" in guild_data and guild_data["templates"]:
                        templates_to_migrate += len(guild_data["templates"])
        
        # Проверяем мигрированные шаблоны
        if os.path.exists("templates_data"):
            for filename in os.listdir("templates_data"):
                if filename.startswith("guild_") and filename.endswith("_templates.json"):
                    guild_id = filename.replace("guild_", "").replace("_templates.json", "")
                    try:
                        with open(f"templates_data/{filename}", "r", encoding="utf-8") as f:
                            templates = json.load(f)
                            migrated_guilds[guild_id] = {
                                'guild_id': guild_id,
                                'guild_name': guild_names.get(guild_id, f"Неизвестный сервер ({guild_id})"),
                                'template_count': len(templates)
                            }
                    except Exception as e:
                        print(f"Ошибка чтения шаблонов для {guild_id}: {e}")
                        pass
                        
    except Exception as e:
        print(f"Ошибка проверки миграции: {e}")
        flash(f"Ошибка загрузки данных: {e}", 'error')
    
    return render_template('admin_panel.html',
                         user=session['user'],
                         templates_to_migrate=templates_to_migrate,
                         migrated_guilds=migrated_guilds)

@app.route('/admin/migrate', methods=['POST'])
def admin_migrate():
    """Выполнить миграцию шаблонов"""
    if 'user' not in session:
        return jsonify({'error': 'Не авторизован'}), 401
    
    try:
        # Импортируем и выполняем миграцию
        import subprocess
        import sys
        
        result = subprocess.run([sys.executable, 'migrate_templates.py'], 
                              capture_output=True, text=True, cwd=os.path.dirname(__file__))
        
        if result.returncode == 0:
            return jsonify({'success': True, 'message': result.stdout})
        else:
            return jsonify({'success': False, 'error': result.stderr})
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/guild/<guild_id>')
def guild_settings(guild_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    start_time = time.time()
    print(f"[GUILD] Загрузка настроек для guild {guild_id}")
    
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        flash('У вас нет прав для управления ботом на этом сервере', 'error')
        return redirect(url_for('dashboard'))

    # Получаем информацию о сервере
    guild_info = next((g for g in user_guilds if g['id'] == guild_id), None)
    if not guild_info:
        guild_info = next((g for g in bot_guilds if g['id'] == guild_id), None)
    if not guild_info:
        guild_info = next((g for g in bot_guilds if g['id'] == guild_id), None)
    # Разрешаем бот-админам открывать сервера, в которых они не состоят, если там есть бот
    if not guild_info:
        guild_info = next((g for g in bot_guilds if g['id'] == guild_id), None)
    if not guild_info:
        flash('Сервер не найден', 'error')
        return redirect(url_for('dashboard'))

    # Быстро получаем настройки (приоритет)
    settings_start = time.time()
    # Полные настройки с дефолтами (исключаем KeyError в шаблоне)
    settings = get_complete_guild_settings(int(guild_id))
    settings_time = (time.time() - settings_start) * 1000
    print(f"[GUILD] Настройки загружены: {settings_time:.2f} мс")
    
    # Получаем данные Discord API с обработкой ошибок
    access_token = session['access_token']
    
    # Каналы
    try:
        discord_start = time.time()
        channels = get_guild_channels(guild_id, access_token)
        channels_time = (time.time() - discord_start) * 1000
        print(f"[GUILD] Каналы загружены: {channels_time:.2f} мс ({len(channels)} шт.)")
    except Exception as e:
        print(f"[GUILD] Ошибка каналов: {e}")
        channels = []
    
    # Форум-каналы
    try:
        forum_start = time.time()
        forum_channels = get_guild_forum_channels(guild_id, access_token)
        forum_time = (time.time() - forum_start) * 1000
        print(f"[GUILD] Форумы загружены: {forum_time:.2f} мс ({len(forum_channels)} шт.)")
    except Exception as e:
        print(f"[GUILD] Ошибка форумов: {e}")
        forum_channels = []
    
    # Роли
    try:
        roles_start = time.time()
        roles = get_guild_roles(guild_id)
        roles_time = (time.time() - roles_start) * 1000
        print(f"[GUILD] Роли загружены: {roles_time:.2f} мс ({len(roles)} шт.)")
    except Exception as e:
        print(f"[GUILD] Ошибка ролей: {e}")
        roles = []
    
    # Recruitment настройки (исправленный блок ниже в актуальной позиции)
    
    total_time = (time.time() - start_time) * 1000
    print(f"[GUILD] Общее время загрузки: {total_time:.2f} мс")
    
    return render_template('guild_settings.html',
                         guild=guild_info,
                         settings=settings,
                         channels=channels,
                         forum_channels=forum_channels,
                         roles=roles)

@app.route('/guild/<guild_id>/settings', methods=['POST'])
def update_guild_settings(guild_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        flash('У вас нет прав для управления ботом на этом сервере', 'error')
        return redirect(url_for('dashboard'))
    
    guild_id_int = int(guild_id)
    
    # Используем простую систему для быстрого сохранения
    if USING_FAST_DB:
        print(f"[WEB] Быстрое сохранение настроек для guild {guild_id_int}")
        
        # Собираем все настройки для пакетного сохранения
        settings_batch = {}
        
        # Основные булевые настройки
        settings_batch['monitoring_enabled'] = 'monitoring_enabled' in request.form
        settings_batch['cleanup_enabled'] = 'cleanup_enabled' in request.form
        settings_batch['reminders_enabled'] = 'reminders_enabled' in request.form
        
        # Роли
        if 'event_creator_role' in request.form and request.form['event_creator_role']:
            settings_batch['event_creator_role'] = int(request.form['event_creator_role'])
        
        if 'moderator_role' in request.form and request.form['moderator_role']:
            settings_batch['moderator_role'] = int(request.form['moderator_role'])
        
        if 'ping_role' in request.form:
            ping_value = request.form['ping_role']
            if ping_value == 'everyone':
                settings_batch['ping_role'] = 'everyone'
            elif ping_value:
                settings_batch['ping_role'] = int(ping_value)
        
        # Каналы
        channels = request.form.getlist('monitored_channels')
        settings_batch['monitored_channels'] = [int(ch) for ch in channels if ch]
        
        # Время напоминания
        if 'reminder_time' in request.form and request.form['reminder_time']:
            raw = request.form['reminder_time'].strip()
            try:
                h, m = map(int, raw.split(':'))
                if h < 0 or m < 0 or m >= 60:
                    raise ValueError()
                settings_batch['reminder_time'] = [h, m]
            except Exception:
                flash('Неверный формат времени напоминания. Используйте ЧЧ:ММ (например 00:15)', 'error')
        
        # Пакетное сохранение всех настроек одной транзакцией
        db = get_settings_db()
        db.batch_set_settings(guild_id_int, settings_batch)
        
        print(f"[WEB] Сохранено {len(settings_batch)} настроек для guild {guild_id_int}")
        
    else:
        # Старая система - по одной настройке
        print(f"[WEB] Обычное сохранение настроек для guild {guild_id_int}")
        
        # Обновляем настройки
        if 'event_creator_role' in request.form and request.form['event_creator_role']:
            set_guild_setting(guild_id_int, 'event_creator_role', int(request.form['event_creator_role']))
        
        if 'moderator_role' in request.form and request.form['moderator_role']:
            set_guild_setting(guild_id_int, 'moderator_role', int(request.form['moderator_role']))
        
        if 'ping_role' in request.form:
            ping_value = request.form['ping_role']
            if ping_value == 'everyone':
                set_guild_setting(guild_id_int, 'ping_role', 'everyone')
            elif ping_value:
                set_guild_setting(guild_id_int, 'ping_role', int(ping_value))
        
        # Сохраняем отслеживаемые каналы всегда (в т.ч. когда список пустой -> очистка)
        channels = request.form.getlist('monitored_channels')
        set_guild_setting(guild_id_int, 'monitored_channels', [int(ch) for ch in channels if ch])
        
        # Булевые настройки
        set_guild_setting(guild_id_int, 'monitoring_enabled', 'monitoring_enabled' in request.form)
        set_guild_setting(guild_id_int, 'cleanup_enabled', 'cleanup_enabled' in request.form)
        set_guild_setting(guild_id_int, 'reminders_enabled', 'reminders_enabled' in request.form)

        # Время напоминания (ЧЧ:ММ до события)
        if 'reminder_time' in request.form and request.form['reminder_time']:
            raw = request.form['reminder_time'].strip()
            try:
                h, m = map(int, raw.split(':'))
                if h < 0 or m < 0 or m >= 60:
                    raise ValueError()
                set_guild_setting(guild_id_int, 'reminder_time', [h, m])
            except Exception:
                flash('Неверный формат времени напоминания. Используйте ЧЧ:ММ (например 00:15)', 'error')

        # Принудительно сохраняем на диск изменения party-настроек
        try:
            if USE_UNIFIED_SETTINGS:
                # Настройки уже сохранены через unified_settings
                print(f"[WEB] Party settings saved via unified_settings for guild {guild_id_int}")
            else:
                # Fallback - сохраняем через старую систему
                save_all_data()
                print(f"[WEB] Party settings saved via legacy system for guild {guild_id_int}")
        except Exception as e:
            print(f"[WEB] Error saving party settings for guild {guild_id_int}: {e}")
    
    # Обработка настроек recruitment бота
    recruit_updates = {}
    
    # Основные роли
    if 'recruit_default_role' in request.form and request.form['recruit_default_role']:
        recruit_updates['default_role'] = request.form['recruit_default_role']
    
    if 'recruit_recruit_role' in request.form and request.form['recruit_recruit_role']:
        recruit_updates['recruit_role'] = request.form['recruit_recruit_role']
    
    # Название гильдии и кулдаун
    if 'recruit_guild_name' in request.form:
        recruit_updates['guild_name'] = request.form['recruit_guild_name']
    
    if 'recruit_cooldown_hours' in request.form:
        try:
            cooldown = int(request.form['recruit_cooldown_hours'])
            if 0 <= cooldown <= 168:
                recruit_updates['cooldown_hours'] = cooldown
        except ValueError:
            pass
    
    # Роли модераторов очков
    moderator_roles = request.form.getlist('recruit_moderator_roles')
    if moderator_roles:
        recruit_updates['points_moderator_roles'] = ','.join(moderator_roles)
    else:
        recruit_updates['points_moderator_roles'] = ''

    # Роли рекрутеров (множественный выбор)
    recruiter_roles = request.form.getlist('recruit_recruiter_roles')
    if recruiter_roles:
        recruit_updates['recruiter_roles'] = ','.join(recruiter_roles)
    else:
        # Если не выбрано ни одной роли, очищаем поле
        recruit_updates['recruiter_roles'] = ''
    
    # Каналы
    if 'recruit_events_channel' in request.form and request.form['recruit_events_channel']:
        recruit_updates['events_channel'] = request.form['recruit_events_channel']
    
    if 'recruit_shop_channel' in request.form and request.form['recruit_shop_channel']:
        recruit_updates['shop_channel'] = request.form['recruit_shop_channel']
    
    if 'recruit_forum_channel' in request.form and request.form['recruit_forum_channel']:
        recruit_updates['forum_channel'] = request.form['recruit_forum_channel']
    
    if 'recruit_points_panel_channel' in request.form and request.form['recruit_points_panel_channel']:
        recruit_updates['points_panel_channel'] = request.form['recruit_points_panel_channel']
    
    if 'recruit_recruit_panel_channel' in request.form and request.form['recruit_recruit_panel_channel']:
        recruit_updates['recruit_panel_channel'] = request.form['recruit_recruit_panel_channel']
    
    # Даты периода очков
    if 'recruit_points_start_date' in request.form:
        recruit_updates['points_start_date'] = request.form['recruit_points_start_date']
    
    if 'recruit_points_end_date' in request.form:
        recruit_updates['points_end_date'] = request.form['recruit_points_end_date']
    
    # Сохраняем настройки recruitment через settings.db
    if recruit_updates:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            success = loop.run_until_complete(update_recruit_settings(guild_id_int, recruit_updates))
            loop.close()
            print(f"[WEB] Recruit settings saved in settings.db for guild {guild_id_int}: {success}")
            if not success:
                flash('Ошибка сохранения настроек рекрутинга!', 'error')
        except Exception as e:
            print(f"Ошибка сохранения настроек recruitment (settings.db): {e}")
            flash('Ошибка сохранения настроек рекрутинга!', 'error')
    
    flash('Настройки сохранены!', 'success')
    return redirect(url_for('guild_settings', guild_id=guild_id))


@app.route('/api/guild/<guild_id>/party-settings')
def api_party_settings(guild_id):
    """Быстрое API для получения настроек сервера"""
    try:
        print(f"[API] Быстрый запрос настроек для guild_id: {guild_id}")
        start_time = time.time()
        
        guild_id_int = int(guild_id)
        # Полный слой с дефолтами
        data = get_complete_guild_settings(guild_id_int)
        exec_time = (time.time() - start_time) * 1000
        print(f"[API] complete_settings: {exec_time:.2f} мс, ключей: {len(data)}")
        return jsonify({
            'guild_id': guild_id_int,
            'settings': data,
            'source': 'complete_with_defaults',
            'load_time_ms': round(exec_time, 2)
        })
        
    except Exception as e:
        exec_time = (time.time() - start_time) * 1000
        print(f"[API] Ошибка в api_party_settings: {exec_time:.2f} мс - {e}")
        return jsonify({
            'error': str(e),
            'load_time_ms': round(exec_time, 2)
        }), 500

@app.route('/api/guild/<guild_id>/unified-settings')
def api_unified_settings(guild_id):
    """Диагностика: вернуть все настройки из единой системы"""
    try:
        if USE_UNIFIED_SETTINGS:
            guild_settings = unified_settings.get_guild_settings(int(guild_id))
            return jsonify({
                'guild_id': int(guild_id), 
                'settings': guild_settings,
                'source': 'unified_settings'
            })
        else:
            return jsonify({
                'guild_id': int(guild_id),
                'error': 'Unified settings not available',
                'source': 'legacy'
            })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/guild/<guild_id>/recruit')
def guild_recruit_management(guild_id):
    """Страница управления рекрутингом и очками"""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        flash('У вас нет прав для управления ботом на этом сервере', 'error')
        return redirect(url_for('dashboard'))
    
    # Получаем информацию о сервере
    guild_info = next((g for g in user_guilds if g['id'] == guild_id), None)
    if not guild_info:
        guild_info = next((g for g in bot_guilds if g['id'] == guild_id), None)
    if not guild_info:
        flash('Сервер не найден', 'error')
        return redirect(url_for('dashboard'))
    
    # Получаем данные recruitment системы
    submissions = []
    purchases = []
    user_points = []
    
    if RECRUIT_DB_AVAILABLE:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            submissions = loop.run_until_complete(get_event_submissions(int(guild_id)))
            purchases = loop.run_until_complete(get_shop_purchases(int(guild_id)))
            user_points = loop.run_until_complete(get_user_points_leaderboard(int(guild_id)))
            loop.close()
        except Exception as e:
            print(f"Ошибка загрузки данных recruitment: {e}")
    
    return render_template('guild_recruit.html',
                         guild=guild_info,
                         submissions=submissions,
                         purchases=purchases,
                         user_points=user_points)

@app.route('/guild/<guild_id>/templates')
def guild_templates(guild_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        flash('У вас нет прав для управления ботом на этом сервере', 'error')
        return redirect(url_for('dashboard'))
    
    guild_info = next((g for g in user_guilds if g['id'] == guild_id), None)
    if not guild_info:
        # Разрешаем бот-админу видеть карточку сервера из списка бота
        bot_guilds = get_bot_guilds()
        guild_info = next((g for g in bot_guilds if g['id'] == guild_id), None)
    templates = get_guild_templates(int(guild_id))
    
    return render_template('guild_templates.html',
                         guild=guild_info,
                         templates=templates)

@app.route('/guild/<guild_id>/templates/create', methods=['POST'])
def create_template(guild_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        flash('У вас нет прав для управления ботом на этом сервере', 'error')
        return redirect(url_for('dashboard'))
    
    template_name = request.form.get('template_name', '').strip()
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    roles_text = request.form.get('roles', '').strip()
    
    if not template_name or not title or not roles_text:
        flash('Заполните все обязательные поля', 'error')
        return redirect(url_for('guild_templates', guild_id=guild_id))
    
    roles = [r.strip() for r in roles_text.split('\n') if r.strip()]
    if not roles:
        flash('Укажите хотя бы одну роль', 'error')
        return redirect(url_for('guild_templates', guild_id=guild_id))
    
    template_data = {
        'title': title,
        'description': description,
        'roles': roles
    }
    
    set_guild_template(int(guild_id), template_name, template_data)
    flash('Шаблон создан!', 'success')
    return redirect(url_for('guild_templates', guild_id=guild_id))

@app.route('/api/guild/<guild_id>/templates')
def api_guild_templates(guild_id):
    """API endpoint для получения шаблонов сервера"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    try:
        templates = get_guild_templates(int(guild_id))
        return jsonify({'templates': templates})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/guild/<guild_id>/templates', methods=['POST'])
def update_template(guild_id):
    """Обновить или создать шаблон"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        return jsonify({'error': 'No permissions'}), 403
    
    try:
        template_name = request.form.get('template_name')
        title = request.form.get('title', '')
        description = request.form.get('description', '')
        roles = request.form.get('roles', '')
        
        if not template_name:
            return jsonify({'error': 'Template name is required'}), 400
        
        # Обработка ролей
        if isinstance(roles, str):
            if '\n' in roles or ',' in roles:
                # Множественные роли - разделяем их
                roles = [role.strip() for role in roles.replace('\n', ',').split(',') if role.strip()]
            else:
                # Одна роль или пустая строка
                roles = [roles.strip()] if roles.strip() else []
        
        template_data = {
            'title': title,
            'description': description,
            'roles': roles
        }
        
        set_guild_template(int(guild_id), template_name, template_data)
        return jsonify({'success': True, 'message': 'Template updated successfully'})
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/guild/<guild_id>/templates/<template_name>/delete', methods=['POST'])
def delete_template_api(guild_id, template_name):
    """API endpoint для удаления шаблона"""
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        return jsonify({'error': 'No permissions'}), 403
    
    try:
        if delete_guild_template(int(guild_id), template_name):
            return jsonify({'success': True, 'message': 'Template deleted successfully'})
        else:
            return jsonify({'error': 'Template not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/guild/<guild_id>/events')
def guild_events(guild_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        flash('У вас нет прав для управления ботом на этом сервере', 'error')
        return redirect(url_for('dashboard'))
    
    guild_info = next((g for g in user_guilds if g['id'] == guild_id), None)
    channels = get_guild_channels(guild_id, session['access_token'])
    templates = get_guild_templates(int(guild_id))
    # Собираем активные и недавние события из ALL_SESSIONS
    try:
        active_events = []
        recent_events = []
        import main; _SESS = main.ALL_SESSIONS
        now_ts = time.time()
        for sid, ev in _SESS.items():
            if ev.get('guild_id') != int(guild_id):
                continue
            item = {
                'id': sid,
                'title': ev.get('title'),
                'channel_id': ev.get('channel_id'),
                'thread_id': ev.get('thread_id'),
                'stopped': bool(ev.get('stopped')), 
                'time': ev.get('time', ''),
            }
            if not ev.get('stopped'):
                active_events.append(item)
            else:
                recent_events.append(item)
        # ограничим историю
        recent_events = recent_events[-10:]
    except Exception:
        active_events, recent_events = [], []

    return render_template('guild_events.html',
                         guild=guild_info,
                         channels=channels,
                         templates=templates,
                         active_events=active_events,
                         recent_events=recent_events)

@app.route('/guild/<guild_id>/events/create', methods=['POST'])
def create_event_web(guild_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        return jsonify({'error': 'Нет прав доступа'}), 403
    
    try:
        # Получаем данные из формы (не JSON для обычной формы)
        if request.is_json:
            data = request.get_json()
        else:
            data = {
                'channel_id': request.form.get('channel_id'),
                'title': request.form.get('title', '').strip(),
                'description': request.form.get('description', '').strip(),
                'time': request.form.get('time', '').strip(),
                'roles': request.form.getlist('roles[]') or request.form.get('roles', '').split('\n')
            }
        
        channel_id = data.get('channel_id')
        title = data.get('title', '').strip()
        description = data.get('description', '').strip()
        time_str = data.get('time', '').strip()
        roles = data.get('roles', [])
        
        # Очищаем роли от пустых строк
        if isinstance(roles, str):
            roles = [r.strip() for r in roles.split('\n') if r.strip()]
        elif isinstance(roles, list):
            roles = [r.strip() for r in roles if r and r.strip()]
        
        if not channel_id or not title or not roles:
            error_msg = 'Заполните все обязательные поля: канал, название события и роли'
            if request.is_json:
                return jsonify({'error': error_msg}), 400
            else:
                flash(error_msg, 'error')
                return redirect(url_for('guild_events', guild_id=guild_id))
        
        try:
            channel_id = int(channel_id)
        except (ValueError, TypeError):
            error_msg = 'Некорректный ID канала'
            if request.is_json:
                return jsonify({'error': error_msg}), 400
            else:
                flash(error_msg, 'error')
                return redirect(url_for('guild_events', guild_id=guild_id))
        
        # Создаем команду для бота через файл очереди
        command_data = {
            'type': 'create_event',
            'guild_id': int(guild_id),
            'channel_id': channel_id,
            'title': title,
            'description': description,
            'time': time_str,
            'roles': roles,
            'creator_id': int(session['user']['id']),
            'timestamp': time.time()
        }
        
        # Сохраняем команду в файл очереди
        queue_file = 'command_queue.json'
        commands = []
        if os.path.exists(queue_file):
            try:
                with open(queue_file, 'r', encoding='utf-8') as f:
                    commands = json.load(f)
            except:
                commands = []
        
        commands.append(command_data)
        
        with open(queue_file, 'w', encoding='utf-8') as f:
            json.dump(commands, f, ensure_ascii=False, indent=2)
        
        success_msg = f'Событие "{title}" поставлено в очередь на создание'
        if request.is_json:
            return jsonify({'success': True, 'message': success_msg})
        else:
            flash(success_msg, 'success')
            return redirect(url_for('guild_events', guild_id=guild_id))
        
    except Exception as e:
        error_msg = f'Ошибка создания события: {str(e)}'
        if request.is_json:
            return jsonify({'error': error_msg}), 500
        else:
            flash(error_msg, 'error')
            return redirect(url_for('guild_events', guild_id=guild_id))

@app.route('/guild/<guild_id>/events/<event_id>/stop', methods=['POST'])
def stop_event_web(guild_id, event_id):
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        return jsonify({'error': 'No permissions'}), 403
    try:
        ev_id = int(event_id)
        import main; _SESS = main.ALL_SESSIONS
        if str(ev_id) not in _SESS:
            return jsonify({'error': 'Event not found'}), 404
        bot = get_bot_instance()
        loop = bot.loop if bot else None
        async def do_stop():
            import main; ALL_SESSIONS = main.ALL_SESSIONS; save_all_data = main.save_all_data; save_event = main.save_event; update_party_message = main.update_party_message
            session = ALL_SESSIONS.get(str(ev_id))
            if not session:
                return False
            if session.get('stopped'):
                return True
            session['stopped'] = True
            ALL_SESSIONS[str(ev_id)] = session
            save_all_data()
            save_event(ev_id, session)
            try:
                await update_party_message_web(ev_id)
            except Exception:
                pass
            return True
        if loop:
            fut = asyncio.run_coroutine_threadsafe(do_stop(), loop)
            ok = fut.result(timeout=10)
        else:
            ok = False
        return jsonify({'success': bool(ok)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/guild/<guild_id>/events/<event_id>/remind', methods=['POST'])
def remind_event_web(guild_id, event_id):
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        return jsonify({'error': 'No permissions'}), 403
    try:
        ev_id = int(event_id)
        import main; _SESS = main.ALL_SESSIONS
        if str(ev_id) not in _SESS:
            return jsonify({'error': 'Event not found'}), 404
        bot = get_bot_instance()
        loop = bot.loop if bot else None
        async def do_remind():
            import main; ALL_SESSIONS = main.ALL_SESSIONS; _bot = main.bot
            session = ALL_SESSIONS.get(str(ev_id))
            if not session:
                return False
            guild = _bot.get_guild(session['guild_id'])
            if not guild:
                return False
            channel = guild.get_channel(session['channel_id'])
            if not channel:
                return False
            thread = None
            if session.get('thread_id'):
                thread = guild.get_thread(session['thread_id'])
            mentions = []
            for role in session.get('party_roles', []):
                uid = role.get('user_id')
                if uid:
                    mentions.append(f"<@{uid}>")
            if not mentions:
                return 'no_participants'
            text = "Напоминание: " + ", ".join(mentions)
            target = thread or channel
            await target.send(text, allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False))
            return True
        if loop:
            fut = asyncio.run_coroutine_threadsafe(do_remind(), loop)
            res = fut.result(timeout=10)
        else:
            res = False
        if res == 'no_participants':
            return jsonify({'success': True, 'message': 'Нет записавшихся участников для напоминания'})
        return jsonify({'success': bool(res)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/guild/<guild_id>/events/<event_id>/clone', methods=['POST'])
def clone_event_web(guild_id, event_id):
    if 'user' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        return jsonify({'error': 'No permissions'}), 403
    try:
        ev_id = int(event_id)
        import main; _SESS = main.ALL_SESSIONS
        if str(ev_id) not in _SESS:
            return jsonify({'error': 'Event not found'}), 404
        bot = get_bot_instance()
        loop = bot.loop if bot else None
        async def do_clone():
            import main
            ALL_SESSIONS = main.ALL_SESSIONS
            _bot = main.bot
            save_all_data = main.save_all_data
            save_event = main.save_event
            update_party_message = main.update_party_message
            session = ALL_SESSIONS.get(str(ev_id))
            if not session:
                return None
            guild = _bot.get_guild(session['guild_id'])
            if not guild:
                return None
            channel = guild.get_channel(session['channel_id'])
            if not channel:
                return None
            role_list = [r['name'] for r in session['party_roles']]
            text = f"**{session['title']} (копия)**\n{session['description']}\n\n"
            if session.get('time'):
                text += f"**Время:** {session['time']}\n\n"
            text += "**Роли:**\n" + "\n".join([f"{i+1}. {r} — Свободно" for i, r in enumerate(role_list)])
            msg = await channel.send(text)
            thread = await msg.create_thread(name=session['title'] + " (копия)")
            new_session_id = msg.id
            ALL_SESSIONS[str(new_session_id)] = {
                'guild_id': session['guild_id'],
                'channel_id': session['channel_id'],
                'main_msg_id': msg.id,
                'thread_id': thread.id,
                'title': session['title'] + ' (копия)',
                'description': session['description'],
                'time': session.get('time', ''),
                'party_roles': [{'name': r, 'user_id': None} for r in role_list],
                'creator_id': session.get('creator_id'),
                'stopped': False,
                'last_reminder_time': 0
            }
            save_all_data()
            save_event(new_session_id, ALL_SESSIONS[str(new_session_id)])
            try:
                await update_party_message_web(new_session_id)
            except Exception:
                pass
            return {
                'id': new_session_id,
                'channel_id': session['channel_id'],
                'url': f"https://discord.com/channels/{guild.id}/{session['channel_id']}/{new_session_id}"
            }
        if loop:
            fut = asyncio.run_coroutine_threadsafe(do_clone(), loop)
            res = fut.result(timeout=15)
        else:
            res = None
        if not res:
            return jsonify({'error': 'Clone failed'}), 500
        return jsonify({'success': True, 'event': res})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/guild/<guild_id>/events/<event_id>')
def event_details(guild_id, event_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        flash('Нет прав доступа', 'error')
        return redirect(url_for('dashboard'))
    guild_info = next((g for g in user_guilds if g['id'] == guild_id), None)
    import main; _SESS = main.ALL_SESSIONS
    ev = _SESS.get(str(event_id)) or _SESS.get(str(int(event_id)))
    if not ev:
        return render_template('event_details.html', guild=guild_info, event=None, not_found=True), 404
    # Соберем ссылку на сообщение
    jump_url = f"https://discord.com/channels/{ev['guild_id']}/{ev['channel_id']}/{ev['main_msg_id']}"
    return render_template('event_details.html', guild=guild_info, event=ev, event_id=str(event_id), jump_url=jump_url)

@app.route('/guild/<guild_id>/events/<event_id>/edit', methods=['GET', 'POST'])
def event_edit(guild_id, event_id):
    if 'user' not in session:
        return redirect(url_for('login'))
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        flash('Нет прав доступа', 'error')
        return redirect(url_for('dashboard'))
    import main; _SESS = main.ALL_SESSIONS
    ev = _SESS.get(str(event_id)) or _SESS.get(str(int(event_id)))
    if not ev:
        flash('Событие не найдено', 'error')
        return redirect(url_for('guild_events', guild_id=guild_id))

    if request.method == 'GET':
        guild_info = next((g for g in user_guilds if g['id'] == guild_id), None)
        roles_text = "\n".join([r.get('name', '') for r in ev.get('party_roles', [])])
        return render_template('event_edit.html', guild=guild_info, event=ev, event_id=str(event_id), roles_text=roles_text)

    # POST — сохранить изменения
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    time_str = request.form.get('time', '').strip()
    roles_raw = request.form.get('roles', '').strip()
    reset_roles = request.form.get('reset_roles') == 'on'

    if not title:
        flash('Название обязательно', 'error')
        return redirect(url_for('event_edit', guild_id=guild_id, event_id=event_id))

    # Применяем изменения внутри event loop бота
    bot = get_bot_instance()
    loop = bot.loop if bot else None
    async def apply_edit():
        import main; ALL_SESSIONS = main.ALL_SESSIONS; save_all_data = main.save_all_data; save_event = main.save_event; update_party_message = main.update_party_message
        s = ALL_SESSIONS.get(str(event_id)) or ALL_SESSIONS.get(str(int(event_id)))
        if not s:
            return False
        s['title'] = title
        s['description'] = description
        s['time'] = time_str
        if roles_raw:
            roles_list = [r.strip() for r in roles_raw.split('\n') if r.strip()]
            if reset_roles:
                s['party_roles'] = [{'name': r, 'user_id': None} for r in roles_list]
            else:
                # Попробуем сохранить назначения там, где имена совпали (по порядку)
                old = s.get('party_roles', [])
                new_list = []
                for idx, name in enumerate(roles_list):
                    uid = None
                    if idx < len(old) and old[idx].get('name') == name:
                        uid = old[idx].get('user_id')
                    new_list.append({'name': name, 'user_id': uid})
                s['party_roles'] = new_list
        ALL_SESSIONS[str(s['main_msg_id'])] = s
        save_all_data()
        save_event(int(s['main_msg_id']), s)
        try:
            await update_party_message_web(int(s['main_msg_id']))
        except Exception:
            pass
        return True

    ok = False
    try:
        if loop:
            fut = asyncio.run_coroutine_threadsafe(apply_edit(), loop)
            ok = fut.result(timeout=10)
    except Exception as e:
        print(f"edit event error: {e}")
        ok = False
    if ok:
        flash('Событие обновлено', 'success')
        return redirect(url_for('event_details', guild_id=guild_id, event_id=event_id))
    else:
        flash('Не удалось обновить событие', 'error')
        return redirect(url_for('event_edit', guild_id=guild_id, event_id=event_id))

@app.route('/guild/<guild_id>/events/guest')
def guild_events_guest(guild_id):
    """Гостевая страница для создания событий участниками без админ-прав"""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    # Проверяем, что пользователь является участником сервера
    user_guilds = get_user_guilds(session['access_token'])
    is_member = any(g['id'] == guild_id for g in user_guilds)
    
    if not is_member:
        flash('Вы не являетесь участником этого сервера', 'error')
        return redirect(url_for('dashboard'))
    
    guild_info = next((g for g in user_guilds if g['id'] == guild_id), None)
    bot_guilds = get_bot_guilds()
    bot_guild = next((g for g in bot_guilds if g['id'] == guild_id), None)
    
    if not bot_guild:
        flash('Бот не подключён к этому серверу', 'error')
        return redirect(url_for('dashboard'))
    
    # Получаем каналы и шаблоны для создания события
    channels = get_guild_channels(guild_id, session['access_token'])
    templates = get_guild_templates(int(guild_id))
    
    # Фильтруем только текстовые каналы, где бот может писать
    text_channels = [ch for ch in channels if ch.get('type') == 0]  # 0 = text channel
    
    return render_template('guild_events_guest.html',
                         guild=guild_info,
                         channels=text_channels,
                         templates=templates,
                         user=session['user'])

@app.route('/guild/<guild_id>/events/guest/create', methods=['POST'])
def create_event_guest(guild_id):
    """Создание события участником без админ-прав"""
    if 'user' not in session:
        return jsonify({'error': 'Требуется авторизация'}), 401
    
    # Проверяем, что пользователь является участником сервера
    user_guilds = get_user_guilds(session['access_token'])
    is_member = any(g['id'] == guild_id for g in user_guilds)
    
    if not is_member:
        flash('Вы не являетесь участником этого сервера', 'error')
        return redirect(url_for('dashboard'))
    
    try:
        # Получаем данные из формы
        if request.is_json:
            data = request.get_json()
        else:
            data = {
                'channel_id': request.form.get('channel_id'),
                'title': request.form.get('title', '').strip(),
                'description': request.form.get('description', '').strip(),
                'time': request.form.get('time', '').strip(),
                'roles': request.form.getlist('roles[]') or request.form.get('roles', '').split('\n')
            }
        
        channel_id = data.get('channel_id')
        title = data.get('title', '').strip()
        description = data.get('description', '').strip()
        time_str = data.get('time', '').strip()
        roles = data.get('roles', [])
        
        # Очищаем роли от пустых строк
        if isinstance(roles, str):
            roles = [r.strip() for r in roles.split('\n') if r.strip()]
        elif isinstance(roles, list):
            roles = [r.strip() for r in roles if r and r.strip()]
        
        if not channel_id or not title or not roles:
            error_msg = 'Заполните все обязательные поля: канал, название события и роли'
            if request.is_json:
                return jsonify({'error': error_msg}), 400
            else:
                flash(error_msg, 'error')
                return redirect(url_for('guild_events_guest', guild_id=guild_id))
        
        try:
            channel_id = int(channel_id)
        except (ValueError, TypeError):
            error_msg = 'Некорректный ID канала'
            if request.is_json:
                return jsonify({'error': error_msg}), 400
            else:
                flash(error_msg, 'error')
                return redirect(url_for('guild_events_guest', guild_id=guild_id))
        
        # Добавляем префикс к названию, чтобы показать, что событие создано участником
        user_name = session['user'].get('username', 'Участник')
        prefixed_title = f"[{user_name}] {title}"
        
        # Создаем команду для бота через файл очереди
        command_data = {
            'type': 'create_event',
            'guild_id': int(guild_id),
            'channel_id': channel_id,
            'title': prefixed_title,
            'description': description,
            'time': time_str,
            'roles': roles,
            'creator_id': int(session['user']['id']),
            'is_guest_event': True,
            'timestamp': time.time()
        }
        
        # Сохраняем команду в файл очереди
        queue_file = 'command_queue.json'
        commands = []
        if os.path.exists(queue_file):
            try:
                with open(queue_file, 'r', encoding='utf-8') as f:
                    commands = json.load(f)
            except:
                commands = []
        
        commands.append(command_data)
        
        with open(queue_file, 'w', encoding='utf-8') as f:
            json.dump(commands, f, ensure_ascii=False, indent=2)
        
        success_msg = f'Событие "{title}" отправлено на создание! Оно появится в канале через несколько секунд.'
        if request.is_json:
            return jsonify({'success': True, 'message': success_msg})
        else:
            flash(success_msg, 'success')
            return redirect(url_for('guild_events_guest', guild_id=guild_id))
        
    except Exception as e:
        error_msg = f'Ошибка создания события: {str(e)}'
        if request.is_json:
            return jsonify({'error': error_msg}), 500
        else:
            flash(error_msg, 'error')
            return redirect(url_for('guild_events_guest', guild_id=guild_id))

@app.route('/guild/<guild_id>/events/role/<role_id>')
def guild_events_role(guild_id, role_id):
    """Страница создания событий для участников с определенной ролью"""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user']['id']
    access_token = session['access_token']
    
    # Проверяем, что пользователь является участником сервера
    user_guilds = get_user_guilds(access_token)
    is_member = any(g['id'] == guild_id for g in user_guilds)
    
    if not is_member:
        flash('Вы не являетесь участником этого сервера', 'error')
        return redirect(url_for('dashboard'))
    
    # Проверяем, что у пользователя есть нужная роль
    if not user_has_role(guild_id, user_id, role_id, access_token):
        flash('У вас нет необходимой роли для доступа к этой странице', 'error')
        return redirect(url_for('dashboard'))
    
    guild_info = next((g for g in user_guilds if g['id'] == guild_id), None)
    bot_guilds = get_bot_guilds()
    bot_guild = next((g for g in bot_guilds if g['id'] == guild_id), None)
    
    if not bot_guild:
        flash('Бот не подключён к этому серверу', 'error')
        return redirect(url_for('dashboard'))
    
    # Получаем информацию о роли
    bot = get_bot_instance()
    role_name = "Особая роль"
    if bot:
        guild_obj = bot.get_guild(int(guild_id))
        if guild_obj:
            role_obj = guild_obj.get_role(int(role_id))
            if role_obj:
                role_name = role_obj.name
    
    # Получаем каналы и шаблоны для создания события
    channels = get_guild_channels(guild_id, access_token)
    templates = get_guild_templates(int(guild_id))
    
    # Фильтруем только текстовые каналы, где бот может писать
    text_channels = [ch for ch in channels if ch.get('type') == 0]  # 0 = text channel
    
    # Получаем события, созданные пользователем с этой ролью
    try:
        user_events = []
        import main; _SESS = main.ALL_SESSIONS
        for sid, ev in _SESS.items():
            if (ev.get('guild_id') == int(guild_id) and 
                ev.get('creator_id') == int(user_id) and 
                ev.get('creator_role_id') == int(role_id)):
                user_events.append({
                    'id': sid,
                    'title': ev.get('title'),
                    'channel_id': ev.get('channel_id'),
                    'stopped': bool(ev.get('stopped')),
                    'time': ev.get('time', ''),
                })
    except Exception:
        user_events = []
    
    return render_template('guild_events_role.html',
                         guild=guild_info,
                         channels=text_channels,
                         templates=templates,
                         user=session['user'],
                         role_name=role_name,
                         role_id=role_id,
                         user_events=user_events)

@app.route('/guild/<guild_id>/events/role/<role_id>/create', methods=['POST'])
def create_event_role(guild_id, role_id):
    """Создание события участником с определенной ролью"""
    if 'user' not in session:
        return jsonify({'error': 'Требуется авторизация'}), 401
    
    user_id = session['user']['id']
    access_token = session['access_token']
    
    # Проверяем, что пользователь является участником сервера
    user_guilds = get_user_guilds(access_token)
    is_member = any(g['id'] == guild_id for g in user_guilds)
    
    if not is_member:
        flash('Вы не являетесь участником этого сервера', 'error')
        return redirect(url_for('dashboard'))
    
    # Проверяем, что у пользователя есть нужная роль
    if not user_has_role(guild_id, user_id, role_id, access_token):
        flash('У вас нет необходимой роли для создания событий', 'error')
        return redirect(url_for('dashboard'))
    
    try:
        # Получаем данные из формы
        if request.is_json:
            data = request.get_json()
        else:
            data = {
                'channel_id': request.form.get('channel_id'),
                'title': request.form.get('title', '').strip(),
                'description': request.form.get('description', '').strip(),
                'time': request.form.get('time', '').strip(),
                'roles': request.form.getlist('roles[]') or request.form.get('roles', '').split('\n')
            }
        
        channel_id = data.get('channel_id')
        title = data.get('title', '').strip()
        description = data.get('description', '').strip()
        time_str = data.get('time', '').strip()
        roles = data.get('roles', [])
        
        # Очищаем роли от пустых строк
        if isinstance(roles, str):
            roles = [r.strip() for r in roles.split('\n') if r.strip()]
        elif isinstance(roles, list):
            roles = [r.strip() for r in roles if r and r.strip()]
        
        if not channel_id or not title or not roles:
            error_msg = 'Заполните все обязательные поля: канал, название события и роли'
            if request.is_json:
                return jsonify({'error': error_msg}), 400
            else:
                flash(error_msg, 'error')
                return redirect(url_for('guild_events_role', guild_id=guild_id, role_id=role_id))
        
        try:
            channel_id = int(channel_id)
        except (ValueError, TypeError):
            error_msg = 'Некорректный ID канала'
            if request.is_json:
                return jsonify({'error': error_msg}), 400
            else:
                flash(error_msg, 'error')
                return redirect(url_for('guild_events_role', guild_id=guild_id, role_id=role_id))
        
        # Получаем название роли для префикса
        bot = get_bot_instance()
        role_name = "Роль"
        if bot:
            guild_obj = bot.get_guild(int(guild_id))
            if guild_obj:
                role_obj = guild_obj.get_role(int(role_id))
                if role_obj:
                    role_name = role_obj.name
        
        user_name = session['user'].get('username', 'Участник')
        prefixed_title = f"[{role_name}] {title}"
        
        # Создаем команду для бота через файл очереди
        command_data = {
            'type': 'create_event',
            'guild_id': int(guild_id),
            'channel_id': channel_id,
            'title': prefixed_title,
            'description': description,
            'time': time_str,
            'roles': roles,
            'creator_id': int(user_id),
            'creator_role_id': int(role_id),
            'is_role_event': True,
            'timestamp': time.time()
        }
        
        # Сохраняем команду в файл очереди
        queue_file = 'command_queue.json'
        commands = []
        if os.path.exists(queue_file):
            try:
                with open(queue_file, 'r', encoding='utf-8') as f:
                    commands = json.load(f)
            except:
                commands = []
        
        commands.append(command_data)
        
        with open(queue_file, 'w', encoding='utf-8') as f:
            json.dump(commands, f, ensure_ascii=False, indent=2)
        
        success_msg = f'Событие "{title}" отправлено на создание! Оно появится в канале через несколько секунд.'
        if request.is_json:
            return jsonify({'success': True, 'message': success_msg})
        else:
            flash(success_msg, 'success')
            return redirect(url_for('guild_events_role', guild_id=guild_id, role_id=role_id))
        
    except Exception as e:
        error_msg = f'Ошибка создания события: {str(e)}'
        if request.is_json:
            return jsonify({'error': error_msg}), 500
        else:
            flash(error_msg, 'error')
            return redirect(url_for('guild_events_role', guild_id=guild_id, role_id=role_id))

@app.route('/guild/<guild_id>/role-settings')
def guild_role_settings(guild_id):
    """Настройка ролей для создания событий"""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        flash('У вас нет прав для управления ботом на этом сервере', 'error')
        return redirect(url_for('dashboard'))
    
    guild_info = next((g for g in user_guilds if g['id'] == guild_id), None)
    
    # Получаем роли сервера через Discord API
    guild_roles = get_guild_roles(guild_id, session['access_token'])
    
    # Получаем текущие настройки ролей
    current_event_roles = get_guild_event_creator_roles(guild_id)
    
    return render_template('guild_role_settings.html',
                         guild=guild_info,
                         guild_roles=guild_roles,
                         current_event_roles=current_event_roles)

@app.route('/guild/<guild_id>/role-settings/update', methods=['POST'])
def update_guild_role_settings(guild_id):
    """Обновление настроек ролей для создания событий"""
    if 'user' not in session:
        return jsonify({'error': 'Требуется авторизация'}), 401
    
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        return jsonify({'error': 'Нет прав доступа'}), 403
    
    try:
        if request.is_json:
            data = request.get_json()
            role_ids = data.get('role_ids', [])
        else:
            role_ids = request.form.getlist('role_ids[]')
        
        # Преобразуем в числа и фильтруем
        role_ids = [int(rid) for rid in role_ids if rid and rid.isdigit()]
        
        # Сохраняем настройки
        if set_guild_event_creator_roles(guild_id, role_ids):
            success_msg = 'Настройки ролей обновлены успешно'
            if request.is_json:
                return jsonify({'success': True, 'message': success_msg})
            else:
                flash(success_msg, 'success')
                return redirect(url_for('guild_role_settings', guild_id=guild_id))
        else:
            error_msg = 'Ошибка при сохранении настроек'
            if request.is_json:
                return jsonify({'error': error_msg}), 500
            else:
                flash(error_msg, 'error')
                return redirect(url_for('guild_role_settings', guild_id=guild_id))
        
    except Exception as e:
        error_msg = f'Ошибка обновления настроек: {str(e)}'
        if request.is_json:
            return jsonify({'error': error_msg}), 500
        else:
            flash(error_msg, 'error')
            return redirect(url_for('guild_role_settings', guild_id=guild_id))

@app.route('/guild/<guild_id>/submissions')
def guild_submissions(guild_id):
    """Страница управления заявками на вступление"""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        flash('У вас нет прав для управления ботом на этом сервере', 'error')
        return redirect(url_for('dashboard'))
    
    guild_info = next((g for g in user_guilds if g['id'] == guild_id), None)
    if not guild_info:
        bot_guilds = get_bot_guilds()
        guild_info = next((g for g in bot_guilds if g['id'] == guild_id), None)
    
    return render_template('guild_submissions.html',
                         guild=guild_info,
                         guild_id=guild_id,
                         guild_name=guild_info['name'] if guild_info else 'Unknown Guild')

@app.route('/guild/<guild_id>/shop')
def guild_shop(guild_id):
    """Страница управления магазином очков"""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user_guilds = get_user_guilds(session['access_token'])
    bot_guilds = get_bot_guilds()
    
    if not user_has_permissions_session(user_guilds, bot_guilds, guild_id):
        flash('У вас нет прав для управления ботом на этом сервере', 'error')
        return redirect(url_for('dashboard'))
    
    guild_info = next((g for g in user_guilds if g['id'] == guild_id), None)
    if not guild_info:
        bot_guilds = get_bot_guilds()
        guild_info = next((g for g in bot_guilds if g['id'] == guild_id), None)
    
    return render_template('guild_shop.html',
                         guild=guild_info,
                         guild_id=guild_id,
                         guild_name=guild_info['name'] if guild_info else 'Unknown Guild')

@app.route('/my-id')
def my_id():
    """Показать ID текущего пользователя"""
    if 'user' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user']['id']
    username = session['user']['username']
    
    return f"""
    <h1>Ваши данные Discord</h1>
    <p><strong>ID:</strong> {user_id}</p>
    <p><strong>Имя:</strong> {username}</p>
    <p>Скопируйте ваш ID и добавьте в config.json в массив admin_users</p>
    <a href="/dashboard">Назад</a>
    """

@app.route('/api/guild/<guild_id>/deploy-panels', methods=['POST'])
def deploy_panels_api(guild_id):
    """API для размещения панелей через веб-интерфейс"""
    if not session.get('user'):
        return jsonify({'success': False, 'message': 'Требуется авторизация'}), 401
    
    # Проверяем права администратора
    user_guilds = session.get('user_guilds', [])
    if not user_guilds:
        # Пытаемся обновить список гильдий если отсутствует (устаревшая сессия)
        token = session.get('access_token') or session.get('token') or session.get('oauth_token')
        if token:
            try:
                user_guilds = get_user_guilds(token)
                session['user_guilds'] = user_guilds
                print(f"[PANELS] Refetched user_guilds: {len(user_guilds)}")
            except Exception as e:
                print(f"[PANELS] Failed to refetch user_guilds: {e}")
    bot_guilds = get_bot_guilds()
    
    guild_info = next((g for g in user_guilds if g['id'] == guild_id), None)
    if not guild_info:
        guild_info = next((g for g in bot_guilds if g['id'] == guild_id), None)
    if not guild_info:
        return jsonify({'success': False, 'message': 'Сервер не найден'}), 404
    
    # Упрощенная проверка: если пользователь числится в user_guilds или является bot admin — разрешаем
    try:
        user_id = session['user']['id']
        user_guild = next((g for g in user_guilds if g['id'] == guild_id), None)
        has_owner_flag = bool(user_guild and user_guild.get('owner'))
        perms_mask = int(user_guild.get('permissions', 0)) if user_guild else 0
        basic_perms = (perms_mask & 0x8) or (perms_mask & 0x20)
        is_admin = is_bot_admin(user_id)
        if not (user_guild and (has_owner_flag or basic_perms or is_admin)):
            print(f"[PANELS] DENY guild={guild_id} user={user_id} owner={has_owner_flag} mask={perms_mask} bot_admin={is_admin}")
            return jsonify({'success': False, 'message': 'Недостаточно прав (упрощённая проверка)'}), 403
        print(f"[PANELS] ALLOW guild={guild_id} user={user_id} owner={has_owner_flag} mask={perms_mask} bot_admin={is_admin}")
    except Exception as e:
        print(f"[PANELS] Ошибка упрощенной проверки прав: {e}. Разрешаем по умолчанию.")
    
    try:
        data = request.get_json()
        panel_type = data.get('panel_type', 'both')
        
        # Получаем настройки через unified слой settings.db
        complete = get_complete_guild_settings(int(guild_id))
        recruit_settings = complete.get('recruit_settings', {})
        
        success_panels = []
        error_panels = []
        
        # Импортируем бота для получения каналов
        import discord
        from discord.ext import commands
        
        # Получаем guild из бота
        bot_instance = get_bot_instance()
        
        if not bot_instance:
            return jsonify({
                'success': False, 
                'message': 'Бот недоступен. Попробуйте использовать Discord команды.'
            }), 500
        
        guild = bot_instance.get_guild(int(guild_id))
        if not guild:
            return jsonify({
                'success': False, 
                'message': 'Сервер не найден в боте'
            }), 404
        
        success_panels = []
        error_panels = []
        
        # Размещение панели набора
        if panel_type in ["recruit", "both"]:
            recruit_channel_id = recruit_settings.get('recruit_panel_channel')
            if recruit_channel_id:
                try:
                    channel = guild.get_channel(int(recruit_channel_id))
                    if channel:
                        # Отправляем асинхронно с более надёжным обработчиком
                        try:
                            # Проверяем, что цикл работает
                            loop = bot_instance.loop
                            if not loop or loop.is_closed():
                                raise Exception("Event loop бота не работает")

                            # Создаем корутину, внутри которой инициализируем Embed/View
                            async def send_panel():
                                # Используем постоянную кнопку из recruit_bot с корректным custom_id и callback
                                from recruit_bot.bot import PersistentApplyButtonView
                                embed = discord.Embed(
                                    title="📝 Заявка в гильдию",
                                    description=(
                                        "Нажмите кнопку ниже, чтобы отправить заявку.\n"
                                        "Модераторы рассмотрят вашу заявку и свяжутся с вами."
                                    ),
                                    color=discord.Color.blue()
                                )
                                view = PersistentApplyButtonView(bot_instance)
                                return await channel.send(embed=embed, view=view)

                            # Выполняем в цикле бота с правильной обработкой
                            if loop.is_running():
                                future = asyncio.run_coroutine_threadsafe(send_panel(), loop)
                                result = future.result(timeout=10)
                                print(f"Панель набора размещена: {result.id}")
                            else:
                                result = loop.run_until_complete(send_panel())
                                print(f"Панель набора размещена: {result.id}")

                        except Exception as send_error:
                            print(f"Ошибка отправки панели набора: {send_error}")
                            raise send_error
                        
                        success_panels.append(f"Панель набора в #{channel.name}")
                    else:
                        error_panels.append("Панель набора (канал не найден)")
                except Exception as e:
                    error_panels.append(f"Панель набора ({str(e)})")
                    print(f"Ошибка размещения панели набора: {e}")
            else:
                error_panels.append("Панель набора (канал не настроен)")
        
        # Размещение панели очков
        if panel_type in ["points", "both"]:
            points_channel_id = recruit_settings.get('points_panel_channel')
            if points_channel_id:
                try:
                    channel = guild.get_channel(int(points_channel_id))
                    if channel:
                        try:
                            # Проверяем, что цикл работает
                            loop = bot_instance.loop
                            if not loop or loop.is_closed():
                                raise Exception("Event loop бота не работает")

                            # Создаем корутину, внутри которой инициализируем Embed/View
                            async def send_points_panel():
                                # Полностью соответствуем панели из команды /events_panel
                                from recruit_bot.ui_components import UnifiedEventView
                                embed = discord.Embed(
                                    title="🎯 Система событий и наград",
                                    description=(
                                        "**🎮 Добро пожаловать в систему событий Albion Online!**\n\n"
                                        "Здесь вы можете:\n"
                                        "🎯 **Подать заявку** на участие в событии\n"
                                        "💰 **Проверить баланс** очков и историю\n"
                                        "🛒 **Купить награды** за накопленные очки\n\n"
                                        "**Доступные события:**\n"
                                        "🕷️ Кристальные жуки (убийство) - 1 очко\n"
                                        "🔵 Синие сферы (доставка) - 1.5 очка\n"
                                        "🟣 Фиолетовые сферы (доставка) - 3 очка\n"
                                        "🟡 Золотые сферы (доставка) - 5 очков\n"
                                        "🌪️ Зеленые вихри (доставка) - 2 очка\n"
                                        "🌀 Синие вихри (доставка) - 3 очка\n"
                                        "🌊 Фиолетовые вихри (доставка) - 6 очков\n"
                                        "💫 Золотые вихри (доставка) - 10 очков"
                                    ),
                                    color=discord.Color.blue()
                                )
                                embed.add_field(
                                    name="🛒 Доступные награды",
                                    value=(
                                        "💰 **200k серебра** - 10 очков\n"
                                        "🎲 **Рандомная вещь** - 30 очков\n"
                                        "⚔️ **Комплект экипировки** - 50 очков"
                                    ),
                                    inline=False
                                )
                                embed.add_field(
                                    name="ℹ️ Как это работает",
                                    value=(
                                        "1. Участвуйте в событиях и зарабатывайте очки\n"
                                        "2. Модератор проверяет и начисляет очки\n"
                                        "3. Обменивайте очки на награды в магазине\n"
                                        "4. Получайте награды в игре от модераторов"
                                    ),
                                    inline=False
                                )
                                embed.set_footer(text="💡 Всегда прикладывайте скриншоты к заявкам!")

                                view = UnifiedEventView()
                                return await channel.send(embed=embed, view=view)

                            # Выполняем в цикле бота с правильной обработкой
                            if loop.is_running():
                                future = asyncio.run_coroutine_threadsafe(send_points_panel(), loop)
                                result = future.result(timeout=10)
                                print(f"Панель очков размещена: {result.id}")
                            else:
                                result = loop.run_until_complete(send_points_panel())
                                print(f"Панель очков размещена: {result.id}")

                        except Exception as send_error:
                            print(f"Ошибка отправки панели очков: {send_error}")
                            raise send_error
                        
                        success_panels.append(f"Панель очков в #{channel.name}")
                    else:
                        error_panels.append("Панель очков (канал не найден)")
                except Exception as e:
                    error_panels.append(f"Панель очков ({str(e)})")
                    print(f"Ошибка размещения панели очков: {e}")
            else:
                error_panels.append("Панель очков (канал не настроен)")
        
        # Формируем ответ
        if success_panels and not error_panels:
            return jsonify({
                'success': True,
                'message': f"✅ Успешно размещены: {', '.join(success_panels)}"
            })
        elif success_panels and error_panels:
            return jsonify({
                'success': True,
                'message': f"✅ Размещены: {', '.join(success_panels)}. ❌ Ошибки: {', '.join(error_panels)}"
            })
        else:
            return jsonify({
                'success': False,
                'message': f"❌ Ошибки размещения: {', '.join(error_panels)}"
            })
            
    except Exception as e:
        print(f"Ошибка в deploy_panels_api: {e}")
        return jsonify({
            'success': False,
            'message': f'Ошибка сервера: {str(e)}'
        }), 500

if __name__ == '__main__':
    app.run(host='localhost', port=8082, debug=True)
