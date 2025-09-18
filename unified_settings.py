# -*- coding: utf-8 -*-
"""
Единая система настроек для Bigbot
Объединяет party и recruit настройки в один JSON файл
"""

import json
import os
import asyncio
from typing import Dict, Any, Optional

# Путь к единому файлу настроек
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
UNIFIED_SETTINGS_FILE = os.path.join(SCRIPT_DIR, "unified_settings.json")

# Настройки по умолчанию для каждой гильдии
DEFAULT_GUILD_SETTINGS = {
    # Party Bot настройки
    "party": {
        "event_creator_role": None,
        "moderator_role": None,
        "ping_role": "everyone",
        "monitored_channels": [],
        "monitoring_enabled": True,
        "cleanup_enabled": True,
        "reminders_enabled": False,
        "monitoring_time": [10, 20],
        "cleanup_time": [0, 0],
        "reminder_time": [0, 15],
        "cleanup_channels": None,
        "event_creator_roles": []  # Поле для множественных ролей создания событий
    },
    # Recruit Bot настройки
    "recruit": {
        "admin_role": None,
        "moderator_role": None,
        "points_moderator_roles": "",
        "recruiter_roles": "",
        "events_channel": None,
        "shop_channel": None,
        "events_data": None,
        "points_start_date": "",
        "points_end_date": "",
        "default_role": None,
        "recruit_role": None,
        "guild_name": "",
        "cooldown_hours": 1,
        # Новые настройки для каналов
        "forum_channel": None,  # Канал-форум для заявок в гильдию
        "points_panel_channel": None,  # Канал для панели подачи заявок на очки
    "recruit_panel_channel": None,  # Канал для панели подачи заявок в гильдию
    # Служебные флаги
    "onboarding_sent": False  # Отправлялось ли приветственное сообщение с ссылкой на веб
    }
}

class UnifiedSettings:
    """Класс для управления едиными настройками"""
    
    def __init__(self):
        self.settings = self._load_settings()
    
    def _load_settings(self) -> Dict[str, Any]:
        """Загрузить настройки из файла"""
        if os.path.exists(UNIFIED_SETTINGS_FILE):
            try:
                with open(UNIFIED_SETTINGS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"Ошибка загрузки настроек: {e}")
                return {"guilds": {}}
        else:
            return {"guilds": {}}
    
    def _save_settings(self):
        """Сохранить настройки в файл"""
        print("💾 Сохранение настроек. SETTINGS в памяти:", self.settings)
        try:
            with open(UNIFIED_SETTINGS_FILE, "r", encoding="utf-8") as f:
                disk_data = json.load(f)
            print("📁 Настройки с диска:", disk_data)
            
            # Объединяем настройки
            merged = self._deep_merge_dicts(disk_data, self.settings)
            print("🔀 Объединённые настройки:", merged)
            
            with open(UNIFIED_SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2, ensure_ascii=False)
            print("✅ Настройки сохранены в файл")
            
            # Перезагружаем настройки в память
            self.settings = merged
            print("🔄 Настройки перезагружены в память")
            
        except FileNotFoundError:
            # Файла нет, создаем новый
            with open(UNIFIED_SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Ошибка сохранения настроек: {e}")
    
    def _deep_merge_dicts(self, base: dict, incoming: dict) -> dict:
        """Глубокое слияние словарей. Значения из incoming имеют приоритет."""
        result = base.copy()
        for key, value in incoming.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge_dicts(result[key], value)
            else:
                result[key] = value
        return result
    
    def get_guild_settings(self, guild_id: int) -> Dict[str, Any]:
        """Получить настройки гильдии"""
        guild_id_str = str(guild_id)
        if "guilds" not in self.settings:
            self.settings["guilds"] = {}
        
        if guild_id_str not in self.settings["guilds"]:
            # Создаем настройки по умолчанию
            self.settings["guilds"][guild_id_str] = {
                "party": DEFAULT_GUILD_SETTINGS["party"].copy(),
                "recruit": DEFAULT_GUILD_SETTINGS["recruit"].copy()
            }
            self._save_settings()
        
        return self.settings["guilds"][guild_id_str]
    
    def get_party_settings(self, guild_id: int) -> Dict[str, Any]:
        """Получить только party настройки"""
        guild_settings = self.get_guild_settings(guild_id)
        return guild_settings.get("party", DEFAULT_GUILD_SETTINGS["party"].copy())
    
    def get_recruit_settings(self, guild_id: int) -> Dict[str, Any]:
        """Получить только recruit настройки"""
        guild_settings = self.get_guild_settings(guild_id)
        return guild_settings.get("recruit", DEFAULT_GUILD_SETTINGS["recruit"].copy())
    
    def set_party_setting(self, guild_id: int, key: str, value: Any):
        """Установить party настройку"""
        guild_settings = self.get_guild_settings(guild_id)
        if "party" not in guild_settings:
            guild_settings["party"] = DEFAULT_GUILD_SETTINGS["party"].copy()
        
        guild_settings["party"][key] = value
        self._save_settings()
    
    def set_recruit_setting(self, guild_id: int, key: str, value: Any):
        """Установить recruit настройку"""
        guild_settings = self.get_guild_settings(guild_id)
        if "recruit" not in guild_settings:
            guild_settings["recruit"] = DEFAULT_GUILD_SETTINGS["recruit"].copy()
        
        guild_settings["recruit"][key] = value
        self._save_settings()
    
    def update_party_settings(self, guild_id: int, updates: Dict[str, Any]):
        """Обновить несколько party настроек"""
        guild_settings = self.get_guild_settings(guild_id)
        if "party" not in guild_settings:
            guild_settings["party"] = DEFAULT_GUILD_SETTINGS["party"].copy()
        
        guild_settings["party"].update(updates)
        self._save_settings()
    
    def update_recruit_settings(self, guild_id: int, updates: Dict[str, Any]):
        """Обновить несколько recruit настроек"""
        guild_settings = self.get_guild_settings(guild_id)
        if "recruit" not in guild_settings:
            guild_settings["recruit"] = DEFAULT_GUILD_SETTINGS["recruit"].copy()
        
        guild_settings["recruit"].update(updates)
        self._save_settings()
    
    def migrate_from_old_settings(self):
        """Миграция из старых файлов настроек"""
        print("🔄 Начинаем миграцию настроек...")
        
        # Миграция из settings.json (party)
        old_settings_file = os.path.join(SCRIPT_DIR, "settings.json")
        if os.path.exists(old_settings_file):
            try:
                with open(old_settings_file, "r", encoding="utf-8") as f:
                    old_settings = json.load(f)
                
                if "guilds" in old_settings:
                    for guild_id, guild_data in old_settings["guilds"].items():
                        print(f"  📋 Мигрируем party настройки для гильдии {guild_id}")
                        
                        # Копируем party настройки
                        party_settings = {}
                        for key in DEFAULT_GUILD_SETTINGS["party"].keys():
                            if key in guild_data:
                                party_settings[key] = guild_data[key]
                        
                        self.update_party_settings(int(guild_id), party_settings)
                
                print("  ✅ Party настройки мигрированы")
            except Exception as e:
                print(f"  ❌ Ошибка миграции party настроек: {e}")
        
        # Миграция из recruit DB
        try:
            # Импортируем EventDatabase для миграции
            from recruit_bot.database import EventDatabase
            
            async def migrate_recruit():
                # Получаем список всех гильдий с настройками
                db_path = os.path.join(SCRIPT_DIR, "potatos_recruit.db")
                if os.path.exists(db_path):
                    import aiosqlite
                    async with aiosqlite.connect(db_path) as db:
                        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='guild_config'")
                        table_exists = await cursor.fetchone()
                        
                        if table_exists:
                            cursor = await db.execute("SELECT guild_id FROM guild_config")
                            guild_ids = await cursor.fetchall()
                            
                            for (guild_id,) in guild_ids:
                                print(f"  📋 Мигрируем recruit настройки для гильдии {guild_id}")
                                config = await EventDatabase.get_guild_config(guild_id)
                                if config:
                                    self.update_recruit_settings(guild_id, config)
            
            # Запускаем миграцию recruit настроек
            try:
                asyncio.run(migrate_recruit())
                print("  ✅ Recruit настройки мигрированы")
            except Exception as e:
                print(f"  ❌ Ошибка миграции recruit настроек: {e}")
                
        except ImportError:
            print("  ⚠️ Модуль recruit_bot недоступен, пропускаем миграцию recruit настроек")
        
        print("✅ Миграция настроек завершена")
    
    def export_settings(self) -> Dict[str, Any]:
        """Экспорт всех настроек"""
        return self.settings.copy()
    
    def import_settings(self, imported_settings: Dict[str, Any]):
        """Импорт настроек"""
        self.settings = imported_settings
        self._save_settings()

# Глобальный экземпляр
unified_settings = UnifiedSettings()

# Функции для обратной совместимости с party_bot
def get_guild_settings(guild_id: int) -> Dict[str, Any]:
    """Совместимость с party_bot - возвращает party настройки"""
    return unified_settings.get_party_settings(guild_id)

def set_guild_setting(guild_id: int, key: str, value: Any):
    """Совместимость с party_bot - устанавливает party настройку"""
    unified_settings.set_party_setting(guild_id, key, value)

def get_guild_setting(guild_id: int, key: str, default=None):
    """Совместимость с party_bot - получает party настройку"""
    party_settings = unified_settings.get_party_settings(guild_id)
    return party_settings.get(key, default)

# Функции для recruit_bot
def get_recruit_config(guild_id: int) -> Dict[str, Any]:
    """Получить recruit настройки"""
    return unified_settings.get_recruit_settings(guild_id)

def update_recruit_config(guild_id: int, **kwargs) -> bool:
    """Обновить recruit настройки"""
    try:
        unified_settings.update_recruit_settings(guild_id, kwargs)
        return True
    except Exception as e:
        print(f"Ошибка обновления recruit настроек: {e}")
        return False
