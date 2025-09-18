#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Простая система настроек на основе SQLite для Bigbot
Заменяет settings.json на более надежную базу данных
"""

import sqlite3
import json
import os
import threading
from typing import Dict, Any, Optional

class SimpleSettingsDB:
    """Простая система настроек на SQLite"""
    
    def __init__(self):
        """Инициализация базы данных"""
        # Размещаем БД в корне проекта
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.db_path = os.path.join(script_dir, "settings.db")
        self._lock = threading.Lock()
        self._init_database()
    
    def _init_database(self):
        """Создание таблицы настроек"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS guild_settings (
                        guild_id TEXT PRIMARY KEY,
                        settings_json TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                conn.commit()
                print(f"✅ База данных настроек готова: {self.db_path}")
            finally:
                conn.close()
    
    def get_default_settings(self) -> Dict[str, Any]:
        """Настройки по умолчанию"""
        return {
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
            "event_creator_roles": []
        }
    
    def get_guild_settings(self, guild_id: int) -> Dict[str, Any]:
        """Получить настройки гильдии"""
        guild_id_str = str(guild_id)
        
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT settings_json FROM guild_settings WHERE guild_id = ?",
                    (guild_id_str,)
                )
                
                row = cursor.fetchone()
                if row:
                    try:
                        settings = json.loads(row[0])
                        # Добавляем недостающие поля
                        defaults = self.get_default_settings()
                        for key, value in defaults.items():
                            if key not in settings:
                                settings[key] = value
                        return settings
                    except json.JSONDecodeError:
                        print(f"❌ Ошибка парсинга настроек для гильдии {guild_id}")
                        return self.get_default_settings()
                else:
                    # Создаем новую гильдию с настройками по умолчанию
                    defaults = self.get_default_settings()
                    self._save_guild_settings(guild_id_str, defaults, conn)
                    return defaults
                    
            finally:
                conn.close()
    
    def _save_guild_settings(self, guild_id_str: str, settings: Dict[str, Any], conn: sqlite3.Connection):
        """Сохранить настройки гильдии (внутренний метод)"""
        cursor = conn.cursor()
        settings_json = json.dumps(settings, ensure_ascii=False)
        cursor.execute(
            "INSERT OR REPLACE INTO guild_settings (guild_id, settings_json, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (guild_id_str, settings_json)
        )
        conn.commit()
    
    def set_guild_setting(self, guild_id: int, key: str, value: Any) -> bool:
        """Установить одну настройку"""
        try:
            guild_id_str = str(guild_id)
            
            with self._lock:
                conn = sqlite3.connect(self.db_path)
                try:
                    # Получаем текущие настройки
                    current_settings = self.get_guild_settings(guild_id)
                    
                    # Обновляем значение
                    current_settings[key] = value
                    
                    # Сохраняем
                    self._save_guild_settings(guild_id_str, current_settings, conn)
                    
                    print(f"✅ Обновлена настройка {key}={value} для гильдии {guild_id}")
                    return True
                    
                finally:
                    conn.close()
                    
        except Exception as e:
            print(f"❌ Ошибка сохранения настройки: {e}")
            return False
    
    def get_guild_setting(self, guild_id: int, key: str, default: Any = None) -> Any:
        """Получить одну настройку"""
        settings = self.get_guild_settings(guild_id)
        return settings.get(key, default)
    
    def get_all_guilds(self) -> Dict[str, Dict[str, Any]]:
        """Получить все гильдии"""
        with self._lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT guild_id, settings_json FROM guild_settings")
                
                result = {}
                for row in cursor.fetchall():
                    guild_id, settings_json = row
                    try:
                        result[guild_id] = json.loads(settings_json)
                    except json.JSONDecodeError:
                        result[guild_id] = self.get_default_settings()
                
                return result
                
            finally:
                conn.close()

# Глобальный экземпляр
settings_db = SimpleSettingsDB()

# Функции для совместимости с существующим кодом
def get_guild_settings(guild_id: int) -> Dict[str, Any]:
    """Получить настройки гильдии"""
    return settings_db.get_guild_settings(guild_id)

def set_guild_setting(guild_id: int, key: str, value: Any) -> bool:
    """Установить настройку гильдии"""
    return settings_db.set_guild_setting(guild_id, key, value)

def get_guild_setting(guild_id: int, key: str, default: Any = None) -> Any:
    """Получить одну настройку"""
    return settings_db.get_guild_setting(guild_id, key, default)

def save_all_data():
    """Для совместимости - в БД сохраняется автоматически"""
    pass

def reload_settings_from_disk():
    """Для совместимости - БД всегда актуальна"""
    pass
