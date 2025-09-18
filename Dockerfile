FROM python:3.11-slim

# Установка зависимостей системы (добавьте свои при необходимости)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Установка рабочей директории
WORKDIR /app

# Копирование зависимостей
COPY requirements.txt ./

# Установка Python-зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY . .

# Открываем порт (Heroku сам пробрасывает $PORT)
EXPOSE 8082

# Переменные окружения (Heroku подставляет свои через dashboard/config:set)
# ENV PORT=8082
# ENV FLASK_SECRET_KEY=... и т.д.

# Команда запуска (замените на вашу основную точку входа)
# Если у вас Flask:
# CMD ["python", "party_bot/web.py"]
# Если у вас Discord-бот:
# CMD ["python", "bot_main.py"]
# Если оба — используйте Procfile для Heroku или docker-compose для локального запуска

# Пример для Flask:
# CMD ["gunicorn", "party_bot.web:app", "--bind", "0.0.0.0:${PORT:-8082}"]

# Пример для Discord-бота:
CMD ["python", "bot_main.py"]
