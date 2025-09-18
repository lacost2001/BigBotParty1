# 🔧 Настройка конфигурации BigBot

BigBot поддерживает два способа конфигурации: **файл .env** и **config.json**. Переменные окружения имеют приоритет над config.json.

## 🚀 Быстрый старт

### Способ 1: Использование .env файла (рекомендуется)

1. **Скопируйте пример конфигурации:**
```bash
cp .env.example .env
```

2. **Отредактируйте .env файл:**
```env
# Обязательно! Ваш Discord bot token
BOT_TOKEN=your_discord_bot_token_here

# URL вашего сервера (для production)
WEB_BASE_URL=https://yourdomain.com

# Остальные настройки по необходимости...
```

3. **Запустите бота:**
```bash
python bot_main.py
# или
./start.sh    # Linux/Mac
start.bat     # Windows
```

### Способ 2: Использование config.json

1. **Скопируйте пример:**
```bash
cp config.example.json config.json
```

2. **Отредактируйте config.json:**
```json
{
    "BOT_TOKEN": "your_discord_bot_token_here",
    "WEB_BASE_URL": "https://yourdomain.com"
}
```

## 📋 Основные настройки

### 🔴 Обязательные:

| Параметр | Описание | Пример |
|----------|----------|--------|
| `BOT_TOKEN` | Discord bot token | `MTQxNjE4...` |

### 🟡 Важные:

| Параметр | Описание | По умолчанию |
|----------|----------|--------------|
| `WEB_BASE_URL` | URL веб-интерфейса | `http://localhost:8082` |
| `DISCORD_CLIENT_ID` | ID Discord приложения | - |
| `DISCORD_CLIENT_SECRET` | Secret Discord приложения | - |

### 🟢 Дополнительные:

| Параметр | Описание | По умолчанию |
|----------|----------|--------------|
| `DISCORD_REDIRECT_URI` | OAuth redirect URI | `http://localhost:8082/callback` |
| `FLASK_SECRET_KEY` | Секретный ключ Flask | `your-secret-key-here` |
| `ADMIN_USERS` | ID админов через запятую | - |
| `DEBUG` | Режим отладки | `false` |
| `PORT` | Порт веб-сервера | `8082` |

## 🌍 Настройка для разных сред

### 🏠 Локальная разработка:
```env
BOT_TOKEN=your_token
WEB_BASE_URL=http://localhost:8082
DEBUG=true
```

### 🖥️ VPS/Выделенный сервер:
```env
BOT_TOKEN=your_token
WEB_BASE_URL=http://your-server-ip:8082
DEBUG=false
```

### 🌐 Продакшн с доменом:
```env
BOT_TOKEN=your_token
WEB_BASE_URL=https://yourdomain.com
DISCORD_REDIRECT_URI=https://yourdomain.com/callback
DEBUG=false
```

### 🐳 Docker:
```dockerfile
ENV BOT_TOKEN=your_token
ENV WEB_BASE_URL=https://yourdomain.com
```

## 🔄 Приоритет настроек

1. **Переменные окружения** (системные или .env)
2. **config.json файл**
3. **Значения по умолчанию**

Пример:
```bash
# В .env файле:
WEB_BASE_URL=https://mysite.com

# В config.json:
"WEB_BASE_URL": "http://localhost:8082"

# Результат: будет использоваться https://mysite.com
```

## 🛡️ Безопасность

### ✅ Рекомендации:
- Используйте .env для секретных данных
- НЕ добавляйте .env в Git (уже в .gitignore)
- Используйте разные токены для dev/prod
- Регулярно меняйте FLASK_SECRET_KEY

### ❌ Не делайте:
- Не коммитьте config.json с реальными токенами
- Не используйте простые пароли для FLASK_SECRET_KEY
- Не включайте DEBUG=true на продакшене

## 🔧 Примеры конфигураций

### Минимальная конфигурация (.env):
```env
BOT_TOKEN=your_discord_bot_token_here
```

### Полная конфигурация (.env):
```env
BOT_TOKEN=your_discord_bot_token_here
WEB_BASE_URL=https://yourdomain.com
DISCORD_CLIENT_ID=1234567890
DISCORD_CLIENT_SECRET=your_client_secret
DISCORD_REDIRECT_URI=https://yourdomain.com/callback
FLASK_SECRET_KEY=super-secret-random-string-change-this
ADMIN_USERS=123456789012345678,987654321098765432
DEBUG=false
PORT=8082
```

### Docker Compose:
```yaml
version: '3.8'
services:
  bigbot:
    build: .
    environment:
      - BOT_TOKEN=your_token
      - WEB_BASE_URL=https://yourdomain.com
    ports:
      - "8082:8082"
```

## 🐛 Отладка

### Проверка загруженной конфигурации:
```bash
python -c "from party_bot.main import CONFIG, WEB_BASE_URL; print(f'WEB_BASE_URL: {WEB_BASE_URL}')"
```

### Проверка переменных окружения:
```bash
echo $BOT_TOKEN        # Linux/Mac
echo %BOT_TOKEN%       # Windows CMD
$env:BOT_TOKEN         # Windows PowerShell
```

### Частые ошибки:
- **"BOT_TOKEN не найден"** → Проверьте .env или config.json
- **"Module not found"** → Установите зависимости: `pip install -r requirements.txt`
- **"Permission denied"** → Проверьте права бота в Discord

## 📞 Поддержка

Если возникли проблемы с конфигурацией:
1. Проверьте примеры выше
2. Убедитесь что .env или config.json существует
3. Проверьте права доступа к файлам
4. Посмотрите логи запуска бота
