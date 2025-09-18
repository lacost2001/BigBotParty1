# -*- coding: utf-8 -*-
"""
Модуль для работы с базой данных событий и очков
"""

import aiosqlite
import logging
import os
from typing import List, Optional, Dict, Tuple
from datetime import datetime, timezone
from .events import EventType, EventAction, EventSubmission

logger = logging.getLogger("potatos_recruit.database")

# Используем абсолютный путь к базе данных в корне проекта
DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "potatos_recruit.db")

class EventDatabase:
    """Класс для работы с базой данных событий"""
    
    @staticmethod
    async def init_event_tables():
        """Инициализация таблиц для системы событий"""
        async with aiosqlite.connect(DB_PATH) as db:
            await db.executescript("""
                -- Таблица для хранения заявок на события
                CREATE TABLE IF NOT EXISTS event_submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    submitter_id INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    action TEXT NOT NULL,
                    group_size INTEGER NOT NULL DEFAULT 1,
                    description TEXT,
                    screenshot_url TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    base_points REAL NOT NULL DEFAULT 0.0,
                    final_multiplier REAL DEFAULT NULL,
                    final_points_per_person REAL DEFAULT NULL,
                    reviewer_id INTEGER DEFAULT NULL,
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT DEFAULT NULL,
                    thread_id INTEGER DEFAULT NULL,
                    message_id INTEGER DEFAULT NULL
                );
                
                -- Таблица для участников событий
                CREATE TABLE IF NOT EXISTS event_participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    submission_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    points_awarded REAL DEFAULT 0.0,
                    FOREIGN KEY (submission_id) REFERENCES event_submissions (id) ON DELETE CASCADE
                );
                
                -- Таблица для общих очков пользователей
                CREATE TABLE IF NOT EXISTS user_points (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    total_points REAL NOT NULL DEFAULT 0.0,
                    events_participated INTEGER NOT NULL DEFAULT 0,
                    last_updated TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                );
                
                -- Индексы для оптимизации
                CREATE INDEX IF NOT EXISTS idx_event_submissions_guild_status 
                ON event_submissions (guild_id, status);
                
                CREATE INDEX IF NOT EXISTS idx_event_submissions_submitter 
                ON event_submissions (submitter_id);
                
                CREATE INDEX IF NOT EXISTS idx_event_participants_user 
                ON event_participants (user_id);
                
                CREATE INDEX IF NOT EXISTS idx_user_points_guild_points 
                ON user_points (guild_id, total_points DESC);
                
                -- Таблица для покупок в магазине
                CREATE TABLE IF NOT EXISTS shop_purchases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    item_id TEXT NOT NULL,
                    item_name TEXT NOT NULL,
                    points_cost INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    admin_notes TEXT,
                    created_at TEXT NOT NULL,
                    processed_at TEXT DEFAULT NULL,
                    processed_by INTEGER DEFAULT NULL
                );
                
                CREATE INDEX IF NOT EXISTS idx_shop_purchases_guild_status 
                ON shop_purchases (guild_id, status);
                
                CREATE INDEX IF NOT EXISTS idx_shop_purchases_user 
                ON shop_purchases (user_id);
                
                -- Таблица конфигурации гильдий
                CREATE TABLE IF NOT EXISTS guild_config (
                    guild_id INTEGER PRIMARY KEY,
                    admin_role TEXT,
                    moderator_role TEXT,
                    points_moderator_roles TEXT,
                    events_channel TEXT,
                    shop_channel TEXT,
                    events_data TEXT,
                    -- Доп. поля для интеграции рекрутинга
                    points_start_date TEXT,
                    points_end_date TEXT,
                    default_role TEXT,
                    recruit_role TEXT,
                    recruiter_roles TEXT,
                    guild_name TEXT,
                    cooldown_hours INTEGER
                );
            """)
            
            # Проверяем существование колонок и добавляем если их нет
            try:
                # Добавляем столбец message_id если он не существует
                await db.execute("ALTER TABLE event_submissions ADD COLUMN message_id INTEGER DEFAULT NULL")
                logger.info("Добавлен столбец message_id в таблицу event_submissions")
            except Exception:
                # Столбец уже существует
                pass
            cursor = await db.execute("PRAGMA table_info(guild_config)")
            columns = [row[1] for row in await cursor.fetchall()]
            
            if 'admin_role' not in columns:
                await db.execute("ALTER TABLE guild_config ADD COLUMN admin_role TEXT")
                logger.info("Добавлена колонка admin_role")
            
            if 'points_moderator_roles' not in columns:
                await db.execute("ALTER TABLE guild_config ADD COLUMN points_moderator_roles TEXT")
                logger.info("Добавлена колонка points_moderator_roles")
            
            if 'shop_channel' not in columns:
                await db.execute("ALTER TABLE guild_config ADD COLUMN shop_channel TEXT")
                logger.info("Добавлена колонка shop_channel")
            
            # Добавляем колонку message_id для обновления статуса заявки
            try:
                await db.execute("ALTER TABLE event_submissions ADD COLUMN message_id INTEGER")
                logger.info("Добавлена колонка message_id в event_submissions")
            except Exception:
                pass  # Колонка уже существует
            
            # Добавляем колонки для периода начисления очков
            try:
                await db.execute("ALTER TABLE guild_config ADD COLUMN points_start_date TEXT")
                await db.execute("ALTER TABLE guild_config ADD COLUMN points_end_date TEXT")
                logger.info("Добавлены колонки для периода начисления очков")
            except Exception:
                pass  # Колонки уже существуют

            # Добавляем новые колонки для ролей/настроек рекрутинга при апгрейде схемы
            for column_def in [
                ("default_role", "TEXT"),
                ("recruit_role", "TEXT"),
                ("recruiter_roles", "TEXT"),
                ("guild_name", "TEXT"),
                ("cooldown_hours", "INTEGER")
            ]:
                col, typ = column_def
                try:
                    await db.execute(f"ALTER TABLE guild_config ADD COLUMN {col} {typ}")
                    logger.info(f"Добавлена колонка {col} в guild_config")
                except Exception:
                    pass  # Колонка уже существует
            
            # Добавляем колонки для исходного сообщения заявки
            try:
                await db.execute("ALTER TABLE event_submissions ADD COLUMN original_message_id INTEGER")
                await db.execute("ALTER TABLE event_submissions ADD COLUMN original_channel_id INTEGER")
                logger.info("Добавлены колонки для исходного сообщения заявки")
            except Exception:
                pass  # Колонки уже существуют
            
            await db.commit()
        logger.info("Таблицы для системы событий инициализированы")
    
    @staticmethod
    async def create_shop_purchase(
        guild_id: int,
        user_id: int,
        item_id: str,
        item_name: str,
        points_cost: int
    ) -> bool:
        """Создать покупку в магазине"""
        async with aiosqlite.connect(DB_PATH) as db:
            # Проверяем баланс пользователя
            cursor = await db.execute("""
                SELECT total_points FROM user_points 
                WHERE guild_id = ? AND user_id = ?
            """, (guild_id, user_id))
            
            row = await cursor.fetchone()
            current_points = row[0] if row else 0
            
            if current_points < points_cost:
                logger.warning(f"Недостаточно очков для покупки: {current_points} < {points_cost}")
                return False
            
            # Списываем очки
            await db.execute("""
                UPDATE user_points 
                SET total_points = total_points - ?,
                    last_updated = ?
                WHERE guild_id = ? AND user_id = ?
            """, (
                points_cost,
                datetime.now(timezone.utc).isoformat(),
                guild_id,
                user_id
            ))
            
            # Создаем запись о покупке
            await db.execute("""
                INSERT INTO shop_purchases (
                    guild_id, user_id, item_id, item_name, points_cost, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                guild_id,
                user_id,
                item_id,
                item_name,
                points_cost,
                datetime.now(timezone.utc).isoformat()
            ))
            
            await db.commit()
            logger.info(f"Покупка создана: {item_name} для пользователя {user_id} за {points_cost} очков")
            return True
    
    @staticmethod
    async def get_latest_purchase_id(guild_id: int, user_id: int) -> int:
        """Получить ID последней покупки пользователя"""
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id FROM shop_purchases 
                WHERE guild_id = ? AND user_id = ? AND status = 'pending'
                ORDER BY created_at DESC
                LIMIT 1
            """, (guild_id, user_id))
            
            row = await cursor.fetchone()
            return row[0] if row else 0
    
    @staticmethod
    async def get_purchase_by_id(purchase_id: int) -> dict:
        """Получить данные покупки по ID"""
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT guild_id, user_id, item_id, item_name, points_cost, status, created_at
                FROM shop_purchases 
                WHERE id = ?
            """, (purchase_id,))
            
            row = await cursor.fetchone()
            if row:
                return {
                    'guild_id': row[0],
                    'user_id': row[1],
                    'item_id': row[2],
                    'item_name': row[3],
                    'points_cost': row[4],
                    'status': row[5],
                    'created_at': row[6]
                }
            return None
    
    @staticmethod
    async def get_pending_purchases(guild_id: int) -> List[Dict]:
        """Получить список ожидающих покупок"""
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT id, user_id, item_id, item_name, points_cost, created_at
                FROM shop_purchases 
                WHERE guild_id = ? AND status = 'pending'
                ORDER BY created_at ASC
            """, (guild_id,))
            
            results = []
            for row in await cursor.fetchall():
                results.append({
                    'id': row[0],
                    'user_id': row[1],
                    'item_id': row[2],
                    'item_name': row[3],
                    'points_cost': row[4],
                    'created_at': row[5]
                })
            
            return results
    
    @staticmethod
    async def process_shop_purchase(
        purchase_id: int,
        admin_id: int,
        completed: bool,
        admin_notes: str = None
    ) -> bool:
        """Обработать покупку (выдать или отклонить)"""
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT guild_id, user_id, points_cost, status 
                FROM shop_purchases 
                WHERE id = ?
            """, (purchase_id,))
            
            row = await cursor.fetchone()
            if not row:
                logger.error(f"Покупка {purchase_id} не найдена")
                return False
            
            guild_id, user_id, points_cost, status = row
            
            if status != 'pending':
                logger.warning(f"Покупка {purchase_id} уже обработана")
                return False
            
            new_status = 'completed' if completed else 'rejected'
            
            # Если покупка отклонена, возвращаем очки
            if not completed:
                await db.execute("""
                    UPDATE user_points 
                    SET total_points = total_points + ?,
                        last_updated = ?
                    WHERE guild_id = ? AND user_id = ?
                """, (
                    points_cost,
                    datetime.now(timezone.utc).isoformat(),
                    guild_id,
                    user_id
                ))
            
            # Обновляем статус покупки
            await db.execute("""
                UPDATE shop_purchases 
                SET status = ?, processed_by = ?, processed_at = ?, admin_notes = ?
                WHERE id = ?
            """, (
                new_status,
                admin_id,
                datetime.now(timezone.utc).isoformat(),
                admin_notes,
                purchase_id
            ))
            
            await db.commit()
            logger.info(f"Покупка {purchase_id} {'выдана' if completed else 'отклонена'}")
            return True
    
    @staticmethod
    async def get_user_purchase_history(
        guild_id: int,
        user_id: int,
        limit: int = 10
    ) -> List[Dict]:
        """Получить историю покупок пользователя"""
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT item_name, points_cost, status, created_at, processed_at, admin_notes
                FROM shop_purchases 
                WHERE guild_id = ? AND user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (guild_id, user_id, limit))
            
            results = []
            for row in await cursor.fetchall():
                results.append({
                    'item_name': row[0],
                    'points_cost': row[1],
                    'status': row[2],
                    'created_at': row[3],
                    'processed_at': row[4],
                    'admin_notes': row[5]
                })
            
            return results
    
    @staticmethod
    async def create_event_submission(
        guild_id: int,
        submission: EventSubmission,
        thread_id: Optional[int] = None,
        original_message_id: Optional[int] = None,
        original_channel_id: Optional[int] = None
    ) -> int:
        """Создать заявку на событие"""
        async with aiosqlite.connect(DB_PATH) as db:
            # Создаем основную заявку
            cursor = await db.execute("""
                INSERT INTO event_submissions (
                    guild_id, submitter_id, event_type, action, group_size,
                    description, screenshot_url, base_points, created_at, thread_id,
                    original_message_id, original_channel_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                guild_id,
                submission.submitter_id,
                submission.event_type.value,
                submission.action.value,
                submission.group_size,
                submission.description,
                submission.screenshot_url,
                submission.calculate_base_points(),
                datetime.now(timezone.utc).isoformat(),
                thread_id,
                original_message_id,
                original_channel_id
            ))
            
            submission_id = cursor.lastrowid
            
            # Добавляем участников
            for user_id in submission.participants:
                await db.execute("""
                    INSERT INTO event_participants (submission_id, user_id)
                    VALUES (?, ?)
                """, (submission_id, user_id))
            
            await db.commit()
            logger.info(f"Создана заявка на событие {submission_id} от пользователя {submission.submitter_id}")
            return submission_id
    
    @staticmethod
    async def approve_event_submission(
        submission_id: int,
        reviewer_id: int,
        final_multiplier: float
    ) -> bool:
        """Одобрить заявку и начислить очки"""
        async with aiosqlite.connect(DB_PATH) as db:
            # Получаем данные заявки
            cursor = await db.execute("""
                SELECT guild_id, base_points, group_size, status
                FROM event_submissions 
                WHERE id = ?
            """, (submission_id,))
            
            row = await cursor.fetchone()
            if not row:
                logger.error(f"Заявка {submission_id} не найдена")
                return False
            
            guild_id, base_points, group_size, status = row
            
            if status != 'pending':
                logger.warning(f"Заявка {submission_id} уже обработана (статус: {status})")
                return False
            
            # Рассчитываем финальные очки
            from .events import EventManager
            final_points_per_person = EventManager.calculate_final_points(
                base_points, final_multiplier, group_size
            )
            
            # Обновляем статус заявки
            await db.execute("""
                UPDATE event_submissions 
                SET status = 'approved', reviewer_id = ?, final_multiplier = ?,
                    final_points_per_person = ?, reviewed_at = ?
                WHERE id = ?
            """, (
                reviewer_id,
                final_multiplier,
                final_points_per_person,
                datetime.now(timezone.utc).isoformat(),
                submission_id
            ))
            
            # Начисляем очки участникам
            await db.execute("""
                UPDATE event_participants 
                SET points_awarded = ?
                WHERE submission_id = ?
            """, (final_points_per_person, submission_id))
            
            # Получаем список участников
            cursor = await db.execute("""
                SELECT user_id FROM event_participants WHERE submission_id = ?
            """, (submission_id,))
            
            participants = [row[0] for row in await cursor.fetchall()]
            
            # Обновляем общую статистику участников
            for user_id in participants:
                await EventDatabase._update_user_points(
                    db, guild_id, user_id, final_points_per_person
                )
            
            await db.commit()
            logger.info(f"Заявка {submission_id} одобрена, начислено {final_points_per_person} очков каждому из {len(participants)} участников")
            return True
    
    @staticmethod
    async def reject_event_submission(
        submission_id: int,
        reviewer_id: int,
        reason: Optional[str] = None
    ) -> bool:
        """Отклонить заявку"""
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT status FROM event_submissions WHERE id = ?
            """, (submission_id,))
            
            row = await cursor.fetchone()
            if not row:
                logger.error(f"Заявка {submission_id} не найдена")
                return False
            
            if row[0] != 'pending':
                logger.warning(f"Заявка {submission_id} уже обработана")
                return False
            
            await db.execute("""
                UPDATE event_submissions 
                SET status = 'rejected', reviewer_id = ?, description = ?,
                    reviewed_at = ?
                WHERE id = ?
            """, (
                reviewer_id,
                f"{reason}\n---\n{await EventDatabase._get_submission_description(submission_id)}" if reason else None,
                datetime.now(timezone.utc).isoformat(),
                submission_id
            ))
            
            await db.commit()
            logger.info(f"Заявка {submission_id} отклонена модератором {reviewer_id}")
            return True
    
    @staticmethod
    async def update_submission_status(
        submission_id: int,
        status: str,
        reviewer_id: int,
        final_points_per_person: float = None
    ) -> bool:
        """Обновить статус заявки"""
        async with aiosqlite.connect(DB_PATH) as db:
            # Получаем данные заявки
            cursor = await db.execute("""
                SELECT guild_id, status FROM event_submissions WHERE id = ?
            """, (submission_id,))
            
            row = await cursor.fetchone()
            if not row:
                logger.error(f"Заявка {submission_id} не найдена")
                return False
            
            guild_id, current_status = row
            
            if current_status != 'pending':
                logger.warning(f"Заявка {submission_id} уже обработана (статус: {current_status})")
                return False
            
            # Обновляем заявку
            if final_points_per_person is not None:
                await db.execute("""
                    UPDATE event_submissions 
                    SET status = ?, reviewer_id = ?, final_points_per_person = ?, reviewed_at = ?
                    WHERE id = ?
                """, (
                    status,
                    reviewer_id,
                    final_points_per_person,
                    datetime.now(timezone.utc).isoformat(),
                    submission_id
                ))
            else:
                await db.execute("""
                    UPDATE event_submissions 
                    SET status = ?, reviewer_id = ?, reviewed_at = ?
                    WHERE id = ?
                """, (
                    status,
                    reviewer_id,
                    datetime.now(timezone.utc).isoformat(),
                    submission_id
                ))
            
            await db.commit()
            logger.info(f"Заявка {submission_id} обновлена: статус={status}, модератор={reviewer_id}")
            return True
    
    @staticmethod
    async def get_submission_participants(submission_id: int) -> List[int]:
        """Получить список участников заявки"""
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT user_id FROM event_participants WHERE submission_id = ?
            """, (submission_id,))
            
            rows = await cursor.fetchall()
            return [row[0] for row in rows]
    
    @staticmethod
    async def get_user_points(guild_id: int, user_id: int) -> Tuple[float, int]:
        """Получить очки и количество событий пользователя"""
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT total_points, events_participated 
                FROM user_points 
                WHERE guild_id = ? AND user_id = ?
            """, (guild_id, user_id))
            
            row = await cursor.fetchone()
            if row:
                return row[0], row[1]
            return 0.0, 0
    
    @staticmethod
    async def add_user_points(guild_id: int, user_id: int, points: float, reason: str = None) -> bool:
        """Добавить очки пользователю"""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                await EventDatabase._update_user_points(db, guild_id, user_id, points)
                await db.commit()
                logger.info(f"Добавлено {points} очков пользователю {user_id} в гильдии {guild_id}" + (f" (причина: {reason})" if reason else ""))
                return True
        except Exception as e:
            logger.error(f"Ошибка добавления очков: {e}")
            return False
    
    @staticmethod
    async def set_user_points(guild_id: int, user_id: int, points: float, reason: str = None) -> bool:
        """Установить точное количество очков пользователю"""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # Проверяем, есть ли запись пользователя
                cursor = await db.execute("""
                    SELECT total_points FROM user_points 
                    WHERE guild_id = ? AND user_id = ?
                """, (guild_id, user_id))
                
                existing = await cursor.fetchone()
                
                if existing:
                    # Обновляем существующую запись
                    await db.execute("""
                        UPDATE user_points 
                        SET total_points = ? 
                        WHERE guild_id = ? AND user_id = ?
                    """, (points, guild_id, user_id))
                else:
                    # Создаем новую запись
                    await db.execute("""
                        INSERT INTO user_points (guild_id, user_id, total_points, events_participated)
                        VALUES (?, ?, ?, 0)
                    """, (guild_id, user_id, points))
                
                await db.commit()
                logger.info(f"Установлено {points} очков пользователю {user_id} в гильдии {guild_id}" + (f" (причина: {reason})" if reason else ""))
                return True
        except Exception as e:
            logger.error(f"Ошибка установки очков: {e}")
            return False
    
    @staticmethod
    async def reset_all_points(guild_id: int) -> bool:
        """Обнулить очки всем пользователям в гильдии"""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                cursor = await db.execute("""
                    SELECT COUNT(*) FROM user_points WHERE guild_id = ? AND total_points > 0
                """, (guild_id,))
                
                affected_count = (await cursor.fetchone())[0]
                
                # Обнуляем все очки
                await db.execute("""
                    UPDATE user_points 
                    SET total_points = 0 
                    WHERE guild_id = ?
                """, (guild_id,))
                
                await db.commit()
                logger.info(f"Обнулены очки для {affected_count} пользователей в гильдии {guild_id}")
                return True
        except Exception as e:
            logger.error(f"Ошибка обнуления очков: {e}")
            return False
    
    @staticmethod
    async def get_leaderboard(guild_id: int, limit: int = 10) -> List[Tuple[int, float, int]]:
        """Получить топ пользователей по очкам"""
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT user_id, total_points, events_participated
                FROM user_points 
                WHERE guild_id = ? AND total_points > 0
                ORDER BY total_points DESC 
                LIMIT ?
            """, (guild_id, limit))
            
            return await cursor.fetchall()
    
    @staticmethod
    async def get_pending_submissions(guild_id: int) -> List[Dict]:
        """Получить список ожидающих заявок"""
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT es.id, es.submitter_id, es.event_type, es.action,
                       es.group_size, es.base_points, es.created_at, es.thread_id,
                       es.description,
                       GROUP_CONCAT(ep.user_id) as participant_ids
                FROM event_submissions es
                LEFT JOIN event_participants ep ON es.id = ep.submission_id
                WHERE es.guild_id = ? AND es.status = 'pending'
                GROUP BY es.id
                ORDER BY es.created_at ASC
            """, (guild_id,))
            
            results = []
            for row in await cursor.fetchall():
                participant_ids = [int(pid) for pid in row[9].split(',') if pid] if row[9] else []
                results.append({
                    'id': row[0],
                    'submitter_id': row[1],
                    'event_type': row[2],
                    'action': row[3],
                    'group_size': row[4],
                    'base_points': row[5],
                    'created_at': row[6],
                    'thread_id': row[7],
                    'description': row[8],
                    'participant_ids': participant_ids
                })
            
            return results
    
    @staticmethod
    async def get_all_submissions(guild_id: int, status: str = None, limit: int = 50) -> List[Dict]:
        """Получить список всех заявок с фильтрацией"""
        async with aiosqlite.connect(DB_PATH) as db:
            if status:
                cursor = await db.execute("""
                    SELECT es.id, es.submitter_id, es.event_type, es.action,
                           es.group_size, es.base_points, es.created_at, es.thread_id,
                           es.description, es.status, es.reviewer_id, es.reviewed_at,
                           GROUP_CONCAT(ep.user_id) as participant_ids
                    FROM event_submissions es
                    LEFT JOIN event_participants ep ON es.id = ep.submission_id
                    WHERE es.guild_id = ? AND es.status = ?
                    GROUP BY es.id
                    ORDER BY es.created_at DESC
                    LIMIT ?
                """, (guild_id, status, limit))
            else:
                cursor = await db.execute("""
                    SELECT es.id, es.submitter_id, es.event_type, es.action,
                           es.group_size, es.base_points, es.created_at, es.thread_id,
                           es.description, es.status, es.reviewer_id, es.reviewed_at,
                           GROUP_CONCAT(ep.user_id) as participant_ids
                    FROM event_submissions es
                    LEFT JOIN event_participants ep ON es.id = ep.submission_id
                    WHERE es.guild_id = ?
                    GROUP BY es.id
                    ORDER BY es.created_at DESC
                    LIMIT ?
                """, (guild_id, limit))
            
            results = []
            for row in await cursor.fetchall():
                participant_ids = [int(pid) for pid in row[12].split(',') if pid] if row[12] else []
                results.append({
                    'id': row[0],
                    'submitter_id': row[1],
                    'event_type': row[2],
                    'action': row[3],
                    'group_size': row[4],
                    'base_points': row[5],
                    'created_at': row[6],
                    'thread_id': row[7],
                    'description': row[8],
                    'status': row[9],
                    'reviewer_id': row[10],
                    'reviewed_at': row[11],
                    'participant_ids': participant_ids
                })
            
            return results
    
    @staticmethod
    async def get_submission_details(submission_id: int) -> Optional[Dict]:
        """Получить детали заявки"""
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT es.*, GROUP_CONCAT(ep.user_id) as participant_ids
                FROM event_submissions es
                LEFT JOIN event_participants ep ON es.id = ep.submission_id
                WHERE es.id = ?
                GROUP BY es.id
            """, (submission_id,))
            
            row = await cursor.fetchone()
            if not row:
                return None
            
            columns = [description[0] for description in cursor.description]
            result = dict(zip(columns, row))
            
            # Парсим участников
            if result['participant_ids']:
                result['participant_ids'] = [int(pid) for pid in result['participant_ids'].split(',')]
            else:
                result['participant_ids'] = []
            
            return result
    
    @staticmethod
    async def delete_event_submission(submission_id: int) -> bool:
        """Полностью удалить заявку на событие из базы данных"""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # Проверяем существование заявки
                cursor = await db.execute("""
                    SELECT id, status FROM event_submissions WHERE id = ?
                """, (submission_id,))
                
                row = await cursor.fetchone()
                if not row:
                    logger.error(f"Заявка {submission_id} не найдена")
                    return False
                
                submission_id_db, status = row
                
                # Удаляем участников (сначала из-за внешнего ключа)
                await db.execute("""
                    DELETE FROM event_participants WHERE submission_id = ?
                """, (submission_id,))
                
                # Удаляем саму заявку
                await db.execute("""
                    DELETE FROM event_submissions WHERE id = ?
                """, (submission_id,))
                
                await db.commit()
                logger.info(f"Заявка {submission_id} полностью удалена из базы данных (статус был: {status})")
                return True
                
        except Exception as e:
            logger.error(f"Ошибка при удалении заявки {submission_id}: {e}")
            return False
    
    @staticmethod
    async def get_user_event_history(
        guild_id: int, 
        user_id: int, 
        limit: int = 10
    ) -> List[Dict]:
        """Получить историю событий пользователя"""
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT es.id, es.event_type, es.action, es.status,
                       es.created_at, es.reviewed_at, ep.points_awarded,
                       es.submitter_id, es.group_size
                FROM event_participants ep
                JOIN event_submissions es ON ep.submission_id = es.id
                WHERE es.guild_id = ? AND ep.user_id = ?
                ORDER BY es.created_at DESC
                LIMIT ?
            """, (guild_id, user_id, limit))
            
            results = []
            for row in await cursor.fetchall():
                results.append({
                    'submission_id': row[0],
                    'event_type': row[1],
                    'action': row[2],
                    'status': row[3],
                    'created_at': row[4],
                    'reviewed_at': row[5],
                    'points_awarded': row[6],
                    'submitter_id': row[7],
                    'group_size': row[8]
                })
            
            return results
    
    @staticmethod
    async def _update_user_points(db, guild_id: int, user_id: int, points_to_add: float):
        """Обновить очки пользователя (внутренний метод)"""
        await db.execute("""
            INSERT INTO user_points (guild_id, user_id, total_points, events_participated, last_updated)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                total_points = total_points + excluded.total_points,
                events_participated = events_participated + 1,
                last_updated = excluded.last_updated
        """, (
            guild_id,
            user_id,
            points_to_add,
            datetime.now(timezone.utc).isoformat()
        ))
    
    @staticmethod
    async def _get_submission_description(submission_id: int) -> Optional[str]:
        """Получить описание заявки (внутренний метод)"""
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT description FROM event_submissions WHERE id = ?
            """, (submission_id,))
            
            row = await cursor.fetchone()
            return row[0] if row else None
    
    @staticmethod
    async def get_guild_event_stats(guild_id: int) -> Dict:
        """Получить статистику событий гильдии"""
        async with aiosqlite.connect(DB_PATH) as db:
            # Общее количество заявок по статусам
            cursor = await db.execute("""
                SELECT status, COUNT(*) 
                FROM event_submissions 
                WHERE guild_id = ? 
                GROUP BY status
            """, (guild_id,))
            
            status_counts = dict(await cursor.fetchall())
            
            # Общее количество начисленных очков
            cursor = await db.execute("""
                SELECT SUM(final_points_per_person * group_size)
                FROM event_submissions 
                WHERE guild_id = ? AND status = 'approved'
            """, (guild_id,))
            
            total_points_distributed = (await cursor.fetchone())[0] or 0
            
            # Количество активных участников
            cursor = await db.execute("""
                SELECT COUNT(DISTINCT user_id)
                FROM user_points 
                WHERE guild_id = ? AND total_points > 0
            """, (guild_id,))
            
            active_users = (await cursor.fetchone())[0] or 0
            
            return {
                'status_counts': status_counts,
                'total_points_distributed': total_points_distributed,
                'active_users': active_users
            }
    
    @staticmethod
    async def get_guild_config(guild_id: int) -> Dict:
        """Получить конфигурацию гильдии"""
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT admin_role, moderator_role, points_moderator_roles, events_channel, shop_channel, 
                       events_data, points_start_date, points_end_date,
                       default_role, recruit_role, recruiter_roles, guild_name, cooldown_hours
                FROM guild_config WHERE guild_id = ?
            """, (guild_id,))
            
            row = await cursor.fetchone()
            if row:
                return {
                    'admin_role': row[0],
                    'moderator_role': row[1],
                    'points_moderator_roles': row[2],
                    'events_channel': row[3],
                    'shop_channel': row[4],
                    'events_data': row[5],
                    'points_start_date': row[6],
                    'points_end_date': row[7],
                    'default_role': row[8],
                    'recruit_role': row[9],
                    'recruiter_roles': row[10],
                    'guild_name': row[11],
                    'cooldown_hours': row[12]
                }
            return {}
    
    @staticmethod
    async def update_guild_config(guild_id: int, **kwargs) -> bool:
        """Обновить конфигурацию гильдии"""
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                # Сначала проверяем, существует ли запись
                cursor = await db.execute("""
                    SELECT guild_id FROM guild_config WHERE guild_id = ?
                """, (guild_id,))
                
                exists = await cursor.fetchone() is not None
                
                if exists:
                    # Обновляем существующую запись
                    set_clauses = []
                    values = []
                    
                    allowed = ['admin_role', 'moderator_role', 'points_moderator_roles', 'events_channel', 'shop_channel', 'events_data', 'points_start_date', 'points_end_date', 'default_role', 'recruit_role', 'recruiter_roles', 'guild_name', 'cooldown_hours']
                    for key, value in kwargs.items():
                        if key in allowed:
                            set_clauses.append(f"{key} = ?")
                            values.append(value)
                    
                    if set_clauses:
                        values.append(guild_id)
                        await db.execute(f"""
                            UPDATE guild_config SET {', '.join(set_clauses)}
                            WHERE guild_id = ?
                        """, values)
                else:
                    # Создаем новую запись с default значениями
                    columns = ['guild_id']
                    values = [guild_id]
                    placeholders = ['?']
                    
                    # Добавляем переданные поля
                    allowed = ['admin_role', 'moderator_role', 'points_moderator_roles', 'events_channel', 'shop_channel', 'events_data', 'points_start_date', 'points_end_date', 'default_role', 'recruit_role', 'recruiter_roles', 'guild_name', 'cooldown_hours']
                    for key, value in kwargs.items():
                        if key in allowed:
                            columns.append(key)
                            values.append(value)
                            placeholders.append('?')
                    
                    await db.execute(f"""
                        INSERT INTO guild_config ({', '.join(columns)})
                        VALUES ({', '.join(placeholders)})
                    """, values)
                
                await db.commit()
                return True
        except Exception as e:
            logger.error(f"Ошибка обновления конфигурации гильдии: {e}")
            return False

    @staticmethod
    async def set_events_channel(guild_id: int, channel_id: int) -> bool:
        """Установить канал для заявок на события"""
        return await EventDatabase.update_guild_config(guild_id, events_channel=str(channel_id))
    
    @staticmethod
    async def set_shop_channel(guild_id: int, channel_id: int) -> bool:
        """Установить канал для покупок в магазине"""
        return await EventDatabase.update_guild_config(guild_id, shop_channel=str(channel_id))
    
    @staticmethod
    async def get_events_channel(guild_id: int) -> Optional[int]:
        """Получить ID канала для заявок на события"""
        config = await EventDatabase.get_guild_config(guild_id)
        events_channel = config.get('events_channel')
        if events_channel:
            try:
                return int(events_channel)
            except (ValueError, TypeError):
                return None
        return None
    
    @staticmethod
    async def get_shop_channel(guild_id: int) -> Optional[int]:
        """Получить ID канала для покупок в магазине"""
        config = await EventDatabase.get_guild_config(guild_id)
        shop_channel = config.get('shop_channel')
        if shop_channel:
            try:
                return int(shop_channel)
            except (ValueError, TypeError):
                return None
        return None
