"""Единый модуль хранения состояния интерактивных заявок.
Избегаем дублирования при разных путях импорта."""
from __future__ import annotations

from typing import Dict, Any

# Единственный словарь активных сессий
active_submissions: Dict[str, Any] = {}

__all__ = ["active_submissions"]
