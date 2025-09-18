"""
UI компоненты для системы событий
"""

from datetime import datetime
import logging
import re
from typing import List, Optional

import aiosqlite
import discord
from discord import ui

from .database import EventDatabase
from .events import (
    SHOP_ITEMS,
    EventAction,
    EventManager,
    EventSubmission,
    EventType,
    ShopManager,
    get_multiplier_options,
)

logger = logging.getLogger("potatos_recruit.ui_components")

async def check_points_date_restrictions(guild_id: int) -> tuple[bool, str]:
    """
    Проверяет, можно ли начислять очки в данный момент на основе настроенных дат.
    Возвращает (можно_начислять, сообщение_об_ошибке)
    """
    config = await EventDatabase.get_guild_config(guild_id)
    if not config:
        return True, ""
    
    start_date = config.get('points_start_date')
    end_date = config.get('points_end_date')
    
    # Если даты не настроены, разрешаем начисление
    if not start_date and not end_date:
        return True, ""
    
    today = datetime.now().strftime('%Y-%m-%d')
    
    # Проверяем начальную дату
    if start_date and today < start_date:
        return False, f"❌ Начисление очков недоступно до {start_date}"
    
    # Проверяем конечную дату
    if end_date and today > end_date:
        return False, f"❌ Период начисления очков закончился {end_date}"
    
    return True, ""

# Глобальный словарь для отслеживания активных сессий заявок
from .submission_state import active_submissions

class InteractiveSubmissionSession:
    """Класс для отслеживания интерактивной сессии подачи заявки"""
    
    def __init__(self, user_id: int, channel_id: int, event_type: EventType, action: EventAction):
        self.user_id = user_id
        self.channel_id = channel_id
        self.event_type = event_type
        self.action = action
        self.participants = [user_id]  # Начинаем с пользователя, который подал заявку
        self.screenshot_url = None
        self.description = None
        self.state = "waiting_participants"  # waiting_participants -> waiting_screenshot -> completed
        self.last_message_id = None
        self.original_message_id = None  # ID исходного сообщения с embed
        self.original_channel_id = None  # ID канала исходного сообщения
        self.temp_id = None  # Временный ID для поиска сообщения


def build_participants_embed(session: "InteractiveSubmissionSession") -> discord.Embed:
    """Строит (или перестраивает) embed участников для интерактивной заявки.

    Логика одна и та же при первом показе и при последующих обновлениях.
    """
    # Временная заявка только для перерасчета очков
    temp_submission = EventSubmission(
        event_type=session.event_type,
        action=session.action,
        participants=session.participants,
        submitter_id=session.user_id,
        group_size=len(session.participants)
    )

    participants_mentions = ", ".join(f"<@{uid}>" for uid in session.participants)
    if not participants_mentions:
        participants_mentions = "(нет участников)"

    embed = discord.Embed(
        title="👥 Участники заявки",
        description="Упомяните (@) дополнительных участников в треде — они будут добавлены автоматически (реакция ➕).",
        color=discord.Color.orange()
    )
    embed.add_field(
        name="✅ Текущие участники",
        value=participants_mentions,
        inline=False
    )
    embed.add_field(
        name="🎮 Событие",
        value=temp_submission.get_event_display_name(),
        inline=True
    )
    embed.add_field(
        name="📊 Базовые очки",
        value=EventManager.format_points_display(temp_submission.calculate_base_points()),
        inline=True
    )
    embed.set_footer(text="Добавьте всех участников перед подтверждением. Скриншот опционален.")
    return embed

async def find_message_by_footer(guild: discord.Guild, footer_text: str) -> tuple[discord.Message, discord.TextChannel]:
    """Ищет сообщение по тексту в footer embed"""
    try:
        logger.info(f"=== Ищем сообщение по footer: '{footer_text}' в гильдии {guild.name} ===")
        
        # Ищем в обычных каналах
        logger.info(f"Проверяем {len(guild.text_channels)} обычных каналов...")
        for channel in guild.text_channels:
            try:
                logger.debug(f"Проверяем канал: {channel.name}")
                async for message in channel.history(limit=100):
                    if message.embeds:
                        for embed in message.embeds:
                            if embed.footer and footer_text in str(embed.footer.text):
                                logger.info(f"Найдено сообщение в канале {channel.name} с footer: {embed.footer.text}")
                                return message, channel
            except discord.Forbidden:
                logger.debug(f"Нет доступа к каналу {channel.name}")
                continue
            except Exception as e:
                logger.error(f"Ошибка поиска в канале {channel.name}: {e}")
                continue
        
        # Ищем в тредах
        logger.info("Ищем в тредах...")
        for channel in guild.text_channels:
            # Активные треды
            if channel.threads:
                logger.debug(f"Проверяем {len(channel.threads)} активных тредов в {channel.name}")
                for thread in channel.threads:
                    try:
                        logger.debug(f"Проверяем тред: {thread.name}")
                        async for message in thread.history(limit=50):
                            if message.embeds:
                                for embed in message.embeds:
                                    if embed.footer and footer_text in str(embed.footer.text):
                                        logger.info(f"Найдено сообщение в треде {thread.name} с footer: {embed.footer.text}")
                                        return message, thread
                    except discord.Forbidden:
                        logger.debug(f"Нет доступа к треду {thread.name}")
                        continue
                    except Exception:
                        continue
            
            # Архивированные треды
            try:
                archived_count = 0
                async for archived_thread in channel.archived_threads(limit=20):
                    archived_count += 1
                    try:
                        logger.debug(f"Проверяем архивированный тред: {archived_thread.name}")
                        async for message in archived_thread.history(limit=50):
                            if message.embeds:
                                for embed in message.embeds:
                                    if embed.footer and footer_text in str(embed.footer.text):
                                        logger.info(f"Найдено сообщение в архивированном треде {archived_thread.name} с footer: {embed.footer.text}")
                                        return message, archived_thread
                    except discord.Forbidden:
                        logger.debug(f"Нет доступа к архивированному треду {archived_thread.name}")
                        continue
                    except Exception:
                        continue
                logger.debug(f"Проверено {archived_count} архивированных тредов в {channel.name}")
            except Exception:
                continue
        
        logger.warning(f"Сообщение с footer '{footer_text}' не найдено")
        return None, None
    except Exception as e:
        logger.error(f"Ошибка в find_message_by_footer: {e}")
        return None, None

async def update_original_event_message_by_submission_id(submission_id: int, guild: discord.Guild, new_status: str, color: discord.Color = None):
    """Обновляет исходное сообщение заявки на событие с новым статусом по submission_id"""
    try:
        logger.info(f"=== Начинаем обновление сообщения для заявки {submission_id} ===")
        logger.info(f"Новый статус: {new_status}, Guild: {guild.name if guild else 'None'}")
        
        # Получаем информацию о заявке из базы данных
        submission_details = await EventDatabase.get_submission_details(submission_id)
        if not submission_details:
            logger.error(f"Заявка {submission_id} не найдена в базе данных")
            return
        
        logger.info(f"Детали заявки: {submission_details}")
        original_message_id = submission_details.get('original_message_id')
        original_channel_id = submission_details.get('original_channel_id')
        logger.info(f"original_message_id: {original_message_id}, original_channel_id: {original_channel_id}")
        
        message = None
        
        # Способ 1: Попробуем найти по сохраненным ID
        if original_message_id and original_channel_id:
            logger.info(f"Пытаемся найти канал {original_channel_id}")
            channel = guild.get_channel(original_channel_id)
            if channel:
                logger.info(f"Канал найден: {channel.name}")
                try:
                    message = await channel.fetch_message(original_message_id)
                    logger.info(f"Найдено сообщение по сохраненным ID: {original_message_id}")
                except discord.NotFound:
                    logger.warning(f"Сообщение {original_message_id} не найдено по сохраненным ID")
                except Exception as e:
                    logger.error(f"Ошибка получения сообщения по ID: {e}")
            else:
                logger.warning(f"Канал {original_channel_id} не найден в гильдии")
        
        # Способ 2: Если не найдено, ищем по footer с ID заявки
        if not message:
            logger.info(f"Ищем сообщение заявки {submission_id} по footer...")
            message, channel = await find_message_by_footer(guild, f"Заявка ID: {submission_id}")
            if message:
                logger.info(f"Найдено сообщение по footer с ID заявки: {submission_id}")
            else:
                logger.warning(f"Сообщение не найдено по footer 'Заявка ID: {submission_id}'")
        
        # Способ 3: Если все еще не найдено, ищем по временному ID (если есть активная сессия)
        if not message:
            logger.info("Ищем по активным сессиям...")
            logger.info(f"Активные сессии: {list(active_submissions.keys())}")
            for session_key, session in active_submissions.items():
                if hasattr(session, 'temp_id') and session.temp_id:
                    logger.info(f"Проверяем сессию с temp_id: {session.temp_id}")
                    temp_message, temp_channel = await find_message_by_footer(guild, session.temp_id)
                    if temp_message:
                        message = temp_message
                        channel = temp_channel
                        logger.info(f"Найдено сообщение по временному ID: {session.temp_id}")
                        break
        
        if not message:
            logger.error(f"Не удалось найти исходное сообщение для заявки {submission_id}")
            return
        
        logger.info("Сообщение найдено! Обновляем...")
        try:
            # Создаем обновленный embed
            embed = discord.Embed(
                title="📝 Заявка на событие",
                description=f"**Событие**: {submission_details['event_type']} - {submission_details['action']}",
                color=color or discord.Color.orange()
            )
            
            submitter_id = submission_details['submitter_id']
            submitter = guild.get_member(submitter_id)
            submitter_mention = submitter.mention if submitter else f"<@{submitter_id}>"
            
            embed.add_field(name="👤 Заявитель", value=submitter_mention, inline=True)
            embed.add_field(name="📊 Базовые очки", value=f"{EventManager.format_points_display(submission_details['base_points'])}", inline=True)
            embed.add_field(name="🔄 Статус", value=new_status, inline=True)
            
            # Обновляем footer с настоящим ID заявки
            embed.set_footer(text=f"Заявка ID: {submission_id}")
            
            await message.edit(embed=embed)
            logger.info(f"Обновлен статус исходного сообщения заявки {submission_id} на: {new_status}")
            
        except Exception as e:
            logger.error(f"Ошибка обновления сообщения заявки: {e}")
            
    except Exception as e:
        logger.error(f"Ошибка в update_original_event_message_by_submission_id: {e}")

async def update_original_event_message_with_id(session: InteractiveSubmissionSession, guild: discord.Guild, submission_id: int, new_status: str, color: discord.Color = None):
    """Обновляет исходное сообщение заявки на событие с новым статусом и ID заявки"""
    try:
        if not session.original_message_id or not session.original_channel_id:
            return
        
        channel = guild.get_channel(session.original_channel_id)
        if not channel:
            return
        
        try:
            message = await channel.fetch_message(session.original_message_id)
            
            # Создаем обновленный embed
            embed = discord.Embed(
                title="📝 Заявка на событие",
                description=f"**Событие**: {session.event_type.value} - {session.action.value}",
                color=color or discord.Color.orange()
            )
            
            submitter = guild.get_member(session.user_id)
            submitter_mention = submitter.mention if submitter else f"<@{session.user_id}>"
            
            # Создаем временный submission для расчета очков
            temp_submission = EventSubmission(
                event_type=session.event_type,
                action=session.action,
                participants=session.participants,
                submitter_id=session.user_id
            )
            
            embed.add_field(name="👤 Заявитель", value=submitter_mention, inline=True)
            embed.add_field(name="📊 Базовые очки", value=f"{EventManager.format_points_display(temp_submission.calculate_base_points())}", inline=True)
            embed.add_field(name="🔄 Статус", value=new_status, inline=True)
            
            # Обновляем footer с настоящим ID заявки
            embed.set_footer(text=f"Заявка ID: {submission_id}")
            
            await message.edit(embed=embed)
            logger.info(f"Обновлен статус исходного сообщения заявки {submission_id} на: {new_status}")
            
        except discord.NotFound:
            logger.warning(f"Исходное сообщение заявки не найдено: {session.original_message_id}")
        except Exception as e:
            logger.error(f"Ошибка обновления исходного сообщения заявки: {e}")
            
    except Exception as e:
        logger.error(f"Ошибка в update_original_event_message_with_id: {e}")

async def update_original_event_message(session: InteractiveSubmissionSession, guild: discord.Guild, new_status: str, color: discord.Color = None):
    """Обновляет исходное сообщение заявки на событие с новым статусом"""
    try:
        if not session.original_message_id or not session.original_channel_id:
            return
        
        channel = guild.get_channel(session.original_channel_id)
        if not channel:
            return
        
        try:
            message = await channel.fetch_message(session.original_message_id)
            
            # Создаем обновленный embed
            embed = discord.Embed(
                title="📝 Заявка на событие",
                description=f"**Событие**: {session.event_type.value} - {session.action.value}",
                color=color or discord.Color.orange()
            )
            
            submitter = guild.get_member(session.user_id)
            submitter_mention = submitter.mention if submitter else f"<@{session.user_id}>"
            
            # Создаем временный submission для расчета очков
            temp_submission = EventSubmission(
                event_type=session.event_type,
                action=session.action,
                participants=session.participants,
                submitter_id=session.user_id
            )
            
            embed.add_field(name="👤 Заявитель", value=submitter_mention, inline=True)
            embed.add_field(name="📊 Базовые очки", value=f"{EventManager.format_points_display(temp_submission.calculate_base_points())}", inline=True)
            embed.add_field(name="🔄 Статус", value=new_status, inline=True)
            
            await message.edit(embed=embed)
            logger.info(f"Обновлен статус исходного сообщения заявки на: {new_status}")
            
        except discord.NotFound:
            logger.warning(f"Исходное сообщение заявки не найдено: {session.original_message_id}")
        except Exception as e:
            logger.error(f"Ошибка обновления исходного сообщения заявки: {e}")
            
    except Exception as e:
        logger.error(f"Ошибка в update_original_event_message: {e}")

async def handle_submission_message(message: discord.Message) -> bool:
    """Обрабатывает сообщения для интерактивных заявок. Возвращает True если сообщение обработано."""
    session_key = f"{message.author.id}_{message.channel.id}"
    if active_submissions:
        logger.debug(
            f"[HANDLE ENTRY] incoming={session_key} total={len(active_submissions)} sample={list(active_submissions.keys())[:5]} dict_id={id(active_submissions)}"
        )
    else:
        logger.debug(f"[HANDLE ENTRY] no active_submissions; incoming={session_key}")

    session = active_submissions.get(session_key)

    # 1) Fallback: если это тред и сессии нет, пробуем ключ по parent_id
    if not session and isinstance(message.channel, discord.Thread) and message.channel.parent_id:
        parent_key = f"{message.author.id}_{message.channel.parent_id}"
        parent_session = active_submissions.get(parent_key)
        if parent_session:
            try:
                parent_session.channel_id = message.channel.id
            except Exception:
                pass
            active_submissions[session_key] = parent_session
            del active_submissions[parent_key]
            session = parent_session
            logger.warning(f"[HANDLE FALLBACK] parent->thread {parent_key} -> {session_key}")

    # 2) Спасательное сканирование: ищем сессию по channel_id (если ключ был создан с другим user_id)
    if not session and isinstance(message.channel, discord.Thread):
        for k, s in list(active_submissions.items()):
            if getattr(s, 'channel_id', None) == message.channel.id:
                logger.warning(f"[HANDLE RESCUE] channel_id match k={k} -> rebind to author_key={session_key}")
                # Нормализуем ключ под владельца сессии, а не под автора сообщения
                owner_key = f"{s.user_id}_{message.channel.id}"
                if k != owner_key:
                    active_submissions[owner_key] = s
                    try:
                        del active_submissions[k]
                    except KeyError:
                        pass
                # Если автор сообщения это владелец — используем session_key, иначе просто работаем через owner_key
                if message.author.id == getattr(s, 'user_id', None):
                    if owner_key != session_key:
                        active_submissions[session_key] = s
                    session = s
                else:
                    session = s
                break
        if not session:
            logger.debug(f"[HANDLE RESCUE] No session found by channel scan for thread={message.channel.id}")

    if not session:
        # Диагностика: если сообщение содержит ключевые слова участников
        lowered = message.content.lower()
        has_mentions = bool(message.mentions)
        has_keywords = any(tok in lowered for tok in ["только я", "@", "участ", "один"])
        
        if has_mentions or has_keywords:
            logger.warning(f"[HANDLE NO SESSION] Нет сессии для {session_key}, но есть упоминания/ключевые слова")
            logger.warning(f"[HANDLE NO SESSION] active_submissions keys: {list(active_submissions.keys())}")
            logger.warning(f"[HANDLE NO SESSION] content='{message.content[:60]}'")
            logger.warning(f"[HANDLE NO SESSION] mentions={[m.id for m in message.mentions]}")
            
            # Пробуем найти любую сессию для этого пользователя
            user_sessions = [k for k in active_submissions.keys() if k.startswith(f"{message.author.id}_")]
            if user_sessions:
                logger.warning(f"[HANDLE NO SESSION] Найдены сессии пользователя: {user_sessions}")
                # Используем первую найденную сессию
                fallback_session = active_submissions[user_sessions[0]]
                logger.warning(f"[HANDLE FALLBACK SESSION] Используем сессию {user_sessions[0]} состояние={fallback_session.state}")
                
                # Если это тред и сессия в состоянии ожидания участников
                if isinstance(message.channel, discord.Thread) and fallback_session.state == "waiting_participants":
                    # Обновляем канал сессии
                    fallback_session.channel_id = message.channel.id
                    # Перемещаем сессию под правильный ключ
                    active_submissions[session_key] = fallback_session
                    del active_submissions[user_sessions[0]]
                    session = fallback_session
                    logger.warning(f"[HANDLE FALLBACK SUCCESS] Сессия перемещена на {session_key}")
        
        if not session:
            return False
    
    # Доп. лог для ключевых фраз
    lowered = message.content.lower()
    if any(tok in lowered for tok in ["только я", "@", "участ", "один"]):
        logger.debug(f"[HANDLE SESSION] Обработка участников для {session_key} state={session.state}")

    # Если владелец сессии и автор не совпадают, игнорируем чужие сообщения
    if session.user_id != message.author.id:
        # Игнорируем чужие сообщения кроме диагностики
        if any(tok in lowered for tok in ["только я", "@", "участ", "один"]):
            logger.debug(f"[HANDLE SKIP] Автор сообщения {message.author.id} != session.user_id {session.user_id}")
        return False
    
    # Проверяем команду "отправить"
    if message.content.lower().strip() in ["отправить", "отправить заявку", "send", "submit"]:
        # Отправляем заявку с уже имеющимся скриншотом или без него
        await complete_submission(message, session, session.screenshot_url)
        
        # Удаляем сессию
        if session_key in active_submissions:
            del active_submissions[session_key]
        
        return True
    
    if session.state == "waiting_participants":
        return await handle_participants_message(message, session)
    elif session.state == "ready_to_submit":
        # После подтверждения участников больше не добавляем автоматически (как в рабочем примере)
        return False
    elif session.state == "waiting_screenshot":
        return await handle_screenshot_message(message, session)
    
    # Если заявка готова к подаче, но пользователь все еще пишет - игнорируем
    return False

async def handle_participants_message(message: discord.Message, session: InteractiveSubmissionSession) -> bool:
    """Обрабатывает сообщение с участниками"""
    logger.info(f"[PARTICIPANTS START] key={session.user_id}_{session.channel_id} msg_author={message.author.id} content='{message.content[:80]}' state={session.state}")
    
    # Добавляем проверку на упоминания или ключевые фразы
    has_mentions = bool(message.mentions)
    has_keywords = any(word in message.content.lower() for word in ['только я', 'участник', 'один'])
    
    logger.info(f"[PARTICIPANTS CHECK] has_mentions={has_mentions} has_keywords={has_keywords} mentions_count={len(message.mentions)}")
    
    if not has_mentions and not has_keywords:
        logger.info(f"[PARTICIPANTS SKIP] Сообщение не содержит участников или ключевых слов")
        return False  # Не обрабатываем сообщения без упоминаний или ключевых слов
        
    try:
        participants = parse_participants_from_message(message)
    except Exception as e:
        logger.error(f"[PARTICIPANTS ERROR] parse failed: {e}")
        await message.channel.send("❌ Ошибка разбора участников (лог записан)")
        return True
    logger.info(f"[PARTICIPANTS PARSED] count={len(participants)} ids={[p.id for p in participants]}")
    
    if not participants:
        embed = discord.Embed(
            title="❌ Участники не найдены",
            description="Упомяните участников через @ или напишите 'только я' если участвуете один",
            color=discord.Color.red()
        )
        await message.channel.send(embed=embed)
        return True
    
    # Добавляем найденных участников (исключая дубликаты)
    for participant in participants:
        if participant.id not in session.participants:
            session.participants.append(participant.id)
    
    # Изменяем состояние на завершено - больше не ждем скриншот
    session.state = "ready_to_submit"
    
    # Создаем embed с участниками и кнопками
    embed = discord.Embed(
        title="✅ Участники добавлены",
        description="Проверьте данные заявки и подтвердите подачу",
        color=discord.Color.green()
    )
    
    participants_list = []
    for user_id in session.participants:
        user = message.guild.get_member(user_id)
        if user:
            participants_list.append(user.mention)
    
    embed.add_field(
        name="👥 Участники",
        value='\n'.join(participants_list),
        inline=False
    )
    
    # Создаем базовый submission для отображения информации
    submission = EventSubmission(
        event_type=session.event_type,
        action=session.action,
        participants=session.participants,
        submitter_id=session.user_id
    )
    
    embed.add_field(
        name="🎮 Событие",
        value=submission.get_event_display_name(),
        inline=True
    )
    
    embed.add_field(
        name="📊 Базовые очки",
        value=f"{EventManager.format_points_display(submission.calculate_base_points())}",
        inline=True
    )
    
    embed.set_footer(text="💡 Скриншот опционален - администратор может запросить отдельно")
    
    # Создаем View с кнопками
    view = SubmissionConfirmView(session)
    
    try:
        await message.channel.send(embed=embed, view=view)
        logger.debug(f"[PARTICIPANTS SENT] session={session.user_id}_{session.channel_id} participants={len(session.participants)}")
    except Exception as e:
        logger.error(f"[PARTICIPANTS ERROR] send embed failed: {e}")
        await message.channel.send("❌ Не удалось отправить embed с участниками")
    return True

async def handle_screenshot_message(message: discord.Message, session: InteractiveSubmissionSession) -> bool:
    """Обрабатывает сообщение со скриншотом"""
    screenshot_url = None
    
    # ИСПРАВЛЕНИЕ: Проверяем прикрепленные файлы в первую очередь
    if message.attachments:
        attachment = message.attachments[0]
        # Проверяем, что это изображение
        if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp']):
            screenshot_url = attachment.url
            session.screenshot_url = screenshot_url  # Сохраняем в сессии
            
            # Создаем кнопку подтверждения отправки
            confirm_view = SubmissionConfirmView(session)
            
            embed = discord.Embed(
                title="📷 Скриншот добавлен",
                description=f"**Файл:** {attachment.filename}\n\n"
                           f"**Событие:** {session.event_type.value} - {session.action.value}\n"
                           f"**Участников:** {len(session.participants)}\n\n"
                           "🚀 **Готовы отправить заявку?**",
                color=discord.Color.blue()
            )
            embed.set_image(url=screenshot_url)
            
            await message.reply(embed=embed, view=confirm_view)
            return True
    
    # Проверяем наличие ссылок в тексте сообщения
    if message.content:
        import re
        urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\\(\\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', message.content)
        if urls:
            screenshot_url = urls[0]
            session.screenshot_url = screenshot_url  # Сохраняем в сессии
            
            # Создаем кнопку подтверждения отправки
            confirm_view = SubmissionConfirmView(session)
            
            embed = discord.Embed(
                title="📷 Скриншот добавлен",
                description=f"**Ссылка:** {screenshot_url}\n\n"
                           f"**Событие:** {session.event_type.value} - {session.action.value}\n"
                           f"**Участников:** {len(session.participants)}\n\n"
                           "🚀 **Готовы отправить заявку?**",
                color=discord.Color.blue()
            )
            embed.set_image(url=screenshot_url)
            
            await message.reply(embed=embed, view=confirm_view)
            return True
        else:
            # ИСПРАВЛЕНИЕ: Более простое сообщение
            await message.reply(
                "✅ **Сообщение получено!** Ожидание проверки администратором.\n\n"
                "💡 *Если вы хотели прикрепить скриншот:*\n"
                "• Прикрепите изображение файлом\n"
                "• Или пришлите ссылку на изображение\n\n"
                "📝 *Для отправки заявки без скриншота напишите:* **отправить**"
            )
            return True
    else:
        # ИСПРАВЛЕНИЕ: Всегда подтверждаем получение сообщения
        await message.reply(
            "✅ **Сообщение получено!** Ожидание проверки администратором.\n\n"
            "📝 *Для отправки заявки напишите:* **отправить**"
        )
        return True

async def complete_submission(context, session: InteractiveSubmissionSession, screenshot_url: str = None):
    """Завершает подачу заявки и создает заявку для модераторов
    
    Args:
        context: discord.Message или discord.Interaction
        session: Сессия интерактивной подачи
        screenshot_url: URL скриншота (опционально)
    """
    # Определяем guild и channel из контекста
    if isinstance(context, discord.Interaction):
        guild = context.guild
        channel = context.channel
        # Исправление: thread_id должен быть ID созданного треда модерации, а не текущего канала
        thread_id = None  # Будет установлен после создания/отправки сообщения
    else:  # discord.Message
        guild = context.guild
        channel = context.channel
        thread_id = None  # Будет установлен после создания/отправки сообщения
    
    # Создаем объект заявки
    submission = EventSubmission(
        event_type=session.event_type,
        action=session.action,
        participants=session.participants,
        submitter_id=session.user_id,
        group_size=len(session.participants),
        description=f"Событие: {session.event_type.value} - {session.action.value}",
        screenshot_url=screenshot_url
    )
    
    # Сохраняем заявку в базу данных
    submission_id = await EventDatabase.create_event_submission(
        guild_id=guild.id,
        submission=submission,
        original_message_id=session.original_message_id,
        original_channel_id=session.original_channel_id
    )
    
    if not submission_id:
        embed = discord.Embed(
            title="❌ Ошибка",
            description="Не удалось сохранить заявку. Попробуйте еще раз.",
            color=discord.Color.red()
        )
        await channel.send(embed=embed)
        return
    
    # Создаем embed для заявки
    embed = discord.Embed(
        title="📋 Заявка на событие",
        description=f"**Событие**: {submission.get_event_display_name()}",
        color=discord.Color.orange()
    )
    
    embed.add_field(name="🆔 ID заявки", value=str(submission_id), inline=True)
    embed.add_field(name="👤 Заявитель", value=f"<@{session.user_id}>", inline=True)
    embed.add_field(name="� Базовые очки", value=f"{EventManager.format_points_display(submission.calculate_base_points())}", inline=True)
    embed.add_field(name="�👥 Участников", value=str(len(session.participants)), inline=True)
    embed.add_field(name="🔄 Статус", value="⏳ Ожидает рассмотрения", inline=True)
    embed.add_field(name="📅 Создана", value=f"<t:{int(__import__('time').time())}:R>", inline=True)
    
    participants_list = []
    for user_id in session.participants:
        user = guild.get_member(user_id)
        if user:
            participants_list.append(user.mention)
    
    embed.add_field(
        name="👥 Участники",
        value='\n'.join(participants_list),
        inline=False
    )
    
    if screenshot_url:
        embed.add_field(name="📷 Скриншот", value=f"[Посмотреть]({screenshot_url})", inline=False)
        embed.set_image(url=screenshot_url)
    
    # Добавляем пинги модераторов
    guild_config = await EventDatabase.get_guild_config(guild.id)
    moderator_role = guild_config.get('moderator_role')
    admin_role = guild_config.get('admin_role')
    
    ping_text = ""
    if moderator_role:
        try:
            role = guild.get_role(int(moderator_role))
            if role:
                ping_text += f"{role.mention} "
        except:
            pass
    
    if admin_role:
        try:
            role = guild.get_role(int(admin_role))
            if role:
                ping_text += f"{role.mention} "
        except:
            pass
    
    content = f"{ping_text}\n🔔 **Новая заявка на событие!**" if ping_text else "🔔 **Новая заявка на событие!**"
    
    # Создаем view для модераторов
    moderator_view = EventModerationView(submission_id, submission)
    
    # Отправляем сообщение и сохраняем его ID
    sent_message = await channel.send(content=content, embed=embed, view=moderator_view)
    
    # Исправление: сохраняем thread_id как ID треда, в котором было отправлено сообщение
    if isinstance(channel, discord.Thread):
        thread_id = channel.id
    else:
        thread_id = None
    
    # Обновляем заявку с ID сообщения и thread_id
    async with aiosqlite.connect("potatos_recruit.db") as db:
        await db.execute("""
            UPDATE event_submissions SET message_id = ?, thread_id = ? WHERE id = ?
        """, (sent_message.id, thread_id, submission_id))
        await db.commit()
        logger.info(f"Сохранены IDs: message_id={sent_message.id}, thread_id={thread_id} для заявки {submission_id}")
    
    # Обновляем статус исходного сообщения
    await update_original_event_message_with_id(
        session, 
        guild, 
        submission_id,
        "⏳ Ожидает рассмотрения", 
        discord.Color.orange()
    )

def parse_participants_from_message(message: discord.Message) -> List[discord.Member]:
    """Парсит участников из сообщения"""
    participants = []
    
    # Проверяем на "только я"
    if "только я" in message.content.lower():
        return [message.author]
    
    # Ищем упоминания пользователей
    for mention in message.mentions:
        if mention not in participants and not mention.bot:
            participants.append(mention)
    
    # Если автора нет в списке, добавляем его
    if message.author not in participants:
        participants.append(message.author)
    
    return participants

class EventSelectMenu(ui.Select):
    """Меню выбора типа события"""
    
    def __init__(self):
        options = []
        event_options = EventManager.get_event_options()
        
        for value, label, description in event_options[:25]:  # Discord лимит 25 опций
            options.append(discord.SelectOption(
                label=label,
                value=value,
                description=description
            ))
        
        super().__init__(
            placeholder="🎯 Выберите тип события...",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        # Сразу откладываем ответ, так как создание треда может занять время
        await interaction.response.defer(ephemeral=True)
        
        # Проверяем ограничения по датам для создания заявок
        can_create, error_message = await check_points_date_restrictions(interaction.guild.id)
        if not can_create:
            await interaction.followup.send(f"❌ Создание заявок сейчас недоступно!\n{error_message}", ephemeral=True)
            return
        
        selected_value = self.values[0]
        
        try:
            event_type, action = EventManager.parse_event_selection(selected_value)
        except ValueError as e:
            await interaction.followup.send(f"❌ Ошибка выбора события: {e}", ephemeral=True)
            return
        
        # Создаем объект заявки
        submission = EventSubmission(
            event_type=event_type,
            action=action,
            participants=[interaction.user.id],
            submitter_id=interaction.user.id
        )
        
        # Сразу создаем тред для заявки
        thread_name = f"{submission.get_event_display_name()} - {interaction.user.display_name}"
        
        embed = discord.Embed(
            title="📝 Заявка на событие",
            description=f"**Событие**: {submission.get_event_display_name()}",
            color=discord.Color.blue()
        )
        
        embed.add_field(name="👤 Заявитель", value=interaction.user.mention, inline=True)
        embed.add_field(name="📊 Базовые очки", value=f"{EventManager.format_points_display(submission.calculate_base_points())}", inline=True)
        embed.add_field(name="🔄 Статус", value="⏳ Ожидает заполнения", inline=True)
        
        # Добавляем временный ID для последующего поиска
        import time
        temp_id = f"temp_{int(time.time())}_{interaction.user.id}"
        embed.set_footer(text=f"Заявка ID: {temp_id}")
        
        try:
            # Получаем настроенный канал событий
            events_channel_id = await EventDatabase.get_events_channel(interaction.guild.id)
            target_channel = interaction.channel  # По умолчанию текущий канал
            
            if events_channel_id:
                events_channel = interaction.guild.get_channel(events_channel_id)
                if events_channel:
                    target_channel = events_channel
            
            # Создаем тред в целевом канале
            message = await target_channel.send(embed=embed)
            thread = await message.create_thread(name=thread_name[:100])
            
            # Создаем интерактивную сессию
            session = InteractiveSubmissionSession(
                user_id=interaction.user.id,
                channel_id=thread.id,  # Используем ID треда вместо канала
                event_type=event_type,
                action=action
            )
            
            # Сохраняем ID исходного сообщения для дальнейшего обновления
            session.original_message_id = message.id
            session.original_channel_id = target_channel.id
            session.temp_id = temp_id  # Сохраняем временный ID
            
            # Сохраняем сессию
            session_key = f"{interaction.user.id}_{thread.id}"
            active_submissions[session_key] = session
            logger.info(f"[SESSION CREATE] key={session_key} event={event_type.value}/{action.value} thread_id={thread.id} active_total={len(active_submissions)} id(active_submissions)={id(active_submissions)}")
            logger.info(f"[SESSION CREATE] Все ключи сессий: {list(active_submissions.keys())}")
            
            # Рабочая логика из примера: сначала просим указать участников (state остаётся waiting_participants)
            session.state = "waiting_participants"
            participants_embed = discord.Embed(
                title="👥 Участники события",
                description="Пингуйте всех участников группы в следующем сообщении",
                color=discord.Color.orange()
            )
            participants_embed.add_field(
                name="🔧 Как указать участников:",
                value="• Напишите `@user1 @user2 @user3` чтобы добавить участников\n• Или напишите `только я` если участвуете один",
                inline=False
            )
            participants_embed.set_footer(text="💡 Пинги должны работать (участники должны быть на сервере)")
            await thread.send(f"{interaction.user.mention}", embed=participants_embed)
            
            await interaction.followup.send(f"✅ Заявка создана! Продолжите заполнение в треде: {thread.mention}", ephemeral=True)
            
        except Exception as e:
            logger.error(f"Ошибка создания треда: {e}")
            await interaction.followup.send(f"❌ Ошибка создания заявки: {e}", ephemeral=True)

class SubmissionConfirmView(ui.View):
    """View с кнопками подтверждения заявки"""
    
    def __init__(self, session: InteractiveSubmissionSession):
        super().__init__(timeout=600)  # 10 минут на принятие решения
        self.session = session
    
    @ui.button(label="✅ Подтвердить заявку", style=discord.ButtonStyle.green)
    async def confirm_submission(self, interaction: discord.Interaction, button: ui.Button):
        """Подтвердить и отправить заявку"""
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message(
                "❌ Только автор заявки может её подтвердить!",
                ephemeral=True
            )
            return
        
        # Отключаем кнопки
        for item in self.children:
            item.disabled = True
        
        # Завершаем подачу заявки без скриншота
        await complete_submission(interaction, self.session, screenshot_url=None)
        
        # Удаляем сессию
        session_key = f"{self.session.user_id}_{self.session.channel_id}"
        if session_key in active_submissions:
            del active_submissions[session_key]
        
        # Обновляем сообщение с отключенными кнопками
        embed = discord.Embed(
            title="✅ Заявка подтверждена",
            description="Заявка отправлена администраторам на рассмотрение",
            color=discord.Color.green()
        )
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    @ui.button(label="📷 Добавить скриншот", style=discord.ButtonStyle.secondary)
    async def add_screenshot(self, interaction: discord.Interaction, button: ui.Button):
        """Добавить скриншот к заявке"""
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message(
                "❌ Только автор заявки может добавить скриншот!",
                ephemeral=True
            )
            return
        
        # Переводим в режим ожидания скриншота
        self.session.state = "waiting_screenshot"
        
        embed = discord.Embed(
            title="📷 Добавление скриншота",
            description="Пришлите ссылку на скриншот в следующем сообщении",
            color=discord.Color.orange()
        )
        embed.add_field(
            name="📝 Как добавить:",
            value="• Загрузите скриншот на imgur.com, prnt.sc или другой сервис\n• Скопируйте ссылку и вставьте в сообщение",
            inline=False
        )
        embed.set_footer(text="💡 Ссылка должна начинаться с http:// или https://")
        
        await interaction.response.send_message(embed=embed)
    
    @ui.button(label="❌ Отменить", style=discord.ButtonStyle.red)
    async def cancel_submission(self, interaction: discord.Interaction, button: ui.Button):
        """Отменить подачу заявки"""
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message(
                "❌ Только автор заявки может её отменить!",
                ephemeral=True
            )
            return
        
        # Удаляем сессию
        session_key = f"{self.session.user_id}_{self.session.channel_id}"
        if session_key in active_submissions:
            del active_submissions[session_key]
        
        # Отключаем кнопки
        for item in self.children:
            item.disabled = True
        
        embed = discord.Embed(
            title="❌ Заявка отменена",
            description="Подача заявки была отменена",
            color=discord.Color.red()
        )
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    async def on_timeout(self):
        """Когда истекает время ожидания"""
        # Удаляем сессию
        session_key = f"{self.session.user_id}_{self.session.channel_id}"
        if session_key in active_submissions:
            del active_submissions[session_key]

class EventSubmissionModal(ui.Modal):
    """Модальное окно для подачи заявки на событие"""
    
    def __init__(self, event_type: EventType, action: EventAction):
        self.event_type = event_type
        self.action = action
        
        submission = EventSubmission(
            event_type=event_type,
            action=action,
            participants=[],
            submitter_id=0
        )
        
        title = f"Заявка: {submission.get_event_display_name()}"
        super().__init__(title=title[:45])  # Discord лимит на длину заголовка
    
    participants_input = ui.TextInput(
        label="Участники",
        placeholder="@User1 @User2 @User3 (пингуйте всех участников)",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True
    )
    
    description_input = ui.TextInput(
        label="Описание события",
        placeholder="Опишите подробности события...",
        style=discord.TextStyle.paragraph,
        max_length=1000,
        required=True
    )
    
    screenshot_input = ui.TextInput(
        label="Скриншот (опционально)",
        placeholder="Ссылка на скриншот",
        max_length=500,
        required=False
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        # Парсим участников из текста
        participants_text = self.participants_input.value
        participants = self._parse_participants(participants_text, interaction.guild)
        
        if not participants:
            await interaction.response.send_message(
                "❌ Не найдены участники. Упомяните участников через @ или укажите их ID.",
                ephemeral=True
            )
            return
        
        if len(participants) > 20:  # Максимум 20 участников
            await interaction.response.send_message(
                "❌ Слишком много участников (максимум 20).",
                ephemeral=True
            )
            return
        
        # Валидация скриншота если указан
        screenshot_url = self.screenshot_input.value.strip() if self.screenshot_input.value else None
        if screenshot_url and not screenshot_url.startswith(('http://', 'https://')):
            await interaction.response.send_message("❌ Неверный формат URL скриншота!", ephemeral=True)
            return
        
        # Создаем объект заявки
        submission = EventSubmission(
            event_type=self.event_type,
            action=self.action,
            participants=[p.id for p in participants],
            submitter_id=interaction.user.id,
            group_size=len(participants),
            description=self.description_input.value.strip(),
            screenshot_url=screenshot_url
        )
        
        # Создаем embed и view для выбора канала
        embed = self._create_confirmation_embed(submission, participants)
        view = ChannelSelectionView(submission, participants)
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    def _parse_participants(self, text: str, guild: discord.Guild) -> List[discord.Member]:
        """Парсить участников из текста"""
        participants = []
        
        # Ищем упоминания пользователей <@!123456789> или <@123456789>
        user_mentions = re.findall(r'<@!?(\d+)>', text)
        for user_id in user_mentions:
            member = guild.get_member(int(user_id))
            if member and member not in participants:
                participants.append(member)
        
        # Ищем просто числовые ID
        user_ids = re.findall(r'\b(\d{17,19})\b', text)
        for user_id in user_ids:
            if user_id not in user_mentions:  # Не дублируем уже найденные
                member = guild.get_member(int(user_id))
                if member and member not in participants:
                    participants.append(member)
        
        return participants
    
    def _create_confirmation_embed(
        self, 
        submission: EventSubmission, 
        participants: List[discord.Member]
    ) -> discord.Embed:
        """Создать embed для подтверждения заявки"""
        embed = discord.Embed(
            title="� Подтверждение заявки на событие",
            description=f"**Событие:** {submission.get_event_display_name()}",
            color=discord.Color.orange()
        )
        
        # Информация о событии
        base_points = submission.calculate_base_points()
        embed.add_field(
            name="� Базовые очки",
            value=f"{EventManager.format_points_display(base_points)} за действие",
            inline=True
        )
        
        embed.add_field(
            name="👥 Размер группы",
            value=f"{submission.group_size} участников",
            inline=True
        )
        
        embed.add_field(
            name="🎯 Очки на человека",
            value="Зависит от множителя модератора",
            inline=True
        )
        
        # Список участников
        participants_text = "\n".join([f"• {p.display_name}" for p in participants[:10]])
        if len(participants) > 10:
            participants_text += f"\n... и ещё {len(participants) - 10} участников"
        
        embed.add_field(
            name="👥 Участники",
            value=participants_text,
            inline=False
        )
        
        # Описание
        embed.add_field(
            name="📝 Описание",
            value=submission.description[:500] + ("..." if len(submission.description) > 500 else ""),
            inline=False
        )
        
        # Скриншот если есть
        if submission.screenshot_url:
            embed.add_field(
                name="📷 Скриншот",
                value=f"[Посмотреть скриншот]({submission.screenshot_url})",
                inline=False
            )
        
        embed.add_field(
            name="➡️ Следующий шаг",
            value="Выберите канал для создания заявки и подтвердите отправку.",
            inline=False
        )
        
        embed.set_footer(text="⚠️ После подтверждения изменить данные будет нельзя")
        
        return embed

class ChannelSelectionView(ui.View):
    """View для выбора канала создания заявки"""
    
    def __init__(self, submission: EventSubmission, participants: List[discord.Member]):
        super().__init__(timeout=300)
        self.submission = submission
        self.participants = participants
        self.add_item(ChannelSelectMenu(submission, participants))
        
    @ui.button(label="❌ Отменить", style=discord.ButtonStyle.secondary)
    async def cancel_submission(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(
            content="❌ Подача заявки отменена.",
            embed=None,
            view=None
        )

class ChannelSelectMenu(ui.Select):
    """Меню выбора канала для создания заявки"""
    
    def __init__(self, submission: EventSubmission, participants: List[discord.Member]):
        self.submission = submission
        self.participants = participants
        
        # Получаем текстовые каналы
        options = []
        
        # Добавляем опцию "Текущий канал"
        options.append(discord.SelectOption(
            label="📝 Текущий канал",
            value="current",
            description="Создать заявку в этом канале",
            emoji="📝"
        ))
        
        super().__init__(
            placeholder="🎯 Выберите канал для создания заявки...",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]
        
        if selected_value == "current":
            target_channel = interaction.channel
        else:
            # Если в будущем добавим другие каналы
            target_channel = interaction.channel
        
        # Создаем финальный view с подтверждением
        view = EventConfirmationView(self.submission, self.participants, target_channel)
        
        embed = discord.Embed(
            title="✅ Готово к отправке",
            description=f"**Событие:** {self.submission.get_event_display_name()}",
            color=discord.Color.green()
        )
        
        embed.add_field(
            name="📍 Канал для заявки",
            value=target_channel.mention,
            inline=True
        )
        
        embed.add_field(
            name="👥 Участников",
            value=str(self.submission.group_size),
            inline=True
        )
        
        if self.submission.screenshot_url:
            embed.add_field(
                name="📷 Скриншот",
                value="✅ Приложен",
                inline=True
            )
        
        embed.add_field(
            name="📝 Описание",
            value=self.submission.description[:200] + ("..." if len(self.submission.description) > 200 else ""),
            inline=False
        )
        
        embed.set_footer(text="🚀 Нажмите 'Подтвердить отправку' для создания заявки")
        
        await interaction.response.edit_message(embed=embed, view=view)

class ParticipantSelectionView(ui.View):
    """View для добавления участников в заявку"""
    
    def __init__(self, submission: EventSubmission):
        super().__init__(timeout=300)
        self.submission = submission
    
    @ui.button(label="➕ Добавить участника", style=discord.ButtonStyle.primary, emoji="👤")
    async def add_participant(self, interaction: discord.Interaction, button: ui.Button):
        modal = AddParticipantModal(self.submission)
        await interaction.response.send_modal(modal)
    
    @ui.button(label="✅ Завершить добавление", style=discord.ButtonStyle.success, emoji="📝")
    async def finish_participants(self, interaction: discord.Interaction, button: ui.Button):
        # Переходим к запросу скриншота
        view = ScreenshotRequestView(self.submission)
        
        embed = discord.Embed(
            title="📷 Добавление скриншота",
            description=f"**Событие**: {self.submission.get_event_display_name()}",
            color=discord.Color.gold()
        )
        
        participants_list = []
        for i, user_id in enumerate(self.submission.participants, 1):
            user = interaction.guild.get_member(user_id)
            if user:
                participants_list.append(f"{i}. {user.mention}")
        
        embed.add_field(
            name="👥 Участники",
            value='\n'.join(participants_list) if participants_list else "Только вы",
            inline=False
        )
        
        embed.add_field(
            name="➡️ Следующий шаг", 
            value="Прикрепите скриншот события или пропустите этот шаг.",
            inline=False
        )
        
        await interaction.response.edit_message(embed=embed, view=view)

class AddParticipantModal(ui.Modal):
    """Модальное окно для добавления одного участника"""
    
    def __init__(self, submission: EventSubmission):
        super().__init__(title="Добавить участника")
        self.submission = submission
    
    participant_input = ui.TextInput(
        label="Участник",
        placeholder="@пользователь или ID пользователя",
        required=True,
        max_length=100
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        participant_text = self.participant_input.value.strip()
        
        # Парсим участника
        participant = None
        
        # Пробуем найти по упоминанию или ID
        user_match = re.search(r'<@!?(\d+)>|(\d+)', participant_text)
        if user_match:
            user_id = int(user_match.group(1) or user_match.group(2))
            participant = interaction.guild.get_member(user_id)
        
        if not participant:
            await interaction.response.send_message("❌ Участник не найден!", ephemeral=True)
            return
        
        if participant.id in self.submission.participants:
            await interaction.response.send_message("❌ Этот участник уже добавлен!", ephemeral=True)
            return
        
        if len(self.submission.participants) >= 20:
            await interaction.response.send_message("❌ Максимум 20 участников!", ephemeral=True)
            return
        
        # Добавляем участника
        self.submission.participants.append(participant.id)
        self.submission.group_size = len(self.submission.participants)
        
        # Обновляем embed
        embed = discord.Embed(
            title="📝 Подача заявки на событие",
            description=f"**Событие**: {self.submission.get_event_display_name()}\n**Описание**: {self.submission.description}",
            color=discord.Color.blue()
        )
        
        participants_list = []
        for i, user_id in enumerate(self.submission.participants, 1):
            user = interaction.guild.get_member(user_id)
            if user:
                participants_list.append(f"{i}. {user.mention}")
        
        embed.add_field(
            name="👥 Участники",
            value='\n'.join(participants_list) if participants_list else "Только вы",
            inline=False
        )
        
        embed.add_field(
            name="➡️ Следующий шаг",
            value="Добавьте ещё участников или завершите добавление.",
            inline=False
        )
        
        view = ParticipantSelectionView(self.submission)
        await interaction.response.edit_message(embed=embed, view=view)

class ScreenshotRequestView(ui.View):
    """View для запроса скриншота"""
    
    def __init__(self, submission: EventSubmission):
        super().__init__(timeout=300)
        self.submission = submission
    
    @ui.button(label="📷 Добавить скриншот", style=discord.ButtonStyle.primary)
    async def add_screenshot(self, interaction: discord.Interaction, button: ui.Button):
        modal = ScreenshotModal(self.submission)
        await interaction.response.send_modal(modal)
    
    @ui.button(label="⏭️ Пропустить скриншот", style=discord.ButtonStyle.secondary)
    async def skip_screenshot(self, interaction: discord.Interaction, button: ui.Button):
        # Завершаем подачу заявки без скриншота
        await self._finalize_submission(interaction, None)
    
    async def _finalize_submission(self, interaction: discord.Interaction, screenshot_url: str = None):
        """Завершить подачу заявки"""
        self.submission.screenshot_url = screenshot_url
        
        # Получаем участников для отображения
        participants = []
        for user_id in self.submission.participants:
            user = interaction.guild.get_member(user_id)
            if user:
                participants.append(user)
        
        # Создаем embed для подтверждения
        embed = self._create_confirmation_embed(self.submission, participants)
        view = EventConfirmationView(self.submission, participants)
        
        await interaction.response.edit_message(embed=embed, view=view)
    
    def _create_confirmation_embed(
        self, 
        submission: EventSubmission, 
        participants: List[discord.Member]
    ) -> discord.Embed:
        """Создать embed для подтверждения заявки"""
        embed = discord.Embed(
            title="🔍 Подтверждение заявки на событие",
            description=f"**Событие:** {submission.get_event_display_name()}",
            color=discord.Color.orange()
        )
        
        # Информация о событии
        base_points = submission.calculate_base_points()

class ScreenshotModal(ui.Modal):
    """Модальное окно для добавления скриншота"""
    
    def __init__(self, submission: EventSubmission):
        super().__init__(title="Добавить скриншот")
        self.submission = submission
    
    screenshot_input = ui.TextInput(
        label="URL скриншота",
        placeholder="Вставьте ссылку на скриншот (например, из Discord)",
        required=True,
        max_length=500
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        screenshot_url = self.screenshot_input.value.strip()
        
        # Простая валидация URL
        if not screenshot_url.startswith(('http://', 'https://')):
            await interaction.response.send_message("❌ Неверный формат URL!", ephemeral=True)
            return
        
        # Завершаем подачу заявки со скриншотом
        await ScreenshotRequestView(self.submission)._finalize_submission(interaction, screenshot_url)
    
    def _create_confirmation_embed(
        self, 
        submission: EventSubmission, 
        participants: List[discord.Member]
    ) -> discord.Embed:
        """Создать embed для подтверждения заявки"""
        embed = discord.Embed(
            title="🔍 Подтверждение заявки на событие",
            description=f"**Событие:** {submission.get_event_display_name()}",
            color=discord.Color.orange()
        )
        
        # Информация о событии
        base_points = submission.calculate_base_points()
        embed.add_field(
            name="📊 Базовые очки",
            value=f"{EventManager.format_points_display(base_points)} за действие",
            inline=True
        )
        
        embed.add_field(
            name="👥 Размер группы",
            value=f"{submission.group_size} участников",
            inline=True
        )
        
        embed.add_field(
            name="🎯 Очки на человека",
            value="Устанавливается модератором",
            inline=True
        )
        
        # Список участников
        participants_text = "\n".join([f"• {p.display_name}" for p in participants[:10]])
        if len(participants) > 10:
            participants_text += f"\n... и ещё {len(participants) - 10} участников"
        
        embed.add_field(
            name="👥 Участники",
            value=participants_text,
            inline=False
        )
        
        # Описание если есть
        if submission.description:
            embed.add_field(
                name="📝 Описание",
                value=submission.description[:200] + ("..." if len(submission.description) > 200 else ""),
                inline=False
            )
        
        embed.add_field(
            name="ℹ️ Следующие шаги",
            value="• Проверьте данные\n• Нажмите **Подтвердить**\n• Прикрепите скриншот\n• Дождитесь проверки модератора",
            inline=False
        )
        
        embed.set_footer(text="⚠️ После подтверждения изменить данные будет нельзя")
        
        return embed

class SubmissionConfirmView(ui.View):
    """View для подтверждения отправки заявки после добавления скриншота"""
    
    def __init__(self, session: InteractiveSubmissionSession):
        super().__init__(timeout=300)
        self.session = session
    
    @ui.button(label="🚀 Отправить заявку", style=discord.ButtonStyle.success)
    async def confirm_submit(self, interaction: discord.Interaction, button: ui.Button):
        # Проверяем, что кнопку нажал автор заявки
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message(
                "❌ Только автор заявки может подтвердить отправку!",
                ephemeral=True
            )
            return
        
        await interaction.response.defer()
        
        # Отправляем заявку
        await complete_submission(interaction, self.session, self.session.screenshot_url)
        
        # Удаляем сессию
        session_key = f"{self.session.user_id}_{self.session.channel_id}"
        if session_key in active_submissions:
            del active_submissions[session_key]
        
        # Отключаем кнопки
        for item in self.children:
            item.disabled = True
        
        await interaction.edit_original_response(view=self)
    
    @ui.button(label="❌ Отмена", style=discord.ButtonStyle.secondary)
    async def cancel_submit(self, interaction: discord.Interaction, button: ui.Button):
        # Проверяем, что кнопку нажал автор заявки
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message(
                "❌ Только автор заявки может отменить!",
                ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title="❌ Отправка отменена",
            description="Вы можете продолжить добавлять скриншоты или написать **отправить** для подачи заявки.",
            color=discord.Color.orange()
        )
        
        # Отключаем кнопки
        for item in self.children:
            item.disabled = True
        
        await interaction.response.edit_message(embed=embed, view=self)

class EventConfirmationView(ui.View):
    """View для подтверждения заявки"""
    
    def __init__(self, submission: EventSubmission, participants: List[discord.Member], target_channel: discord.TextChannel = None, original_message_id: int = None, original_channel_id: int = None):
        super().__init__(timeout=300)
        self.submission = submission
        self.participants = participants
        self.target_channel = target_channel
        self.original_message_id = original_message_id
        self.original_channel_id = original_channel_id
    
    @ui.button(label="✅ Подтвердить", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: ui.Button):
        # Проверяем, что это автор заявки
        if interaction.user.id != self.submission.submitter_id:
            await interaction.response.send_message(
                "❌ Только автор заявки может её подтвердить.",
                ephemeral=True
            )
            return
        
        # Создаем тред для заявки
        if self.target_channel:
            # Используем выбранный канал
            target_channel = self.target_channel
        else:
            # Используем настроенный канал событий (старая логика)
            forum_channel = await self._get_events_forum_channel(interaction.guild)
            if not forum_channel:
                await interaction.response.send_message(
                    "❌ Канал для событий не настроен. Обратитесь к администратору.",
                    ephemeral=True
                )
                return
            target_channel = forum_channel
        
        # Создаем тред
        thread_name = f"{self.submission.get_event_display_name()} - {interaction.user.display_name}"
        
        # Формируем контент треда
        participants_mentions = " ".join([p.mention for p in self.participants])
        thread_content = f"""
**📋 Заявка на событие**

**🎯 Событие:** {self.submission.get_event_display_name()}
**👤 Заявитель:** {interaction.user.mention}
**👥 Участники:** {participants_mentions}
**📊 Базовые очки:** {EventManager.format_points_display(self.submission.calculate_base_points())}
**🔢 Размер группы:** {self.submission.group_size}

{f"**📝 Описание:** {self.submission.description}" if self.submission.description else ""}

**⚠️ Требуется:** Прикрепите скриншот события следующим сообщением!
        """.strip()
        
        try:
            # Если у нас нет исходного сообщения, создаем его
            original_message = None
            original_channel = None
            
            if not self.original_message_id or not self.original_channel_id:
                # Создаем embed для исходного сообщения заявки
                embed = discord.Embed(
                    title="📝 Заявка на событие",
                    description=f"**Событие**: {self.submission.get_event_display_name()}",
                    color=discord.Color.blue()
                )
                
                embed.add_field(name="👤 Заявитель", value=interaction.user.mention, inline=True)
                embed.add_field(name="📊 Базовые очки", value=f"{EventManager.format_points_display(self.submission.calculate_base_points())}", inline=True)
                embed.add_field(name="🔄 Статус", value="⏳ Ожидает рассмотрения", inline=True)
                
                # Создаем временный ID для последующего поиска
                import time
                temp_id = f"temp_{int(time.time())}_{interaction.user.id}"
                embed.set_footer(text=f"Заявка ID: {temp_id}")
                
                # Определяем канал для исходного сообщения
                if self.target_channel:
                    events_channel = self.target_channel
                else:
                    # Получаем настроенный канал событий
                    events_channel_id = await EventDatabase.get_events_channel(interaction.guild.id)
                    if events_channel_id:
                        events_channel = interaction.guild.get_channel(events_channel_id)
                    else:
                        events_channel = interaction.channel
                
                # Отправляем исходное сообщение
                original_message = await events_channel.send(embed=embed)
                original_channel = events_channel
                
                self.original_message_id = original_message.id
                self.original_channel_id = original_channel.id
            
            # Проверяем тип канала и создаем тред соответственно
            if isinstance(target_channel, discord.ForumChannel):
                # Форум - создаем тред с сообщением
                thread_with_msg = await target_channel.create_thread(
                    name=thread_name[:100],  # Discord лимит на название треда
                    content=thread_content
                )
                thread = thread_with_msg.thread
            else:
                # Обычный канал - создаем тред из сообщения
                # Сначала отправляем сообщение
                message = await target_channel.send(content=thread_content)
                # Создаем тред из сообщения
                thread = await message.create_thread(
                    name=thread_name[:100]
                )
            
            # Настраиваем права доступа к треду
            await self._setup_thread_permissions(thread, interaction.user.id, interaction.guild)
            
            # Сохраняем заявку в базу данных
            submission_id = await EventDatabase.create_event_submission(
                guild_id=interaction.guild.id,
                submission=self.submission,
                thread_id=thread.id,
                original_message_id=self.original_message_id,
                original_channel_id=self.original_channel_id
            )
            
            # Обновляем footer исходного сообщения с настоящим ID заявки
            if original_message and submission_id:
                try:
                    embed = original_message.embeds[0]
                    embed.set_footer(text=f"Заявка ID: {submission_id}")
                    await original_message.edit(embed=embed)
                    logger.info(f"Обновлен footer исходного сообщения заявки {submission_id}")
                except Exception as e:
                    logger.error(f"Ошибка обновления footer исходного сообщения: {e}")
            
            # Создаем view для модераторов
            moderator_view = EventModerationView(submission_id, self.submission)
            
            # Отправляем сообщение для модераторов
            await thread.send(
                "**🔍 Заявка ожидает рассмотрения модератором**\n\n"
                "📸 **Не забудьте прикрепить скриншот!**",
                view=moderator_view
            )
            
            # Отправляем сообщение-напоминание о скриншоте
            await thread.send(
                f"{interaction.user.mention} 📸 **Важно:** Прикрепите скриншот события следующим сообщением!\n\n"
                "**Что должно быть на скриншоте:**\n"
                "• Название события или объекта\n"
                "• Участники группы в кадре\n"
                "• Время события (если видно)\n"
                "• Результат (захват/доставка/убийство)"
            )
            
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Нет прав для создания тредов. Обратитесь к администратору.",
                ephemeral=True
            )
            return
        except Exception as e:
            logger.error(f"Ошибка создания треда: {e}")
            await interaction.response.send_message(
                "❌ Ошибка при создании заявки. Попробуйте позже.",
                ephemeral=True
            )
            return
        
        await interaction.response.send_message(
            f"✅ Заявка создана! Перейдите в тред: {thread.mention}\n"
            f"📸 **Не забудьте прикрепить скриншот события!**",
            ephemeral=True
        )
    
    @ui.button(label="❌ Отменить", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.submission.submitter_id:
            await interaction.response.send_message(
                "❌ Только автор заявки может её отменить.",
                ephemeral=True
            )
            return
        
        await interaction.response.send_message("❌ Заявка отменена.", ephemeral=True)
    
    async def _get_events_forum_channel(self, guild: discord.Guild) -> Optional[discord.ForumChannel]:
        """Получить канал форума для событий"""
        # Ищем канал с названием содержащим "event" или "событи"
        for channel in guild.channels:
            if isinstance(channel, discord.ForumChannel):
                name_lower = channel.name.lower()
                if any(keyword in name_lower for keyword in ['event', 'событи', 'ивент']):
                    return channel
        
        # Если не найден, возвращаем первый форум канал
        for channel in guild.channels:
            if isinstance(channel, discord.ForumChannel):
                return channel
        
        return None
    
    async def _setup_thread_permissions(self, thread: discord.Thread, submitter_id: int, guild: discord.Guild):
        """Настроить права доступа к треду"""
        try:
            # Получаем конфигурацию гильдии для ролей рекрутеров
            import aiosqlite
            from database import DB_PATH
            
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("""
                    SELECT recruiter_roles FROM guild_config WHERE guild_id = ?
                """, (guild.id,))
                row = await cursor.fetchone()
                await cursor.close()
            
            if not row:
                return
            
            recruiter_role_ids = row[0]
            if not recruiter_role_ids:
                return
            
            # Парсим ID ролей
            role_ids = [int(rid) for rid in recruiter_role_ids.split(',') if rid.strip().isdigit()]
            
            # Запрещаем всем писать по умолчанию
            await thread.edit(
                send_messages=False
            )
            
            # Разрешаем заявителю
            submitter = guild.get_member(submitter_id)
            if submitter:
                await thread.set_permissions(submitter, send_messages=True, read_messages=True)
            
            # Разрешаем модераторам и администраторам
            for role_id in role_ids:
                role = guild.get_role(role_id)
                if role:
                    await thread.set_permissions(role, send_messages=True, read_messages=True)
            
            # Разрешаем администраторам
            for role in guild.roles:
                if role.permissions.administrator:
                    await thread.set_permissions(role, send_messages=True, read_messages=True)
                    
        except Exception as e:
            logger.warning(f"Не удалось настроить права треда: {e}")

class PointsInputModal(ui.Modal):
    """Модальное окно для ввода количества очков"""
    
    def __init__(self, submission_id: int):
        super().__init__(title="Указать очки за событие")
        self.submission_id = submission_id
    
    points_input = ui.TextInput(
        label="Очки на человека",
        placeholder="Введите количество очков для каждого участника",
        required=True,
        max_length=10
    )
    
    notes_input = ui.TextInput(
        label="Комментарий (опционально)",
        placeholder="Причина такого количества очков...",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            points_per_person = float(self.points_input.value)
            if points_per_person < 0:
                await interaction.response.send_message("❌ Количество очков не может быть отрицательным!", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("❌ Неверный формат числа!", ephemeral=True)
            return
        
        # Получаем детали заявки
        submission_details = await EventDatabase.get_submission_details(self.submission_id)
        if not submission_details:
            await interaction.response.send_message("❌ Заявка не найдена!", ephemeral=True)
            return
        
        guild_id = submission_details.get('guild_id')
        
        # Проверяем ограничения по датам
        can_award, error_message = await check_points_date_restrictions(guild_id)
        if not can_award:
            await interaction.response.send_message(error_message, ephemeral=True)
            return
        
        # Обновляем статус заявки
        logger.info(f"Обновляем статус заявки {self.submission_id} на 'approved'")
        success = await EventDatabase.update_submission_status(
            self.submission_id,
            "approved",
            interaction.user.id,
            final_points_per_person=points_per_person
        )
        
        if not success:
            logger.error(f"Ошибка обновления статуса заявки {self.submission_id}")
            await interaction.response.send_message("❌ Ошибка при обновлении заявки!", ephemeral=True)
            return
        else:
            logger.info(f"Статус заявки {self.submission_id} успешно обновлен на 'approved'")
        
        # Начисляем очки участникам
        participants = await EventDatabase.get_submission_participants(self.submission_id)
        guild_id = submission_details.get('guild_id')
        
        for participant_id in participants:
            await EventDatabase.add_user_points(guild_id, participant_id, points_per_person)
        
        # Создаем embed с результатом
        embed = discord.Embed(
            title="✅ Заявка одобрена и очки начислены",
            color=discord.Color.green()
        )
        
        embed.add_field(name="📝 Заявка", value=f"ID: {self.submission_id}", inline=True)
        embed.add_field(name="💰 Очки на человека", value=f"{points_per_person}", inline=True)
        embed.add_field(name="👥 Участников", value=f"{len(participants)}", inline=True)
        embed.add_field(name="📊 Всего очков", value=f"{points_per_person * len(participants)}", inline=True)
        embed.add_field(name="👤 Модератор", value=interaction.user.mention, inline=True)
        embed.add_field(name="⏰ Время", value=f"<t:{int(interaction.created_at.timestamp())}:F>", inline=True)
        
        if self.notes_input.value:
            embed.add_field(name="📝 Комментарий", value=self.notes_input.value, inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class MultiplierSelectMenu(ui.Select):
    """Меню выбора множителя очков для модераторов"""
    
    def __init__(self, submission_id: int):
        self.submission_id = submission_id
        
        options = []
        for value, label in get_multiplier_options():
            options.append(discord.SelectOption(
                label=f"Множитель {label}",
                value=value,
                description=f"Умножить базовые очки на {label}"
            ))
        
        super().__init__(
            placeholder="📊 Выберите множитель очков...",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        multiplier = float(self.values[0])
        
        # Получаем детали заявки для подтверждения
        submission_details = await EventDatabase.get_submission_details(self.submission_id)
        if not submission_details:
            await interaction.response.send_message("❌ Заявка не найдена.", ephemeral=True)
            return
        
        base_points = submission_details['base_points']
        group_size = submission_details['group_size']
        final_points = EventManager.calculate_final_points(base_points, multiplier, group_size)
        
        # Создаем embed подтверждения
        embed = discord.Embed(
            title="✅ Подтверждение начисления очков",
            color=discord.Color.green()
        )
        
        embed.add_field(name="📊 Базовые очки", value=str(base_points), inline=True)
        embed.add_field(name="✖️ Множитель", value=f"x{multiplier}", inline=True)
        embed.add_field(name="👥 Размер группы", value=str(group_size), inline=True)
        embed.add_field(
            name="🎯 Очки каждому участнику", 
            value=f"**{EventManager.format_points_display(final_points)}**",
            inline=False
        )
        embed.add_field(
            name="💰 Всего очков к начислению",
            value=f"**{EventManager.format_points_display(final_points * group_size)}**",
            inline=False
        )
        
        view = FinalApprovalView(self.submission_id, multiplier)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class FinalApprovalView(ui.View):
    """Финальное подтверждение начисления очков"""
    
    def __init__(self, submission_id: int, multiplier: float):
        super().__init__(timeout=300)
        self.submission_id = submission_id
        self.multiplier = multiplier
    
    @ui.button(label="✅ Начислить очки", style=discord.ButtonStyle.success)
    async def approve_button(self, interaction: discord.Interaction, button: ui.Button):
        success = await EventDatabase.approve_event_submission(
            self.submission_id,
            interaction.user.id,
            self.multiplier
        )
        
        if success:
            submission_details = await EventDatabase.get_submission_details(self.submission_id)
            final_points = submission_details['final_points_per_person']
            
            await interaction.response.send_message(
                f"✅ Очки начислены! Каждый участник получил **{EventManager.format_points_display(final_points)}** очков.",
                ephemeral=True
            )
            
            # Уведомляем в треде
            thread = interaction.guild.get_thread(submission_details['thread_id'])
            if thread:
                await thread.send(
                    f"✅ **Заявка одобрена!** Модератор {interaction.user.mention}\n"
                    f"🎯 Начислено **{EventManager.format_points_display(final_points)}** очков каждому участнику"
                )
        else:
            await interaction.response.send_message(
                "❌ Ошибка при начислении очков. Возможно, заявка уже обработана.",
                ephemeral=True
            )
    
    @ui.button(label="❌ Отклонить", style=discord.ButtonStyle.danger)
    async def reject_button(self, interaction: discord.Interaction, button: ui.Button):
        modal = RejectReasonModal(self.submission_id)
        await interaction.response.send_modal(modal)

class RejectReasonModal(ui.Modal):
    """Модальное окно для указания причины отклонения"""
    
    def __init__(self, submission_id: int):
        super().__init__(title="Причина отклонения")
        self.submission_id = submission_id
        
        self.reason_input = ui.TextInput(
            label="Причина отклонения",
            placeholder="Укажите причину отклонения заявки...",
            style=discord.TextStyle.paragraph,
            max_length=500,
            required=True
        )
        
        self.add_item(self.reason_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        reason = self.reason_input.value.strip()
        
        success = await EventDatabase.reject_event_submission(
            self.submission_id,
            interaction.user.id,
            reason
        )
        
        if success:
            # Создаем embed с результатом отклонения
            embed = discord.Embed(
                title="❌ Заявка отклонена",
                description=f"Заявка #{self.submission_id} была отклонена",
                color=discord.Color.red()
            )
            embed.add_field(name="Причина", value=reason, inline=False)
            embed.add_field(name="Модератор", value=interaction.user.mention, inline=True)
            
            # Создаем view для завершения заявки
            completion_view = EventCompletionView(self.submission_id, "rejected")
            
            await interaction.response.send_message(embed=embed, view=completion_view, ephemeral=False)
        else:
            await interaction.response.send_message(
                "❌ Ошибка при отклонении заявки.",
                ephemeral=True
            )

class PointsSelectionView(ui.View):
    """View для выбора количества очков"""
    
    def __init__(self, submission_id: int):
        super().__init__(timeout=300)  # 5 минут
        self.submission_id = submission_id
        self.add_item(PointsSelectMenu(submission_id))

class PointsSelectMenu(ui.Select):
    """Select Menu для выбора количества очков"""
    
    def __init__(self, submission_id: int):
        self.submission_id = submission_id
        
        options = [
            discord.SelectOption(
                label="1 очко", 
                value="1",
                description="Минимальное участие",
                emoji="🥉"
            ),
            discord.SelectOption(
                label="2 очка", 
                value="2",
                description="Базовое участие",
                emoji="🥈"
            ),
            discord.SelectOption(
                label="3 очка", 
                value="3",
                description="Хорошее участие",
                emoji="🥇"
            ),
            discord.SelectOption(
                label="5 очков", 
                value="5",
                description="Отличное участие",
                emoji="⭐"
            ),
            discord.SelectOption(
                label="8 очков", 
                value="8",
                description="Выдающееся участие",
                emoji="💎"
            ),
            discord.SelectOption(
                label="10 очков", 
                value="10",
                description="Максимальное участие",
                emoji="👑"
            ),
            discord.SelectOption(
                label="0 очков", 
                value="0",
                description="Отклонить без очков",
                emoji="❌"
            )
        ]
        
        super().__init__(
            placeholder="Выберите количество очков...",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        points_per_person = float(self.values[0])
        
        # Получаем детали заявки
        submission_details = await EventDatabase.get_submission_details(self.submission_id)
        if not submission_details:
            await interaction.response.send_message("❌ Заявка не найдена!", ephemeral=True)
            return
        
        if points_per_person == 0:
            # Отклоняем заявку
            logger.info(f"Отклоняем заявку {self.submission_id}")
            success = await EventDatabase.update_submission_status(
                self.submission_id,
                "rejected",
                interaction.user.id
            )
            
            if success:
                logger.info(f"Статус заявки {self.submission_id} успешно обновлен на 'rejected'")
                embed = discord.Embed(
                    title="❌ Заявка отклонена",
                    description=f"Заявка #{self.submission_id} была отклонена",
                    color=discord.Color.red()
                )
                embed.add_field(name="Модератор", value=interaction.user.mention, inline=True)
                
                # Создаем view для завершения заявки
                completion_view = EventCompletionView(self.submission_id, "rejected")
                
                await interaction.response.edit_message(embed=embed, view=completion_view)
            else:
                logger.error(f"Ошибка при отклонении заявки {self.submission_id}")
                await interaction.response.send_message("❌ Ошибка при отклонении заявки!", ephemeral=True)
        else:
            # Получаем guild_id для проверки дат
            guild_id = submission_details.get('guild_id')
            
            # Проверяем ограничения по датам
            can_award, error_message = await check_points_date_restrictions(guild_id)
            if not can_award:
                await interaction.response.send_message(error_message, ephemeral=True)
                return
            
            # Одобряем заявку
            logger.info(f"Одобряем заявку {self.submission_id} с {points_per_person} очками")
            success = await EventDatabase.update_submission_status(
                self.submission_id,
                "approved",
                interaction.user.id,
                final_points_per_person=points_per_person
            )
            
            if not success:
                logger.error(f"Ошибка при одобрении заявки {self.submission_id}")
                await interaction.response.send_message("❌ Ошибка при обновлении заявки!", ephemeral=True)
                return
            else:
                logger.info(f"Статус заявки {self.submission_id} успешно обновлен на 'approved' с {points_per_person} очками")
            
            # Начисляем очки участникам
            participants = await EventDatabase.get_submission_participants(self.submission_id)
            
            for participant_id in participants:
                await EventDatabase.add_user_points(guild_id, participant_id, points_per_person)
            
            # Создаем embed с результатом
            embed = discord.Embed(
                title="✅ Заявка одобрена",
                description=f"Заявка #{self.submission_id} одобрена и очки начислены!",
                color=discord.Color.green()
            )
            embed.add_field(name="💎 Очки на человека", value=str(points_per_person), inline=True)
            embed.add_field(name="👥 Участников", value=str(len(participants)), inline=True)
            embed.add_field(name="💰 Всего начислено", value=str(points_per_person * len(participants)), inline=True)
            embed.add_field(name="👤 Модератор", value=interaction.user.mention, inline=False)
            
            # Создаем view для завершения заявки
            completion_view = EventCompletionView(self.submission_id, "approved")
            
            await interaction.response.edit_message(embed=embed, view=completion_view)

async def update_original_submission_message(submission_id: int, status: str, moderator_name: str, points: float = None, reason: str = None, interaction=None):
    """Обновляет исходное сообщение заявки с новым статусом и закрывает тред"""
    try:
        logger.info(f"Начинаем обновление заявки {submission_id}, статус: {status}")
        
        # Получаем бота из interaction
        if interaction is None:
            logger.error("Для обновления сообщения требуется interaction")
            return
        
        bot = interaction.client
        async with aiosqlite.connect("potatos_recruit.db") as db:
            # Получаем данные заявки включая thread_id
            cursor = await db.execute("""
                SELECT message_id, guild_id, submitter_id, event_type, action, screenshot_url, thread_id
                FROM event_submissions 
                WHERE id = ?
            """, (submission_id,))
            
            row = await cursor.fetchone()
            if not row:
                logger.error(f"Заявка {submission_id} не найдена в базе данных")
                return
            
            message_id, guild_id, submitter_id, event_type, action, screenshot_url, thread_id = row
            logger.info(f"Данные заявки: message_id={message_id}, guild_id={guild_id}, thread_id={thread_id}")
            
            # Получаем участников
            participants = await EventDatabase.get_submission_participants(submission_id)
            logger.info(f"Найдено участников: {len(participants)}")
            
            # Создаем submission объект для отображения
            submission = EventSubmission(
                event_type=EventType(event_type),
                action=EventAction(action),
                participants=participants,
                submitter_id=submitter_id,
                screenshot_url=screenshot_url
            )
            
            # Создаем обновленный embed
            if status == "approved":
                embed = discord.Embed(
                    title="✅ Заявка одобрена",
                    description=f"**Событие**: {submission.get_event_display_name()}",
                    color=discord.Color.green()
                )
                embed.add_field(name="🆔 ID заявки", value=str(submission_id), inline=True)
                embed.add_field(name="💎 Очки на человека", value=str(points) if points else "N/A", inline=True)
                embed.add_field(name="👤 Модератор", value=moderator_name, inline=True)
                embed.add_field(name="👤 Заявитель", value=f"<@{submitter_id}>", inline=True)
                embed.add_field(name="👥 Участников", value=str(len(participants)), inline=True)
                embed.add_field(name="📅 Статус", value="✅ Одобрена", inline=True)
            elif status == "rejected":
                embed = discord.Embed(
                    title="❌ Заявка отклонена",
                    description=f"**Событие**: {submission.get_event_display_name()}",
                    color=discord.Color.red()
                )
                embed.add_field(name="🆔 ID заявки", value=str(submission_id), inline=True)
                embed.add_field(name="👤 Модератор", value=moderator_name, inline=True)
                embed.add_field(name="👤 Заявитель", value=f"<@{submitter_id}>", inline=True)
                embed.add_field(name="👥 Участников", value=str(len(participants)), inline=True)
                embed.add_field(name="📅 Статус", value="❌ Отклонена", inline=True)
                # Добавляем причину отклонения, если она есть
                if reason:
                    embed.add_field(name="📝 Причина отклонения", value=reason, inline=False)
            else:
                return
            
            # Добавляем список участников
            participants_list = []
            for user_id in participants:
                participants_list.append(f"<@{user_id}>")
            
            if participants_list:
                embed.add_field(
                    name="👥 Участники",
                    value='\n'.join(participants_list),
                    inline=False
                )
            
            # Добавляем скриншот если есть
            if screenshot_url:
                embed.add_field(name="📷 Скриншот", value=f"[Посмотреть]({screenshot_url})", inline=False)
                embed.set_image(url=screenshot_url)
            
            # Добавляем информацию о закрытии треда
            embed.set_footer(text="Заявка обработана • Тред закрыт")
            
            # Работаем с гильдиями бота
            for guild in bot.guilds:
                if guild.id == guild_id:
                    logger.info(f"Найдена гильдия {guild.name} (ID: {guild_id})")
                    
                    # Сначала отправляем уведомление в тред перед его закрытием
                    if thread_id:
                        logger.info(f"Ищем тред с ID: {thread_id}")
                        try:
                            thread = guild.get_thread(thread_id)
                            if thread:
                                logger.info(f"Тред найден: {thread.name}")
                                if status == "approved":
                                    await thread.send(
                                        f"✅ **Заявка одобрена** модератором {moderator_name}\n"
                                        f"💎 **Очки:** {points if points else 'N/A'} на человека\n"
                                        f"🔒 **Тред будет закрыт**"
                                    )
                                elif status == "rejected":
                                    reject_message = f"❌ **Заявка отклонена** модератором {moderator_name}\n"
                                    if reason:
                                        reject_message += f"📝 **Причина:** {reason}\n"
                                    reject_message += "🔒 **Тред будет закрыт**"
                                    await thread.send(reject_message)
                                
                                # Теперь закрываем тред
                                await thread.edit(archived=True, reason=f"Заявка {status} модератором {moderator_name}")
                                logger.info(f"Тред {thread_id} закрыт после {status} заявки {submission_id}")
                            else:
                                logger.warning(f"Тред с ID {thread_id} не найден")
                        except Exception as e:
                            logger.error(f"Ошибка работы с тредом {thread_id}: {e}")
                    else:
                        logger.warning(f"thread_id отсутствует для заявки {submission_id}")
                    
                    # Затем обновляем исходное сообщение
                    if message_id:
                        logger.info(f"Ищем сообщение с ID: {message_id}")
                        message_found = False
                        
                        # Сначала ищем в конкретном треде, если thread_id известен
                        if thread_id:
                            try:
                                target_thread = guild.get_thread(thread_id)
                                if target_thread:
                                    message = await target_thread.fetch_message(message_id)
                                    await message.edit(embed=embed, view=None)
                                    logger.info(f"Сообщение заявки {submission_id} обновлено со статусом {status} в треде {target_thread.name}")
                                    message_found = True
                            except discord.NotFound:
                                logger.warning(f"Сообщение {message_id} не найдено в треде {thread_id}")
                            except Exception as e:
                                logger.error(f"Ошибка при поиске сообщения в треде {thread_id}: {e}")
                        
                        # Если не найдено в целевом треде, ищем в обычных каналах
                        if not message_found:
                            logger.info("Ищем сообщение в обычных каналах...")
                            for channel in guild.text_channels:
                                try:
                                    message = await channel.fetch_message(message_id)
                                    await message.edit(embed=embed, view=None)
                                    logger.info(f"Сообщение заявки {submission_id} обновлено со статусом {status} в канале {channel.name}")
                                    message_found = True
                                    break
                                except discord.NotFound:
                                    continue
                                except discord.Forbidden:
                                    logger.warning(f"Нет прав для редактирования сообщения в канале {channel.name}")
                                    continue
                                except Exception as e:
                                    logger.error(f"Ошибка при обновлении сообщения в канале {channel.name}: {e}")
                                    continue
                        
                        # Если не найдено в обычных каналах, ищем во всех тредах
                        if not message_found:
                            logger.info("Сообщение не найдено в обычных каналах, ищем во всех тредах...")
                            for channel in guild.text_channels:
                                # Ищем в активных тредах
                                for thread in channel.threads:
                                    try:
                                        message = await thread.fetch_message(message_id)
                                        await message.edit(embed=embed, view=None)
                                        logger.info(f"Сообщение заявки {submission_id} обновлено со статусом {status} в треде {thread.name}")
                                        message_found = True
                                        break
                                    except discord.NotFound:
                                        continue
                                    except discord.Forbidden:
                                        continue
                                    except Exception:
                                        continue
                                
                                # Ищем в архивированных тредах
                                if not message_found:
                                    try:
                                        async for archived_thread in channel.archived_threads(limit=50):
                                            try:
                                                message = await archived_thread.fetch_message(message_id)
                                                await message.edit(embed=embed, view=None)
                                                logger.info(f"Сообщение заявки {submission_id} обновлено со статусом {status} в архивном треде {archived_thread.name}")
                                                message_found = True
                                                break
                                            except discord.NotFound:
                                                continue
                                            except discord.Forbidden:
                                                continue
                                            except Exception:
                                                continue
                                    except Exception as e:
                                        logger.error(f"Ошибка при поиске в архивных тредах канала {channel.name}: {e}")
                                
                                if message_found:
                                    break
                        
                        if not message_found:
                            logger.error(f"Сообщение с ID {message_id} не найдено ни в одном канале или треде")
                    else:
                        logger.warning(f"message_id отсутствует для заявки {submission_id}")
                    
                    break
            else:
                logger.error(f"Гильдия с ID {guild_id} не найдена")
            
            # Обновляем исходное сообщение заявки
            if status == "approved":
                await update_original_event_message_by_submission_id(
                    submission_id, 
                    bot.get_guild(guild_id), 
                    "✅ Одобрена", 
                    discord.Color.green()
                )
            elif status == "rejected":
                await update_original_event_message_by_submission_id(
                    submission_id, 
                    bot.get_guild(guild_id), 
                    "❌ Отклонена", 
                    discord.Color.red()
                )
            
    except Exception as e:
        logger.error(f"Ошибка обновления исходного сообщения заявки: {e}")

class EventModerationView(ui.View):
    """View для модерации событий"""
    
    def __init__(self, submission_id: int, submission: EventSubmission):
        super().__init__(timeout=None)  # Бессрочная view
        self.submission_id = submission_id
        self.submission = submission
    
    @ui.button(label="✅ Одобрить", style=discord.ButtonStyle.success, custom_id="approve_event")
    async def approve_button(self, interaction: discord.Interaction, button: ui.Button):
        # Проверяем права модератора (можно добавить проверку роли)
        if not (interaction.user.guild_permissions.manage_messages or 
                interaction.user.guild_permissions.administrator):
            await interaction.response.send_message(
                "❌ У вас нет прав для модерации событий.",
                ephemeral=True
            )
            return
        
        # Показываем Select Menu для выбора очков
        view = PointsSelectionView(self.submission_id)
        embed = discord.Embed(
            title="🎯 Выберите количество очков",
            description="Выберите подходящее количество очков для этого события:",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    @ui.button(label="❌ Отклонить", style=discord.ButtonStyle.danger, custom_id="reject_event")
    async def reject_button(self, interaction: discord.Interaction, button: ui.Button):
        # Проверяем права модератора
        if not (interaction.user.guild_permissions.manage_messages or 
                interaction.user.guild_permissions.administrator):
            await interaction.response.send_message(
                "❌ У вас нет прав для модерации событий.",
                ephemeral=True
            )
            return
        
        modal = RejectReasonModal(self.submission_id)
        await interaction.response.send_modal(modal)

class EventCompletionView(ui.View):
    """View для завершения заявки после одобрения/отклонения"""
    
    def __init__(self, submission_id: int, status: str):
        super().__init__(timeout=300)  # 5 минут на завершение
        self.submission_id = submission_id
        self.status = status  # "approved" или "rejected"
    
    @ui.button(label="🔒 Закрыть заявку", style=discord.ButtonStyle.secondary, custom_id="close_submission")
    async def close_submission_button(self, interaction: discord.Interaction, button: ui.Button):
        # Проверяем права модератора
        if not (interaction.user.guild_permissions.manage_messages or 
                interaction.user.guild_permissions.administrator):
            await interaction.response.send_message(
                "❌ У вас нет прав для завершения заявок.",
                ephemeral=True
            )
            return
        
        try:
            # Получаем детали заявки
            submission_details = await EventDatabase.get_submission_details(self.submission_id)
            if not submission_details:
                await interaction.response.send_message("❌ Заявка не найдена!", ephemeral=True)
                return
            
            guild_id = submission_details.get('guild_id')
            guild = interaction.guild or interaction.client.get_guild(guild_id)
            
            if not guild:
                await interaction.response.send_message("❌ Гильдия не найдена!", ephemeral=True)
                return
            
            # Обновляем исходное сообщение заявки
            if self.status == "approved":
                await update_original_event_message_by_submission_id(
                    self.submission_id, 
                    guild, 
                    "✅ Одобрена", 
                    discord.Color.green()
                )
                status_text = "одобрена"
                status_emoji = "✅"
            else:  # rejected
                await update_original_event_message_by_submission_id(
                    self.submission_id, 
                    guild, 
                    "❌ Отклонена", 
                    discord.Color.red()
                )
                status_text = "отклонена"
                status_emoji = "❌"
            
            # Обновляем embed треда на "завершено"
            embed = discord.Embed(
                title=f"{status_emoji} Заявка {status_text} и закрыта",
                description=f"Заявка #{self.submission_id} была {status_text} и успешно закрыта модератором {interaction.user.mention}.",
                color=discord.Color.green() if self.status == "approved" else discord.Color.red()
            )
            embed.add_field(name="Модератор", value=interaction.user.mention, inline=True)
            embed.add_field(name="Статус", value=f"{status_emoji} {status_text.capitalize()}", inline=True)
            
            # Отключаем все кнопки
            for item in self.children:
                item.disabled = True
            
            await interaction.response.edit_message(embed=embed, view=self)
            
            # Закрываем тред (архивируем)
            if hasattr(interaction.channel, 'archived'):
                try:
                    await interaction.channel.edit(archived=True, locked=True)
                except Exception as e:
                    logger.warning(f"Не удалось заархивировать тред: {e}")
            
        except Exception as e:
            logger.error(f"Ошибка при закрытии заявки {self.submission_id}: {e}")
            await interaction.response.send_message(f"❌ Ошибка при закрытии заявки: {e}", ephemeral=True)

class EventSubmissionView(ui.View):
    """Главная view для подачи заявок на события"""
    
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(EventSelectMenu())

# Кнопка для запуска подачи заявки на событие
class EventSubmitButton(ui.Button):
    def __init__(self):
        super().__init__(
            label="🎯 Подать заявку на событие",
            style=discord.ButtonStyle.primary,
            custom_id="event_submit_button"
        )
    
    async def callback(self, interaction: discord.Interaction):
        view = EventSubmissionView()
        await interaction.response.send_message(
            "🎯 **Подача заявки на игровое событие**\n\n"
            "Выберите тип события из списка ниже:",
            view=view,
            ephemeral=True
        )

class PersistentEventSubmitView(ui.View):
    """Постоянная view с кнопкой подачи заявки"""
    
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(EventSubmitButton())

# ─── Компоненты магазина ──────────────────────────────────────────────────────

class ShopModerationView(ui.View):
    """View с кнопками для модерации покупок"""
    
    def __init__(self, purchase_id: int, buyer_id: int = 0, item = None):
        super().__init__(timeout=24*60*60)  # 24 часа вместо None для персистентности
        self.purchase_id = purchase_id
        self.buyer_id = buyer_id
        self.item = item
        
        # Создаем кнопки с уникальными custom_id для каждой покупки
        self.clear_items()
        
        approve_btn = ui.Button(
            label="✅ Выдать",
            style=discord.ButtonStyle.success,
            custom_id=f"shop_approve_{purchase_id}"
        )
        approve_btn.callback = self._approve_callback
        
        reject_btn = ui.Button(
            label="❌ Отклонить", 
            style=discord.ButtonStyle.danger,
            custom_id=f"shop_reject_{purchase_id}"
        )
        reject_btn.callback = self._reject_callback
        
        self.add_item(approve_btn)
        self.add_item(reject_btn)
    
    async def _approve_callback(self, interaction: discord.Interaction):
        """Одобрить покупку"""
        # Получаем purchase_id из custom_id кнопки
        button = interaction.data.get('custom_id', '')
        if button.startswith('shop_approve_'):
            purchase_id = int(button.split('_')[-1])
        else:
            purchase_id = self.purchase_id
            
        if purchase_id <= 0:
            await interaction.response.send_message("❌ Ошибка: не найден ID покупки", ephemeral=True)
            return
        
        # Получаем данные покупки из базы
        purchase_data = await EventDatabase.get_purchase_by_id(purchase_id)
        if not purchase_data:
            await interaction.response.send_message("❌ Покупка не найдена", ephemeral=True)
            return
        
        # Проверяем права
        if not interaction.user.guild_permissions.administrator:
            guild_config = await EventDatabase.get_guild_config(interaction.guild.id)
            points_moderator_roles = guild_config.get('points_moderator_roles', '')
            
            user_role_ids = [role.id for role in interaction.user.roles]
            moderator_role_ids = [int(rid.strip()) for rid in points_moderator_roles.split(',') if rid.strip().isdigit()]
            
            if not any(role_id in user_role_ids for role_id in moderator_role_ids):
                await interaction.response.send_message(
                    "❌ У вас нет прав для модерации покупок!", 
                    ephemeral=True
                )
                return
        
        await interaction.response.defer()
        
        # Обрабатываем покупку
        success = await EventDatabase.process_shop_purchase(
            purchase_id=purchase_id,
            admin_id=interaction.user.id,
            completed=True,
            admin_notes="Выдано через кнопку модерации"
        )
        
        if success:
            # Получаем информацию о товаре
            from events import ShopManager
            item = ShopManager.get_item_by_id(purchase_data['item_id'])
            
            # Обновляем сообщение
            embed = discord.Embed(
                title="✅ Покупка одобрена!",
                color=discord.Color.green(),
                timestamp=interaction.created_at
            )
            
            embed.add_field(name="🆔 ID покупки", value=f"#{purchase_id}", inline=True)
            embed.add_field(name="👤 Покупатель", value=f"<@{purchase_data['user_id']}>", inline=True)
            embed.add_field(name="🎁 Товар", value=purchase_data['item_name'], inline=True)
            embed.add_field(name="👮 Модератор", value=interaction.user.mention, inline=True)
            if item:
                embed.add_field(name="📝 Описание", value=item.description, inline=False)
            
            embed.set_footer(text="Товар выдан!")
            
            # Удаляем кнопки
            view = ui.View()
            
            await interaction.edit_original_response(embed=embed, view=view)
            
            # Уведомляем покупателя в ЛС
            try:
                buyer = interaction.guild.get_member(purchase_data['user_id'])
                if buyer:
                    dm_embed = discord.Embed(
                        title="✅ Ваша покупка выдана!",
                        description=f"Товар **{purchase_data['item_name']}** был выдан модератором {interaction.user.mention}",
                        color=discord.Color.green()
                    )
                    await buyer.send(embed=dm_embed)
            except:
                pass  # Игнорируем ошибки отправки ЛС
                
        else:
            await interaction.followup.send(
                "❌ Ошибка при обработке покупки. Возможно, она уже была обработана.",
                ephemeral=True
            )
    
    async def _reject_callback(self, interaction: discord.Interaction):
        """Отклонить покупку"""
        # Получаем purchase_id из custom_id кнопки
        button = interaction.data.get('custom_id', '')
        if button.startswith('shop_reject_'):
            purchase_id = int(button.split('_')[-1])
        else:
            purchase_id = self.purchase_id
            
        if purchase_id <= 0:
            await interaction.response.send_message("❌ Ошибка: не найден ID покупки", ephemeral=True)
            return
        
        # Получаем данные покупки из базы
        purchase_data = await EventDatabase.get_purchase_by_id(purchase_id)
        if not purchase_data:
            await interaction.response.send_message("❌ Покупка не найдена", ephemeral=True)
            return
        
        # Проверяем права
        if not interaction.user.guild_permissions.administrator:
            guild_config = await EventDatabase.get_guild_config(interaction.guild.id)
            points_moderator_roles = guild_config.get('points_moderator_roles', '')
            
            user_role_ids = [role.id for role in interaction.user.roles]
            moderator_role_ids = [int(rid.strip()) for rid in points_moderator_roles.split(',') if rid.strip().isdigit()]
            
            if not any(role_id in user_role_ids for role_id in moderator_role_ids):
                await interaction.response.send_message(
                    "❌ У вас нет прав для модерации покупок!", 
                    ephemeral=True
                )
                return
        
        # Показываем модальное окно для ввода причины
        modal = RejectPurchaseModal(purchase_id, purchase_data['user_id'], purchase_data)
        await interaction.response.send_modal(modal)

class RejectPurchaseModal(ui.Modal):
    """Модальное окно для ввода причины отклонения покупки"""
    
    def __init__(self, purchase_id: int, buyer_id: int, purchase_data: dict):
        super().__init__(title="Отклонение покупки")
        self.purchase_id = purchase_id
        self.buyer_id = buyer_id
        self.purchase_data = purchase_data
    
    reason = ui.TextInput(
        label="Причина отклонения",
        placeholder="Укажите причину отклонения покупки...",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # Обрабатываем покупку
        success = await EventDatabase.process_shop_purchase(
            purchase_id=self.purchase_id,
            admin_id=interaction.user.id,
            completed=False,
            admin_notes=self.reason.value
        )
        
        if success:
            # Обновляем исходное сообщение
            embed = discord.Embed(
                title="❌ Покупка отклонена!",
                color=discord.Color.red(),
                timestamp=interaction.created_at
            )
            
            embed.add_field(name="🆔 ID покупки", value=f"#{self.purchase_id}", inline=True)
            embed.add_field(name="👤 Покупатель", value=f"<@{self.buyer_id}>", inline=True)
            embed.add_field(name="🎁 Товар", value=self.purchase_data['item_name'], inline=True)
            embed.add_field(name="👮 Модератор", value=interaction.user.mention, inline=True)
            embed.add_field(name="📝 Причина", value=self.reason.value, inline=False)
            
            embed.set_footer(text="Очки возвращены покупателю")
            
            # Находим исходное сообщение и обновляем его
            try:
                # Получаем сообщение из interaction
                original_message = None
                
                # Ищем сообщение в канале
                async for message in interaction.channel.history(limit=50):
                    if (message.embeds and 
                        len(message.embeds) > 0 and 
                        message.embeds[0].title == "🛒 Новая покупка в магазине!" and
                        f"#{self.purchase_id}" in str(message.embeds[0].fields[0].value)):
                        original_message = message
                        break
                
                if original_message:
                    view = ui.View()  # Пустое view для удаления кнопок
                    await original_message.edit(embed=embed, view=view)
                else:
                    await interaction.followup.send(embed=embed)
                    
            except Exception as e:
                logger.error(f"Ошибка обновления сообщения о покупке: {e}")
                await interaction.followup.send(embed=embed)
            
            # Уведомляем покупателя в ЛС
            try:
                buyer = interaction.guild.get_member(self.buyer_id)
                if buyer:
                    dm_embed = discord.Embed(
                        title="❌ Ваша покупка отклонена",
                        description=f"Покупка **{self.purchase_data['item_name']}** была отклонена.",
                        color=discord.Color.red()
                    )
                    dm_embed.add_field(name="📝 Причина", value=self.reason.value, inline=False)
                    dm_embed.add_field(name="💰 Возврат", value=f"Вам возвращено {self.purchase_data['points_cost']} очков", inline=False)
                    await buyer.send(embed=dm_embed)
            except:
                pass  # Игнорируем ошибки отправки ЛС
                
        else:
            await interaction.followup.send(
                "❌ Ошибка при обработке покупки. Возможно, она уже была обработана.",
                ephemeral=True
            )

class ShopSelectMenu(ui.Select):
    """Меню выбора товара в магазине"""
    
    def __init__(self):
        options = []
        
        for item in SHOP_ITEMS.values():
            options.append(discord.SelectOption(
                label=f"{item.name} - {item.cost} очков",
                value=item.id,
                description=item.description,
                emoji=item.emoji
            ))
        
        super().__init__(
            placeholder="🛒 Выберите товар для покупки...",
            min_values=1,
            max_values=1,
            options=options
        )
    
    async def callback(self, interaction: discord.Interaction):
        item_id = self.values[0]
        item = ShopManager.get_item_by_id(item_id)
        
        if not item:
            await interaction.response.send_message("❌ Товар не найден.", ephemeral=True)
            return
        
        # Проверяем баланс пользователя
        points, _ = await EventDatabase.get_user_points(interaction.guild.id, interaction.user.id)
        
        if points < item.cost:
            await interaction.response.send_message(
                f"❌ Недостаточно очков!\n"
                f"💎 Нужно: **{item.cost}** очков\n"
                f"💰 У вас: **{EventManager.format_points_display(points)}** очков\n"
                f"📈 Не хватает: **{item.cost - points}** очков",
                ephemeral=True
            )
            return
        
        # Показываем подтверждение покупки
        embed = discord.Embed(
            title="🛒 Подтверждение покупки",
            description=f"Вы собираетесь купить: **{item.name}**",
            color=discord.Color.orange()
        )
        
        embed.add_field(name="💎 Стоимость", value=f"{item.cost} очков", inline=True)
        embed.add_field(name="💰 Ваш баланс", value=f"{EventManager.format_points_display(points)} очков", inline=True)
        embed.add_field(name="💳 Остаток", value=f"{EventManager.format_points_display(points - item.cost)} очков", inline=True)
        embed.add_field(name="📝 Описание", value=item.description, inline=False)
        
        embed.set_footer(text="⚠️ После покупки очки будут списаны! Товар выдается модератором.")
        
        view = ShopPurchaseConfirmView(item)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class ShopPurchaseConfirmView(ui.View):
    """View для подтверждения покупки"""
    
    def __init__(self, item):
        super().__init__(timeout=300)
        self.item = item
    
    @ui.button(label="✅ Купить", style=discord.ButtonStyle.success)
    async def confirm_purchase(self, interaction: discord.Interaction, button: ui.Button):
        # Совершаем покупку
        success = await EventDatabase.create_shop_purchase(
            guild_id=interaction.guild.id,
            user_id=interaction.user.id,
            item_id=self.item.id,
            item_name=self.item.name,
            points_cost=self.item.cost
        )
        
        if success:
            embed = discord.Embed(
                title="✅ Покупка успешна!",
                description=f"Вы купили: **{self.item.name}**",
                color=discord.Color.green()
            )
            
            embed.add_field(
                name="📋 Что дальше?",
                value=(
                    "1. Ваши очки списаны\n"
                    "2. Заявка отправлена модераторам\n"
                    "3. Ожидайте выдачи товара в игре\n"
                    "4. Вы получите уведомление о выдаче"
                ),
                inline=False
            )
            
            embed.set_footer(text="💡 Проверить статус покупки можно командой /balance")
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
            # Уведомляем модераторов о новой покупке
            await self._notify_moderators_about_purchase(interaction)
            
        else:
            await interaction.response.send_message(
                "❌ Ошибка при покупке. Возможно, недостаточно очков или произошла техническая ошибка.",
                ephemeral=True
            )
    
    async def _notify_moderators_about_purchase(self, interaction: discord.Interaction):
        """Уведомить модераторов о новой покупке"""
        try:
            # Получаем конфигурацию гильдии
            guild_config = await EventDatabase.get_guild_config(interaction.guild.id)
            points_moderator_roles = guild_config.get('points_moderator_roles', '')
            
            # Формируем пинги ролей
            ping_text = ""
            if points_moderator_roles:
                role_ids = [rid.strip() for rid in points_moderator_roles.split(',') if rid.strip()]
                for role_id in role_ids:
                    try:
                        role = interaction.guild.get_role(int(role_id))
                        if role:
                            ping_text += f"{role.mention} "
                    except ValueError:
                        continue
            
            if ping_text:
                # Получаем ID покупки из базы данных
                purchase_id = await EventDatabase.get_latest_purchase_id(
                    interaction.guild.id, 
                    interaction.user.id
                )
                
                # Создаем embed для уведомления
                embed = discord.Embed(
                    title="🛒 Новая покупка в магазине!",
                    color=discord.Color.blue(),
                    timestamp=interaction.created_at
                )
                
                embed.add_field(name="🆔 ID покупки", value=f"#{purchase_id}", inline=True)
                embed.add_field(name="👤 Покупатель", value=interaction.user.mention, inline=True)
                embed.add_field(name="🎁 Товар", value=self.item.name, inline=True)
                embed.add_field(name="💰 Стоимость", value=f"{self.item.cost} очков", inline=True)
                embed.add_field(name="📝 Описание", value=self.item.description, inline=False)
                
                embed.set_footer(text="Используйте кнопки ниже или команду /shop_process для обработки")
                
                # Создаем кнопки для модерации покупки
                view = ShopModerationView(purchase_id, interaction.user.id, self.item)
                
                content = f"{ping_text}\n🔔 **Требуется обработка покупки в магазине!**"
                
                # Отправляем уведомление в канал магазина или в канал событий
                shop_channel_id = guild_config.get('shop_channel')
                target_channel_id = shop_channel_id if shop_channel_id else guild_config.get('events_channel')
                
                if target_channel_id:
                    try:
                        channel = interaction.guild.get_channel(int(target_channel_id))
                        if channel:
                            await channel.send(content=content, embed=embed, view=view)
                        else:
                            # Если каналы не найдены, отправляем в текущий канал
                            await interaction.followup.send(content=content, embed=embed, view=view)
                    except:
                        await interaction.followup.send(content=content, embed=embed, view=view)
                else:
                    await interaction.followup.send(content=content, embed=embed, view=view)
                    
        except Exception as e:
            logger.error(f"Ошибка уведомления модераторов о покупке: {e}")
    
    @ui.button(label="❌ Отменить", style=discord.ButtonStyle.secondary)
    async def cancel_purchase(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("❌ Покупка отменена.", ephemeral=True)

class ShopButton(ui.Button):
    """Кнопка открытия магазина"""
    
    def __init__(self):
        super().__init__(
            label="🛒 Магазин",
            style=discord.ButtonStyle.secondary,
            custom_id="shop_button"
        )
    
    async def callback(self, interaction: discord.Interaction):
        # Получаем баланс пользователя
        points, events_count = await EventDatabase.get_user_points(
            interaction.guild.id, 
            interaction.user.id
        )
        
        embed = ShopManager.get_shop_embed()
        embed.add_field(
            name="💰 Ваш баланс",
            value=f"**{EventManager.format_points_display(points)}** очков\n🎪 Событий: {events_count}",
            inline=False
        )
        
        view = ui.View()
        view.add_item(ShopSelectMenu())
        
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class BalanceButton(ui.Button):
    """Кнопка проверки баланса"""
    
    def __init__(self):
        super().__init__(
            label="💰 Баланс",
            style=discord.ButtonStyle.secondary,
            custom_id="balance_button"
        )
    
    async def callback(self, interaction: discord.Interaction):
        # Получаем данные пользователя
        points, events_count = await EventDatabase.get_user_points(
            interaction.guild.id, 
            interaction.user.id
        )
        
        # Получаем историю событий
        event_history = await EventDatabase.get_user_event_history(
            interaction.guild.id,
            interaction.user.id,
            limit=3
        )
        
        # Получаем историю покупок
        purchase_history = await EventDatabase.get_user_purchase_history(
            interaction.guild.id,
            interaction.user.id,
            limit=3
        )
        
        embed = discord.Embed(
            title="💰 Ваш баланс",
            color=discord.Color.gold()
        )
        
        embed.add_field(
            name="💎 Очки",
            value=f"**{EventManager.format_points_display(points)}**",
            inline=True
        )
        
        embed.add_field(
            name="🎪 События",
            value=f"**{events_count}**",
            inline=True
        )
        
        if points > 0:
            embed.add_field(
                name="📊 Среднее за событие",
                value=f"**{EventManager.format_points_display(points / events_count)}**",
                inline=True
            )
        
        # История событий
        if event_history:
            history_text = ""
            for event in event_history:
                status_emoji = {"approved": "✅", "rejected": "❌", "pending": "⏳"}.get(event['status'], "❓")
                points_text = f"+{EventManager.format_points_display(event['points_awarded'])}" if event['points_awarded'] else "0"
                
                # Форматируем тип события
                try:
                    event_type = EventType(event['event_type'])
                    action = EventAction(event['action'])
                    from events import EVENT_CONFIG
                    event_info = EVENT_CONFIG[event_type]
                    event_name = f"{event_info.emoji} {event_info.name_ru}"
                except:
                    event_name = f"{event['event_type']} ({event['action']})"
                
                history_text += f"{status_emoji} {event_name} - {points_text}\n"
            
            embed.add_field(
                name="📝 Последние события",
                value=history_text[:300] + ("..." if len(history_text) > 300 else ""),
                inline=False
            )
        
        # История покупок
        if purchase_history:
            purchase_text = ""
            for purchase in purchase_history:
                status_emoji = {"completed": "✅", "rejected": "❌", "pending": "⏳"}.get(purchase['status'], "❓")
                purchase_text += f"{status_emoji} {purchase['item_name']} - {purchase['points_cost']} очков\n"
            
            embed.add_field(
                name="🛒 Последние покупки",
                value=purchase_text[:300] + ("..." if len(purchase_text) > 300 else ""),
                inline=False
            )
        
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.set_footer(text="💡 Участвуйте в событиях чтобы получить больше очков!")
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ─── Единый интерфейс ─────────────────────────────────────────────────────────
class UnifiedEventView(ui.View):
    """Единый интерфейс с всеми кнопками"""
    
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(EventSubmitButton())
        self.add_item(BalanceButton())
        self.add_item(ShopButton())
        self.add_item(PointsRequestButton())

class PointsRequestButton(ui.Button):
    """Кнопка для подачи заявки на зачисление очков"""
    
    def __init__(self):
        super().__init__(
            label="💰 Зачислить очки",
            style=discord.ButtonStyle.secondary,
            custom_id="points_request_btn"
        )
    
    async def callback(self, interaction: discord.Interaction):
        modal = PointsRequestModal()
        await interaction.response.send_modal(modal)

class PointsRequestModal(ui.Modal):
    """Модальное окно для заявки на зачисление очков"""
    
    def __init__(self):
        super().__init__(title="📝 Заявка на зачисление очков")
    
    recipient = ui.TextInput(
        label="Получатель очков",
        placeholder="@username или ID пользователя",
        required=True,
        max_length=100
    )
    
    points = ui.TextInput(
        label="Количество очков",
        placeholder="Введите количество очков для зачисления",
        required=True,
        max_length=10
    )
    
    reason = ui.TextInput(
        label="Причина зачисления",
        placeholder="Опишите причину зачисления очков",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            points_amount = float(self.points.value)
            if points_amount <= 0:
                await interaction.response.send_message("❌ Количество очков должно быть положительным числом!", ephemeral=True)
                return
        except ValueError:
            await interaction.response.send_message("❌ Неверный формат количества очков!", ephemeral=True)
            return
        
        # Получаем конфигурацию гильдии для пинга ролей
        guild_config = await EventDatabase.get_guild_config(interaction.guild.id)
        points_moderator_roles = guild_config.get('points_moderator_roles', '')
        
        # Создаем embed для заявки
        embed = discord.Embed(
            title="💰 Заявка на зачисление очков",
            color=discord.Color.gold(),
            timestamp=interaction.created_at
        )
        
        embed.add_field(name="Получатель", value=self.recipient.value, inline=True)
        embed.add_field(name="Количество очков", value=f"+{points_amount}", inline=True)
        embed.add_field(name="Заявитель", value=interaction.user.mention, inline=True)
        embed.add_field(name="Причина", value=self.reason.value, inline=False)
        
        # Создаем view с кнопками для модераторов
        view = PointsRequestView(
            recipient_input=self.recipient.value,
            points_amount=points_amount,
            reason=self.reason.value,
            requester_id=interaction.user.id
        )
        
        # Формируем пинги ролей
        ping_text = ""
        if points_moderator_roles:
            role_ids = [rid.strip() for rid in points_moderator_roles.split(',') if rid.strip()]
            for role_id in role_ids:
                try:
                    role = interaction.guild.get_role(int(role_id))
                    if role:
                        ping_text += f"{role.mention} "
                except ValueError:
                    continue
        
        content = f"{ping_text}\n🔔 **Новая заявка на зачисление очков!**" if ping_text else "🔔 **Новая заявка на зачисление очков!**"
        
        await interaction.response.send_message(
            content=content,
            embed=embed,
            view=view
        )

class PointsRequestView(ui.View):
    """View для обработки заявок на зачисление очков"""
    
    def __init__(self, recipient_input: str, points_amount: float, reason: str, requester_id: int):
        super().__init__(timeout=None)
        self.recipient_input = recipient_input
        self.points_amount = points_amount
        self.reason = reason
        self.requester_id = requester_id
    
    @ui.button(label="✅ Одобрить", style=discord.ButtonStyle.success, custom_id="approve_points")
    async def approve_points(self, interaction: discord.Interaction, button: ui.Button):
        # Проверяем права модератора
        guild_config = await EventDatabase.get_guild_config(interaction.guild.id)
        points_moderator_roles = guild_config.get('points_moderator_roles', '')
        
        has_permission = False
        if points_moderator_roles:
            role_ids = [rid.strip() for rid in points_moderator_roles.split(',') if rid.strip()]
            user_role_ids = [str(role.id) for role in interaction.user.roles]
            has_permission = any(role_id in user_role_ids for role_id in role_ids)
        
        # Также проверяем админские роли
        admin_role = guild_config.get('admin_role')
        moderator_role = guild_config.get('moderator_role')
        if admin_role and str(interaction.user.get_role(int(admin_role))) in [str(role) for role in interaction.user.roles]:
            has_permission = True
        if moderator_role and str(interaction.user.get_role(int(moderator_role))) in [str(role) for role in interaction.user.roles]:
            has_permission = True
        
        if not has_permission:
            await interaction.response.send_message("❌ У вас нет прав для обработки заявок на очки!", ephemeral=True)
            return
        
        # Парсим получателя
        recipient_user = None
        
        # Пробуем найти по упоминанию или ID
        user_id_match = re.search(r'<@!?(\d+)>|(\d+)', self.recipient_input)
        if user_id_match:
            user_id = int(user_id_match.group(1) or user_id_match.group(2))
            recipient_user = interaction.guild.get_member(user_id)
        
        if not recipient_user:
            await interaction.response.send_message("❌ Не удалось найти указанного пользователя!", ephemeral=True)
            return
        
        # Зачисляем очки
        success = await EventDatabase.add_user_points(
            guild_id=interaction.guild.id,
            user_id=recipient_user.id,
            points=self.points_amount
        )
        
        if success:
            # Обновляем embed
            embed = interaction.message.embeds[0]
            embed.color = discord.Color.green()
            embed.add_field(name="✅ Статус", value=f"Одобрено {interaction.user.mention}", inline=False)
            
            # Убираем кнопки
            await interaction.response.edit_message(embed=embed, view=None)
            
            # Отправляем уведомление получателю
            try:
                await recipient_user.send(
                    f"🎉 Вам зачислено **{self.points_amount}** очков!\n"
                    f"Причина: {self.reason}\n"
                    f"Одобрено: {interaction.user.mention}"
                )
            except discord.Forbidden:
                pass
        else:
            await interaction.response.send_message("❌ Ошибка при зачислении очков!", ephemeral=True)
    
    @ui.button(label="❌ Отклонить", style=discord.ButtonStyle.danger, custom_id="reject_points")
    async def reject_points(self, interaction: discord.Interaction, button: ui.Button):
        # Проверяем права модератора (аналогично approve_points)
        guild_config = await EventDatabase.get_guild_config(interaction.guild.id)
        points_moderator_roles = guild_config.get('points_moderator_roles', '')
        
        has_permission = False
        if points_moderator_roles:
            role_ids = [rid.strip() for rid in points_moderator_roles.split(',') if rid.strip()]
            user_role_ids = [str(role.id) for role in interaction.user.roles]
            has_permission = any(role_id in user_role_ids for role_id in role_ids)
        
        admin_role = guild_config.get('admin_role')
        moderator_role = guild_config.get('moderator_role')
        if admin_role and str(interaction.user.get_role(int(admin_role))) in [str(role) for role in interaction.user.roles]:
            has_permission = True
        if moderator_role and str(interaction.user.get_role(int(moderator_role))) in [str(role) for role in interaction.user.roles]:
            has_permission = True
        
        if not has_permission:
            await interaction.response.send_message("❌ У вас нет прав для обработки заявок на очки!", ephemeral=True)
            return
        
        # Обновляем embed
        embed = interaction.message.embeds[0]
        embed.color = discord.Color.red()
        embed.add_field(name="❌ Статус", value=f"Отклонено {interaction.user.mention}", inline=False)
        
        # Убираем кнопки
        await interaction.response.edit_message(embed=embed, view=None)

class ResetPointsConfirmationView(ui.View):
    """View для подтверждения сброса очков всем пользователям"""
    
    def __init__(self):
        super().__init__(timeout=60)  # 1 минута на подтверждение
    
    @ui.button(label="✅ Да, обнулить очки всем", style=discord.ButtonStyle.danger)
    async def confirm_reset(self, interaction: discord.Interaction, button: ui.Button):
        # Двойная проверка прав администратора
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ Только администраторы могут выполнить это действие.",
                ephemeral=True
            )
            return
        
        try:
            # Выполняем сброс очков
            success = await EventDatabase.reset_all_points(interaction.guild.id)
            
            if success:
                embed = discord.Embed(
                    title="✅ Очки обнулены",
                    description="Очки всех пользователей на сервере были успешно обнулены.",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="👤 Администратор", 
                    value=interaction.user.mention, 
                    inline=True
                )
                embed.set_footer(text=f"Выполнено {interaction.user.display_name}")
                
                # Отключаем кнопки
                for item in self.children:
                    item.disabled = True
                
                await interaction.response.edit_message(embed=embed, view=self)
            else:
                await interaction.response.send_message(
                    "❌ Ошибка при обнулении очков. Проверьте логи.",
                    ephemeral=True
                )
                
        except Exception as e:
            logger.error(f"Ошибка сброса очков: {e}")
            await interaction.response.send_message(
                f"❌ Ошибка при обнулении очков: {e}",
                ephemeral=True
            )
    
    @ui.button(label="❌ Отмена", style=discord.ButtonStyle.secondary)
    async def cancel_reset(self, interaction: discord.Interaction, button: ui.Button):
        embed = discord.Embed(
            title="❌ Сброс отменен",
            description="Очки пользователей остались без изменений.",
            color=discord.Color.orange()
        )
        
        # Отключаем кнопки
        for item in self.children:
            item.disabled = True
        
        await interaction.response.edit_message(embed=embed, view=self)
