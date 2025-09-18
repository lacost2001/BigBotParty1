# 🚀 Развертывание BigBot на VPS с собственным доменом

Данный гид покрывает два варианта: (1) Docker + Nginx reverse proxy + Let's Encrypt и (2) Нативный (systemd) без контейнеров. Выберите подходящий.

---
## ✅ Предварительные требования

| Что | Пример |
|-----|--------|
| VPS | Ubuntu 22.04 LTS (рекомендуется) |
| Домен | example.com (или поддомен bot.example.com) |
| Права | Пользователь с sudo |
| Discord | Bot Token, Client ID, Client Secret |

DNS: Настройте A-запись `bot.example.com` -> IP вашего сервера.

---
## 🧪 Проверка сервера
```bash
ssh root@SERVER_IP
uname -a
lsb_release -a   # Должно показать Ubuntu/Debian
```
Обновите систему:
```bash
apt update && apt upgrade -y
```

---
## 👤 Создание системного пользователя
```bash
adduser bigbot --disabled-password --gecos ""
usermod -aG sudo bigbot
su - bigbot
```
(Опционально: настроить SSH-ключи)

---
## 🗂️ Структура каталогов
```
/opt/bigbot          # Код
/opt/bigbot/.venv    # Виртуальное окружение
/var/log/bigbot      # Логи
/var/backups/bigbot  # Резервные копии БД
```
Создадим каталоги:
```bash
sudo mkdir -p /opt/bigbot /var/log/bigbot /var/backups/bigbot
sudo chown -R bigbot:bigbot /opt/bigbot /var/log/bigbot /var/backups/bigbot
```

---
## 📥 Клонирование репозитория
```bash
cd /opt/bigbot
git clone https://github.com/you/Bigbot.git .
```
(Замените URL на ваш)

---
## 🔐 Создание .env
```bash
cp .env.example .env
nano .env
```
Минимум заполните:
```env
BOT_TOKEN=YOUR_DISCORD_BOT_TOKEN
WEB_BASE_URL=https://bot.example.com
DISCORD_CLIENT_ID=... 
DISCORD_CLIENT_SECRET=...
DISCORD_REDIRECT_URI=https://bot.example.com/callback
FLASK_SECRET_KEY=$(tr -dc 'A-Za-z0-9' </dev/urandom | head -c 48)
```

---
## 🐍 Виртуальное окружение (нативный вариант)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```
Проверка:
```bash
python bot_main.py --help || echo OK
```

---
## 🐳 Вариант с Docker (альтернатива)
### 1. Установка Docker + Compose
```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
apt install -y docker-compose-plugin
```
### 2. Запуск контейнера
```bash
docker compose up -d --build
```
Логи:
```bash
docker compose logs -f --tail 100
```
Обновление (pull + rebuild):
```bash
git pull
docker compose up -d --build
```
Бэкапы БД (если том): см. раздел ниже.

---
## 🌐 Установка Nginx (reverse proxy)
```bash
sudo apt install -y nginx
sudo rm /etc/nginx/sites-enabled/default
```
Создайте файл `/etc/nginx/sites-available/bigbot.conf`:
```nginx
server {
    listen 80;
    server_name bot.example.com;

    location / {
        proxy_pass http://127.0.0.1:8082/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300;
    }

    client_max_body_size 20M;
}
```
Активировать:
```bash
sudo ln -s /etc/nginx/sites-available/bigbot.conf /etc/nginx/sites-enabled/bigbot.conf
sudo nginx -t && sudo systemctl reload nginx
```

---
## 🔒 SSL (Let's Encrypt / Certbot)
```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d bot.example.com --agree-tos -m admin@example.com --no-eff-email
```
Автопродление уже создаётся. Проверка:
```bash
sudo systemctl status certbot.timer
```

---
## 🧭 Systemd сервис (нативный запуск)
Создайте `/etc/systemd/system/bigbot.service`:
```ini
[Unit]
Description=BigBot Discord + Web
After=network.target

[Service]
Type=simple
User=bigbot
WorkingDirectory=/opt/bigbot
Environment=PYTHONPATH=/opt/bigbot
Environment=WEB_BASE_URL=https://bot.example.com
ExecStart=/opt/bigbot/.venv/bin/python bot_main.py
Restart=always
RestartSec=10
StandardOutput=append:/var/log/bigbot/app.log
StandardError=append:/var/log/bigbot/error.log

[Install]
WantedBy=multi-user.target
```
Запуск:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bigbot
sudo systemctl status bigbot
```
Логи:
```bash
tail -f /var/log/bigbot/app.log
```

---
## ♻️ Обновление версии
```bash
cd /opt/bigbot
git pull
source .venv/bin/activate
pip install -r requirements.txt --upgrade
sudo systemctl restart bigbot
```
(Для Docker: rebuild через compose)

---
## 💾 Резервные копии (SQLite + конфиг)
Скрипт `/usr/local/bin/backup_bigbot.sh`:
```bash
#!/bin/bash
set -e
BACKUP_DIR=/var/backups/bigbot/$(date +%Y-%m-%d)
mkdir -p "$BACKUP_DIR"
cp /opt/bigbot/*.db "$BACKUP_DIR" 2>/dev/null || true
cp /opt/bigbot/config.json "$BACKUP_DIR" 2>/dev/null || true
cp /opt/bigbot/.env "$BACKUP_DIR" 2>/dev/null || true
find /var/backups/bigbot -type d -mtime +14 -exec rm -rf {} +
```
```bash
sudo chmod +x /usr/local/bin/backup_bigbot.sh
```
Cron (ежедневно 03:10):
```bash
sudo crontab -e
# Добавьте строку:
10 3 * * * /usr/local/bin/backup_bigbot.sh >/dev/null 2>&1
```

---
## 🔍 Диагностика
| Симптом | Проверка |
|---------|----------|
| Бот не онлайн | `systemctl status bigbot` / `docker ps` |
| 502 Bad Gateway | `nginx -t`, логи Nginx `/var/log/nginx/error.log` |
| OAuth не работает | Redirect URI совпадает? HTTPS? |
| Команды не обновляются | Дать 5-10 минут / перепригласить бота |

---
## 🛡️ Безопасность
- Не храните репозиторий под root
- Выдайте минимальные Discord права в пригласительной ссылке
- Регулярно обновляйте зависимости
- Используйте сложный `FLASK_SECRET_KEY`
- Ограничьте доступ к логам (`chmod 640`)

---
## ✨ Быстрый чеклист (нативно)
1. Создать пользователя bigbot ✔
2. Клонировать код в /opt/bigbot ✔
3. Создать .env и заполнить ✔
4. Настроить venv и pip install ✔
5. Настроить systemd сервис ✔
6. Поставить Nginx + SSL ✔
7. Протестировать доступ https://bot.example.com ✔
8. Настроить бэкапы и cron ✔

---
## ❓ FAQ
**Q:** Можно ли сменить домен позже?  
**A:** Да. Обновите DNS, поменяйте `WEB_BASE_URL` в `.env` и перезапустите сервис.

**Q:** Как обновить токен бота?  
**A:** Измените `BOT_TOKEN` в `.env`, затем `systemctl restart bigbot`.

**Q:** Что если упал процесс?  
**A:** systemd автоматически перезапустит, см. `Restart=always`.

---
Готово! Ваш BigBot доступен по HTTPS 🎉
