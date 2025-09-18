FROM python:3.11-slim

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Создание рабочей директории
WORKDIR /app

# Копирование файлов зависимостей
COPY requirements.txt .

# Установка Python зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование исходного кода
COPY . .

# Создание директории для базы данных
RUN mkdir -p /app/data

# Открытие порта для веб-интерфейса
EXPOSE 8082

# Переменные окружения
ENV PYTHONPATH=/app
ENV WEB_BASE_URL=http://localhost:8082

# Команда запуска
CMD ["python", "bot_main.py"]
