#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bigbot - Объединенный Discord бот
Включает в себя:
- Party Bot: управление мероприятиями, шаблонами, статистикой
- Recruit Bot: система рекрутинга, очков и магазина
"""

import asyncio
import logging
import os
import sys
import threading
import time
from pathlib import Path

# Загрузка переменных окружения из .env файла
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ Переменные окружения загружены из .env файла")
except ImportError:
    print("⚠️ python-dotenv не установлен, используем только системные переменные окружения")
except Exception as e:
    print(f"⚠️ Ошибка загрузки .env: {e}")

# === Коррекция PYTHONPATH для пакетных импортов (party_bot.*) ===
PROJECT_ROOT = Path(__file__).resolve().parent  # Bigbot/
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
PARENT_ROOT = PROJECT_ROOT.parent  # верхняя директория
if str(PARENT_ROOT) not in sys.path:
    sys.path.insert(0, str(PARENT_ROOT))
os.environ.setdefault("PYTHONPATH", os.pathsep.join([str(PROJECT_ROOT), str(PARENT_ROOT), os.environ.get("PYTHONPATH", "")]))

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)

logger = logging.getLogger("bigbot")

def run_bot():
    """Запуск объединенного Discord бота"""
    try:
        logger.info("🤖 Запуск объединенного Discord бота...")
        
        # Импортируем и запускаем главный бот модуль
        from party_bot.main import start_bot_with_reconnect as party_main
        import asyncio
        asyncio.run(party_main())
        
    except KeyboardInterrupt:
        logger.info("🤖 Discord бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")
        raise

def run_web():
    """Запуск веб-сервера"""
    try:
        logger.info("🌐 Запуск веб-сервера...")
        time.sleep(3)  # Даем боту время запуститься
        
        # Импортируем и запускаем веб-сервер
        from party_bot.web import app
        app.run(host='localhost', port=8082, debug=False, use_reloader=False, threaded=True)
        
    except KeyboardInterrupt:
        logger.info("🌐 Веб-сервер остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Ошибка запуска веб-сервера: {e}")
        raise

def main():
    """Главная функция запуска"""
    print("=" * 60)
    print("🚀 BIGBOT: Объединенный Discord бот + веб-интерфейс")
    print("=" * 60)
    print()
    print("📋 Компоненты:")
    print("  🎉 Party Bot: события, шаблоны, статистика")
    print("  👥 Recruit Bot: рекрутинг, очки, магазин")
    print("  🌐 Веб-интерфейс: управление через браузер")
    print()
    print("🌍 Веб-доступ: http://localhost:8082")
    print("🛑 Остановка: Ctrl+C")
    print("=" * 60)
    print()
    
    try:
        # Запускаем бота в отдельном потоке
        bot_thread = threading.Thread(target=run_bot, daemon=True)
        bot_thread.start()
        # Запускаем веб-сервер в главном потоке
        run_web()
        # После завершения веб-сервера корректно завершаем поток бота
        if bot_thread.is_alive():
            print("🕐 Ожидание завершения бота...")
            # Дадим немного времени для корректного завершения
            bot_thread.join(timeout=5)

        print("\n" + "=" * 60)
        print("🛑 ОСТАНОВКА ВСЕХ СЕРВИСОВ")
        print("=" * 60)
        print("✅ Все сервисы успешно остановлены")
    except KeyboardInterrupt:
        print("\n⚠️ Принудительная остановка (Ctrl+C)")
    except Exception as e:
        logger.error(f"❌ Необработанное исключение в main(): {e}")
    finally:
        pass

if __name__ == "__main__":
    main()
