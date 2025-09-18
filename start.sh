#!/bin/bash

echo "============================================================"
echo "            BigBot - Discord Events & Recruitment Bot"
echo "============================================================"
echo ""

# Проверка наличия Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python 3 не найден! Установите Python 3.11+"
    exit 1
fi

# Проверка конфигурации
if [ ! -f "config.json" ] && [ ! -f ".env" ]; then
    echo "[WARNING] Ни config.json ни .env файл не найдены!"
    if [ -f "config.example.json" ]; then
        echo "Копирую config.example.json в config.json..."
        cp config.example.json config.json
    fi
    if [ -f ".env.example" ]; then
        echo "Копирую .env.example в .env..."
        cp .env.example .env
    fi
    echo ""
    echo "[ВАЖНО] Отредактируйте config.json или .env и добавьте ваш Discord bot token!"
    echo "Затем запустите этот скрипт снова."
    exit 1
fi

# Создание виртуального окружения если не существует
if [ ! -d ".venv" ]; then
    echo "Создание виртуального окружения..."
    python3 -m venv .venv
fi

# Активация виртуального окружения
echo "Активация виртуального окружения..."
source .venv/bin/activate

# Установка зависимостей
echo "Проверка зависимостей..."
pip install -r requirements.txt

# Запуск бота
echo ""
echo "Запуск BigBot..."
echo "Веб-интерфейс: http://localhost:8082"
echo "Для остановки нажмите Ctrl+C"
echo "============================================================"
python bot_main.py
