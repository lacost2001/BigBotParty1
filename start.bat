@echo off
echo ============================================================
echo             BigBot - Discord Events ^& Recruitment Bot
echo ============================================================
echo.

REM Проверка наличия Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python не найден! Установите Python 3.11+
    pause
    exit /b 1
)

REM Проверка конфигурации
if not exist "config.json" (
    if not exist ".env" (
        echo [WARNING] Ни config.json ни .env файл не найдены!
        if exist "config.example.json" (
            echo Копирую config.example.json в config.json...
            copy "config.example.json" "config.json"
        )
        if exist ".env.example" (
            echo Копирую .env.example в .env...
            copy ".env.example" ".env"
        )
        echo.
        echo [ВАЖНО] Отредактируйте config.json или .env и добавьте ваш Discord bot token!
        echo Затем запустите этот скрипт снова.
        pause
        exit /b 1
    )
)

REM Создание виртуального окружения если не существует
if not exist ".venv" (
    echo Создание виртуального окружения...
    python -m venv .venv
)

REM Активация виртуального окружения
echo Активация виртуального окружения...
call .venv\Scripts\activate.bat

REM Установка зависимостей
echo Проверка зависимостей...
pip install -r requirements.txt

REM Запуск бота
echo.
echo Запуск BigBot...
echo Веб-интерфейс: http://localhost:8082
echo Для остановки нажмите Ctrl+C
echo ============================================================
python bot_main.py

pause
