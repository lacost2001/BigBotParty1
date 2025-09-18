# -*- coding: utf-8 -*-
import os
import logging
import asyncio
import re
from datetime import datetime, timezone
import textwrap
from typing import Set
import json

import aiosqlite
import aiohttp
import discord
from discord import app_commands, ui
from discord.ext import commands
from dotenv import load_dotenv

# Импорты новой системы событий
from .database import EventDatabase, DB_PATH
from .ui_components import PersistentEventSubmitView, UnifiedEventView, ResetPointsConfirmationView

# Пытаемся импортировать единую систему настроек для авто-настройки
try:
    from ..unified_settings import unified_settings as _unified_settings
except Exception:
    _unified_settings = None
from .events import EventManager, ShopManager

# Импорт унифицированной системы настроек
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unified_settings import UnifiedSettings

# ─── Логирование ───────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler()
    ]
)
# Устанавливаем кодировку для консоли Windows
if os.name == 'nt':  # Windows
    import sys
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

logger = logging.getLogger("potatos_recruit")

# ─── Константы ─────────────────────────────────────────────────────────────────
# Используем общий путь к БД из database.py (Bigbot/potatos_recruit.db)
##ALBION_API_BASE = "https://gameinfo.albiononline.com/api/gameinfo"
ALBION_API_BASE = "https://gameinfo-ams.albiononline.com/api/gameinfo"

# ─── Albion Online API функции ────────────────────────────────────────────────
async def search_albion_player(player_name: str) -> dict | None:
    """Ищет игрока в Albion Online по имени"""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{ALBION_API_BASE}/search"
            params = {"q": player_name}
            headers = {"Accept": "application/json"}
            
            async with session.get(url, params=params, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    
                    # Ищем точное совпадение в списке игроков
                    for player in data.get("players", []):
                        if player["Name"].lower() == player_name.lower():
                            return player
                    
                    # Если точного совпадения нет, возвращаем первого найденного
                    if data.get("players"):
                        return data["players"][0]
                        
                return None
    except Exception as e:
        logger.error(f"Ошибка при поиске игрока {player_name}: {e}")
        return None

async def search_albion_player_detailed(player_name: str) -> list:
    """Ищет игроков в Albion Online и возвращает список всех найденных"""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{ALBION_API_BASE}/search"
            params = {"q": player_name}
            headers = {"Accept": "application/json"}
            
            async with session.get(url, params=params, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    return data.get("players", [])
                return []
    except Exception as e:
        logger.error(f"Ошибка при поиске игрока {player_name}: {e}")
        return []

async def search_albion_player_with_options(player_name: str) -> list:
    """Ищет игроков с возможностью выбора из нескольких вариантов"""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{ALBION_API_BASE}/search"
            params = {"q": player_name}
            headers = {"Accept": "application/json"}
            
            async with session.get(url, params=params, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    players = data.get("players", [])
                    
                    # Сортируем по точности совпадения
                    def match_score(player):
                        name = player["Name"].lower()
                        search = player_name.lower()
                        if name == search:
                            return 0  # Точное совпадение
                        elif name.startswith(search):
                            return 1  # Начинается с
                        elif search in name:
                            return 2  # Содержит
                        else:
                            return 3  # Другое
                    
                    return sorted(players, key=match_score)
                return []
    except Exception as e:
        logger.error(f"Ошибка при поиске игрока {player_name}: {e}")
        return []

async def get_albion_player_stats(player_id: str) -> dict | None:
    """Получает статистику игрока по ID"""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{ALBION_API_BASE}/players/{player_id}"
            headers = {"Accept": "application/json"}
            
            async with session.get(url, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                return None
    except Exception as e:
        logger.error(f"Ошибка при получении статистики игрока {player_id}: {e}")
        return None

async def get_albion_player_kills(player_id: str, limit: int = 10) -> list:
    """Получает список убийств игрока"""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{ALBION_API_BASE}/players/{player_id}/kills"
            params = {"limit": limit}
            headers = {"Accept": "application/json"}
            
            async with session.get(url, params=params, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                return []
    except Exception as e:
        logger.error(f"Ошибка при получении убийств игрока {player_id}: {e}")
        return []

async def get_albion_player_deaths(player_id: str, limit: int = 10) -> list:
    """Получает список смертей игрока"""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{ALBION_API_BASE}/players/{player_id}/deaths"
            params = {"limit": limit}
            headers = {"Accept": "application/json"}
            
            async with session.get(url, params=params, headers=headers) as response:
                if response.status == 200:
                    return await response.json()
                return []
    except Exception as e:
        logger.error(f"Ошибка при получении смертей игрока {player_id}: {e}")
        return []

def format_timestamp(timestamp: str) -> str:
    """Форматирует timestamp для Discord"""
    try:
        dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        return f"<t:{int(dt.timestamp())}:R>"
    except:
        return timestamp

# ─── Discord Intents ───────────────────────────────────────────────────────────
INTENTS = discord.Intents.default()
INTENTS.members = True
INTENTS.guilds = True
INTENTS.messages = True
INTENTS.message_content = True

# ─── ENV ───────────────────────────────────────────────────────────────────────
# Only load token when running as a script; allow safe import as a package
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN") if __name__ == "__main__" else None

# ─── Вспомогательные функции ───────────────────────────────────────────────────
def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def comma_join(ids: Set[int]) -> str:
    return ",".join(map(str, ids))

def comma_split(s: str | None) -> Set[int]:
    if not s:
        return set()
    return {int(tok) for tok in s.split(",") if tok.strip().isdigit()}

# ─── Инициализация БД ──────────────────────────────────────────────────────────
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS guild_config (
                guild_id           INTEGER PRIMARY KEY,
                default_role       INTEGER,
                recruit_role       INTEGER,
                recruiter_roles    TEXT,
                forum_id           INTEGER,
                apply_channel_id   INTEGER,
                guild_name         TEXT,
                cooldown_hours     INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS applications (
                thread_id    INTEGER PRIMARY KEY,
                author_id    INTEGER,
                ign          TEXT,
                age          INTEGER,
                goals        TEXT,
                referral     TEXT,
                status       TEXT,
                reviewer_id  INTEGER,
                created_at   TEXT,
                decided_at   TEXT
            );
            """
        )
        
        # Добавляем столбец guild_name если его нет (для существующих баз)
        try:
            await db.execute("ALTER TABLE guild_config ADD COLUMN guild_name TEXT")
            await db.commit()
        except Exception:
            # Столбец уже существует
            pass
            
        # Добавляем столбец cooldown_hours если его нет (для существующих баз)
        try:
            await db.execute("ALTER TABLE guild_config ADD COLUMN cooldown_hours INTEGER DEFAULT 1")
            await db.commit()
        except Exception:
            # Столбец уже существует
            pass
        
        # Добавляем колонки для системы событий
        event_columns = [
            ('admin_role', 'TEXT'),
            ('moderator_role', 'TEXT'),
            ('points_moderator_roles', 'TEXT'),
            ('events_channel', 'TEXT'),
            ('shop_channel', 'TEXT'),
            ('events_data', 'TEXT')
        ]
        
        for column_name, column_type in event_columns:
            try:
                await db.execute(f"ALTER TABLE guild_config ADD COLUMN {column_name} {column_type}")
                logger.info(f"Добавлена колонка {column_name}")
                await db.commit()
            except Exception:
                # Столбец уже существует
                pass
            
        await db.commit()
    
    # Инициализируем таблицы для системы событий
    await EventDatabase.init_event_tables()
    logger.info("База данных инициализирована")

# ─── Модальное окно ────────────────────────────────────────────────────────────
class ApplyModal(ui.Modal, title="Заявка в гильдию Potatos"):
    ign = ui.TextInput(label="Ник в игре", max_length=30)
    age = ui.TextInput(label="Возраст", max_length=2, placeholder="18")
    goals = ui.TextInput(label="Ваши цели в игре", style=discord.TextStyle.paragraph, max_length=300)
    referral = ui.TextInput(label="Откуда узнали о гильдии", style=discord.TextStyle.short, max_length=100)

    def __init__(self, bot: commands.Bot, cfg: dict):
        super().__init__()
        self.bot = bot
        self.cfg = cfg

    async def on_submit(self, interaction: discord.Interaction):
        logger.info(f"Пользователь {interaction.user} подал заявку: {self.ign.value}")

        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                "SELECT created_at FROM applications WHERE author_id=? ORDER BY created_at DESC LIMIT 1",
                (interaction.user.id,),
            )
            row = await cur.fetchone()
            await cur.close()

        if row:
            last_time = datetime.fromisoformat(row[0])
            delta = datetime.now(timezone.utc) - last_time
            cooldown_hours = self.cfg.get("cooldown_hours", 1)
            
            # Проверяем кулдаун только если он больше 0
            if cooldown_hours > 0 and delta.total_seconds() < cooldown_hours * 3600:
                remaining_hours = cooldown_hours - (delta.total_seconds() / 3600)
                remaining_text = f"{remaining_hours:.1f} ч" if remaining_hours >= 1 else f"{remaining_hours * 60:.0f} мин"
                await interaction.response.send_message(
                    f"❌ Подождите {remaining_text} перед повторной подачей заявки.\n"
                    f"📅 Кулдаун: {cooldown_hours} ч",
                    ephemeral=True,
                )
                return

        await interaction.response.send_message("✅ Заявка успешно отправлена! Ссылка придёт в личные сообщения.", ephemeral=True)

        forum: discord.ForumChannel = interaction.guild.get_channel(self.cfg["forum_id"])
        if not forum:
            logger.error("Канал форума не найден. Обратитесь к администратору.")
            return

        # Обрабатываем никнейм - убираем "Guild" и "potatos" если есть
        clean_ign = self.ign.value.replace("Guild", "").replace("potatos", "").replace("Potatos", "").strip()
        
        thread_name = clean_ign
        
        # Получаем статистику из Albion Online для рекрутеров (приоритет Europe серверу)
        players = await search_albion_player_with_options(clean_ign)
        logger.info(f"Поиск игрока {clean_ign} (исходный: {self.ign.value}): найдено игроков={len(players) if players else 0}")
        
        # Инициализируем переменные
        guild_name = "Нет гильдии"
        alliance_name = "Нет альянса"
        player_data = None
        
        if players:
            # Берем первого игрока (наиболее точное совпадение)
            player_data = players[0]
            guild_name = player_data.get("GuildName", "Нет гильдии")
            alliance_name = player_data.get("AllianceName", "Нет альянса")
            player_id = player_data["Id"]
            
            logger.info(f"Данные игрока {clean_ign}: гильдия={guild_name}, альянс={alliance_name} (Europe сервер)")
        else:
            logger.warning(f"Игрок {clean_ign} не найден в Albion Online")

        content = textwrap.dedent(f"""
            **Ник:** {self.ign.value}
            **Возраст:** {self.age.value}
            **Цели:** {self.goals.value}
            **Откуда узнали:** {self.referral.value}

            Автор: {interaction.user.mention}
            Время подачи: <t:{int(datetime.now(timezone.utc).timestamp())}:F>

            _(Прикрепите сюда скриншот персонажа сообщением с картинкой)_
        """).strip()

        thread_with_msg = await forum.create_thread(name=thread_name, content=content)
        thread = thread_with_msg.thread
        logger.info(f"Создан тред заявки {thread.id} для пользователя {interaction.user.id}")

        # Отправляем кнопку напоминания о скриншоте
        await thread.send(
            f"📸 **Важно:** {interaction.user.mention}, прикрепите скриншот вашей игровой статистики!",
            view=ScreenshotReminderView(interaction.user.id)
        )

        recruiter_ids = self.cfg.get("recruiter_roles")
        if recruiter_ids is None or recruiter_ids == "":
            recruiter_ids = set()
        elif isinstance(recruiter_ids, str):
            recruiter_ids = comma_split(recruiter_ids)
        elif not isinstance(recruiter_ids, set):
            try:
                recruiter_ids = set(recruiter_ids)
            except Exception:
                recruiter_ids = set()
        recruiter_mentions = " ".join(f"<@&{rid}>" for rid in recruiter_ids)

        # Создаём информативное сообщение для рекрутеров
        review_embed = discord.Embed(
            title="🔍 Рассмотрение заявки",
            description=f"**Кандидат:** {clean_ign}\n**Автор заявки:** {interaction.user.mention}",
            color=discord.Color.orange()
        )
        
        logger.info(f"Создание embed для рекрутеров. Player data найден: {player_data is not None}")
        
        if player_data:
            logger.info(f"Добавление статистики Albion для {clean_ign}")
            
            # Получаем детальную статистику для заявки
            player_id = player_data["Id"]
            detailed_stats = await get_albion_player_stats(player_id)
            
            # Основная статистика для рекрутеров
            basic_stats = f"🏰 **Гильдия:** {guild_name}\n" \
                         f"⚔️ **Альянс:** {alliance_name}\n" \
                         f"💰 **Kill Fame:** {player_data.get('KillFame', 0):,}\n" \
                         f"💀 **Death Fame:** {player_data.get('DeathFame', 0):,}\n" \
                         f"📊 **Fame Ratio:** {player_data.get('FameRatio', 0):.2f}"
            
            # Добавляем PvE статистику если доступна
            if detailed_stats and detailed_stats.get("LifetimeStatistics"):
                lifetime_stats = detailed_stats["LifetimeStatistics"]
                pve_stats = lifetime_stats.get("PvE", {})
                gathering_stats = lifetime_stats.get("Gathering", {})
                
                if pve_stats.get("Total", 0) > 0:
                    basic_stats += f"\n\n🏆 **PvE опыт:** {pve_stats.get('Total', 0):,}"
                    basic_stats += f"\n📍 **Outlands PvE:** {pve_stats.get('Outlands', 0):,}"
                
                total_gathered = gathering_stats.get("All", {}).get("Total", 0)
                if total_gathered > 0:
                    basic_stats += f"\n⛏️ **Собрано ресурсов:** {total_gathered:,}"
            
            review_embed.add_field(
                name="📊 Статистика Albion Online",
                value=basic_stats,
                inline=False
            )
            
            # Дополнительная информация
            cfg_guild_name = self.cfg.get("guild_name", "")
            if guild_name == cfg_guild_name:
                review_embed.add_field(
                    name="⚠️ Внимание",
                    value="🔴 **Игрок уже состоит в нашей гильдии!**",
                    inline=False
                )
            elif player_data.get('KillFame', 0) > 50000000:  # 50M+
                review_embed.add_field(
                    name="✨ Оценка",
                    value="🟢 **Опытный игрок с высоким Kill Fame**",
                    inline=False
                )
            elif player_data.get('KillFame', 0) < 1000000:  # <1M
                review_embed.add_field(
                    name="ℹ️ Оценка",
                    value="🟡 **Новичок, может потребоваться обучение**",
                    inline=False
                )
            
            # Ссылки для детального анализа
            official_url = f"https://albiononline.com/en/killboard/player/{player_id}"
            detailed_url = f"https://albiononlinetools.com/player/player-search.php?playerID={player_id}&sv=europe"
            
            review_embed.add_field(
                name="🔗 Дополнительная информация",
                value=f"[📋 Официальный killboard]({official_url})\n[📈 Детальная статистика]({detailed_url})",
                inline=False
            )
        else:
            logger.info(f"Игрок {clean_ign} не найден, добавляем сообщение об ошибке")
            review_embed.add_field(
                name="❌ Статистика Albion Online",
                value="Игрок не найден в Albion Online\nВозможно, неверный ник или игрок не играет в Albion",
                inline=False
            )
        
        # Информация о заявке
        review_embed.add_field(
            name="📝 Данные заявки",
            value=f"**Возраст:** {self.age.value}\n"
                  f"**Цели:** {self.goals.value[:100]}{'...' if len(self.goals.value) > 100 else ''}\n"
                  f"**Откуда узнал:** {self.referral.value}",
            inline=False
        )
        
        review_embed.set_footer(text="Используйте кнопки ниже для принятия решения")
        
        await thread.send(
            content=f"{recruiter_mentions}\n\n**📋 Новая заявка на рассмотрение:**",
            embed=review_embed,
            view=ReviewView(self.bot, self.cfg, thread.id, interaction.user.id),
        )

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO applications (thread_id, author_id, ign, age, goals, referral, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    thread.id,
                    interaction.user.id,
                    self.ign.value,
                    int(self.age.value),
                    self.goals.value,
                    self.referral.value,
                    utcnow_iso(),
                ),
            )
            await db.commit()

        try:
            await interaction.user.edit(nick=clean_ign)
        except discord.Forbidden:
            logger.warning(f"Нет прав для смены ника пользователя {interaction.user.id}")

        try:
            await interaction.user.send(f"✅ Ваша заявка создана: {thread.jump_url}")
        except discord.Forbidden:
            logger.warning(f"Не удалось отправить ЛС пользователю {interaction.user.id}")


# ─── Кнопка напоминания о скриншоте ────────────────────────────────────────────
class ScreenshotReminderButton(ui.Button):
    def __init__(self, author_id: int):
        super().__init__(
            label="📸 Напомнить о скриншоте",
            style=discord.ButtonStyle.secondary,
            custom_id=f"screenshot_reminder_{author_id}"
        )
        self.author_id = author_id

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            f"<@{self.author_id}> 📸 **Напоминание о скриншоте:**\n\n"
            "Не забудьте прикрепить скриншот вашей **игровой статистики** из Albion Online!\n\n"
            "**Как сделать скриншот статистики:**\n"
            "1. Откройте игру Albion Online\n"
            "2. Нажмите клавишу **N** (окно статистики)\n"
            "3. Сделайте скриншот общей статистики\n"
            "4. Прикрепите изображение сообщением в этот тред\n\n"
            "💡 *Скриншот поможет рекрутерам лучше оценить ваш игровой опыт!*",
            ephemeral=False
        )

# ─── View с кнопкой напоминания ───────────────────────────────────────────────
class ScreenshotReminderView(ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=None)
        self.add_item(ScreenshotReminderButton(author_id))

# ─── Кнопки «Принять / Отклонить» ──────────────────────────────────────────────
class ReviewView(ui.View):
    def __init__(self, bot, cfg, thread_id: int, author_id: int):
        super().__init__(timeout=None)  # View бессрочная
        self.bot = bot
        self.cfg = cfg
        self.thread_id = thread_id
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Если это фиктивная регистрация (cfg пустой), пропускаем проверку
        if not self.cfg:
            return False
            
        member = interaction.user
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Ошибка: нет данных сервера.", ephemeral=True)
            return False

        allowed_ids = self.cfg.get("recruiter_roles", set())
        if isinstance(allowed_ids, str):
            allowed_ids = comma_split(allowed_ids)

        if member.guild_permissions.administrator or any(
            role.id in allowed_ids for role in member.roles
        ):
            return True

        await interaction.response.send_message(
            "❌ У вас нет прав использовать эту кнопку.", ephemeral=True
        )
        return False

    @ui.button(label="Принять", style=discord.ButtonStyle.success, emoji="✅", custom_id="review_accept")
    async def accept(self, interaction: discord.Interaction, _button: ui.Button):
        await self._process_review(interaction, accepted=True)

    @ui.button(label="Отклонить", style=discord.ButtonStyle.danger, emoji="⛔", custom_id="review_deny")
    async def deny(self, interaction: discord.Interaction, _button: ui.Button):
        await self._process_review(interaction, accepted=False)

    async def _process_review(self, interaction: discord.Interaction, *, accepted: bool):
        # Если это фиктивная регистрация, игнорируем
        if not self.cfg:
            await interaction.response.send_message("Ошибка конфигурации.", ephemeral=True)
            return
            
        member = interaction.guild.get_member(self.author_id)
        # Аккуратно получаем роли из конфига (могут быть строками)
        def _role_by_key(key: str):
            rid = self.cfg.get(key)
            if rid is None:
                return None
            try:
                rid_int = int(rid)
            except Exception:
                return None
            return interaction.guild.get_role(rid_int)

        default_role = _role_by_key("default_role")
        recruit_role = _role_by_key("recruit_role")

        if not member:
            await interaction.response.send_message("Пользователь не найден.", ephemeral=True)
            return

        role_issue = False
        try:
            if accepted:
                # Удаляем дефолтную роль только если она задана
                if default_role is not None:
                    await member.remove_roles(default_role, reason="Заявка принята")
                # Добавляем роль рекрута только если она задана
                if recruit_role is not None:
                    await member.add_roles(recruit_role, reason="Заявка принята")
            else:
                # На отклонении добавляем дефолтную роль, если есть
                if default_role is not None:
                    await member.add_roles(default_role, reason="Заявка отклонена")
        except discord.Forbidden:
            await interaction.response.send_message("Нет прав изменять роли.", ephemeral=True)
            return
        except AttributeError:
            # Роли не настроены — продолжим без их изменения и сообщим ниже
            role_issue = True

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                UPDATE applications
                   SET status = ?, reviewer_id = ?, decided_at = ?
                 WHERE thread_id = ?
                """,
                (
                    "accepted" if accepted else "denied",
                    interaction.user.id,
                    utcnow_iso(),
                    self.thread_id,
                ),
            )
            await db.commit()

        msg = f"Заявка {'принята' if accepted else 'отклонена'}."
        if role_issue:
            msg += " ⚠️ Роли не настроены или недоступны — изменения ролей не выполнены."
        await interaction.response.send_message(msg, ephemeral=True)

        thread = interaction.guild.get_thread(self.thread_id)
        if thread:
            extra = " (без изменения ролей)" if role_issue else ""
            await thread.send(
                f"Заявка {'принята' if accepted else 'отклонена'} модератором {interaction.user.mention}{extra}"
            )
            await thread.edit(locked=True, archived=True)


# ─── Постоянная кнопка подачи заявки ───────────────────────────────────────────
class ApplyButton(ui.Button):
    def __init__(self, bot: commands.Bot):
        super().__init__(
            label="Подать заявку",
            style=discord.ButtonStyle.primary,
            custom_id="persistent_apply_button"  # <-- обязательно
        )
        self.bot = bot

    async def callback(self, interaction: discord.Interaction):
        try:
            logger.info(f"[ApplyButton] interaction from {interaction.user} in guild {interaction.guild.id}")
            # 1) Пытаемся собрать конфиг максимально быстро из unified_settings
            cfg = None
            forum_id = None
            apply_channel_id = None
            if _unified_settings is not None:
                try:
                    # Базовые recruit-настройки
                    rs = _unified_settings.get_recruit_settings(interaction.guild.id) or {}
                    # Фоллбэк: если нужных полей нет, берем party.recruit_settings и дополняем
                    try:
                        guild_all = _unified_settings.get_guild_settings(interaction.guild.id)
                        party_rs = (guild_all or {}).get("party", {}).get("recruit_settings", {}) or {}
                        # Не затираем существующие, только добавляем отсутствующие
                        for k, v in party_rs.items():
                            if k not in rs or rs.get(k) in (None, "", []):
                                rs[k] = v
                    except Exception:
                        pass
                    def _to_int(val):
                        try:
                            return int(val) if val is not None else None
                        except Exception:
                            return None
                    forum_id = _to_int(rs.get("forum_channel"))
                    apply_channel_id = _to_int(rs.get("recruit_panel_channel") or rs.get("apply_channel_id"))
                    if forum_id and apply_channel_id:
                        cfg = {
                            "default_role": rs.get("default_role"),
                            "recruit_role": rs.get("recruit_role"),
                            "recruiter_roles": rs.get("recruiter_roles"),
                            "forum_id": forum_id,
                            "apply_channel_id": apply_channel_id,
                            "guild_name": rs.get("guild_name", ""),
                            "cooldown_hours": rs.get("cooldown_hours", 1),
                        }
                        # Фоновый апсерт в БД
                        async def _upsert_cfg():
                            try:
                                import aiosqlite
                                from .database import DB_PATH
                                async with aiosqlite.connect(DB_PATH) as db:
                                    await db.execute(
                                        """
                                        INSERT INTO guild_config (
                                            guild_id, default_role, recruit_role, recruiter_roles,
                                            forum_id, apply_channel_id, guild_name, cooldown_hours
                                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                                        ON CONFLICT(guild_id) DO UPDATE SET
                                            default_role     = excluded.default_role,
                                            recruit_role     = excluded.recruit_role,
                                            recruiter_roles  = excluded.recruiter_roles,
                                            forum_id         = excluded.forum_id,
                                            apply_channel_id = excluded.apply_channel_id,
                                            guild_name       = excluded.guild_name,
                                            cooldown_hours   = excluded.cooldown_hours
                                        """,
                                        (
                                            interaction.guild_id,
                                            str(cfg.get("default_role")) if cfg.get("default_role") else None,
                                            str(cfg.get("recruit_role")) if cfg.get("recruit_role") else None,
                                            str(cfg.get("recruiter_roles")) if cfg.get("recruiter_roles") is not None else None,
                                            forum_id,
                                            apply_channel_id,
                                            cfg.get("guild_name", ""),
                                            int(cfg.get("cooldown_hours", 1)),
                                        ),
                                    )
                                    await db.commit()
                            except Exception:
                                pass
                        self.bot.loop.create_task(_upsert_cfg())
                except Exception:
                    cfg = None

            # 2) Если unified_settings недостаточно, быстро читаем из БД
            if not cfg:
                cfg = await RecruitCog(self.bot)._get_cfg(interaction.guild.id)

            # 3) Если форум не задан — предложим выбрать его интерактивно
            if not cfg or not cfg.get("forum_id"):
                # Ищем доступные forum-каналы
                forum_channels = [ch for ch in interaction.guild.channels if isinstance(ch, discord.ForumChannel)]
                if not forum_channels:
                    await interaction.response.send_message(
                        "❌ На сервере нет каналов-форумов. Создайте форум-канал в Discord и повторите попытку.",
                        ephemeral=True,
                    )
                    return

                class ForumSelect(ui.Select):
                    def __init__(self, bot: commands.Bot, channels: list[discord.ForumChannel]):
                        options = [
                            discord.SelectOption(label=ch.name[:100], value=str(ch.id)) for ch in channels
                        ]
                        super().__init__(placeholder="Выберите форум для заявок", min_values=1, max_values=1, options=options)
                        self.bot = bot

                    async def callback(self, select_interaction: discord.Interaction):
                        try:
                            chosen_id = int(self.values[0])
                            # Сохраняем в unified_settings (и дублируем в БД асинхронно)
                            if _unified_settings is not None:
                                rs = _unified_settings.get_recruit_settings(select_interaction.guild.id)
                                rs["forum_channel"] = str(chosen_id)
                                _unified_settings.update_recruit_settings(select_interaction.guild.id, rs)

                            async def _upsert_forum():
                                try:
                                    import aiosqlite
                                    from .database import DB_PATH
                                    async with aiosqlite.connect(DB_PATH) as db:
                                        await db.execute(
                                            """
                                            INSERT INTO guild_config (guild_id, forum_id)
                                            VALUES (?, ?)
                                            ON CONFLICT(guild_id) DO UPDATE SET forum_id = excluded.forum_id
                                            """,
                                            (select_interaction.guild_id, chosen_id),
                                        )
                                        await db.commit()
                                except Exception:
                                    pass

                            self.bot.loop.create_task(_upsert_forum())

                            # Формируем cfg и открываем модалку
                            rs2 = _unified_settings.get_recruit_settings(select_interaction.guild.id) if _unified_settings else {}
                            def _to_int(val):
                                try:
                                    return int(val) if val is not None else None
                                except Exception:
                                    return None
                            cfg2 = {
                                "default_role": rs2.get("default_role"),
                                "recruit_role": rs2.get("recruit_role"),
                                "recruiter_roles": rs2.get("recruiter_roles"),
                                "forum_id": chosen_id,
                                "apply_channel_id": _to_int(rs2.get("recruit_panel_channel") or rs2.get("apply_channel_id")),
                                "guild_name": rs2.get("guild_name", ""),
                                "cooldown_hours": rs2.get("cooldown_hours", 1),
                            }
                            if not select_interaction.response.is_done():
                                await select_interaction.response.send_modal(ApplyModal(self.bot, cfg2))
                            else:
                                await select_interaction.followup.send("Откройте модалку повторно, действие уже обработано.", ephemeral=True)
                        except Exception:
                            try:
                                if not select_interaction.response.is_done():
                                    await select_interaction.response.send_message("❌ Ошибка выбора форума.", ephemeral=True)
                            except Exception:
                                pass

                class ForumSelectView(ui.View):
                    def __init__(self, bot: commands.Bot, channels: list[discord.ForumChannel]):
                        super().__init__(timeout=120)
                        self.add_item(ForumSelect(bot, channels))

                await interaction.response.send_message(
                    "Пожалуйста, выберите форум-канал для заявок:",
                    view=ForumSelectView(self.bot, forum_channels),
                    ephemeral=True,
                )
                return

            if not interaction.response.is_done():
                await interaction.response.send_modal(ApplyModal(self.bot, cfg))
            else:
                await interaction.followup.send("Откройте модалку повторно, действие уже обработано.", ephemeral=True)

        except Exception as e:
            logger.exception(f"[ApplyButton] Ошибка обработки взаимодействия: {e}")
            # Гарантировано отвечаем пользователю при любых ошибках
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ Ошибка взаимодействия. Сообщите администратору.", ephemeral=True)
                else:
                    await interaction.followup.send("❌ Ошибка взаимодействия. Сообщите администратору.", ephemeral=True)
            except Exception:
                pass

# ─── View с постоянной кнопкой ────────────────────────────────────────────────
class PersistentApplyButtonView(ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)  # Важно: timeout=None для персистентности
        self.add_item(ApplyButton(bot))

# ─── View с постоянной кнопкой запроса очков ─────────────────────────────────
class PersistentPointsRequestView(ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        # Используем кнопку из ui_components
        from .ui_components import PointsRequestButton
        self.add_item(PointsRequestButton())
        
        
# ─── Глобальные переменные View ───────────────────────────────────────────────
persistent_view: PersistentApplyButtonView | None = None
persistent_event_view: PersistentEventSubmitView | None = None
persistent_points_view: PersistentPointsRequestView | None = None
unified_event_view: UnifiedEventView | None = None
        


# ─── Cog с командами ───────────────────────────────────────────────────────────
class RecruitCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        logger.info("Cog RecruitCog загружен")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Обрабатывает сообщения для интерактивных заявок"""
        # Добавляем подробное логирование всех сообщений
        logger.info(f"[COG ON_MESSAGE] author={message.author.id} bot={message.author.bot} channel_type={type(message.channel).__name__} channel_id={message.channel.id} content='{message.content[:100]}'")
        
        # Игнорируем сообщения от ботов
        if message.author.bot:
            logger.debug(f"[COG ON_MESSAGE] Игнорируем сообщение от бота {message.author.id}")
            return

        try:
            from .submission_state import active_submissions as _as
            logger.info(f"[COG ON_MESSAGE] active_submissions size={len(_as)} keys={list(_as.keys())[:3]}")
        except Exception as e:
            logger.error(f"[COG ON_MESSAGE] Ошибка импорта active_submissions: {e}")
            pass
        
        # Игнорируем команды
        if message.content.startswith(self.bot.command_prefix):
            logger.debug(f"[COG ON_MESSAGE] Игнорируем команду: {message.content[:50]}")
            return
        
        # Обрабатываем интерактивные заявки СНАЧАЛА
        try:
            from .ui_components import handle_submission_message
            handled = await handle_submission_message(message)
            logger.info(f"[COG ON_MESSAGE] handle_submission_message returned: {handled} for message: '{message.content[:50]}'")
            if handled:
                return
        except Exception as e:
            logger.error(f"Ошибка обработки интерактивной заявки: {e}")
        
        # Добавляем дебаг логи для тредов (после основного обработчика)
        if isinstance(message.channel, discord.Thread):
            logger.info(f"[COG THREAD MSG] thread={message.channel.id} author={message.author.id} content='{message.content[:80]}'")
            # Аварийный прямой парс участников если основной обработчик по какой-то причине не срабатывает
            try:
                from .submission_state import active_submissions
                session_key = f"{message.author.id}_{message.channel.id}"
                session = active_submissions.get(session_key)
                logger.info(f"[COG FALLBACK CHECK] session_key={session_key} found_session={session is not None}")
                if session and getattr(session, 'state', None) == 'waiting_participants':
                    from .ui_components import handle_participants_message, parse_participants_from_message
                    # Если пользователь ничего не ввёл осмысленного (пусто), пропускаем
                    if message.content.strip() or message.mentions:
                        # Пробуем разобрать участников (даже если нет ключевых слов)
                        logger.warning(f"[COG FALLBACK PARTICIPANTS] Пробуем fallback для {session_key}")
                        ok = await handle_participants_message(message, session)
                        if ok:
                            logger.warning(f"[COG FALLBACK PARTICIPANTS] Auto-handled first message for {session_key}")
                            return
            except Exception as e:
                logger.error(f"[COG FALLBACK PARTICIPANTS ERROR] {e}")

        # Дополнительная обработка для сохранения скриншотов
        if isinstance(message.channel, discord.Thread):
            try:
                from .submission_state import active_submissions
                session_key = f"{message.author.id}_{message.channel.id}"
                logger.info(f"[COG THREAD IMG CHECK] primary_key={session_key} active={len(active_submissions)}")

                # Fallback: если нет точной записи, пробуем найти по parent_id (каналу) — вдруг session сохранена до создания треда
                session = active_submissions.get(session_key)
                if not session and message.channel.parent_id:
                    parent_key = f"{message.author.id}_{message.channel.parent_id}"
                    if parent_key in active_submissions:
                        # Мигрируем сессию на thread.id
                        session = active_submissions[parent_key]
                        logger.info(f"[COG THREAD MIGRATE] Перенос сессии {parent_key} -> {session_key}")
                        # Обновляем channel_id внутри сессии
                        try:
                            session.channel_id = message.channel.id
                        except Exception:
                            pass
                        active_submissions[session_key] = session
                        del active_submissions[parent_key]

                if session_key in active_submissions:
                    has_attachment = bool(message.attachments)
                    content_lower = message.content.lower()
                    has_image_url = any(token in content_lower for token in ['http://i.imgur.com', 'https://i.imgur.com', 'http://imgur.com', 'https://imgur.com', '.png', '.jpg', '.jpeg', '.gif', '.webp'])
                    if has_attachment or has_image_url:
                        try:
                            await message.add_reaction("✅")
                            await message.reply("✅ Скриншот получен. Напишите 'отправить' чтобы завершить заявку", mention_author=False)
                            return
                        except Exception as e:
                            logger.error(f"Ошибка при автоподтверждении скриншота в треде {message.channel.id}: {e}")
                else:
                    logger.debug(f"Пропуск треда {message.channel.id}: нет активной сессии для пользователя {message.author.id}")
            except Exception as e:
                logger.error(f"Ошибка проверки активной сессии: {e}")

        # Проверяем, если это тред заявки без активной сессии (потерянная сессия после перезапуска)
        if isinstance(message.channel, discord.Thread):
            # Проверяем, является ли это тредом заявки (по названию)
            if any(keyword in message.channel.name.lower() for keyword in ['заявка', 'событие', 'кристал', 'убийство', 'ганк']):
                # Проверяем, что пользователь пытается что-то написать об участниках
                content_lower = message.content.lower()
                if any(word in content_lower for word in ['@', 'только я', 'участник', 'один']):
                    logger.info(f"Обнаружена потерянная сессия в треде {message.channel.id} для пользователя {message.author.id}")
                    embed = discord.Embed(
                        title="⚠️ Сессия потеряна",
                        description="Ваша сессия подачи заявки была сброшена из-за перезапуска бота.",
                        color=discord.Color.orange()
                    )
                    embed.add_field(
                        name="🔄 Что делать?",
                        value="Пожалуйста, начните заявку заново через команду `/events_panel` в основном канале.",
                        inline=False
                    )
                    await message.channel.send(embed=embed)
                    return

    # Удалены команды /recruit_setup, /setup_points (перенесены в веб). Оставлено как комментарий для истории.

    # Удалена команда /setup_channels.

    # Удалена команда /setup_forum.

    # Удалена команда /setup_panels.

    # Удалена команда /setup_dates.

    # ── /deploy_panels ────────────────────────────────────────────────────────
    @app_commands.command(name="deploy_panels", description="Разместить панели заявок в настроенных каналах")
    @app_commands.describe(
        panel_type="Тип панели для размещения"
    )
    @app_commands.choices(panel_type=[
        app_commands.Choice(name="Панель набора (заявки на прием)", value="recruit"),
        app_commands.Choice(name="Панель очков (заявки на очки)", value="points"),
        app_commands.Choice(name="Обе панели", value="both")
    ])
    @app_commands.default_permissions(administrator=True)
    async def deploy_panels(
        self, 
        interaction: discord.Interaction, 
        panel_type: str = "both"
    ):
        # Веб-администрирование: перенесено в веб-интерфейс
        base_url = "http://localhost:8082"
        gid = interaction.guild.id if interaction.guild else 0
        await interaction.response.send_message(
            f"⚙️ Размещайте панели через веб: {base_url}/guild/{gid}/recruit",
            ephemeral=True,
        )
        return
        """Размещение панелей заявок в настроенных каналах"""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ Только администраторы могут размещать панели!",
                ephemeral=True
            )
            return

        try:
            settings = UnifiedSettings()
            guild_settings = settings.get_guild_settings(str(interaction.guild.id))
            recruit_settings = guild_settings.get('recruit', {})
            
            success_panels = []
            error_panels = []
            
            # Размещение панели набора
            if panel_type in ["recruit", "both"]:
                recruit_channel_id = recruit_settings.get('recruit_panel_channel')
                if recruit_channel_id:
                    try:
                        channel = interaction.guild.get_channel(int(recruit_channel_id))
                        if channel:
                            embed = discord.Embed(
                                title="🎯 Подача заявки на прием в гильдию",
                                description=(
                                    "Нажмите кнопку ниже, чтобы подать заявку на прием в гильдию.\n"
                                    "Ваша заявка будет рассмотрена модераторами."
                                ),
                                color=discord.Color.blue()
                            )
                            
                            # Используем персистентный view
                            global persistent_view
                            await channel.send(embed=embed, view=persistent_view)
                            success_panels.append("Панель набора")
                        else:
                            error_panels.append("Панель набора (канал не найден)")
                    except Exception as e:
                        error_panels.append(f"Панель набора ({str(e)})")
                else:
                    error_panels.append("Панель набора (канал не настроен)")
            
            # Размещение панели очков
            if panel_type in ["points", "both"]:
                points_channel_id = recruit_settings.get('points_panel_channel')
                if points_channel_id:
                    try:
                        channel = interaction.guild.get_channel(int(points_channel_id))
                        if channel:
                            embed = discord.Embed(
                                title="⭐ Подача заявки на выдачу очков",
                                description=(
                                    "Нажмите кнопку ниже, чтобы подать заявку на выдачу очков за участие в событиях.\n"
                                    "Укажите событие и дату участия для проверки."
                                ),
                                color=discord.Color.gold()
                            )
                            
                            # Используем персистентный view
                            global persistent_points_view
                            await channel.send(embed=embed, view=persistent_points_view)
                            success_panels.append("Панель очков")
                        else:
                            error_panels.append("Панель очков (канал не найден)")
                    except Exception as e:
                        error_panels.append(f"Панель очков ({str(e)})")
                else:
                    error_panels.append("Панель очков (канал не настроен)")
            
            # Формируем ответ
            embed = discord.Embed(
                title="📋 Результат размещения панелей",
                color=discord.Color.green() if success_panels and not error_panels else discord.Color.orange()
            )
            
            if success_panels:
                embed.add_field(
                    name="✅ Успешно размещены",
                    value="\n".join([f"• {panel}" for panel in success_panels]),
                    inline=False
                )
            
            if error_panels:
                embed.add_field(
                    name="❌ Ошибки размещения",
                    value="\n".join([f"• {panel}" for panel in error_panels]),
                    inline=False
                )
            
            if not success_panels and not error_panels:
                embed.description = "❌ Нет настроенных каналов для размещения панелей!"
                embed.color = discord.Color.red()
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"Размещены панели для гильдии {interaction.guild.id}: success={success_panels}, errors={error_panels}")
            
        except Exception as e:
            logger.error(f"Ошибка при размещении панелей для гильдии {interaction.guild.id}: {e}")
            await interaction.response.send_message(
                "❌ Ошибка размещения панелей!",
                ephemeral=True
            )

    # ── /apply ────────────────────────────────────────────────────────────────
    @app_commands.command(name="apply", description="Отправить кнопку подачи заявки")
    async def apply(self, interaction: discord.Interaction):
        global persistent_view

        cfg = await self._get_cfg(interaction.guild_id)
        if not cfg:
            await interaction.response.send_message(
                "❌ Бот не настроен! Используйте /setup.", ephemeral=True
            )
            return

        apply_channel = interaction.guild.get_channel(cfg["apply_channel_id"])
        if not apply_channel:
            await interaction.response.send_message("❌ Канал для заявок не найден.", ephemeral=True)
            return

        # используем уже зарегистрированную View (без дублирования кнопки)
        await apply_channel.send(
            "Нажмите кнопку, чтобы подать заявку:",
            view=persistent_view,
        )

        await interaction.response.send_message(
            f"✅ Кнопка отправлена в {apply_channel.mention}", ephemeral=True
        )

    # ── /info ────────────────────────────────────────────────────────────────
    @app_commands.command(name="info", description="Показать информацию о заявке игрока")
    async def info(self, interaction: discord.Interaction, player: discord.Member = None):
        # Если игрок не указан, показываем информацию о себе
        target_user = player if player else interaction.user
        
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                """
                SELECT status, reviewer_id, created_at, decided_at, thread_id, ign, age, goals, referral
                  FROM applications
                 WHERE author_id = ?
              ORDER BY created_at DESC
                 LIMIT 1
                """,
                (target_user.id,),
            )
            row = await cur.fetchone()
            await cur.close()

        if not row:
            await interaction.response.send_message(f"❌ У {target_user.mention} нет заявок.", ephemeral=True)
            return

        status, reviewer_id, created_at, decided_at, thread_id, ign, age, goals, referral = row
        reviewer_mention = f"<@{reviewer_id}>" if reviewer_id else "–"
        decided_str = decided_at or "–"
        thread_url = f"https://discord.com/channels/{interaction.guild_id}/{thread_id}"

        embed = discord.Embed(
            title=f"📋 Информация о заявке: {target_user.display_name}",
            color=discord.Color.blurple()
        )
        
        # Основная информация о заявке
        embed.add_field(name="🎮 Ник в игре", value=ign, inline=True)
        embed.add_field(name="📊 Статус", value=status.capitalize(), inline=True)
        embed.add_field(name="👤 Рассмотрел", value=reviewer_mention, inline=True)
        
        # Временные метки
        embed.add_field(name="📅 Дата подачи", value=created_at, inline=True)
        embed.add_field(name="✅ Дата решения", value=decided_str, inline=True)
        embed.add_field(name="🔗 Ссылка на тред", value=f"[Перейти к заявке]({thread_url})", inline=True)
        
        # Детали заявки
        embed.add_field(name="🎂 Возраст", value=str(age), inline=True)
        embed.add_field(name="📍 Откуда узнал", value=referral, inline=True)
        embed.add_field(name="🎯 Цели", value=goals[:100] + "..." if len(goals) > 100 else goals, inline=False)

        # Добавляем статистику Albion если возможно (приоритет Europe сервер)
        players = await search_albion_player_with_options(ign)
        if players:
            player_data = players[0]  # Берем наиболее точное совпадение
            guild_name = player_data.get("GuildName", "Нет гильдии")
            alliance_name = player_data.get("AllianceName", "Нет альянса")
            kill_fame = player_data.get("KillFame", 0)
            
            embed.add_field(
                name="⚔️ Текущий статус в Albion (Europe)",
                value=f"🏰 Гильдия: {guild_name}\n⚔️ Альянс: {alliance_name}\n💰 Kill Fame: {kill_fame:,}",
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /history ────────────────────────────────────────────────────────────────
    @app_commands.command(name="history", description="Показать историю заявок пользователя")
    async def history(self, interaction: discord.Interaction, member: discord.Member):
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                """
                SELECT status, reviewer_id, created_at, decided_at, thread_id
                  FROM applications
                 WHERE author_id = ?
              ORDER BY created_at DESC
                """,
                (member.id,),
            )
            rows = await cur.fetchall()
            await cur.close()

        if not rows:
            await interaction.response.send_message(
                f"❌ У пользователя {member.mention} нет заявок.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"История заявок: {member.display_name}",
            color=discord.Color.green()
        )

        for idx, (status, reviewer_id, created_at, decided_at, thread_id) in enumerate(rows, 1):
            reviewer = f"<@{reviewer_id}>" if reviewer_id else "–"
            decided = decided_at or "–"
            thread_url = f"https://discord.com/channels/{interaction.guild_id}/{thread_id}"
            embed.add_field(
                name=f"Заявка #{idx}",
                value=(
                    f"Статус: **{status.capitalize()}**\n"
                    f"Рассмотрел: {reviewer}\n"
                    f"Дата подачи: {created_at}\n"
                    f"Дата решения: {decided}\n"
                    f"[Ссылка]({thread_url})"
                ),
                inline=False
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /ebal ──────────────────────────────────────────────────────────────────
    @app_commands.command(name="ebal", description="Изменить баланс очков пользователя")
    @app_commands.describe(
        member="Пользователь для изменения баланса",
        amount="Количество очков для добавления или вычитания (может быть отрицательным)"
    )
    async def ebal(self, interaction: discord.Interaction, member: discord.Member, amount: int):
        # Проверяем права модератора очков
        if not (interaction.user.guild_permissions.manage_messages or 
                interaction.user.guild_permissions.administrator):
            await interaction.response.send_message(
                "❌ У вас нет прав для управления балансом очков.",
                ephemeral=True
            )
            return
        
        try:
            # Получаем текущий баланс (функция возвращает tuple: (points, events_count))
            current_balance_data = await EventDatabase.get_user_points(interaction.guild.id, member.id)
            current_balance = current_balance_data[0]  # Берем только очки
            
            # Вычисляем новый баланс
            new_balance = current_balance + amount
            
            # Проверяем, чтобы баланс не стал отрицательным
            if new_balance < 0:
                await interaction.response.send_message(
                    f"❌ Нельзя установить отрицательный баланс!\n"
                    f"Текущий баланс: **{current_balance}** очков\n"
                    f"Попытка изменить на: **{amount:+}**\n"
                    f"Результат: **{new_balance}** (недопустимо)",
                    ephemeral=True
                )
                return
            
            # Обновляем баланс
            await EventDatabase.set_user_points(interaction.guild.id, member.id, new_balance)
            
            # Создаем embed с результатом
            embed = discord.Embed(
                title="💰 Баланс обновлен",
                color=discord.Color.green() if amount > 0 else discord.Color.orange()
            )
            
            embed.add_field(
                name="👤 Пользователь", 
                value=member.mention, 
                inline=True
            )
            embed.add_field(
                name="📊 Изменение", 
                value=f"{amount:+} очков", 
                inline=True
            )
            embed.add_field(
                name="👤 Модератор", 
                value=interaction.user.mention, 
                inline=True
            )
            embed.add_field(
                name="💎 Баланс до", 
                value=f"{current_balance} очков", 
                inline=True
            )
            embed.add_field(
                name="💎 Баланс после", 
                value=f"{new_balance} очков", 
                inline=True
            )
            
            action_text = "добавлено" if amount > 0 else "списано"
            embed.set_footer(text=f"Модератором {interaction.user.display_name} {action_text} {abs(amount)} очков")
            
            await interaction.response.send_message(embed=embed, ephemeral=False)
            
        except Exception as e:
            logger.error(f"Ошибка изменения баланса: {e}")
            await interaction.response.send_message(
                f"❌ Ошибка при изменении баланса: {e}",
                ephemeral=True
            )

    # ── /balance ───────────────────────────────────────────────────────────────
    @app_commands.command(name="balance", description="Показать балансы очков (топ или конкретного пользователя)")
    @app_commands.describe(
        member="Пользователь для просмотра баланса (необязательно - по умолчанию топ-10)",
        limit="Количество пользователей в топе (по умолчанию 10)"
    )
    async def balance(self, interaction: discord.Interaction, member: discord.Member = None, limit: int = 10):
        try:
            if member:
                # Показываем баланс конкретного пользователя
                balance_data = await EventDatabase.get_user_points(interaction.guild.id, member.id)
                current_balance = balance_data[0]
                events_count = balance_data[1]
                
                embed = discord.Embed(
                    title="💰 Баланс очков",
                    color=discord.Color.blue()
                )
                
                embed.add_field(
                    name="👤 Пользователь", 
                    value=member.mention, 
                    inline=True
                )
                embed.add_field(
                    name="💎 Очки", 
                    value=f"{current_balance} очков", 
                    inline=True
                )
                embed.add_field(
                    name="🎯 События", 
                    value=f"{events_count} участий", 
                    inline=True
                )
                
                # Получаем позицию в рейтинге
                leaderboard = await EventDatabase.get_leaderboard(interaction.guild.id, 100)
                position = None
                for i, (user_id, points, events) in enumerate(leaderboard, 1):
                    if user_id == member.id:
                        position = i
                        break
                
                if position:
                    embed.add_field(
                        name="🏆 Позиция в рейтинге", 
                        value=f"#{position}", 
                        inline=True
                    )
                
                embed.set_thumbnail(url=member.display_avatar.url)
                await interaction.response.send_message(embed=embed, ephemeral=True)
                
            else:
                # Показываем топ пользователей
                if limit < 1 or limit > 50:
                    limit = 10
                
                leaderboard = await EventDatabase.get_leaderboard(interaction.guild.id, limit)
                
                if not leaderboard:
                    embed = discord.Embed(
                        title="💰 Топ балансов очков",
                        description="Пока никто не заработал очки!",
                        color=discord.Color.orange()
                    )
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return
                
                embed = discord.Embed(
                    title=f"🏆 Топ-{len(leaderboard)} балансов очков",
                    color=discord.Color.gold()
                )
                
                # Определяем эмодзи для позиций
                position_emojis = ["🥇", "🥈", "🥉"] + ["🏅"] * (limit - 3)
                
                description_lines = []
                for i, (user_id, points, events) in enumerate(leaderboard):
                    user = interaction.guild.get_member(user_id)
                    username = user.display_name if user else f"Пользователь ID: {user_id}"
                    emoji = position_emojis[i] if i < len(position_emojis) else "🏅"
                    
                    description_lines.append(
                        f"{emoji} **#{i+1}** {username} — **{points}** очков ({events} событий)"
                    )
                
                embed.description = "\n".join(description_lines)
                
                # Добавляем статистику
                total_users = len(leaderboard)
                total_points = sum(points for _, points, _ in leaderboard)
                total_events = sum(events for _, _, events in leaderboard)
                
                embed.add_field(
                    name="📊 Общая статистика",
                    value=f"👥 Активных игроков: {total_users}\n💎 Всего очков: {total_points}\n🎯 Всего событий: {total_events}",
                    inline=False
                )
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
                
        except Exception as e:
            logger.error(f"Ошибка просмотра баланса: {e}")
            await interaction.response.send_message(
                f"❌ Ошибка при получении баланса: {e}",
                ephemeral=True
            )

    # ── /reset_all_points ──────────────────────────────────────────────────────
    @app_commands.command(name="reset_all_points", description="Обнулить очки всем пользователям (только админ)")
    async def reset_all_points(self, interaction: discord.Interaction):
        # Проверяем права администратора
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ Только администраторы могут обнулить очки всем пользователям.",
                ephemeral=True
            )
            return
        
        # Создаем view подтверждения
        view = ResetPointsConfirmationView()
        
        embed = discord.Embed(
            title="⚠️ Подтверждение сброса очков",
            description=(
                "**ВНИМАНИЕ!** Вы собираетесь обнулить очки **ВСЕМ** пользователям на сервере.\n\n"
                "Это действие **НЕОБРАТИМО** и удалит все накопленные очки у всех участников.\n\n"
                "Вы уверены, что хотите продолжить?"
            ),
            color=discord.Color.red()
        )
        embed.set_footer(text="Действие требует подтверждения администратора")
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="events_panel", description="Единый панель для событий, баланса и магазина")
    @app_commands.default_permissions(manage_messages=True)
    async def events_panel(self, interaction: discord.Interaction):
        global unified_event_view

        # Проверяем права
        if not (interaction.user.guild_permissions.manage_messages or 
                interaction.user.guild_permissions.administrator):
            await interaction.response.send_message(
                "❌ У вас нет прав для использования этой команды.",
                ephemeral=True
            )
            return

        # Создаем новый view если нет глобального
        view = unified_event_view if unified_event_view else UnifiedEventView()

        # Отправляем единый интерфейс
        embed = discord.Embed(
            title="🎯 Система событий и наград",
            description=(
                "**🎮 Добро пожаловать в систему событий Albion Online!**\n\n"
                "Здесь вы можете:\n"
                "🎯 **Подать заявку** на участие в событии\n"
                "💰 **Проверить баланс** очков и историю\n"
                "🛒 **Купить награды** за накопленные очки\n\n"
                "**Доступные события:**\n"
                "🕷️ Кристальные жуки (убийство) - 1 очко\n"
                "🔵 Синие сферы (доставка) - 1.5 очка\n"
                "🟣 Фиолетовые сферы (доставка) - 3 очка\n"
                "🟡 Золотые сферы (доставка) - 5 очков\n"
                "🌪️ Зеленые вихри (доставка) - 2 очка\n"
                "🌀 Синие вихри (доставка) - 3 очка\n"
                "🌊 Фиолетовые вихри (доставка) - 6 очков\n"
                "💫 Золотые вихри (доставка) - 10 очков"
            ),
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="🛒 Доступные награды",
            value=(
                "💰 **200k серебра** - 10 очков\n"
                "🎲 **Рандомная вещь** - 30 очков\n"
                "⚔️ **Комплект экипировки** - 50 очков"
            ),
            inline=False
        )
        
        embed.add_field(
            name="ℹ️ Как это работает",
            value=(
                "1. Участвуйте в событиях и зарабатывайте очки\n"
                "2. Модератор проверяет и начисляет очки\n"
                "3. Обменивайте очки на награды в магазине\n"
                "4. Получайте награды в игре от модераторов"
            ),
            inline=False
        )
        
        embed.set_footer(text="💡 Всегда прикладывайте скриншоты к заявкам!")

        await interaction.channel.send(
            embed=embed,
            view=view
        )

        await interaction.response.send_message(
            "✅ Единый интерфейс событий отправлен!", 
            ephemeral=True
        )

    @app_commands.command(name="test_debug", description="Тестовая команда для проверки работы бота")
    async def test_debug(self, interaction: discord.Interaction):
        """Тестовая команда для диагностики"""
        global unified_event_view

        embed = discord.Embed(
            title="🔧 Диагностика бота",
            description="Проверка работы компонентов",
            color=discord.Color.blue()
        )
        
        # Проверяем unified_event_view
        view_status = "✅ Инициализирован" if unified_event_view else "❌ Не найден"
        embed.add_field(name="UnifiedEventView", value=view_status, inline=False)
        
        # Проверяем активные сессии
        try:
            from .submission_state import active_submissions
            sessions_count = len(active_submissions)
            # Покажем первые ключи
            keys_preview = list(active_submissions.keys())[:5]
            embed.add_field(name="Активные сессии", value=f"{sessions_count} шт. {keys_preview}", inline=False)
        except Exception as e:
            embed.add_field(name="Активные сессии", value=f"Ошибка: {e}", inline=False)
        
        # Проверяем канал
        embed.add_field(
            name="Канал",
            value=f"ID: {interaction.channel.id}\nТип: {type(interaction.channel).__name__}",
            inline=False
        )
        
        # Исправлено: ранее здесь была повреждённая строка с 'ephemeral=Truemmand(...)'
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="debug_sessions", description="Отладка активных сессий заявок")
    async def debug_sessions(self, interaction: discord.Interaction):
        """Отладочная команда для проверки активных сессий"""
        try:
            from .submission_state import active_submissions
            
            if not active_submissions:
                embed = discord.Embed(
                    title="🔍 Активные сессии", 
                    description="❌ Нет активных сессий",
                    color=discord.Color.orange()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
                
            embed = discord.Embed(
                title="🔍 Активные сессии", 
                description=f"Найдено {len(active_submissions)} активных сессий",
                color=discord.Color.green()
            )
            
            for i, (key, session) in enumerate(active_submissions.items()):
                if i >= 10:  # Ограничиваем до 10 сессий
                    embed.add_field(
                        name="...",
                        value=f"И ещё {len(active_submissions) - 10} сессий",
                        inline=False
                    )
                    break
                    
                participants_count = len(getattr(session, 'participants', []))
                embed.add_field(
                    name=f"#{i+1} Ключ: {key}",
                    value=f"👤 Пользователь: <@{session.user_id}>\n"
                          f"📺 Канал: <#{session.channel_id}>\n"
                          f"🔄 Состояние: {session.state}\n"
                          f"👥 Участников: {participants_count}",
                    inline=True
                )
            
            embed.set_footer(text=f"ID объекта: {id(active_submissions)}")
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            embed = discord.Embed(
                title="❌ Ошибка",
                description=f"Не удалось получить информацию о сессиях: {e}",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="dump_sessions", description="Диагностика: показать активные интерактивные сессии")
    async def dump_sessions(self, interaction: discord.Interaction):
        try:
            from .submission_state import active_submissions
            if not active_submissions:
                await interaction.response.send_message("Активных сессий нет", ephemeral=True)
                return
            lines = []
            for key, sess in list(active_submissions.items())[:20]:
                lines.append(f"{key} state={getattr(sess,'state', '?')} participants={len(getattr(sess,'participants', []))}")
            txt = "\n".join(lines)
            if len(txt) > 1900:
                txt = txt[:1900] + "..."
            await interaction.response.send_message(f"```\n{txt}\n```", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Ошибка: {e}", ephemeral=True)

    @app_commands.command(name="test_submission", description="Создать тестовую заявку для отладки")
    async def test_submission(self, interaction: discord.Interaction):
        """Создает тестовую заявку напрямую"""
        try:
            from .ui_components import InteractiveSubmissionSession, EventType, EventAction, active_submissions
            
            # Создаем тестовую сессию
            session = InteractiveSubmissionSession(
                user_id=interaction.user.id,
                channel_id=interaction.channel.id,
                event_type=EventType.CRYSTAL_SPIDER,
                action=EventAction.KILL
            )
            
            # Добавляем в активные сессии
            session_key = f"{interaction.user.id}_{interaction.channel.id}"
            active_submissions[session_key] = session
            
            embed = discord.Embed(
                title="🧪 Тестовая сессия создана",
                description=f"Сессия {session_key} добавлена в активные",
                color=discord.Color.green()
            )
            embed.add_field(name="Событие", value="Кристальные жуки (убийство)", inline=False)
            embed.add_field(name="Статус", value=session.state, inline=False)
            embed.add_field(name="Участники", value=f"{len(session.participants)} чел.", inline=False)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
            # Отправляем инструкцию в канал
            instruction_embed = discord.Embed(
                title="👥 Участники события",
                description="Пингуйте всех участников группы в следующем сообщении",
                color=discord.Color.orange()
            )
            instruction_embed.add_field(
                name="🔧 Как указать участников:",
                value="• Напишите `@user1 @user2 @user3` чтобы добавить участников\n• Или напишите `только я` если участвуете один",
                inline=False
            )
            
            await interaction.followup.send(embed=instruction_embed)
            
        except Exception as e:
            await interaction.response.send_message(f"❌ Ошибка: {e}", ephemeral=True)

    @app_commands.command(name="shop_admin", description="Управление покупками в магазине (для админов)")
    @app_commands.default_permissions(administrator=True)
    async def shop_admin(self, interaction: discord.Interaction):
        # Получаем ожидающие покупки
        pending_purchases = await EventDatabase.get_pending_purchases(interaction.guild.id)
        
        if not pending_purchases:
            await interaction.response.send_message(
                "✅ Нет ожидающих покупок в магазине.",
                ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title="🛒 Управление магазином",
            description=f"Ожидающих покупок: **{len(pending_purchases)}**",
            color=discord.Color.orange()
        )
        
        purchase_text = ""
        for purchase in pending_purchases[:10]:  # Показываем первые 10
            user = interaction.guild.get_member(purchase['user_id'])
            user_name = user.display_name if user else f"ID:{purchase['user_id']}"
            
            purchase_text += (
                f"🆔 **ID:** {purchase['id']}\n"
                f"👤 **Игрок:** {user_name}\n"
                f"🛍️ **Товар:** {purchase['item_name']}\n"
                f"💎 **Стоимость:** {purchase['points_cost']} очков\n"
                f"📅 **Дата:** {purchase['created_at'][:10]}\n\n"
            )
        
        if len(pending_purchases) > 10:
            purchase_text += f"... и ещё {len(pending_purchases) - 10} покупок"
        
        embed.description += f"\n\n{purchase_text}"
        embed.set_footer(text="Используйте /shop_process для обработки покупок")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="shop_process", description="Обработать покупку (выдать или отклонить)")
    @app_commands.default_permissions(administrator=True) 
    async def shop_process(
        self,
        interaction: discord.Interaction,
        purchase_id: int,
        action: str,
        reason: str = None
    ):
        if action.lower() not in ['выдать', 'отклонить', 'give', 'reject']:
            await interaction.response.send_message(
                "❌ Действие должно быть 'выдать' или 'отклонить'.",
                ephemeral=True
            )
            return
        
        completed = action.lower() in ['выдать', 'give']
        
        success = await EventDatabase.process_shop_purchase(
            purchase_id=purchase_id,
            admin_id=interaction.user.id,
            completed=completed,
            admin_notes=reason
        )
        
        if success:
            action_text = "выдана" if completed else "отклонена"
            await interaction.response.send_message(
                f"✅ Покупка #{purchase_id} {action_text}.",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Ошибка при обработке покупки #{purchase_id}. Возможно, она уже обработана.",
                ephemeral=True
            )

    @app_commands.command(name="check_guild_members", description="Проверить принятых игроков на членство в гильдии")
    @app_commands.default_permissions(manage_guild=True)
    async def check_guild_members(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        cfg = await self._get_cfg(interaction.guild_id)
        if not cfg or not cfg.get("guild_name"):
            await interaction.followup.send("❌ Бот не настроен или не указано название гильдии. Используйте /setup.")
            return
        
        guild_name = cfg["guild_name"]
        
        # Получаем всех принятых игроков
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                """
                SELECT author_id, ign, created_at, decided_at, thread_id
                  FROM applications
                 WHERE status = 'accepted'
              ORDER BY decided_at DESC
                """,
            )
            rows = await cur.fetchall()
            await cur.close()
        
        if not rows:
            await interaction.followup.send("❌ Нет принятых заявок для проверки.")
            return
        
        embed = discord.Embed(
            title=f"🔍 Проверка членства в гильдии '{guild_name}'",
            description=f"Проверяю {len(rows)} принятых игроков...",
            color=discord.Color.orange()
        )
        
        not_in_guild = []
        in_guild = []
        not_found = []
        errors = []
        
        # Проверяем каждого игрока (приоритет Europe сервер)
        for author_id, ign, created_at, decided_at, thread_id in rows:
            try:
                players = await search_albion_player_with_options(ign)
                
                if not players:
                    not_found.append({
                        "ign": ign,
                        "author_id": author_id,
                        "thread_id": thread_id,
                        "decided_at": decided_at
                    })
                    continue
                
                player_data = players[0]  # Берем наиболее точное совпадение
                
                current_guild = player_data.get("GuildName")
                
                if current_guild == guild_name:
                    in_guild.append({
                        "ign": ign,
                        "author_id": author_id,
                        "guild": current_guild,
                        "alliance": player_data.get("AllianceName", "Нет альянса"),
                        "thread_id": thread_id,
                        "decided_at": decided_at
                    })
                else:
                    not_in_guild.append({
                        "ign": ign,
                        "author_id": author_id,
                        "current_guild": current_guild or "Нет гильдии",
                        "thread_id": thread_id,
                        "decided_at": decided_at
                    })
                    
                # Небольшая пауза чтобы не перегружать API
                await asyncio.sleep(0.5)
                
            except Exception as e:
                errors.append({"ign": ign, "error": str(e)})
        
        # Обновляем embed с результатами
        embed.description = f"✅ Проверка завершена! Проверено: {len(rows)} игроков"
        
        # Отправляем основной embed с краткой статистикой
        embed.set_footer(text="Используйте /info Player для подробной информации о конкретном игроке")
        await interaction.followup.send(embed=embed)
        
        # Отправляем детальные результаты отдельными сообщениями
        # Игроки НЕ в гильдии (самое важное)
        if not_in_guild:
            # Разбиваем на части по 5 игроков чтобы не превысить лимит
            chunks = [not_in_guild[i:i + 5] for i in range(0, len(not_in_guild), 5)]
            
            for i, chunk in enumerate(chunks):
                chunk_embed = discord.Embed(
                    title=f"🔴 НЕ в гильдии - Часть {i + 1}/{len(chunks)}" if len(chunks) > 1 else f"🔴 НЕ в гильдии ({len(not_in_guild)})",
                    color=discord.Color.red()
                )
                
                for player in chunk:
                    thread_url = f"https://discord.com/channels/{interaction.guild_id}/{player['thread_id']}"
                    chunk_embed.add_field(
                        name=f"👤 {player['ign']}",
                        value=f"**Пользователь:** <@{player['author_id']}>\n"
                              f"**Текущая гильдия:** {player['current_guild']}\n"
                              f"**Принят:** {player['decided_at'][:10] if player['decided_at'] else 'Неизвестно'}\n"
                              f"[📋 Заявка]({thread_url})",
                        inline=True
                    )
                
                await interaction.followup.send(embed=chunk_embed)
                await asyncio.sleep(0.5)  # Пауза между сообщениями
        
        # Игроки В гильдии
        if in_guild:
            # Разбиваем на части по 6 игроков
            chunks = [in_guild[i:i + 6] for i in range(0, len(in_guild), 6)]
            
            for i, chunk in enumerate(chunks):
                chunk_embed = discord.Embed(
                    title=f"✅ В гильдии - Часть {i + 1}/{len(chunks)}" if len(chunks) > 1 else f"✅ В гильдии ({len(in_guild)})",
                    color=discord.Color.green()
                )
                
                for player in chunk:
                    thread_url = f"https://discord.com/channels/{interaction.guild_id}/{player['thread_id']}"
                    chunk_embed.add_field(
                        name=f"👤 {player['ign']}",
                        value=f"**Пользователь:** <@{player['author_id']}>\n"
                              f"**Гильдия:** {player['guild']}\n"
                              f"**Принят:** {player['decided_at'][:10] if player['decided_at'] else 'Неизвестно'}\n"
                              f"[📋 Заявка]({thread_url})",
                        inline=True
                    )
                
                await interaction.followup.send(embed=chunk_embed)
                await asyncio.sleep(0.5)  # Пауза между сообщениями
        
        # Не найдены в Albion
        if not_found:
            # Разбиваем на части по 8 игроков
            chunks = [not_found[i:i + 8] for i in range(0, len(not_found), 8)]
            
            for i, chunk in enumerate(chunks):
                chunk_embed = discord.Embed(
                    title=f"❓ Не найдены в Albion - Часть {i + 1}/{len(chunks)}" if len(chunks) > 1 else f"❓ Не найдены в Albion ({len(not_found)})",
                    color=discord.Color.orange()
                )
                
                chunk_text = ""
                for player in chunk:
                    thread_url = f"https://discord.com/channels/{interaction.guild_id}/{player['thread_id']}"
                    chunk_text += f"• **{player['ign']}** (<@{player['author_id']}>) - [Заявка]({thread_url})\n"
                
                chunk_embed.description = chunk_text
                await interaction.followup.send(embed=chunk_embed)
                await asyncio.sleep(0.5)  # Пауза между сообщениями
        
        # Ошибки
        if errors:
            error_embed = discord.Embed(
                title=f"⚠️ Ошибки при проверке ({len(errors)})",
                color=discord.Color.orange()
            )
            
            error_text = ""
            for error in errors[:10]:  # Показываем первые 10 ошибок
                error_text += f"• **{error['ign']}**: {error['error'][:50]}...\n"
            
            if len(errors) > 10:
                error_text += f"\n... и ещё {len(errors) - 10} ошибок"
            
            error_embed.description = error_text
            await interaction.followup.send(embed=error_embed)
    @app_commands.command(name="albion", description="Получить полную статистику игрока Albion Online (Europe сервер)")
    async def albion_stats(self, interaction: discord.Interaction, player_name: str):
        await interaction.response.defer(ephemeral=True)
        
        # Очищаем никнейм от Guild и Potatos
        clean_player_name = player_name.replace("Guild", "").replace("potatos", "").replace("Potatos", "").strip()
        
        # Поиск игроков с возможностью выбора
        players = await search_albion_player_with_options(clean_player_name)
        
        if not players:
            await interaction.followup.send(f"❌ Игроки с именем '{clean_player_name}' не найдены на Europe сервере.")
            return
        
        # Если найден только один игрок - показываем его статистику сразу
        if len(players) == 1:
            player_data = players[0]
        else:
            # Если найдено несколько игроков - показываем меню выбора
            embed = discord.Embed(
                title="🎮 Найдено несколько игроков",
                description=f"**Поиск:** {clean_player_name}\n**🌍 Сервер:** Europe\n\n"
                           f"Найдено **{len(players)}** игроков с похожими именами. Выберите нужного:",
                color=discord.Color.blue()
            )
            
            view = PlayerSelectView(players, interaction.user, clean_player_name)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            return
            
        player_data = players[0]
        player_id = player_data["Id"]
        player_name_found = player_data["Name"]
        
        # Получаем детальную статистику
        stats = await get_albion_player_stats(player_id)
        kills = await get_albion_player_kills(player_id, 10)
        deaths = await get_albion_player_deaths(player_id, 10)
        
        if not stats:
            await interaction.followup.send(f"❌ Не удалось получить детальную статистику для '{player_name_found}'.")
            return
        
        # Основная информация
        guild_name = stats.get("GuildName", "")
        alliance_name = stats.get("AllianceName", "")
        kill_fame = stats.get("KillFame", 0)
        death_fame = stats.get("DeathFame", 0)
        fame_ratio = stats.get("FameRatio", 0)
        
        # LifetimeStatistics
        lifetime_stats = stats.get("LifetimeStatistics", {})
        
        # Создаем основной embed
        embed = discord.Embed(
            title=f"📊 Полная статистика Albion Online",
            description=f"**Игрок:** {player_name_found}\n**🔍 Поиск по:** {clean_player_name}\n**🌍 Сервер:** Europe",
            color=discord.Color.gold()
        )
        
        # Гильдия и альянс
        embed.add_field(
            name="🏰 Гильдия", 
            value=guild_name if guild_name else "❌ Нет гильдии", 
            inline=True
        )
        embed.add_field(
            name="⚔️ Альянс", 
            value=alliance_name if alliance_name else "❌ Нет альянса", 
            inline=True
        )
        embed.add_field(name="🆔 ID", value=f"`{player_id}`", inline=True)
        
        # Расчет общего опыта
        total_pve_fame = lifetime_stats.get("PvE", {}).get("Total", 0) if lifetime_stats else 0
        total_gathering_fame = lifetime_stats.get("Gathering", {}).get("All", {}).get("Total", 0) if lifetime_stats else 0
        total_crafting_fame = lifetime_stats.get("Crafting", {}).get("Total", 0) if lifetime_stats else 0
        total_fame = kill_fame + total_pve_fame + total_gathering_fame + total_crafting_fame
        
        # Общий опыт
        embed.add_field(
            name="🌟 Общий опыт",
            value=f"📊 **Всего Fame:** {total_fame:,}\n"
                  f"⚔️ **PvP:** {kill_fame:,}\n"
                  f"🏆 **PvE:** {total_pve_fame:,}\n"
                  f"⛏️ **Сбор:** {total_gathering_fame:,}\n"
                  f"🔨 **Крафт:** {total_crafting_fame:,}",
            inline=False
        )
        
        # PvP статистика
        embed.add_field(
            name="⚔️ PvP Статистика",
            value=f"💰 **Kill Fame:** {kill_fame:,}\n"
                  f"💀 **Death Fame:** {death_fame:,}\n"
                  f"� **Fame Ratio:** {fame_ratio:.2f}\n"
                  f"🗡️ **Убийств:** {len(kills)}\n"
                  f"⚰️ **Смертей:** {len(deaths)}",
            inline=False
        )
        
        # PvE статистика из LifetimeStatistics
        if lifetime_stats and lifetime_stats.get("PvE"):
            pve_stats = lifetime_stats["PvE"]
            embed.add_field(
                name="🏆 PvE Статистика",
                value=f"🌟 **Общий PvE:** {pve_stats.get('Total', 0):,}\n"
                      f"👑 **Royal:** {pve_stats.get('Royal', 0):,}\n"
                      f"🌍 **Outlands:** {pve_stats.get('Outlands', 0):,}\n"
                      f"✨ **Avalon:** {pve_stats.get('Avalon', 0):,}\n"
                      f"🔥 **Hellgate:** {pve_stats.get('Hellgate', 0):,}\n"
                      f"🌀 **Corrupted:** {pve_stats.get('CorruptedDungeon', 0):,}\n"
                      f"🌫️ **Mists:** {pve_stats.get('Mists', 0):,}",
                inline=True
            )
        
        # Сбор ресурсов из LifetimeStatistics
        if lifetime_stats and lifetime_stats.get("Gathering"):
            gathering = lifetime_stats["Gathering"]
            total_gathered = gathering.get("All", {}).get("Total", 0)
            embed.add_field(
                name="⛏️ Сбор ресурсов",
                value=f"📦 **Всего собрано:** {total_gathered:,}\n"
                      f"🌿 **Fiber:** {gathering.get('Fiber', {}).get('Total', 0):,}\n"
                      f"🐻 **Hide:** {gathering.get('Hide', {}).get('Total', 0):,}\n"
                      f"⛏️ **Ore:** {gathering.get('Ore', {}).get('Total', 0):,}\n"
                      f"🪨 **Rock:** {gathering.get('Rock', {}).get('Total', 0):,}\n"
                      f"🪵 **Wood:** {gathering.get('Wood', {}).get('Total', 0):,}",
                inline=True
            )
        
        # Крафт и дополнительные навыки
        crafting_fame = lifetime_stats.get("Crafting", {}).get("Total", 0) if lifetime_stats else 0
        fishing_fame = lifetime_stats.get("FishingFame", 0) if lifetime_stats else 0
        farming_fame = lifetime_stats.get("FarmingFame", 0) if lifetime_stats else 0
        
        if crafting_fame > 0 or fishing_fame > 0 or farming_fame > 0:
            embed.add_field(
                name="� Дополнительные навыки",
                value=f"🔨 **Крафт:** {crafting_fame:,}\n"
                      f"🎣 **Рыбалка:** {fishing_fame:,}\n"
                      f"🌾 **Фермерство:** {farming_fame:,}",
                inline=True
            )
        
        # Последние PvP события
        if kills or deaths:
            pvp_events = []
            
            # Добавляем убийства
            for kill in kills[:3]:
                victim = kill.get("Victim", {}).get("Name", "Неизвестно")
                timestamp = format_timestamp(kill.get("TimeStamp", ""))
                pvp_events.append(f"🗡️ Убил {victim} {timestamp}")
            
            # Добавляем смерти
            for death in deaths[:3]:
                killer = death.get("Killer", {}).get("Name", "Неизвестно")
                timestamp = format_timestamp(death.get("TimeStamp", ""))
                pvp_events.append(f"💀 Убит {killer} {timestamp}")
            
            # Сортируем по времени (новые сверху)
            pvp_events.sort(key=lambda x: x, reverse=True)
            
            embed.add_field(
                name="🎯 Последние PvP события",
                value="\n".join(pvp_events[:5]) or "Нет данных",
                inline=False
            )
        
        # Оценка игрока на основе общего опыта
        if total_fame > 1000000000:  # 1B+ общего опыта
            rating = "🌟 **Легенда** - невероятно высокий общий опыт"
        elif total_fame > 500000000:  # 500M+ общего опыта
            rating = "🔥 **Топ игрок** - очень высокий общий опыт"
        elif total_fame > 200000000:  # 200M+ общего опыта
            rating = "🟢 **Опытный игрок** - высокий общий опыт"
        elif total_fame > 50000000:  # 50M+ общего опыта
            rating = "🟡 **Средний игрок** - умеренный общий опыт"
        elif total_fame > 10000000:  # 10M+ общего опыта
            rating = "🟠 **Начинающий игрок** - низкий общий опыт"
        else:
            rating = "🔴 **Новичок** - очень низкий общий опыт"
        
        embed.add_field(name="📈 Оценка", value=rating, inline=False)
        
        # Ссылки на профили с фокусом на Europe
        official_profile_url = f"https://albiononline.com/en/killboard/player/{player_id}"
        detailed_profile_url = f"https://albiononlinetools.com/player/player-search.php?playerID={player_id}&sv=europe"
        
        embed.add_field(
            name="🔗 Внешние профили", 
            value=f"[📋 Официальный killboard]({official_profile_url})\n[📈 Детальная статистика (Europe)]({detailed_profile_url})", 
            inline=False
        )
        
        # Информация об обновлении данных
        timestamp = lifetime_stats.get("Timestamp") if lifetime_stats else None
        if timestamp:
            last_update = format_timestamp(timestamp)
            embed.set_footer(text=f"🌍 Сервер: Europe | Данные обновлены: {last_update}")
        else:
            embed.set_footer(text="🌍 Сервер: Europe | Данные предоставлены Albion Online API")
        
        await interaction.followup.send(embed=embed)

    # ── /albion_search ──────────────────────────────────────────────────────────────
    @app_commands.command(name="albion_search", description="Найти всех игроков Albion Online по части имени (Europe сервер)")
    async def albion_search(self, interaction: discord.Interaction, search_term: str):
        await interaction.response.defer(ephemeral=True)
        
        # Очищаем поисковый термин от Guild и Potatos
        clean_search_term = search_term.replace("Guild", "").replace("potatos", "").replace("Potatos", "").strip()
        
        # Поиск всех совпадающих игроков с сортировкой
        players = await search_albion_player_with_options(clean_search_term)
        
        if not players:
            await interaction.followup.send(f"❌ Игроки с именем содержащим '{clean_search_term}' не найдены на Europe сервере.")
            return
        
        # Если найдено более 1 игрока - показываем меню выбора
        if len(players) > 1:
            embed = discord.Embed(
                title="🔍 Результаты поиска игроков",
                description=f"**Поиск:** {clean_search_term}\n**🌍 Сервер:** Europe\n\n"
                           f"Найдено **{len(players)}** игроков. Выберите нужного для получения полной статистики:",
                color=discord.Color.blue()
            )
            
            view = PlayerSelectView(players, interaction.user, clean_search_term)
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        else:
            # Если найден только один игрок - показываем краткую информацию
            player = players[0]
            name = player["Name"]
            guild = player.get("GuildName") or "Нет гильдии"
            alliance = player.get("AllianceName") or "Нет альянса"
            kill_fame = player.get("KillFame") or 0
            death_fame = player.get("DeathFame") or 0
            player_id = player["Id"]
            
            embed = discord.Embed(
                title=f"🎮 Найден игрок: {name}",
                description=f"**🔍 Поиск по:** {clean_search_term}\n**🌍 Сервер:** Europe",
                color=discord.Color.green()
            )
            
            embed.add_field(name="🏰 Гильдия", value=guild, inline=True)
            embed.add_field(name="⚔️ Альянс", value=alliance, inline=True)
            embed.add_field(name="🆔 ID", value=f"`{player_id}`", inline=True)
            
            embed.add_field(name="💰 Kill Fame", value=f"{kill_fame:,}", inline=True)
            embed.add_field(name="💀 Death Fame", value=f"{death_fame:,}", inline=True)
            embed.add_field(name="📊 Ratio", value=f"{(kill_fame / max(death_fame, 1)):.2f}", inline=True)
            
            # Ссылка на детальную статистику
            tools_url = f"https://albiononlinetools.com/player/player-search.php?playerID={player_id}&sv=europe"
            embed.add_field(name="� Детальная статистика", value=f"[📈 Europe сервер]({tools_url})", inline=False)
            
            embed.set_footer(text="🌍 Сервер: Europe | Используйте /albion для полной статистики")
            
            await interaction.followup.send(embed=embed)

    # ── Внутренний метод для загрузки конфигурации ───────────────────────────
    async def _get_cfg(self, guild_id: int):
        # Гарантируем наличие схемы
        try:
            await init_db()
        except Exception:
            pass
        async with aiosqlite.connect(DB_PATH) as db:
            cur = await db.execute(
                """
                SELECT default_role, recruit_role, recruiter_roles, forum_id, apply_channel_id, guild_name, cooldown_hours
                  FROM guild_config
                 WHERE guild_id = ?
                """,
                (guild_id,),
            )
            row = await cur.fetchone()
            await cur.close()

        if not row:
            # Фоллбэк к unified_settings
            try:
                rs = _unified_settings.get_recruit_settings(guild_id) if _unified_settings else None
            except Exception:
                rs = None
            if rs:
                return {
                    "default_role": rs.get("default_role"),
                    "recruit_role": rs.get("recruit_role"),
                    "recruiter_roles": rs.get("recruiter_roles"),
                    "forum_id": rs.get("forum_channel"),
                    "apply_channel_id": rs.get("recruit_panel_channel") or rs.get("apply_channel_id"),
                    "guild_name": rs.get("guild_name", ""),
                    "cooldown_hours": rs.get("cooldown_hours", 1),
                }
            return None

        default_role, recruit_role, recruiter_roles_csv, forum_id, apply_channel_id, guild_name, cooldown_hours = row
        return {
            "default_role": default_role,
            "recruit_role": recruit_role,
            "recruiter_roles": comma_split(recruiter_roles_csv),
            "forum_id": forum_id,
            "apply_channel_id": apply_channel_id,
            "guild_name": guild_name,
            "cooldown_hours": cooldown_hours if cooldown_hours is not None else 1,  # По умолчанию 1 час только если NULL
        }


# ─── Select Menu для выбора игрока ─────────────────────────────────────────────
class PlayerSelectMenu(ui.Select):
    def __init__(self, players: list, interaction_user, original_search_term: str):
        self.original_search_term = original_search_term
        self.interaction_user = interaction_user
        
        options = []
        for i, player in enumerate(players[:25]):  # Discord ограничивает до 25 опций
            name = player["Name"]
            guild = player.get("GuildName") or "Нет гильдии"
            alliance = player.get("AllianceName") or "Нет альянса"
            kill_fame = player.get("KillFame", 0)
            
            # Создаём описание для опции
            description = f"🏰 {guild[:30]} | 💰 {kill_fame:,} Kill Fame"
            if len(description) > 100:
                description = description[:97] + "..."
                
            options.append(discord.SelectOption(
                label=name,
                description=description,
                value=str(i),
                emoji="🎮"
            ))
        
        super().__init__(
            placeholder="🌍 Выберите игрока (Europe сервер)...",
            options=options,
            custom_id="player_select"
        )
        self.players = players

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.interaction_user.id:
            await interaction.response.send_message("❌ Только автор команды может выбирать игрока.", ephemeral=True)
            return
            
        selected_index = int(self.values[0])
        selected_player = self.players[selected_index]
        
        logger.info(f"Пользователь {interaction.user} выбрал игрока: {selected_player['Name']} (индекс {selected_index})")
        
        # Отправляем полную статистику выбранного игрока
        await self.send_full_player_stats(interaction, selected_player)

    async def send_full_player_stats(self, interaction: discord.Interaction, player_data: dict):
        await interaction.response.defer()
        
        player_id = player_data["Id"]
        player_name_found = player_data["Name"]
        
        # Получаем детальную статистику
        stats = await get_albion_player_stats(player_id)
        kills = await get_albion_player_kills(player_id, 10)
        deaths = await get_albion_player_deaths(player_id, 10)
        
        if not stats:
            await interaction.followup.send(f"❌ Не удалось получить детальную статистику для '{player_name_found}'.")
            return
        
        # Основная информация
        guild_name = stats.get("GuildName", "")
        alliance_name = stats.get("AllianceName", "")
        kill_fame = stats.get("KillFame", 0)
        death_fame = stats.get("DeathFame", 0)
        fame_ratio = stats.get("FameRatio", 0)
        
        # LifetimeStatistics
        lifetime_stats = stats.get("LifetimeStatistics", {})
        
        # Создаем основной embed
        embed = discord.Embed(
            title=f"📊 Полная статистика Albion Online",
            description=f"**Игрок:** {player_name_found}\n**🔍 Поиск по:** {self.original_search_term}\n**🌍 Сервер:** Europe",
            color=discord.Color.gold()
        )
        
        # Гильдия и альянс
        embed.add_field(
            name="🏰 Гильдия", 
            value=guild_name if guild_name else "❌ Нет гильдии", 
            inline=True
        )
        embed.add_field(
            name="⚔️ Альянс", 
            value=alliance_name if alliance_name else "❌ Нет альянса", 
            inline=True
        )
        embed.add_field(name="🆔 ID", value=f"`{player_id}`", inline=True)
        
        # Расчет общего опыта
        total_pve_fame = lifetime_stats.get("PvE", {}).get("Total", 0) if lifetime_stats else 0
        total_gathering_fame = lifetime_stats.get("Gathering", {}).get("All", {}).get("Total", 0) if lifetime_stats else 0
        total_crafting_fame = lifetime_stats.get("Crafting", {}).get("Total", 0) if lifetime_stats else 0
        total_fame = kill_fame + total_pve_fame + total_gathering_fame + total_crafting_fame
        
        # Общий опыт
        embed.add_field(
            name="🌟 Общий опыт",
            value=f"📊 **Всего Fame:** {total_fame:,}\n"
                  f"⚔️ **PvP:** {kill_fame:,}\n"
                  f"🏆 **PvE:** {total_pve_fame:,}\n"
                  f"⛏️ **Сбор:** {total_gathering_fame:,}\n"
                  f"🔨 **Крафт:** {total_crafting_fame:,}",
            inline=False
        )
        
        # Общий опыт
        embed.add_field(
            name="🌟 Общий опыт",
            value=f"📊 **Всего Fame:** {total_fame:,}\n"
                  f"⚔️ **PvP:** {kill_fame:,}\n"
                  f"🏆 **PvE:** {total_pve_fame:,}\n"
                  f"⛏️ **Сбор:** {total_gathering_fame:,}\n"
                  f"🔨 **Крафт:** {total_crafting_fame:,}",
            inline=False
        )
        
        # PvP статистика
        embed.add_field(
            name="⚔️ PvP Статистика",
            value=f"💰 **Kill Fame:** {kill_fame:,}\n"
                  f"💀 **Death Fame:** {death_fame:,}\n"
                  f"📊 **Fame Ratio:** {fame_ratio:.2f}\n"
                  f"🗡️ **Убийств:** {len(kills)}\n"
                  f"⚰️ **Смертей:** {len(deaths)}",
            inline=False
        )
        
        # PvE статистика из LifetimeStatistics
        if lifetime_stats and lifetime_stats.get("PvE"):
            pve_stats = lifetime_stats["PvE"]
            embed.add_field(
                name="🏆 PvE Статистика",
                value=f"🌟 **Общий PvE:** {pve_stats.get('Total', 0):,}\n"
                      f"👑 **Royal:** {pve_stats.get('Royal', 0):,}\n"
                      f"🌍 **Outlands:** {pve_stats.get('Outlands', 0):,}\n"
                      f"✨ **Avalon:** {pve_stats.get('Avalon', 0):,}\n"
                      f"🔥 **Hellgate:** {pve_stats.get('Hellgate', 0):,}\n"
                      f"🌀 **Corrupted:** {pve_stats.get('CorruptedDungeon', 0):,}\n"
                      f"🌫️ **Mists:** {pve_stats.get('Mists', 0):,}",
                inline=True
            )
        
        # Сбор ресурсов из LifetimeStatistics
        if lifetime_stats and lifetime_stats.get("Gathering"):
            gathering = lifetime_stats["Gathering"]
            total_gathered = gathering.get("All", {}).get("Total", 0)
            embed.add_field(
                name="⛏️ Сбор ресурсов",
                value=f"📦 **Всего собрано:** {total_gathered:,}\n"
                      f"🌿 **Fiber:** {gathering.get('Fiber', {}).get('Total', 0):,}\n"
                      f"🐻 **Hide:** {gathering.get('Hide', {}).get('Total', 0):,}\n"
                      f"⛏️ **Ore:** {gathering.get('Ore', {}).get('Total', 0):,}\n"
                      f"🪨 **Rock:** {gathering.get('Rock', {}).get('Total', 0):,}\n"
                      f"🪵 **Wood:** {gathering.get('Wood', {}).get('Total', 0):,}",
                inline=True
            )
        
        # Крафт и дополнительные навыки
        crafting_fame = lifetime_stats.get("Crafting", {}).get("Total", 0) if lifetime_stats else 0
        fishing_fame = lifetime_stats.get("FishingFame", 0) if lifetime_stats else 0
        farming_fame = lifetime_stats.get("FarmingFame", 0) if lifetime_stats else 0
        
        if crafting_fame > 0 or fishing_fame > 0 or farming_fame > 0:
            embed.add_field(
                name="🔨 Дополнительные навыки",
                value=f"🔨 **Крафт:** {crafting_fame:,}\n"
                      f"🎣 **Рыбалка:** {fishing_fame:,}\n"
                      f"🌾 **Фермерство:** {farming_fame:,}",
                inline=True
            )
        
        # Последние PvP события
        if kills or deaths:
            pvp_events = []
            
            # Добавляем убийства
            for kill in kills[:3]:
                victim = kill.get("Victim", {}).get("Name", "Неизвестно")
                timestamp = format_timestamp(kill.get("TimeStamp", ""))
                pvp_events.append(f"🗡️ Убил {victim} {timestamp}")
            
            # Добавляем смерти
            for death in deaths[:3]:
                killer = death.get("Killer", {}).get("Name", "Неизвестно")
                timestamp = format_timestamp(death.get("TimeStamp", ""))
                pvp_events.append(f"💀 Убит {killer} {timestamp}")
            
            # Сортируем по времени (новые сверху)
            pvp_events.sort(key=lambda x: x, reverse=True)
            
            embed.add_field(
                name="🎯 Последние PvP события",
                value="\n".join(pvp_events[:5]) or "Нет данных",
                inline=False
            )
        
        # Оценка игрока на основе общего опыта
        if total_fame > 1000000000:  # 1B+ общего опыта
            rating = "🌟 **Легенда** - невероятно высокий общий опыт"
        elif total_fame > 500000000:  # 500M+ общего опыта
            rating = "🔥 **Топ игрок** - очень высокий общий опыт"
        elif total_fame > 200000000:  # 200M+ общего опыта
            rating = "🟢 **Опытный игрок** - высокий общий опыт"
        elif total_fame > 50000000:  # 50M+ общего опыта
            rating = "🟡 **Средний игрок** - умеренный общий опыт"
        elif total_fame > 10000000:  # 10M+ общего опыта
            rating = "🟠 **Начинающий игрок** - низкий общий опыт"
        else:
            rating = "🔴 **Новичок** - очень низкий общий опыт"
        
        embed.add_field(name="📈 Оценка", value=rating, inline=False)
        
        # Ссылки на профили с фокусом на Europe
        official_profile_url = f"https://albiononline.com/en/killboard/player/{player_id}"
        detailed_profile_url = f"https://albiononlinetools.com/player/player-search.php?playerID={player_id}&sv=europe"
        
        embed.add_field(
            name="🔗 Внешние профили", 
            value=f"[📋 Официальный killboard]({official_profile_url})\n[📈 Детальная статистика (Europe)]({detailed_profile_url})", 
            inline=False
        )
        
        # Информация об обновлении данных
        timestamp = lifetime_stats.get("Timestamp") if lifetime_stats else None
        if timestamp:
            last_update = format_timestamp(timestamp)
            embed.set_footer(text=f"🌍 Сервер: Europe | Данные обновлены: {last_update}")
        else:
            embed.set_footer(text="🌍 Сервер: Europe | Данные предоставлены Albion Online API")
        
        await interaction.followup.send(embed=embed)

class PlayerSelectView(ui.View):
    def __init__(self, players: list, interaction_user, original_search_term: str):
        super().__init__(timeout=300)  # 5 минут на выбор
        self.add_item(PlayerSelectMenu(players, interaction_user, original_search_term))

# ─── Основной бот ─────────────────────────────────────────────────────────────
class RecruitBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=INTENTS)

    async def setup_hook(self):
        """Вызывается при запуске бота"""
        global persistent_view, persistent_event_view, persistent_points_view, unified_event_view
        
        # Инициализируем базу данных
        await init_db()
        
        # Создаём персистентные View и регистрируем их
        global persistent_view, persistent_event_view, persistent_points_view, unified_event_view
        
        persistent_view = PersistentApplyButtonView(self)
        persistent_event_view = PersistentEventSubmitView()
        persistent_points_view = PersistentPointsRequestView(self)
        unified_event_view = UnifiedEventView()
        
        self.add_view(persistent_view)
        self.add_view(persistent_event_view)
        self.add_view(persistent_points_view)
        self.add_view(unified_event_view)
        
        # Добавляем Cog
        await self.add_cog(RecruitCog(self))
        
        logger.info("Бот настроен и готов к работе")

    async def on_ready(self):
        logger.info(f"✅ Бот {self.user} подключен к Discord!")
        logger.info(f"🌍 Сервера: {len(self.guilds)}")
        
        # Синхронизируем slash команды
        try:
            synced = await self.tree.sync()
            logger.info(f"🔄 Синхронизировано {len(synced)} slash команд")
        except Exception as e:
            logger.error(f"❌ Ошибка синхронизации команд: {e}")
    
    async def on_message(self, message: discord.Message):
        """Обрабатывает сообщения для интерактивных заявок"""
        # Добавляем подробное логирование всех сообщений
        logger.info(f"[ON_MESSAGE ENTRY] author={message.author.id} bot={message.author.bot} channel_type={type(message.channel).__name__} channel_id={message.channel.id} content='{message.content[:100]}'")
        
        # Игнорируем сообщения от ботов
        if message.author.bot:
            logger.debug(f"[ON_MESSAGE] Игнорируем сообщение от бота {message.author.id}")
            return

        try:
            from .submission_state import active_submissions as _as
            logger.info(f"[ON_MESSAGE] active_submissions size={len(_as)} keys={list(_as.keys())[:3]}")
        except Exception as e:
            logger.error(f"[ON_MESSAGE] Ошибка импорта active_submissions: {e}")
            pass
        
        # Игнорируем команды
        if message.content.startswith(self.command_prefix):
            logger.debug(f"[ON_MESSAGE] Игнорируем команду: {message.content[:50]}")
            return
        
        # Обрабатываем интерактивные заявки СНАЧАЛА
        try:
            from .ui_components import handle_submission_message
            handled = await handle_submission_message(message)
            logger.info(f"[ON_MESSAGE] handle_submission_message returned: {handled} for message: '{message.content[:50]}'")
            if handled:
                return
        except Exception as e:
            logger.error(f"Ошибка обработки интерактивной заявки: {e}")
        
        # Добавляем дебаг логи для тредов (после основного обработчика)
        if isinstance(message.channel, discord.Thread):
            logger.info(f"[THREAD MSG] thread={message.channel.id} author={message.author.id} content='{message.content[:80]}'")
            # Аварийный прямой парс участников если основной обработчик по какой-то причине не срабатывает
            try:
                from .submission_state import active_submissions
                session_key = f"{message.author.id}_{message.channel.id}"
                session = active_submissions.get(session_key)
                logger.info(f"[FALLBACK CHECK] session_key={session_key} found_session={session is not None}")
                if session and getattr(session, 'state', None) == 'waiting_participants':
                    from .ui_components import handle_participants_message, parse_participants_from_message
                    # Если пользователь ничего не ввёл осмысленного (пусто), пропускаем
                    if message.content.strip() or message.mentions:
                        # Пробуем разобрать участников (даже если нет ключевых слов)
                        logger.warning(f"[FALLBACK PARTICIPANTS] Пробуем fallback для {session_key}")
                        ok = await handle_participants_message(message, session)
                        if ok:
                            logger.warning(f"[FALLBACK PARTICIPANTS] Auto-handled first message for {session_key}")
                            return
            except Exception as e:
                logger.error(f"[FALLBACK PARTICIPANTS ERROR] {e}")
        
        # Реагируем на изображения ТОЛЬКО в тредах активных заявок на очки
        if isinstance(message.channel, discord.Thread):
            try:
                from .submission_state import active_submissions
                session_key = f"{message.author.id}_{message.channel.id}"
                logger.info(f"[THREAD IMG CHECK] primary_key={session_key} active={len(active_submissions)}")

                # Fallback: если нет точной записи, пробуем найти по parent_id (каналу) — вдруг session сохранена до создания треда
                session = active_submissions.get(session_key)
                if not session and message.channel.parent_id:
                    parent_key = f"{message.author.id}_{message.channel.parent_id}"
                    if parent_key in active_submissions:
                        # Мигрируем сессию на thread.id
                        session = active_submissions[parent_key]
                        logger.info(f"[THREAD MIGRATE] Перенос сессии {parent_key} -> {session_key}")
                        # Обновляем channel_id внутри сессии
                        try:
                            session.channel_id = message.channel.id
                        except Exception:
                            pass
                        active_submissions[session_key] = session
                        del active_submissions[parent_key]

                if session_key in active_submissions:
                    has_attachment = bool(message.attachments)
                    content_lower = message.content.lower()
                    has_image_url = any(token in content_lower for token in ['http://i.imgur.com', 'https://i.imgur.com', 'http://imgur.com', 'https://imgur.com', '.png', '.jpg', '.jpeg', '.gif', '.webp'])
                    if has_attachment or has_image_url:
                        try:
                            await message.add_reaction("✅")
                            await message.reply("✅ Скриншот получен. Напишите 'отправить' чтобы завершить заявку", mention_author=False)
                            return
                        except Exception as e:
                            logger.error(f"Ошибка при автоподтверждении скриншота в треде {message.channel.id}: {e}")
                else:
                    logger.debug(f"Пропуск треда {message.channel.id}: нет активной сессии для пользователя {message.author.id}")
            except Exception as e:
                logger.error(f"Ошибка проверки активной сессии: {e}")

        # Проверяем, если это тред заявки без активной сессии (потерянная сессия после перезапуска)
        if isinstance(message.channel, discord.Thread):
            # Проверяем, является ли это тредом заявки (по названию)
            if any(keyword in message.channel.name.lower() for keyword in ['заявка', 'событие', 'кристал', 'убийство', 'ганк']):
                # Проверяем, что пользователь пытается что-то написать об участниках
                content_lower = message.content.lower()
                if any(word in content_lower for word in ['@', 'только я', 'участник', 'один']):
                    logger.info(f"Обнаружена потерянная сессия в треде {message.channel.id} для пользователя {message.author.id}")
                    embed = discord.Embed(
                        title="⚠️ Сессия потеряна",
                        description="Ваша сессия подачи заявки была сброшена из-за перезапуска бота.",
                        color=discord.Color.orange()
                    )
                    embed.add_field(
                        name="🔄 Что делать?",
                        value="Пожалуйста, начните заявку заново через команду `/events_panel` в основном канале.",
                        inline=False
                    )
                    await message.channel.send(embed=embed)
                    return

        # Обрабатываем обычные команды
        await self.process_commands(message)# ─── Запуск бота ──────────────────────────────────────────────────────────────
async def main():
    bot = RecruitBot()
    
    try:
        await bot.start(TOKEN)
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
    finally:
        await bot.close()

if __name__ == "__main__":
    asyncio.run(main())
