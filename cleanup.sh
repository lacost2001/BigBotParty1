#!/bin/bash

# BigBot Project Cleanup Script
# Удаляет все временные и служебные файлы для подготовки к production

echo "🧹 Очистка проекта BigBot..."

# Удаление Python cache
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find . -name "*.pyc" -delete 2>/dev/null
find . -name "*.pyo" -delete 2>/dev/null
find . -name "*.pyd" -delete 2>/dev/null

# Удаление временных файлов IDE
rm -rf .vscode/ .idea/ *.sublime-* 2>/dev/null

# Удаление логов и временных файлов
find . -name "*.log" -delete 2>/dev/null
find . -name "*.tmp" -delete 2>/dev/null
find . -name "*.temp" -delete 2>/dev/null
find . -name "*.bak" -delete 2>/dev/null
find . -name "*~" -delete 2>/dev/null

# Удаление Flask сессий
rm -rf flask_session/ */flask_session/ 2>/dev/null

# Удаление тестовых и отладочных файлов
find . -name "test_*.py" -delete 2>/dev/null
find . -name "debug_*.py" -delete 2>/dev/null
find . -name "check_*.py" -delete 2>/dev/null

# Удаление файлов разработки
rm -f .env pyproject.toml requirements_env.txt 2>/dev/null
rm -rf .ruff_cache/ .pytest_cache/ .coverage 2>/dev/null

# Удаление backup файлов настроек
find . -name "settings_backup_*.json" -delete 2>/dev/null

echo "✅ Очистка завершена!"
echo "📁 Проект готов к развертыванию"
