"""
ReqrutPot package: recruitment and events system for Discord.

This package exposes modules:
- events: event types and points logic
- database: aiosqlite persistence for events/points/shop
- ui_components: Discord UI views and handlers
- bot: legacy standalone runner and RecruitCog

Note: The standalone runner in bot.py should only execute when run as __main__.
"""

__all__ = [
    "events",
    "database",
    "ui_components",
    "bot",
]
