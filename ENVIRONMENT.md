# BigBot Environment Configuration

## Переменные окружения

Для настройки бота можно использовать переменные окружения вместо config.json:

### Основные переменные:
- `BOT_TOKEN` - Discord bot token (обязательно)
- `WEB_BASE_URL` - URL веб-интерфейса (по умолчанию: http://localhost:8082)

### Настройка на разных системах:

#### Windows:
```cmd
set BOT_TOKEN=your_discord_bot_token_here
set WEB_BASE_URL=http://your-server-url:8082
```

#### Linux/Mac:
```bash
export BOT_TOKEN=your_discord_bot_token_here
export WEB_BASE_URL=http://your-server-url:8082
```

#### Docker:
```dockerfile
ENV BOT_TOKEN=your_discord_bot_token_here
ENV WEB_BASE_URL=http://your-server-url:8082
```

#### .env файл (для разработки):
```env
BOT_TOKEN=your_discord_bot_token_here
WEB_BASE_URL=http://localhost:8082
```

## Приоритет настроек:

1. Переменные окружения
2. config.json файл
3. Значения по умолчанию

## Безопасность:

- **НЕ** добавляйте config.json в git если он содержит реальные токены
- Используйте переменные окружения на продакшн серверах
- Храните токены в безопасном месте
