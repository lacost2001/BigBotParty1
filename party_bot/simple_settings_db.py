"""
Простая и быстрая система настроек для Discord бота
Создана с чистого листа для максимальной производительности веб-интерфейса
"""

import sqlite3
import json
import threading
import os
from typing import Any, Dict, Optional
from datetime import datetime

class SimpleSettingsDB:
    """Простая и быстрая база данных настроек"""
    
    def __init__(self, db_path: str = None):
        if db_path is None:
            # Создаём БД в корне проекта
            db_path = os.path.join(os.path.dirname(__file__), "..", "settings.db")
        
        self.db_path = os.path.abspath(db_path)
        self.lock = threading.Lock()
        self._init_database()
        print(f"✅ Простая база данных настроек готова: {self.db_path}")
    
    def _init_database(self):
        """Создание простой структуры базы данных"""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Одна простая таблица для всех настроек
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    guild_id TEXT NOT NULL,
                    setting_key TEXT NOT NULL,
                    setting_value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (guild_id, setting_key)
                )
            ''')
            
            # Индекс для быстрого поиска
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_settings_lookup 
                ON settings (guild_id, setting_key)
            ''')
            
            # Триггер для автоматического обновления времени
            cursor.execute('''
                CREATE TRIGGER IF NOT EXISTS update_settings_timestamp 
                AFTER UPDATE ON settings
                BEGIN
                    UPDATE settings SET updated_at = CURRENT_TIMESTAMP 
                    WHERE guild_id = NEW.guild_id AND setting_key = NEW.setting_key;
                END
            ''')
            
            conn.commit()
            conn.close()
    
    def set_guild_setting(self, guild_id: int, key: str, value: Any):
        """Быстрая установка одной настройки"""
        guild_id_str = str(guild_id)
        
        # Сериализуем значение в JSON
        if isinstance(value, (dict, list)):
            value_str = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, bool):
            value_str = "true" if value else "false"
        else:
            value_str = str(value)
        
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Используем REPLACE для быстрой вставки/обновления
            cursor.execute('''
                REPLACE INTO settings (guild_id, setting_key, setting_value, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ''', (guild_id_str, key, value_str))
            
            conn.commit()
            conn.close()
    
    def get_guild_setting(self, guild_id: int, key: str, default: Any = None) -> Any:
        """Быстрое получение одной настройки"""
        guild_id_str = str(guild_id)
        
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT setting_value FROM settings 
                WHERE guild_id = ? AND setting_key = ?
            ''', (guild_id_str, key))
            
            result = cursor.fetchone()
            conn.close()
            
            if result:
                value_str = result[0]
                # Десериализуем значение
                try:
                    # Пробуем JSON
                    return json.loads(value_str)
                except (json.JSONDecodeError, TypeError):
                    # Если не JSON, проверяем bool
                    if value_str.lower() in ('true', 'false'):
                        return value_str.lower() == 'true'
                    # Пробуем число
                    try:
                        if '.' in value_str:
                            return float(value_str)
                        else:
                            return int(value_str)
                    except ValueError:
                        # Возвращаем как строку
                        return value_str
            else:
                return default
    
    def get_guild_settings(self, guild_id: int) -> Dict[str, Any]:
        """Получение всех настроек сервера одним запросом"""
        guild_id_str = str(guild_id)
        
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                SELECT setting_key, setting_value FROM settings 
                WHERE guild_id = ?
            ''', (guild_id_str,))
            
            results = cursor.fetchall()
            conn.close()
            
            settings = {}
            for key, value_str in results:
                # Десериализуем значение
                try:
                    # Пробуем JSON
                    settings[key] = json.loads(value_str)
                except (json.JSONDecodeError, TypeError):
                    # Если не JSON, проверяем bool
                    if value_str.lower() in ('true', 'false'):
                        settings[key] = value_str.lower() == 'true'
                    else:
                        # Пробуем число
                        try:
                            if '.' in value_str:
                                settings[key] = float(value_str)
                            else:
                                settings[key] = int(value_str)
                        except ValueError:
                            # Оставляем как строку
                            settings[key] = value_str
            
            return settings
    
    def batch_set_settings(self, guild_id: int, settings: Dict[str, Any]):
        """Быстрая установка множественных настроек одной транзакцией"""
        guild_id_str = str(guild_id)
        
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Начинаем транзакцию
            cursor.execute('BEGIN TRANSACTION')
            
            try:
                for key, value in settings.items():
                    # Сериализуем значение
                    if isinstance(value, (dict, list)):
                        value_str = json.dumps(value, ensure_ascii=False)
                    elif isinstance(value, bool):
                        value_str = "true" if value else "false"
                    else:
                        value_str = str(value)
                    
                    cursor.execute('''
                        REPLACE INTO settings (guild_id, setting_key, setting_value, updated_at)
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ''', (guild_id_str, key, value_str))
                
                cursor.execute('COMMIT')
                
            except Exception as e:
                cursor.execute('ROLLBACK')
                raise e
            finally:
                conn.close()
    
    def delete_guild_setting(self, guild_id: int, key: str):
        """Удаление настройки"""
        guild_id_str = str(guild_id)
        
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                DELETE FROM settings 
                WHERE guild_id = ? AND setting_key = ?
            ''', (guild_id_str, key))
            
            conn.commit()
            conn.close()
    
    def delete_guild_settings(self, guild_id: int):
        """Удаление всех настроек сервера"""
        guild_id_str = str(guild_id)
        
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('''
                DELETE FROM settings WHERE guild_id = ?
            ''', (guild_id_str,))
            
            conn.commit()
            conn.close()
    
    def get_all_guilds(self) -> list:
        """Получение списка всех серверов с настройками"""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute('SELECT DISTINCT guild_id FROM settings ORDER BY guild_id')
            results = cursor.fetchall()
            conn.close()
            
            return [row[0] for row in results]
    
    def get_stats(self) -> Dict[str, Any]:
        """Получение статистики базы данных"""
        with self.lock:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            # Количество серверов
            cursor.execute('SELECT COUNT(DISTINCT guild_id) FROM settings')
            guilds_count = cursor.fetchone()[0]
            
            # Общее количество настроек
            cursor.execute('SELECT COUNT(*) FROM settings')
            settings_count = cursor.fetchone()[0]
            
            # Размер базы данных
            cursor.execute('SELECT page_count * page_size as size FROM pragma_page_count(), pragma_page_size()')
            size_result = cursor.fetchone()
            db_size = size_result[0] if size_result else 0
            
            # Последние обновления
            cursor.execute('SELECT guild_id, setting_key, updated_at FROM settings ORDER BY updated_at DESC LIMIT 5')
            recent_updates = cursor.fetchall()
            
            conn.close()
            
            return {
                'guilds_count': guilds_count,
                'settings_count': settings_count,
                'db_size_bytes': db_size,
                'db_size_kb': db_size / 1024 if db_size else 0,
                'avg_settings_per_guild': settings_count / max(guilds_count, 1),
                'recent_updates': recent_updates
            }

# Глобальный экземпляр для быстрого доступа
_db_instance = None

def get_settings_db() -> SimpleSettingsDB:
    """Получить глобальный экземпляр базы данных настроек"""
    global _db_instance
    if _db_instance is None:
        _db_instance = SimpleSettingsDB()
    return _db_instance

# Функции для обратной совместимости
def get_guild_setting(guild_id: int, key: str, default: Any = None) -> Any:
    """Получить настройку сервера"""
    return get_settings_db().get_guild_setting(guild_id, key, default)

def set_guild_setting(guild_id: int, key: str, value: Any):
    """Установить настройку сервера"""
    get_settings_db().set_guild_setting(guild_id, key, value)

def get_guild_settings(guild_id: int) -> Dict[str, Any]:
    """Получить все настройки сервера"""
    return get_settings_db().get_guild_settings(guild_id)

def save_all_data():
    """Заглушка для совместимости - данные сохраняются автоматически"""
    pass

def reload_settings_from_disk():
    """Заглушка для совместимости - данные всегда актуальные"""
    pass

if __name__ == "__main__":
    # Быстрый тест
    print("🚀 Тестирование простой системы настроек")
    
    db = SimpleSettingsDB()
    test_guild_id = 123456789012345678
    
    # Тест одиночных настроек
    print("✏️ Тест одиночных настроек...")
    db.set_guild_setting(test_guild_id, "monitoring_enabled", True)
    db.set_guild_setting(test_guild_id, "event_creator_roles", [111, 222, 333])
    db.set_guild_setting(test_guild_id, "ping_role", "everyone")
    db.set_guild_setting(test_guild_id, "reminder_time", [0, 15])
    
    # Тест пакетных настроек
    print("✏️ Тест пакетных настроек...")
    batch_settings = {
        "cleanup_enabled": False,
        "reminders_enabled": True,
        "monitored_channels": [444, 555, 666],
        "monitoring_time": [10, 22],
        "custom_setting": "test_value"
    }
    db.batch_set_settings(test_guild_id, batch_settings)
    
    # Проверяем результаты
    print("📖 Чтение настроек...")
    all_settings = db.get_guild_settings(test_guild_id)
    for key, value in all_settings.items():
        print(f"  {key}: {value} ({type(value).__name__})")
    
    # Статистика
    print(f"\n📊 Статистика БД:")
    stats = db.get_stats()
    for key, value in stats.items():
        if key != 'recent_updates':
            print(f"  {key}: {value}")
    
    print("\n✅ Простая система готова к работе!")
