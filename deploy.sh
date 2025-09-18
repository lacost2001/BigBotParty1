#!/bin/bash

# BigBot Deployment Script
# Этот скрипт автоматически развертывает BigBot на Linux сервере

set -e

echo "🚀 BigBot Deployment Script"
echo "=========================="

# Проверка Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 не найден. Установите Python 3.11+"
    exit 1
fi

# Проверка Git
if ! command -v git &> /dev/null; then
    echo "❌ Git не найден. Установите Git"
    exit 1
fi

# Создание пользователя для бота (если не существует)
if ! id "bigbot" &>/dev/null; then
    echo "👤 Создание пользователя bigbot..."
    sudo useradd -r -s /bin/false bigbot
fi

# Создание директории
INSTALL_DIR="/opt/bigbot"
echo "📁 Создание директории $INSTALL_DIR..."
sudo mkdir -p $INSTALL_DIR
sudo chown bigbot:bigbot $INSTALL_DIR

# Клонирование репозитория (если указан)
if [ ! -z "$1" ]; then
    echo "📥 Клонирование из $1..."
    sudo -u bigbot git clone $1 $INSTALL_DIR
    cd $INSTALL_DIR
else
    echo "📁 Используем текущую директорию..."
    sudo cp -r . $INSTALL_DIR/
    sudo chown -R bigbot:bigbot $INSTALL_DIR
    cd $INSTALL_DIR
fi

# Создание виртуального окружения
echo "🐍 Создание виртуального окружения..."
sudo -u bigbot python3 -m venv .venv

# Установка зависимостей
echo "📦 Установка зависимостей..."
sudo -u bigbot .venv/bin/pip install -r requirements.txt

# Создание конфигурации
if [ ! -f "config.json" ]; then
    echo "⚙️ Создание файла конфигурации..."
    sudo -u bigbot cp config.example.json config.json
    echo "❗ ВАЖНО: Отредактируйте config.json с вашим bot token!"
fi

# Создание systemd service
echo "🔧 Создание systemd сервиса..."
sudo tee /etc/systemd/system/bigbot.service > /dev/null <<EOF
[Unit]
Description=BigBot Discord Bot
After=network.target

[Service]
Type=simple
User=bigbot
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python bot_main.py
Restart=always
RestartSec=10
Environment=PYTHONPATH=$INSTALL_DIR

[Install]
WantedBy=multi-user.target
EOF

# Перезагрузка systemd
sudo systemctl daemon-reload

# Создание директории для логов
sudo mkdir -p /var/log/bigbot
sudo chown bigbot:bigbot /var/log/bigbot

echo "✅ Установка завершена!"
echo ""
echo "📝 Следующие шаги:"
echo "1. Отредактируйте $INSTALL_DIR/config.json"
echo "2. Добавьте ваш Discord bot token"
echo "3. Запустите сервис: sudo systemctl start bigbot"
echo "4. Включите автозапуск: sudo systemctl enable bigbot"
echo ""
echo "🔍 Команды управления:"
echo "  sudo systemctl start bigbot     # Запуск"
echo "  sudo systemctl stop bigbot      # Остановка"
echo "  sudo systemctl status bigbot    # Статус"
echo "  sudo systemctl restart bigbot   # Перезапуск"
echo "  sudo journalctl -u bigbot -f    # Просмотр логов"
echo ""
echo "🌐 Веб-интерфейс будет доступен на http://your-server-ip:8082"
