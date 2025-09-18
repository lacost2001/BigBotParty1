"""
Модуль для обработки игровых событий в Albion Online
Поддерживает: кристальные жуки, сферы, вихри разных цветов
"""

from dataclasses import dataclass
from enum import Enum
import logging
from typing import Dict, List, Optional

logger = logging.getLogger("potatos_recruit.events")

class EventType(Enum):
    """Типы игровых событий"""
    CRYSTAL_SPIDER = "crystal_spider"
    SPHERE_BLUE = "sphere_blue"
    SPHERE_PURPLE = "sphere_purple"
    SPHERE_GOLD = "sphere_gold"
    VORTEX_GREEN = "vortex_green"
    VORTEX_BLUE = "vortex_blue"
    VORTEX_PURPLE = "vortex_purple"
    VORTEX_GOLD = "vortex_gold"

class EventAction(Enum):
    """Типы действий с событиями"""
    KILL = "kill"
    CAPTURE = "capture"
    TRANSPORT = "transport"

@dataclass
class EventInfo:
    """Информация о типе события"""
    type: EventType
    name_ru: str
    emoji: str
    base_points: Dict[EventAction, float]  # Базовые очки за разные действия
    
# Конфигурация событий с эмодзи и базовыми очками
EVENT_CONFIG = {
    EventType.CRYSTAL_SPIDER: EventInfo(
        type=EventType.CRYSTAL_SPIDER,
        name_ru="Кристальный жук",
        emoji="🕷️",
        base_points={
            EventAction.KILL: 1.0
        }
    ),
    EventType.SPHERE_BLUE: EventInfo(
        type=EventType.SPHERE_BLUE,
        name_ru="Синяя сфера",
        emoji="🔵",
        base_points={
            EventAction.TRANSPORT: 1.5
        }
    ),
    EventType.SPHERE_PURPLE: EventInfo(
        type=EventType.SPHERE_PURPLE,
        name_ru="Фиолетовая сфера",
        emoji="🟣",
        base_points={
            EventAction.TRANSPORT: 3.0
        }
    ),
    EventType.SPHERE_GOLD: EventInfo(
        type=EventType.SPHERE_GOLD,
        name_ru="Золотая сфера",
        emoji="🟡",
        base_points={
            EventAction.TRANSPORT: 5.0
        }
    ),
    EventType.VORTEX_GREEN: EventInfo(
        type=EventType.VORTEX_GREEN,
        name_ru="Зеленый вихрь",
        emoji="🌪️",
        base_points={
            EventAction.TRANSPORT: 2.0
        }
    ),
    EventType.VORTEX_BLUE: EventInfo(
        type=EventType.VORTEX_BLUE,
        name_ru="Синий вихрь",
        emoji="🌀",
        base_points={
            EventAction.TRANSPORT: 3.0
        }
    ),
    EventType.VORTEX_PURPLE: EventInfo(
        type=EventType.VORTEX_PURPLE,
        name_ru="Фиолетовый вихрь",
        emoji="🌊",
        base_points={
            EventAction.TRANSPORT: 6.0
        }
    ),
    EventType.VORTEX_GOLD: EventInfo(
        type=EventType.VORTEX_GOLD,
        name_ru="Золотой вихрь",
        emoji="💫",
        base_points={
            EventAction.TRANSPORT: 10.0
        }
    )
}

# Множители очков для модераторов
POINT_MULTIPLIERS = [1.0, 1.5, 2.0, 3.0, 5.0, 6.0, 10.0]

@dataclass
class EventSubmission:
    """Данные заявки на ивент"""
    event_type: EventType
    action: EventAction
    participants: List[int]  # Discord ID участников
    submitter_id: int
    screenshot_url: Optional[str] = None
    group_size: int = 1
    description: Optional[str] = None
    
    def calculate_base_points(self) -> float:
        """Рассчитать базовые очки за ивент"""
        event_info = EVENT_CONFIG[self.event_type]
        return event_info.base_points.get(self.action, 0.0)
    
    def get_event_display_name(self) -> str:
        """Получить отображаемое название события"""
        event_info = EVENT_CONFIG[self.event_type]
        action_names = {
            EventAction.KILL: "убийство",
            EventAction.CAPTURE: "захват",
            EventAction.TRANSPORT: "доставка"
        }
        action_name = action_names.get(self.action, str(self.action.value))
        return f"{event_info.emoji} {event_info.name_ru} ({action_name})"

class EventManager:
    """Менеджер для обработки игровых событий"""
    
    @staticmethod
    def get_event_options() -> List[tuple]:
        """Получить список опций для событий (для Select Menu)"""
        options = []
        
        # Кристальные жуки
        crystal_info = EVENT_CONFIG[EventType.CRYSTAL_SPIDER]
        options.append((
            EventType.CRYSTAL_SPIDER.value + "_kill",
            f"{crystal_info.emoji} {crystal_info.name_ru} (убийство)",
            f"Базовые очки: {crystal_info.base_points[EventAction.KILL]}"
        ))
        
        # Сферы (только доставка)
        for sphere_type in [EventType.SPHERE_BLUE, EventType.SPHERE_PURPLE, EventType.SPHERE_GOLD]:
            sphere_info = EVENT_CONFIG[sphere_type]
            
            # Доставка сферы
            options.append((
                f"{sphere_type.value}_transport",
                f"{sphere_info.emoji} {sphere_info.name_ru} (доставка)",
                f"Базовые очки: {sphere_info.base_points[EventAction.TRANSPORT]}"
            ))
        
        # Вихри (только доставка)
        for vortex_type in [EventType.VORTEX_GREEN, EventType.VORTEX_BLUE,
                           EventType.VORTEX_PURPLE, EventType.VORTEX_GOLD]:
            vortex_info = EVENT_CONFIG[vortex_type]
            
            # Доставка вихря
            options.append((
                f"{vortex_type.value}_transport",
                f"{vortex_info.emoji} {vortex_info.name_ru} (доставка)",
                f"Базовые очки: {vortex_info.base_points[EventAction.TRANSPORT]}"
            ))
        
        return options
    
    @staticmethod
    def parse_event_selection(selection: str) -> tuple[EventType, EventAction]:
        """Парсить выбор события из строки"""
        parts = selection.split("_")
        if len(parts) < 2:
            raise ValueError(f"Неверный формат выбора события: {selection}")
        
        action_str = parts[-1]
        event_str = "_".join(parts[:-1])
        
        try:
            event_type = EventType(event_str)
            action = EventAction(action_str)
            return event_type, action
        except ValueError as e:
            raise ValueError(f"Неизвестный тип события или действия: {selection}") from e
    
    @staticmethod
    def validate_event_action(event_type: EventType, action: EventAction) -> bool:
        """Проверить, доступно ли действие для данного типа события"""
        event_info = EVENT_CONFIG.get(event_type)
        if not event_info:
            return False
        return action in event_info.base_points
    
    @staticmethod
    def get_available_actions(event_type: EventType) -> List[EventAction]:
        """Получить доступные действия для типа события"""
        event_info = EVENT_CONFIG.get(event_type)
        if not event_info:
            return []
        return list(event_info.base_points.keys())
    
    @staticmethod
    def calculate_final_points(base_points: float, multiplier: float, group_size: int) -> float:
        """Рассчитать финальные очки с учетом множителя и размера группы"""
        if group_size <= 0:
            return 0.0
        
        total_points = base_points * multiplier
        points_per_person = total_points / group_size
        return round(points_per_person, 2)
    
    @staticmethod
    def format_points_display(points: float) -> str:
        """Отформатировать отображение очков"""
        if points == int(points):
            return str(int(points))
        return f"{points:.1f}"

# Константы для UI
MAX_PARTICIPANTS = 20  # Максимальное количество участников в группе
MIN_PARTICIPANTS = 1   # Минимальное количество участников

def get_multiplier_options() -> List[tuple]:
    """Получить опции множителей для модераторов"""
    return [(str(m), f"x{EventManager.format_points_display(m)}") for m in POINT_MULTIPLIERS]

# ─── Конфигурация магазина ────────────────────────────────────────────────────
from dataclasses import dataclass
from typing import Dict


@dataclass
class ShopItem:
    """Информация о товаре в магазине"""
    id: str
    name: str
    description: str
    cost: int
    emoji: str
    category: str

# Конфигурация товаров в магазине
SHOP_ITEMS = {
    "silver_200k": ShopItem(
        id="silver_200k",
        name="200,000 серебра",
        description="Получите 200k серебра в игре",
        cost=10,
        emoji="💰",
        category="currency"
    ),
    "random_item": ShopItem(
        id="random_item",
        name="Рандомная вещь",
        description="Случайный предмет из звездного лута",
        cost=30,
        emoji="🎲",
        category="items"
    ),
    "gear_set": ShopItem(
        id="gear_set",
        name="Комплект экипировки",
        description="Полный сет экипировки на выбор",
        cost=50,
        emoji="⚔️",
        category="gear"
    )
}

class ShopManager:
    """Менеджер для обработки магазина"""
    
    @staticmethod
    def get_shop_items() -> List[ShopItem]:
        """Получить список всех товаров"""
        return list(SHOP_ITEMS.values())
    
    @staticmethod
    def get_item_by_id(item_id: str) -> Optional[ShopItem]:
        """Получить товар по ID"""
        return SHOP_ITEMS.get(item_id)
    
    @staticmethod
    def get_shop_embed() -> "discord.Embed":
        """Создать embed с товарами магазина"""
        import discord
        
        embed = discord.Embed(
            title="🛒 Магазин гильдии",
            description="Обменяйте очки за события на награды!",
            color=discord.Color.gold()
        )
        
        for item in SHOP_ITEMS.values():
            embed.add_field(
                name=f"{item.emoji} {item.name}",
                value=f"💎 **{item.cost} очков**\n{item.description}",
                inline=True
            )
        
        embed.set_footer(text="💡 Очки можно получить участвуя в событиях!")
        return embed
