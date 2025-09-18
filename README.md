# 🤖 Bigbot - Объединенный Discord Бот + Веб-интерфейс

Полнофункциональный Discord бот для управления событиями, рекрутингом и системой очков с современным веб-интерфейсом.

## 🌟 Основные возможности

### 🎉 Party Bot (События)
- **Создание событий** через Discord команды или веб-интерфейс
- **Интерактивные панели** для записи участников с ролями
- **Шаблоны событий** для быстрого создания
- **Автоматическое управление** (напоминания, остановка, клонирование)
- **Статистика участия** в событиях

### 👥 Recruit Bot (Рекрутинг)
- **Система подачи заявок** с кнопками Apply
- **Проверка через Albion Online API** (автоматическое получение данных игрока)
- **Система очков** за участие в событиях
- **Магазин** для трат очков
- **Управление заявками** через веб-интерфейс

### 🌐 Веб-интерфейс
- **Discord OAuth2** авторизация
- **Панель управления** серверами
- **Создание и управление событиями**
- **Настройка ролей и каналов**
- **Статистика и аналитика**
- **Темная тема** с современным дизайном

## 🚀 Быстрый старт

### 1. Установка зависимостей

```bash
# Клонируйте репозиторий
git clone <your-repo-url>
cd Bigbot

# Установите зависимости
pip install -r requirements.txt
pip install -r requirements_env.txt  # Для поддержки .env файлов
```

### 2. Настройка конфигурации

Скопируйте `.env.example` в `.env` и заполните:

```bash
cp .env.example .env
```

**Основные настройки в `.env`:**
```env
# Discord Bot Token (обязательно)
BOT_TOKEN=your_discord_bot_token_here

# Discord App для веб OAuth (обязательно для веб-интерфейса)
DISCORD_CLIENT_ID=your_discord_app_client_id
DISCORD_CLIENT_SECRET=your_discord_app_client_secret

# Веб-интерфейс
FLASK_SECRET_KEY=your_super_secret_key_for_flask_sessions
DISCORD_REDIRECT_URI=http://localhost:8082/callback

# Администраторы бота (Discord User IDs через запятую)
ADMIN_USERS=123456789012345678,987654321098765432
```

### 3. Настройка Discord Developer Portal

1. Перейдите в [Discord Developer Portal](https://discord.com/developers/applications)
2. Создайте новое приложение или выберите существующее
3. В разделе **Bot**:
   - Скопируйте токен в `BOT_TOKEN`
   - Включите все **Privileged Gateway Intents**
4. В разделе **OAuth2 → General**:
   - Скопируйте Client ID в `DISCORD_CLIENT_ID`
   - Скопируйте Client Secret в `DISCORD_CLIENT_SECRET`
   - Добавьте Redirect URI: `http://localhost:8082/callback`

### 4. Запуск бота

```bash
# Запуск бота + веб-интерфейса (рекомендуется)
python bot_main.py

# Или только Discord бот
python bot_main.py --bot-only
```

Веб-интерфейс будет доступен по адресу: **http://localhost:8082**

## 📋 Подробная настройка

### Настройка через веб-интерфейс

1. Откройте http://localhost:8082
2. Авторизуйтесь через Discord
3. Добавьте бота на сервер через панель управления
4. Настройте роли и каналы через веб-интерфейс

### Основные команды бота

- `/setup` - Настройка ролей для ивентов и модерации
- `/party` - Создание событий
- `/templates` - Управление шаблонами событий
- `/settings` - Просмотр всех настроек сервера

### Система очков (Recruit Bot)

Автоматически начисляются очки за:
- Участие в событиях
- Одобренные заявки на рекрутинг
- Активность в гильдии

## 🔧 Продвинутые настройки

### Настройка базы данных

По умолчанию используются SQLite файлы:
- `events.db` - События party bot
- `potatos_recruit.db` - Данные recruit bot
- `sessions.json` - Активные сессии событий
- `settings.json` - Настройки серверов

### Настройка логирования

```env
LOG_LEVEL=INFO
LOG_FILE=bigbot.log
DEBUG=false
```

### Изменение портов

```env
PORT=8082
HOST=localhost
```

## 🐳 Docker развёртывание

```dockerfile
# Создайте Dockerfile на основе примера
FROM python:3.11-slim

WORKDIR /app
COPY requirements*.txt ./
RUN pip install -r requirements.txt -r requirements_env.txt

COPY . .
EXPOSE 8082

CMD ["python", "bot_main.py"]
```

```bash
# Сборка и запуск
docker build -t bigbot .
docker run -p 8082:8082 --env-file .env bigbot
```

## 🌍 Развертывание на VPS (Production)

Подробное пошаговое руководство: см. файл `DEPLOY_VPS.md`.

Кратко:
1. Настроить DNS (A запись: bot.yourdomain.com -> IP)
2. Создать пользователя bigbot и клонировать репозиторий в /opt/bigbot
3. Скопировать `.env.example` → `.env`, заполнить переменные
4. Установить зависимости (venv) или использовать Docker
5. Настроить Nginx reverse proxy + SSL (certbot)
6. Запустить через systemd или `docker compose up -d`
7. Настроить резервные копии (cron + скрипт)

```bash
# Пример systemd перезапуска после обновления
cd /opt/bigbot
git pull
source .venv/bin/activate
pip install -r requirements.txt --upgrade
sudo systemctl restart bigbot
```

## 🛠️ Структура проекта

```
Bigbot/
├── bot_main.py              # Главный файл запуска
├── config.json              # Основная конфигурация (fallback)
├── .env                     # Переменные окружения (приоритет)
├── requirements.txt         # Python зависимости
│
├── party_bot/               # Модуль событий
│   ├── main.py             # Основная логика party bot
│   └── web.py              # Веб-интерфейс
│
├── recruit_bot/             # Модуль рекрутинга
│   ├── bot.py              # Recruit bot логика
│   ├── database.py         # Работа с БД очков
│   └── ui_components.py    # UI компоненты Discord
│
├── templates/               # HTML шаблоны веб-интерфейса
│   ├── base.html           # Базовый шаблон
│   ├── dashboard.html      # Панель управления
│   ├── guild_events.html   # Управление событиями
│   └── ...
│
├── unified_settings.py      # Единая система настроек
└── templates_data/          # Шаблоны событий серверов
```

## 🔍 Диагностика проблем

### Проверка конфигурации

Откройте в браузере: http://localhost:8082/debug

### Логи бота

```bash
# Просмотр логов в реальном времени
tail -f bigbot.log

# Проверка последних ошибок
grep "ERROR\|❌" bigbot.log
```

### Общие проблемы

1. **Бот не отвечает на команды**
   - Проверьте права бота на сервере
   - Убедитесь что включены все Intents в Developer Portal

2. **Веб-интерфейс не работает**
   - Проверьте DISCORD_CLIENT_ID и DISCORD_CLIENT_SECRET
   - Убедитесь что Redirect URI настроен правильно

3. **Ошибки импорта**
   - Переустановите зависимости: `pip install -r requirements.txt`

## 💡 Расширение функционала

### Добавление новых команд

Смотрите примеры в `party_bot/main.py`:

```python
@bot.tree.command(name="newcommand", description="Описание команды")
async def new_command(interaction: discord.Interaction):
    await interaction.response.send_message("Ответ команды")
```

### Добавление веб-маршрутов

В `party_bot/web.py`:

```python
@app.route('/new-route')
def new_route():
    return render_template('new_template.html')
```

## 📞 Поддержка

- Создайте Issue в GitHub для багов
- Проверьте [документацию Discord.py](https://discordpy.readthedocs.io/)
- Для веб-части: [Flask документация](https://flask.palletsprojects.com/)

## 📄 Лицензия

MIT License - см. файл LICENSE

---

**Сделано с ❤️ для Discord сообществ**
