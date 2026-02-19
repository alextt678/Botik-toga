import asyncio
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Union
import logging
import json
import aiofiles
import traceback

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter, TelegramNetworkError

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = "7078059729:AAG4JvDdzbHV-3ga-LfjEziTA7W3NMmgnZY"
ADMIN_USERNAME = "JDD452"
ADMIN_ID = 5138605368
MEDIA_DIR = "temp_media"

# Лимиты для разных типов постов
LIMITS = {
    'regular': 4,
    'livery': 4,
    'sticker': 1
}

# Тексты для лимитов
LIMIT_TEXTS = {
    'regular': "⚠️ Максимум 4 файла! Нельзя отправить больше 4 файлов (фото/видео)",
    'livery': "⚠️ Максимум 4 фото! Нельзя отправить больше 4 фото",
    'sticker': "⚠️ Только 1 фото! Нельзя отправить больше 1 фото"
}

MAX_QUEUE_SIZE = 100

os.makedirs(MEDIA_DIR, exist_ok=True)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ==================== СОСТОЯНИЯ ДЛЯ FSM ====================
class PostStates(StatesGroup):
    collecting_media = State()
    collecting_livery_photo = State()
    waiting_livery_body_file = State()
    waiting_livery_glass_file = State()
    collecting_sticker_photo = State()
    waiting_sticker_file = State()
    confirm_post = State()

# ==================== БАЗА ДАННЫХ ====================
DB_FILE = "posts.json"
CHANNELS_FILE = "channels.json"
BACKUP_DIR = "backups"

os.makedirs(BACKUP_DIR, exist_ok=True)

class Database:
    def __init__(self):
        self.posts: List[Dict] = []
        self.channels: List[Dict] = []
        self.current_channel: Optional[str] = None
        self.last_save = datetime.now()
        self.load()
    
    def start_auto_save(self):
        """Запускает автосохранение (вызывать после запуска event loop)"""
        asyncio.create_task(self.auto_save())
        logger.info("Автосохранение запущено")
    
    def load(self):
        try:
            if os.path.exists(DB_FILE):
                with open(DB_FILE, 'r', encoding='utf-8') as f:
                    self.posts = json.load(f)
                logger.info(f"Загружено {len(self.posts)} постов")
        except Exception as e:
            logger.error(f"Ошибка загрузки постов: {e}")
            self.posts = []
            self.restore_from_backup()
        
        try:
            if os.path.exists(CHANNELS_FILE):
                with open(CHANNELS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.channels = data.get('channels', [])
                    self.current_channel = data.get('current_channel')
                logger.info(f"Загружено {len(self.channels)} каналов")
        except Exception as e:
            logger.error(f"Ошибка загрузки каналов: {e}")
            self.channels = []
            self.current_channel = None
    
    async def save(self):
        try:
            if (datetime.now() - self.last_save).seconds > 3600:
                await self.create_backup()
            
            async with aiofiles.open(DB_FILE, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(self.posts, ensure_ascii=False, indent=2))
            
            async with aiofiles.open(CHANNELS_FILE, 'w', encoding='utf-8') as f:
                data = {
                    'channels': self.channels,
                    'current_channel': self.current_channel
                }
                await f.write(json.dumps(data, ensure_ascii=False, indent=2))
            
            self.last_save = datetime.now()
            logger.info("Данные успешно сохранены")
            
        except Exception as e:
            logger.error(f"Ошибка сохранения данных: {e}")
    
    async def auto_save(self):
        """Автоматическое сохранение каждые 5 минут"""
        while True:
            await asyncio.sleep(300)
            await self.save()
    
    async def create_backup(self):
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = f"{BACKUP_DIR}/posts_{timestamp}.json"
            
            async with aiofiles.open(backup_file, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(self.posts, ensure_ascii=False, indent=2))
            
            await self.clean_old_backups()
            
        except Exception as e:
            logger.error(f"Ошибка создания бэкапа: {e}")
    
    async def clean_old_backups(self):
        try:
            now = datetime.now()
            for file in os.listdir(BACKUP_DIR):
                file_path = os.path.join(BACKUP_DIR, file)
                if os.path.isfile(file_path):
                    file_time = datetime.fromtimestamp(os.path.getctime(file_path))
                    if (now - file_time).days > 7:
                        os.remove(file_path)
                        logger.info(f"Удалён старый бэкап: {file}")
        except Exception as e:
            logger.error(f"Ошибка очистки бэкапов: {e}")
    
    def restore_from_backup(self):
        try:
            backups = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith('posts_')])
            if backups:
                latest = backups[-1]
                with open(os.path.join(BACKUP_DIR, latest), 'r', encoding='utf-8') as f:
                    self.posts = json.load(f)
                logger.info(f"Восстановлено из бэкапа: {latest}")
        except Exception as e:
            logger.error(f"Ошибка восстановления из бэкапа: {e}")
    
    def add_post(self, user_id: int, username: str, content: Dict) -> int:
        if len(self.posts) > MAX_QUEUE_SIZE:
            self.clean_old_posts(60)
        
        post_id = len(self.posts) + 1
        post = {
            'id': post_id,
            'user_id': user_id,
            'username': username,
            'content': content,
            'status': 'pending',
            'created_at': datetime.now().isoformat(),
            'scheduled_time': None,
            'channel': self.current_channel
        }
        self.posts.append(post)
        return post_id
    
    def get_pending_posts(self) -> List[Dict]:
        return [p for p in self.posts if p['status'] == 'pending']
    
    def get_post(self, post_id: int) -> Optional[Dict]:
        for p in self.posts:
            if p['id'] == post_id:
                return p
        return None
    
    def approve_post(self, post_id: int, scheduled_time: str = None):
        post = self.get_post(post_id)
        if post:
            post['status'] = 'approved'
            post['scheduled_time'] = scheduled_time
    
    def get_next_post(self) -> Optional[Dict]:
        approved = [p for p in self.posts if p['status'] == 'approved' and p.get('channel') == self.current_channel]
        if approved:
            approved.sort(key=lambda x: x['created_at'])
            return approved[0]
        return None
    
    def mark_published(self, post_id: int):
        post = self.get_post(post_id)
        if post:
            post['status'] = 'published'
            post['published_at'] = datetime.now().isoformat()
    
    def delete_post(self, post_id: int):
        self.posts = [p for p in self.posts if p['id'] != post_id]
    
    def clean_old_posts(self, days: int = 30):
        now = datetime.now()
        before = len(self.posts)
        self.posts = [
            p for p in self.posts 
            if datetime.fromisoformat(p['created_at']) > now - timedelta(days=days)
        ]
        after = len(self.posts)
        if before != after:
            logger.info(f"Очистка: удалено {before - after} старых постов")
    
    def clean_published_posts(self):
        before = len(self.posts)
        self.posts = [p for p in self.posts if p['status'] != 'published']
        after = len(self.posts)
        if before != after:
            logger.info(f"Очистка: удалено {before - after} опубликованных постов")
    
    def get_stats(self) -> Dict:
        oldest = None
        newest = None
        if self.posts:
            try:
                oldest = min([datetime.fromisoformat(p['created_at']) for p in self.posts])
                newest = max([datetime.fromisoformat(p['created_at']) for p in self.posts])
            except:
                pass
        
        return {
            'total': len(self.posts),
            'pending': len([p for p in self.posts if p['status'] == 'pending']),
            'approved': len([p for p in self.posts if p['status'] == 'approved']),
            'published': len([p for p in self.posts if p['status'] == 'published']),
            'oldest': oldest,
            'newest': newest
        }
    
    def add_channel(self, channel_id: str, title: str = None):
        for ch in self.channels:
            if ch['id'] == channel_id:
                return False
        
        self.channels.append({
            'id': channel_id,
            'title': title or channel_id,
            'added_at': datetime.now().isoformat()
        })
        return True
    
    def remove_channel(self, channel_id: str):
        self.channels = [ch for ch in self.channels if ch['id'] != channel_id]
        if self.current_channel == channel_id:
            self.current_channel = self.channels[0]['id'] if self.channels else None
    
    def set_current_channel(self, channel_id: str):
        for ch in self.channels:
            if ch['id'] == channel_id:
                self.current_channel = channel_id
                return True
        return False
    
    def get_channels_list(self) -> List[Dict]:
        return self.channels
    
    def get_current_channel(self) -> Optional[Dict]:
        for ch in self.channels:
            if ch['id'] == self.current_channel:
                return ch
        return None

db = Database()

# ==================== ФУНКЦИИ ПРОВЕРКИ ====================

def is_admin(username: Optional[str]) -> bool:
    return username == ADMIN_USERNAME if username else False

async def check_bot_in_channel(channel_id: str) -> bool:
    try:
        chat = await bot.get_chat(channel_id)
        msg = await bot.send_message(channel_id, "🔍 Проверка связи...")
        await msg.delete()
        return True
    except Exception as e:
        logger.error(f"Ошибка проверки канала {channel_id}: {e}")
        return False

def is_txt_file(file_name: Optional[str]) -> bool:
    return file_name and file_name.lower().endswith('.txt')

def check_limit(post_type: str, current_count: int, additional: int = 1) -> bool:
    limit = LIMITS.get(post_type, 4)
    return (current_count + additional) <= limit

def get_limit_text(post_type: str) -> str:
    return LIMIT_TEXTS.get(post_type, "⚠️ Превышен лимит файлов")

# ==================== ФУНКЦИИ АВТОУДАЛЕНИЯ ====================

async def delete_message_after(chat_id: int, message_id: int, seconds: int = 10):
    """Удаляет сообщение через указанное количество секунд"""
    await asyncio.sleep(seconds)
    try:
        await bot.delete_message(chat_id, message_id)
    except:
        pass

async def delete_user_messages(user_id: int, messages_to_keep: List[int] = None):
    """Удаляет все старые сообщения пользователя, кроме указанных"""
    if user_id in temp_data and 'message_ids' in temp_data[user_id]:
        messages = temp_data[user_id]['message_ids'].copy()
        keep_ids = messages_to_keep or []
        
        for msg_id in messages:
            if msg_id not in keep_ids:
                try:
                    await bot.delete_message(user_id, msg_id)
                except:
                    pass
        
        # Очищаем список, оставляем только те, что нужно сохранить
        temp_data[user_id]['message_ids'] = [msg_id for msg_id in messages if msg_id in keep_ids]

# ==================== ВРЕМЕННОЕ ХРАНИЛИЩЕ ====================
temp_data: Dict[int, Dict] = {}
temp_channel_add: Dict[int, bool] = {}

# Очистка старых временных данных
async def clean_temp_data():
    while True:
        await asyncio.sleep(3600)
        now = datetime.now()
        to_delete = []
        for user_id, data in temp_data.items():
            if 'created_at' in data:
                try:
                    created = datetime.fromisoformat(data['created_at'])
                    if (now - created).seconds > 7200:  # 2 часа
                        # Удаляем все сообщения пользователя
                        if 'message_ids' in data:
                            for msg_id in data['message_ids']:
                                try:
                                    await bot.delete_message(user_id, msg_id)
                                except:
                                    pass
                        to_delete.append(user_id)
                except:
                    to_delete.append(user_id)
        
        for user_id in to_delete:
            del temp_data[user_id]
            logger.info(f"Удалены устаревшие временные данные пользователя {user_id}")

# ==================== ФУНКЦИЯ ПРОВЕРКИ АКТИВНОГО ПОСТА ====================

async def check_active_post(user_id: int, state: FSMContext) -> bool:
    """Проверяет, есть ли у пользователя активный пост"""
    current_state = await state.get_state()
    
    # Проверяем состояние FSM
    if current_state is not None:
        await bot.send_message(
            user_id,
            "⏳ У тебя уже есть пост на модерации! Дождись проверки."
        )
        return True
    
    # Проверяем временные данные
    if user_id in temp_data:
        # Если данные есть, но состояние сброшено - чистим их
        if 'message_ids' in temp_data[user_id]:
            for msg_id in temp_data[user_id]['message_ids']:
                try:
                    await bot.delete_message(user_id, msg_id)
                except:
                    pass
        del temp_data[user_id]
        return False
    
    return False

# ==================== ИНИЦИАЛИЗАЦИЯ БОТА ====================
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== КЛАВИАТУРЫ ====================

def get_start_keyboard(is_admin_user: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    if is_admin_user:
        builder.button(text="📋 Очередь", callback_data="admin_queue")
        builder.button(text="📊 Статистика", callback_data="admin_stats")
        builder.button(text="📢 Управление каналами", callback_data="manage_channels")
        builder.button(text="🧹 Очистка", callback_data="clean_menu")
        
        current = db.get_current_channel()
        if current:
            builder.button(text=f"✅ Текущий: {current.get('title', current['id'])}", 
                          callback_data="no_action")
    else:
        builder.button(text="📤 Обычный пост", callback_data="new_regular")
        builder.button(text="👕 Ливрея", callback_data="new_livery")
        builder.button(text="🏷️ Наклейка", callback_data="new_sticker")
    
    builder.adjust(1)
    return builder.as_markup()

def get_cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="cancel_post")
    return builder.as_markup()

def get_confirm_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, отправить", callback_data="confirm_send")
    builder.button(text="🔄 Нет, заново", callback_data="confirm_redo")
    builder.button(text="❌ Отмена", callback_data="cancel_post")
    builder.adjust(1)
    return builder.as_markup()

def get_clean_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🧹 Удалить опубликованные", callback_data="clean_published")
    builder.button(text="🗑️ Удалить старше 30 дней", callback_data="clean_30days")
    builder.button(text="📊 Размер базы", callback_data="clean_stats")
    builder.button(text="◀️ Назад", callback_data="back_to_admin")
    builder.adjust(1)
    return builder.as_markup()

def get_channels_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить канал", callback_data="add_channel")
    
    for ch in db.get_channels_list():
        title = ch.get('title', ch['id'])
        is_current = "✅ " if ch['id'] == db.current_channel else ""
        builder.button(text=f"{is_current}{title}", callback_data=f"select_channel_{ch['id']}")
    
    builder.button(text="◀️ Назад", callback_data="back_to_admin")
    builder.adjust(1)
    return builder.as_markup()

def get_channel_actions_keyboard(channel_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    is_current = channel_id == db.current_channel
    
    if not is_current:
        builder.button(text="✅ Сделать текущим", callback_data=f"set_current_{channel_id}")
    
    builder.button(text="❌ Удалить канал", callback_data=f"delete_channel_{channel_id}")
    builder.button(text="◀️ Назад к списку", callback_data="manage_channels")
    builder.adjust(1)
    return builder.as_markup()

def get_content_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Готово", callback_data="content_done")
    builder.button(text="❌ Отмена", callback_data="cancel_post")
    builder.adjust(1)
    return builder.as_markup()

def get_post_navigation_keyboard(post_id: int, total: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    
    nav_row = []
    if post_id > 1:
        nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"nav_prev_{post_id}"))
    nav_row.append(InlineKeyboardButton(text=f"{post_id}/{total}", callback_data="no_action"))
    if post_id < total:
        nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"nav_next_{post_id}"))
    
    if nav_row:
        builder.row(*nav_row)
    
    builder.row(
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"nav_approve_{post_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"nav_reject_{post_id}")
    )
    
    builder.row(
        InlineKeyboardButton(text="⏱️ 10 сек", callback_data=f"nav_10sec_{post_id}"),
        InlineKeyboardButton(text="⏰ 10 мин", callback_data=f"nav_10min_{post_id}"),
        InlineKeyboardButton(text="📅 Завтра", callback_data=f"nav_sched_{post_id}")
    )
    
    builder.row(
        InlineKeyboardButton(text="🔙 В админ-меню", callback_data="back_to_admin"),
        InlineKeyboardButton(text="🗑️ Удалить пост", callback_data=f"nav_delete_{post_id}")
    )
    
    return builder.as_markup()

def get_moderation_keyboard(post_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Одобрить", callback_data=f"approve_{post_id}")
    builder.button(text="❌ Отклонить", callback_data=f"reject_{post_id}")
    builder.button(text="🔙 В админ-меню", callback_data="back_to_admin")
    builder.adjust(2, 1)
    return builder.as_markup()

def get_time_keyboard(post_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⏱️ 10 секунд", callback_data=f"time_10sec_{post_id}")
    builder.button(text="⏰ 10 минут", callback_data=f"time_10min_{post_id}")
    builder.button(text="📅 Завтра 9:00", callback_data=f"time_schedule_{post_id}")
    builder.button(text="🔙 В админ-меню", callback_data="back_to_admin")
    builder.adjust(1)
    return builder.as_markup()

def get_new_post_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📤 Обычный пост", callback_data="new_regular")
    builder.button(text="👕 Ливрея", callback_data="new_livery")
    builder.button(text="🏷️ Наклейка", callback_data="new_sticker")
    builder.adjust(1)
    return builder.as_markup()

# ==================== ДЕКОРАТОР ДЛЯ ОБРАБОТКИ ОШИБОК ====================

def error_handler(func):
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except TelegramRetryAfter as e:
            logger.warning(f"Flood control, waiting {e.retry_after} seconds")
            await asyncio.sleep(e.retry_after)
        except TelegramNetworkError as e:
            logger.error(f"Network error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in {func.__name__}: {e}\n{traceback.format_exc()}")
    return wrapper

# ==================== ОБРАБОТЧИК ОТМЕНЫ ====================

@dp.callback_query(F.data == "cancel_post")
@error_handler
async def cancel_post(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    # Удаляем все сообщения пользователя
    if user_id in temp_data:
        await delete_user_messages(user_id)
        del temp_data[user_id]
    
    await state.clear()
    
    try:
        await callback.message.delete()
    except:
        pass
    
    text = (
        "👋 Привет! Что хочешь отправить?\n\n"
        "📤 Обычный пост - фото/видео (максимум 4 файла)\n"
        "👕 Ливрея - только фото (максимум 4 фото) + 2 файла .txt\n"
        "🏷️ Наклейка - только 1 фото + 1 файл .txt\n\n"
        "⚠️ Файлы .txt должны быть в формате .txt"
    )
    
    msg = await bot.send_message(
        user_id,
        text,
        reply_markup=get_start_keyboard(False)
    )
    
    # Автоудаление через 5 минут
    asyncio.create_task(delete_message_after(user_id, msg.message_id, 300))
    
    await callback.answer("❌ Создание поста отменено")

# ==================== ОБРАБОТЧИКИ КОМАНД ====================

@dp.message(Command("start"))
@error_handler
async def cmd_start(message: types.Message, state: FSMContext):
    user = message.from_user
    user_id = user.id
    admin_user = is_admin(user.username)
    
    # Очищаем предыдущую сессию если есть
    if user_id in temp_data:
        await delete_user_messages(user_id)
        del temp_data[user_id]
    await state.clear()
    
    # Удаляем команду /start
    try:
        await message.delete()
    except:
        pass
    
    if admin_user:
        current = db.get_current_channel()
        if current:
            text = f"🔑 Панель администратора\n📢 Текущий канал: {current.get('title', current['id'])}"
        else:
            text = "🔑 Панель администратора\n⚠️ Канал не выбран! Добавьте канал в управлении."
        
        msg = await message.answer(text, reply_markup=get_start_keyboard(True))
    else:
        text = (
            "👋 Привет! Что хочешь отправить?\n\n"
            "📤 Обычный пост - фото/видео (максимум 4 файла)\n"
            "👕 Ливрея - только фото (максимум 4 фото) + 2 файла .txt\n"
            "🏷️ Наклейка - только 1 фото + 1 файл .txt\n\n"
            "⚠️ Файлы .txt должны быть в формате .txt"
        )
        msg = await message.answer(text, reply_markup=get_start_keyboard(False))
    
    # Автоудаление через 10 минут
    asyncio.create_task(delete_message_after(user_id, msg.message_id, 600))

@dp.message(Command("clean"))
@error_handler
async def cmd_clean(message: types.Message):
    if not is_admin(message.from_user.username):
        await message.answer("⛔ Доступ запрещён")
        return
    
    await message.answer("🧹 Меню очистки:", reply_markup=get_clean_keyboard())

# ==================== УПРАВЛЕНИЕ КАНАЛАМИ ====================

@dp.callback_query(F.data == "manage_channels")
@error_handler
async def manage_channels(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    channels = db.get_channels_list()
    
    if not channels:
        text = "📢 У вас нет добавленных каналов.\nНажмите 'Добавить канал' и отправьте ссылку или ID канала."
    else:
        text = "📢 Список каналов:\n✅ - текущий канал для публикаций"
    
    await callback.message.edit_text(text, reply_markup=get_channels_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "add_channel")
@error_handler
async def add_channel_start(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    temp_channel_add[callback.from_user.id] = True
    
    await callback.message.edit_text(
        "📝 Отправьте ссылку на канал или его ID\n"
        "Примеры:\n"
        "- @moy_kanal\n"
        "- -1001234567890\n"
        "- https://t.me/moy_kanal\n\n"
        "❗️ Бот должен быть администратором канала!",
        reply_markup=InlineKeyboardBuilder()
            .button(text="◀️ Отмена", callback_data="manage_channels")
            .as_markup()
    )
    await callback.answer()

@dp.message(F.text)
@error_handler
async def handle_channel_input(message: types.Message):
    user_id = message.from_user.id
    
    if user_id in temp_channel_add and is_admin(message.from_user.username):
        channel_input = message.text.strip()
        
        if 't.me/' in channel_input:
            channel_input = channel_input.split('t.me/')[-1].split('/')[0]
            if not channel_input.startswith('@'):
                channel_input = '@' + channel_input
        
        status = await check_bot_in_channel(channel_input)
        
        if status:
            try:
                chat = await bot.get_chat(channel_input)
                title = chat.title
            except:
                title = channel_input
            
            db.add_channel(channel_input, title)
            
            if len(db.get_channels_list()) == 1:
                db.set_current_channel(channel_input)
            
            await db.save()
            
            await message.answer(
                f"✅ Канал {title} успешно добавлен!",
                reply_markup=get_channels_keyboard()
            )
        else:
            await message.answer(
                "❌ Не удалось добавить канал.\n"
                "Проверьте:\n"
                "1. Бот является администратором канала\n"
                "2. Ссылка или ID правильные\n"
                "3. Канал существует",
                reply_markup=get_channels_keyboard()
            )
        
        del temp_channel_add[user_id]

@dp.callback_query(F.data.startswith("select_channel_"))
@error_handler
async def select_channel(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    channel_id = callback.data.replace("select_channel_", "")
    
    channel = None
    for ch in db.get_channels_list():
        if ch['id'] == channel_id:
            channel = ch
            break
    
    if not channel:
        await callback.answer("❌ Канал не найден", show_alert=True)
        return
    
    text = f"📢 Канал: {channel.get('title', channel['id'])}\n"
    text += f"ID: {channel['id']}\n"
    text += f"Добавлен: {channel.get('added_at', 'неизвестно')[:16]}\n"
    
    if channel_id == db.current_channel:
        text += "\n✅ Это текущий канал для публикаций"
    
    await callback.message.edit_text(text, reply_markup=get_channel_actions_keyboard(channel_id))
    await callback.answer()

@dp.callback_query(F.data.startswith("set_current_"))
@error_handler
async def set_current_channel(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    channel_id = callback.data.replace("set_current_", "")
    
    if db.set_current_channel(channel_id):
        await db.save()
        await callback.answer("✅ Текущий канал изменён")
        await manage_channels(callback)
    else:
        await callback.answer("❌ Ошибка", show_alert=True)

@dp.callback_query(F.data.startswith("delete_channel_"))
@error_handler
async def delete_channel(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    channel_id = callback.data.replace("delete_channel_", "")
    
    db.remove_channel(channel_id)
    await db.save()
    
    await callback.answer("✅ Канал удалён")
    await manage_channels(callback)

@dp.callback_query(F.data == "back_to_admin")
@error_handler
async def back_to_admin(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current = db.get_current_channel()
    if current:
        text = f"🔑 Панель администратора\n📢 Текущий канал: {current.get('title', current['id'])}"
    else:
        text = "🔑 Панель администратора\n⚠️ Канал не выбран!"
    
    try:
        await callback.message.delete()
    except:
        pass
    
    msg = await bot.send_message(
        callback.from_user.id,
        text,
        reply_markup=get_start_keyboard(True)
    )
    
    # Автоудаление через 10 минут
    asyncio.create_task(delete_message_after(callback.from_user.id, msg.message_id, 600))
    await callback.answer()

# ==================== УПРАВЛЕНИЕ ОЧИСТКОЙ ====================

@dp.callback_query(F.data == "clean_menu")
@error_handler
async def clean_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await callback.message.edit_text("🧹 Меню очистки:", reply_markup=get_clean_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "clean_published")
@error_handler
async def clean_published(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    before = len(db.posts)
    db.clean_published_posts()
    await db.save()
    after = len(db.posts)
    
    await callback.message.edit_text(
        f"🧹 Удалено опубликованных постов: {before - after}\n"
        f"📊 Осталось записей: {after}",
        reply_markup=get_clean_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "clean_30days")
@error_handler
async def clean_30days(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    before = len(db.posts)
    db.clean_old_posts(30)
    await db.save()
    after = len(db.posts)
    
    await callback.message.edit_text(
        f"🧹 Удалено записей старше 30 дней: {before - after}\n"
        f"📊 Осталось записей: {after}",
        reply_markup=get_clean_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "clean_stats")
@error_handler
async def clean_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    stats = db.get_stats()
    
    text = "📊 *Статистика базы данных:*\n\n"
    text += f"📝 Всего записей: {stats['total']}\n"
    text += f"⏳ На модерации: {stats['pending']}\n"
    text += f"✅ Одобрено: {stats['approved']}\n"
    text += f"📢 Опубликовано: {stats['published']}\n"
    
    if stats['oldest']:
        text += f"\n🕐 Самая старая запись: {stats['oldest'].strftime('%d.%m.%Y')}\n"
        text += f"🕐 Самая новая запись: {stats['newest'].strftime('%d.%m.%Y')}"
    
    await callback.message.edit_text(text, parse_mode='Markdown', reply_markup=get_clean_keyboard())
    await callback.answer()

# ==================== НАЧАЛО СОЗДАНИЯ ПОСТОВ ====================

@dp.callback_query(F.data == "new_regular")
@error_handler
async def new_regular(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    # Проверяем, нет ли активного поста
    if await check_active_post(user_id, state):
        await callback.answer()
        return
    
    await callback.answer()
    await state.set_state(PostStates.collecting_media)
    
    # Удаляем старые сообщения
    if user_id in temp_data:
        await delete_user_messages(user_id)
    
    temp_data[user_id] = {
        'photos': [], 
        'videos': [], 
        'type': 'regular',
        'created_at': datetime.now().isoformat(),
        'message_ids': []
    }
    
    try:
        await callback.message.delete()
    except:
        pass
    
    msg = await callback.message.answer(
        "📤 Отправляй фото или видео (максимум 4 файла)\n"
        "Можно отправить несколько файлов одним сообщением\n"
        "Когда закончишь добавлять файлы - нажми Готово",
        reply_markup=get_content_keyboard()
    )
    
    temp_data[user_id]['msg_id'] = msg.message_id
    temp_data[user_id]['message_ids'].append(msg.message_id)

@dp.callback_query(F.data == "new_livery")
@error_handler
async def new_livery(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    # Проверяем, нет ли активного поста
    if await check_active_post(user_id, state):
        await callback.answer()
        return
    
    await callback.answer()
    await state.set_state(PostStates.collecting_livery_photo)
    
    # Удаляем старые сообщения
    if user_id in temp_data:
        await delete_user_messages(user_id)
    
    temp_data[user_id] = {
        'photos': [], 
        'body_file': None, 
        'glass_file': None, 
        'type': 'livery',
        'created_at': datetime.now().isoformat(),
        'message_ids': []
    }
    
    try:
        await callback.message.delete()
    except:
        pass
    
    msg = await callback.message.answer(
        "👕 Создание ливреи\n\n"
        "1. Отправь фото ливреи (максимум 4 фото, видео нельзя)\n"
        "2. Когда закончишь добавлять фото - нажми Готово\n"
        "3. Затем отправь файлы .txt\n\n"
        "⚠️ Файлы должны быть строго в формате .txt",
        reply_markup=get_content_keyboard()
    )
    
    temp_data[user_id]['msg_id'] = msg.message_id
    temp_data[user_id]['message_ids'].append(msg.message_id)

@dp.callback_query(F.data == "new_sticker")
@error_handler
async def new_sticker(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    # Проверяем, нет ли активного поста
    if await check_active_post(user_id, state):
        await callback.answer()
        return
    
    await callback.answer()
    await state.set_state(PostStates.collecting_sticker_photo)
    
    # Удаляем старые сообщения
    if user_id in temp_data:
        await delete_user_messages(user_id)
    
    temp_data[user_id] = {
        'photos': [], 
        'sticker_file': None, 
        'type': 'sticker',
        'created_at': datetime.now().isoformat(),
        'message_ids': []
    }
    
    try:
        await callback.message.delete()
    except:
        pass
    
    msg = await callback.message.answer(
        "🏷️ Создание наклейки\n\n"
        "1. Отправь фото наклейки (только 1 фото, видео нельзя)\n"
        "2. После фото отправь файл с наклейкой (.txt)\n\n"
        "⚠️ Файл должен быть в формате .txt",
        reply_markup=get_content_keyboard()
    )
    
    temp_data[user_id]['msg_id'] = msg.message_id
    temp_data[user_id]['message_ids'].append(msg.message_id)

# ==================== СБОР МЕДИА ====================

@dp.message(PostStates.collecting_media, F.photo | F.video | F.media_group)
@error_handler
async def collect_regular_media(message: types.Message, state: FSMContext, album: List[types.Message] = None):
    user_id = message.from_user.id
    
    if user_id not in temp_data:
        await message.reply("Сначала выбери тип поста через /start")
        return
    
    # Сохраняем ID сообщения для автоудаления
    if user_id in temp_data and 'message_ids' in temp_data[user_id]:
        temp_data[user_id]['message_ids'].append(message.message_id)
    
    data = temp_data[user_id]
    current_count = len(data.get('photos', [])) + len(data.get('videos', []))
    
    # Если это альбом (несколько фото/видео в одном сообщении)
    if album:
        total_in_album = len(album)
        if not check_limit('regular', current_count, total_in_album):
            reply_msg = await message.reply(
                f"❌ Нельзя добавить {total_in_album} файлов! "
                f"Лимит {LIMITS['regular']} файла, уже есть {current_count}. "
                f"Можно добавить максимум {LIMITS['regular'] - current_count}.",
                reply_markup=get_content_keyboard()
            )
            if user_id in temp_data:
                temp_data[user_id]['message_ids'].append(reply_msg.message_id)
            asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 5))
            return
        
        added_photos = 0
        added_videos = 0
        
        for msg in album:
            if msg.photo:
                photo = msg.photo[-1]
                data['photos'].append(photo.file_id)
                added_photos += 1
            elif msg.video:
                data['videos'].append(msg.video.file_id)
                added_videos += 1
        
        new_count = current_count + added_photos + added_videos
        reply_msg = await message.reply(
            f"✅ Добавлено файлов: {added_photos + added_videos} "
            f"({new_count}/{LIMITS['regular']})"
        )
        if user_id in temp_data:
            temp_data[user_id]['message_ids'].append(reply_msg.message_id)
        asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 3))
    
    # Если это одиночное сообщение
    else:
        added = False
        file_type = ""
        
        if message.photo:
            if not check_limit('regular', current_count):
                reply_msg = await message.reply(
                    f"❌ Лимит {LIMITS['regular']} файла! Нельзя добавить больше.",
                    reply_markup=get_content_keyboard()
                )
                if user_id in temp_data:
                    temp_data[user_id]['message_ids'].append(reply_msg.message_id)
                asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 5))
                return
            
            photo = message.photo[-1]
            data['photos'].append(photo.file_id)
            added = True
            file_type = "фото"
        
        elif message.video:
            if not check_limit('regular', current_count):
                reply_msg = await message.reply(
                    f"❌ Лимит {LIMITS['regular']} файла! Нельзя добавить больше.",
                    reply_markup=get_content_keyboard()
                )
                if user_id in temp_data:
                    temp_data[user_id]['message_ids'].append(reply_msg.message_id)
                asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 5))
                return
            
            data['videos'].append(message.video.file_id)
            added = True
            file_type = "видео"
        
        if added:
            new_count = current_count + 1
            reply_msg = await message.reply(f"✅ {file_type} добавлено ({new_count}/{LIMITS['regular']})")
            if user_id in temp_data:
                temp_data[user_id]['message_ids'].append(reply_msg.message_id)
            asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 3))
    
    # Обновляем сообщение с прогрессом
    if user_id in temp_data:
        if data.get('msg_id'):
            try:
                await bot.delete_message(user_id, data['msg_id'])
                # Удаляем старый msg_id из списка
                if data['msg_id'] in temp_data[user_id]['message_ids']:
                    temp_data[user_id]['message_ids'].remove(data['msg_id'])
            except:
                pass
        
        total = len(data.get('photos', [])) + len(data.get('videos', []))
        
        msg_text = f"📦 Собрано: {total}/{LIMITS['regular']} файлов\n"
        if total == LIMITS['regular']:
            msg_text += "✅ Лимит достигнут! Больше добавить нельзя. Нажми Готово"
        else:
            msg_text += "Можешь добавить ещё или нажать Готово"
        
        msg = await message.answer(
            msg_text,
            reply_markup=get_content_keyboard()
        )
        data['msg_id'] = msg.message_id
        temp_data[user_id]['message_ids'].append(msg.message_id)

@dp.message(PostStates.collecting_livery_photo, F.photo | F.media_group)
@error_handler
async def collect_livery_photo(message: types.Message, state: FSMContext, album: List[types.Message] = None):
    user_id = message.from_user.id
    
    if user_id not in temp_data:
        await message.reply("Сначала выбери тип поста через /start")
        return
    
    # Сохраняем ID сообщения для автоудаления
    if user_id in temp_data and 'message_ids' in temp_data[user_id]:
        temp_data[user_id]['message_ids'].append(message.message_id)
    
    if message.video:
        reply_msg = await message.reply(
            "❌ Для ливреи можно отправлять только фото!",
            reply_markup=get_content_keyboard()
        )
        if user_id in temp_data:
            temp_data[user_id]['message_ids'].append(reply_msg.message_id)
        asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 5))
        return
    
    data = temp_data[user_id]
    current_count = len(data.get('photos', []))
    
    # Если это альбом (несколько фото в одном сообщении)
    if album:
        total_in_album = len(album)
        if not check_limit('livery', current_count, total_in_album):
            reply_msg = await message.reply(
                f"❌ Нельзя добавить {total_in_album} фото! "
                f"Лимит {LIMITS['livery']} фото, уже есть {current_count}. "
                f"Можно добавить максимум {LIMITS['livery'] - current_count}.",
                reply_markup=get_content_keyboard()
            )
            if user_id in temp_data:
                temp_data[user_id]['message_ids'].append(reply_msg.message_id)
            asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 5))
            return
        
        added_photos = 0
        for msg in album:
            if msg.photo:
                photo = msg.photo[-1]
                data['photos'].append(photo.file_id)
                added_photos += 1
        
        new_count = current_count + added_photos
        reply_msg = await message.reply(
            f"✅ Добавлено фото: {added_photos} ({new_count}/{LIMITS['livery']})"
        )
        if user_id in temp_data:
            temp_data[user_id]['message_ids'].append(reply_msg.message_id)
        asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 3))
    
    # Если это одиночное сообщение
    else:
        if message.photo:
            if not check_limit('livery', current_count):
                reply_msg = await message.reply(
                    f"❌ Лимит {LIMITS['livery']} фото! Нельзя добавить больше.",
                    reply_markup=get_content_keyboard()
                )
                if user_id in temp_data:
                    temp_data[user_id]['message_ids'].append(reply_msg.message_id)
                asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 5))
                return
            
            photo = message.photo[-1]
            data['photos'].append(photo.file_id)
            new_count = current_count + 1
            reply_msg = await message.reply(f"✅ Фото добавлено ({new_count}/{LIMITS['livery']})")
            if user_id in temp_data:
                temp_data[user_id]['message_ids'].append(reply_msg.message_id)
            asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 3))
    
    # Обновляем сообщение с прогрессом
    if user_id in temp_data:
        if data.get('msg_id'):
            try:
                await bot.delete_message(user_id, data['msg_id'])
                # Удаляем старый msg_id из списка
                if data['msg_id'] in temp_data[user_id]['message_ids']:
                    temp_data[user_id]['message_ids'].remove(data['msg_id'])
            except:
                pass
        
        total = len(data['photos'])
        
        msg_text = f"📦 Собрано фото: {total}/{LIMITS['livery']}\n"
        if total == LIMITS['livery']:
            msg_text += "✅ Лимит достигнут! Больше добавить нельзя. Нажми Готово"
        else:
            msg_text += "Можешь добавить ещё или нажать Готово"
        
        msg = await message.answer(
            msg_text,
            reply_markup=get_content_keyboard()
        )
        data['msg_id'] = msg.message_id
        temp_data[user_id]['message_ids'].append(msg.message_id)

@dp.message(PostStates.collecting_sticker_photo, F.photo | F.media_group)
@error_handler
async def collect_sticker_photo(message: types.Message, state: FSMContext, album: List[types.Message] = None):
    user_id = message.from_user.id
    
    if user_id not in temp_data:
        await message.reply("Сначала выбери тип поста через /start")
        return
    
    # Сохраняем ID сообщения для автоудаления
    if user_id in temp_data and 'message_ids' in temp_data[user_id]:
        temp_data[user_id]['message_ids'].append(message.message_id)
    
    if message.video:
        reply_msg = await message.reply(
            "❌ Для наклейки можно отправлять только фото!",
            reply_markup=get_content_keyboard()
        )
        if user_id in temp_data:
            temp_data[user_id]['message_ids'].append(reply_msg.message_id)
        asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 5))
        return
    
    data = temp_data[user_id]
    current_count = len(data.get('photos', []))
    
    # Если это альбом (несколько фото в одном сообщении)
    if album:
        total_in_album = len(album)
        if not check_limit('sticker', current_count, total_in_album):
            reply_msg = await message.reply(
                f"❌ Нельзя добавить {total_in_album} фото! "
                f"Для наклейки нужно только 1 фото. "
                f"Уже есть {current_count}.",
                reply_markup=get_content_keyboard()
            )
            if user_id in temp_data:
                temp_data[user_id]['message_ids'].append(reply_msg.message_id)
            asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 5))
            return
        
        # Добавляем только первое фото из альбома
        for msg in album[:1]:
            if msg.photo:
                photo = msg.photo[-1]
                data['photos'].append(photo.file_id)
                break
        
        new_count = current_count + 1
        reply_msg = await message.reply(
            f"✅ Фото добавлено ({new_count}/{LIMITS['sticker']})"
        )
        if user_id in temp_data:
            temp_data[user_id]['message_ids'].append(reply_msg.message_id)
        asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 3))
    
    # Если это одиночное сообщение
    else:
        if message.photo:
            if not check_limit('sticker', current_count):
                reply_msg = await message.reply(
                    f"❌ Для наклейки нужно только 1 фото! Нельзя добавить больше.",
                    reply_markup=get_content_keyboard()
                )
                if user_id in temp_data:
                    temp_data[user_id]['message_ids'].append(reply_msg.message_id)
                asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 5))
                return
            
            photo = message.photo[-1]
            data['photos'].append(photo.file_id)
            new_count = current_count + 1
            reply_msg = await message.reply(f"✅ Фото добавлено ({new_count}/{LIMITS['sticker']})")
            if user_id in temp_data:
                temp_data[user_id]['message_ids'].append(reply_msg.message_id)
            asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 3))
    
    # Обновляем сообщение с прогрессом
    if user_id in temp_data:
        if data.get('msg_id'):
            try:
                await bot.delete_message(user_id, data['msg_id'])
                # Удаляем старый msg_id из списка
                if data['msg_id'] in temp_data[user_id]['message_ids']:
                    temp_data[user_id]['message_ids'].remove(data['msg_id'])
            except:
                pass
        
        total = len(data['photos'])
        
        msg_text = f"📦 Собрано фото: {total}/{LIMITS['sticker']}\n"
        if total == LIMITS['sticker']:
            msg_text += "✅ Фото получено! Нажми Готово для продолжения"
        else:
            msg_text += "Отправь фото"
        
        msg = await message.answer(
            msg_text,
            reply_markup=get_content_keyboard()
        )
        data['msg_id'] = msg.message_id
        temp_data[user_id]['message_ids'].append(msg.message_id)

# ==================== ОБРАБОТКА НАЖАТИЯ "ГОТОВО" ====================

@dp.callback_query(F.data == "content_done")
@error_handler
async def content_done(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    current_state = await state.get_state()
    
    if user_id not in temp_data:
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    
    data = temp_data[user_id]
    
    if current_state == PostStates.collecting_media.state:
        total = len(data.get('photos', [])) + len(data.get('videos', []))
        if total == 0:
            await callback.answer("❌ Сначала отправь файлы", show_alert=True)
            return
        
        text = "📋 *Проверь содержимое:*\n\n"
        if data.get('photos'):
            text += f"📸 Фото: {len(data['photos'])}\n"
        if data.get('videos'):
            text += f"🎥 Видео: {len(data['videos'])}\n"
        text += f"\n📊 Всего: {total}/{LIMITS['regular']}\n"
        text += "Всё верно?"
        
        await state.set_state(PostStates.confirm_post)
        await callback.message.edit_text(text, parse_mode='Markdown', reply_markup=get_confirm_keyboard())
    
    elif current_state == PostStates.collecting_livery_photo.state:
        if not data.get('photos'):
            await callback.answer("❌ Сначала отправь фото", show_alert=True)
            return
        
        await state.set_state(PostStates.waiting_livery_body_file)
        await callback.message.edit_text(
            "📁 Отправь файл на КУЗОВ (только .txt)\n"
            "Можно отправить только один файл",
            reply_markup=get_cancel_keyboard()
        )
    
    elif current_state == PostStates.collecting_sticker_photo.state:
        if not data.get('photos'):
            await callback.answer("❌ Сначала отправь фото", show_alert=True)
            return
        
        if len(data['photos']) != LIMITS['sticker']:
            await callback.answer(
                f"❌ Для наклейки нужно ровно {LIMITS['sticker']} фото (сейчас {len(data['photos'])})", 
                show_alert=True
            )
            return
        
        await state.set_state(PostStates.waiting_sticker_file)
        await callback.message.edit_text(
            "📁 Отправь файл с наклейкой (только .txt)\n"
            "Можно отправить только один файл",
            reply_markup=get_cancel_keyboard()
        )
    
    await callback.answer()

# ==================== ПОДТВЕРЖДЕНИЕ ОТПРАВКИ ====================

@dp.callback_query(F.data == "confirm_send")
@error_handler
async def confirm_send(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    if user_id not in temp_data:
        await callback.answer("❌ Ошибка", show_alert=True)
        await state.clear()
        return
    
    data = temp_data[user_id]
    username = callback.from_user.username or f"id{user_id}"
    
    if data['type'] == 'regular':
        content = {
            'type': 'regular',
            'photos': data.get('photos', []),
            'videos': data.get('videos', [])
        }
    elif data['type'] == 'livery':
        content = {
            'type': 'livery',
            'photos': data.get('photos', []),
            'files': {
                'body': data['body_file'],
                'glass': data['glass_file']
            }
        }
    elif data['type'] == 'sticker':
        content = {
            'type': 'sticker',
            'photos': data.get('photos', []),
            'files': {
                'sticker': data['sticker_file']
            }
        }
    
    post_id = db.add_post(user_id, username, content)
    await db.save()
    
    await send_to_admin(post_id, content, username)
    
    # Удаляем все сообщения пользователя, кроме финального
    if 'message_ids' in data:
        for msg_id in data['message_ids']:
            try:
                await bot.delete_message(user_id, msg_id)
            except:
                pass
    
    del temp_data[user_id]
    await state.clear()
    
    post_type_text = {
        'regular': 'Обычный пост',
        'livery': 'Ливрея',
        'sticker': 'Наклейка'
    }.get(data['type'], 'Пост')
    
    await callback.message.edit_text(f"✅ {post_type_text} отправлен на проверку!")

@dp.callback_query(F.data == "confirm_redo")
@error_handler
async def confirm_redo(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    if user_id not in temp_data:
        await callback.answer("❌ Ошибка", show_alert=True)
        await state.clear()
        return
    
    data = temp_data[user_id]
    
    # Удаляем все старые сообщения
    if 'message_ids' in data:
        for msg_id in data['message_ids']:
            try:
                await bot.delete_message(user_id, msg_id)
            except:
                pass
    
    if data['type'] == 'regular':
        data['photos'] = []
        data['videos'] = []
        data['message_ids'] = []
        await state.set_state(PostStates.collecting_media)
        msg = await callback.message.edit_text(
            f"📤 Отправляй фото или видео (максимум {LIMITS['regular']} файлов) заново:",
            reply_markup=get_content_keyboard()
        )
        data['msg_id'] = msg.message_id
        data['message_ids'].append(msg.message_id)
    
    elif data['type'] == 'livery':
        data['photos'] = []
        data['body_file'] = None
        data['glass_file'] = None
        data['message_ids'] = []
        await state.set_state(PostStates.collecting_livery_photo)
        msg = await callback.message.edit_text(
            f"👕 Отправь фото ливреи (максимум {LIMITS['livery']} фото) заново:",
            reply_markup=get_content_keyboard()
        )
        data['msg_id'] = msg.message_id
        data['message_ids'].append(msg.message_id)
    
    elif data['type'] == 'sticker':
        data['photos'] = []
        data['sticker_file'] = None
        data['message_ids'] = []
        await state.set_state(PostStates.collecting_sticker_photo)
        msg = await callback.message.edit_text(
            f"🏷️ Отправь фото наклейки (только {LIMITS['sticker']} фото) заново:",
            reply_markup=get_content_keyboard()
        )
        data['msg_id'] = msg.message_id
        data['message_ids'].append(msg.message_id)

# ==================== СБОР ФАЙЛОВ ДЛЯ ЛИВРЕИ ====================

@dp.message(PostStates.waiting_livery_body_file, F.document)
@error_handler
async def get_livery_body_file(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id not in temp_data:
        await message.reply("Сначала выбери тип поста через /start")
        return
    
    # Сохраняем ID сообщения для автоудаления
    if user_id in temp_data and 'message_ids' in temp_data[user_id]:
        temp_data[user_id]['message_ids'].append(message.message_id)
    
    if not message.document:
        reply_msg = await message.reply("❌ Отправь файл в формате .txt")
        if user_id in temp_data:
            temp_data[user_id]['message_ids'].append(reply_msg.message_id)
        asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 5))
        return
    
    file_name = message.document.file_name
    if not is_txt_file(file_name):
        reply_msg = await message.reply("❌ Файл должен быть в формате .txt")
        if user_id in temp_data:
            temp_data[user_id]['message_ids'].append(reply_msg.message_id)
        asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 5))
        return
    
    temp_data[user_id]['body_file'] = {
        'file_id': message.document.file_id,
        'file_name': file_name
    }
    
    await state.set_state(PostStates.waiting_livery_glass_file)
    
    # Удаляем предыдущее сообщение с инструкцией
    if user_id in temp_data and temp_data[user_id].get('msg_id'):
        try:
            await bot.delete_message(user_id, temp_data[user_id]['msg_id'])
            if temp_data[user_id]['msg_id'] in temp_data[user_id]['message_ids']:
                temp_data[user_id]['message_ids'].remove(temp_data[user_id]['msg_id'])
        except:
            pass
    
    msg = await message.answer(
        "✅ Файл кузова получен\n\n"
        "📁 Теперь отправь файл на СТЕКЛО (только .txt)",
        reply_markup=get_cancel_keyboard()
    )
    if user_id in temp_data:
        temp_data[user_id]['msg_id'] = msg.message_id
        temp_data[user_id]['message_ids'].append(msg.message_id)

@dp.message(PostStates.waiting_livery_glass_file, F.document)
@error_handler
async def get_livery_glass_file(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id not in temp_data:
        await message.reply("Сначала выбери тип поста через /start")
        return
    
    # Сохраняем ID сообщения для автоудаления
    if user_id in temp_data and 'message_ids' in temp_data[user_id]:
        temp_data[user_id]['message_ids'].append(message.message_id)
    
    if not message.document:
        reply_msg = await message.reply("❌ Отправь файл в формате .txt")
        if user_id in temp_data:
            temp_data[user_id]['message_ids'].append(reply_msg.message_id)
        asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 5))
        return
    
    file_name = message.document.file_name
    if not is_txt_file(file_name):
        reply_msg = await message.reply("❌ Файл должен быть в формате .txt")
        if user_id in temp_data:
            temp_data[user_id]['message_ids'].append(reply_msg.message_id)
        asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 5))
        return
    
    temp_data[user_id]['glass_file'] = {
        'file_id': message.document.file_id,
        'file_name': file_name
    }
    
    data = temp_data[user_id]
    text = "📋 *Проверь содержимое ливреи:*\n\n"
    text += f"📸 Фото: {len(data['photos'])}/{LIMITS['livery']}\n"
    text += f"📁 Кузов: {data['body_file']['file_name']}\n"
    text += f"📁 Стекло: {data['glass_file']['file_name']}\n"
    text += "\nВсё верно?"
    
    await state.set_state(PostStates.confirm_post)
    
    # Удаляем предыдущее сообщение с инструкцией
    if user_id in temp_data and temp_data[user_id].get('msg_id'):
        try:
            await bot.delete_message(user_id, temp_data[user_id]['msg_id'])
            if temp_data[user_id]['msg_id'] in temp_data[user_id]['message_ids']:
                temp_data[user_id]['message_ids'].remove(temp_data[user_id]['msg_id'])
        except:
            pass
    
    msg = await message.answer(text, parse_mode='Markdown', reply_markup=get_confirm_keyboard())
    if user_id in temp_data:
        temp_data[user_id]['msg_id'] = msg.message_id
        temp_data[user_id]['message_ids'].append(msg.message_id)

# ==================== СБОР ФАЙЛА ДЛЯ НАКЛЕЙКИ ====================

@dp.message(PostStates.waiting_sticker_file, F.document)
@error_handler
async def get_sticker_file(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id not in temp_data:
        await message.reply("Сначала выбери тип поста через /start")
        return
    
    # Сохраняем ID сообщения для автоудаления
    if user_id in temp_data and 'message_ids' in temp_data[user_id]:
        temp_data[user_id]['message_ids'].append(message.message_id)
    
    if not message.document:
        reply_msg = await message.reply("❌ Отправь файл в формате .txt")
        if user_id in temp_data:
            temp_data[user_id]['message_ids'].append(reply_msg.message_id)
        asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 5))
        return
    
    file_name = message.document.file_name
    if not is_txt_file(file_name):
        reply_msg = await message.reply("❌ Файл должен быть в формате .txt")
        if user_id in temp_data:
            temp_data[user_id]['message_ids'].append(reply_msg.message_id)
        asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 5))
        return
    
    temp_data[user_id]['sticker_file'] = {
        'file_id': message.document.file_id,
        'file_name': file_name
    }
    
    data = temp_data[user_id]
    text = "📋 *Проверь содержимое наклейки:*\n\n"
    text += f"📸 Фото: {len(data['photos'])}/{LIMITS['sticker']}\n"
    text += f"🏷️ Файл: {data['sticker_file']['file_name']}\n"
    text += "\nВсё верно?"
    
    await state.set_state(PostStates.confirm_post)
    
    # Удаляем предыдущее сообщение с инструкцией
    if user_id in temp_data and temp_data[user_id].get('msg_id'):
        try:
            await bot.delete_message(user_id, temp_data[user_id]['msg_id'])
            if temp_data[user_id]['msg_id'] in temp_data[user_id]['message_ids']:
                temp_data[user_id]['message_ids'].remove(temp_data[user_id]['msg_id'])
        except:
            pass
    
    msg = await message.answer(text, parse_mode='Markdown', reply_markup=get_confirm_keyboard())
    if user_id in temp_data:
        temp_data[user_id]['msg_id'] = msg.message_id
        temp_data[user_id]['message_ids'].append(msg.message_id)

# ==================== ФУНКЦИЯ ОТПРАВКИ КНОПКИ ПОЛЬЗОВАТЕЛЮ ====================

async def send_new_post_button(user_id: int):
    try:
        text = (
            "👋 Привет! Что хочешь отправить?\n\n"
            "📤 Обычный пост - фото/видео (максимум 4 файла)\n"
            "👕 Ливрея - только фото (максимум 4 фото) + 2 файла .txt\n"
            "🏷️ Наклейка - только 1 фото + 1 файл .txt\n\n"
            "⚠️ Файлы .txt должны быть в формате .txt"
        )
        msg = await bot.send_message(
            user_id,
            text,
            reply_markup=get_new_post_keyboard()
        )
        # Автоудаление через 10 минут
        asyncio.create_task(delete_message_after(user_id, msg.message_id, 600))
    except Exception as e:
        logger.error(f"Не удалось отправить кнопку пользователю {user_id}: {e}")

# ==================== ОТПРАВКА АДМИНУ ====================

async def send_to_admin(post_id: int, content: Dict, username: str):
    current_channel = db.get_current_channel()
    channel_text = f" для {current_channel.get('title', db.current_channel)}" if current_channel else ""
    
    post_type_text = {
        'regular': '📤 Обычный пост',
        'livery': '👕 Ливрея',
        'sticker': '🏷️ Наклейка'
    }.get(content['type'], '📌 Пост')
    
    try:
        for photo_id in content.get('photos', []):
            await bot.send_photo(
                ADMIN_ID,
                photo_id,
                caption=f"{post_type_text} #{post_id} от @{username}{channel_text}"
            )
        
        for video_id in content.get('videos', []):
            await bot.send_video(
                ADMIN_ID,
                video_id,
                caption=f"{post_type_text} #{post_id} от @{username}{channel_text}"
            )
        
        if content['type'] == 'livery':
            if content['files'].get('body'):
                await bot.send_document(
                    ADMIN_ID,
                    content['files']['body']['file_id'],
                    caption=f"📁 КУЗОВ для поста #{post_id}"
                )
            if content['files'].get('glass'):
                await bot.send_document(
                    ADMIN_ID,
                    content['files']['glass']['file_id'],
                    caption=f"📁 СТЕКЛО для поста #{post_id}"
                )
        
        elif content['type'] == 'sticker':
            if content['files'].get('sticker'):
                await bot.send_document(
                    ADMIN_ID,
                    content['files']['sticker']['file_id'],
                    caption=f"🏷️ Наклейка для поста #{post_id}"
                )
        
        await bot.send_message(
            ADMIN_ID,
            f"🔍 {post_type_text} #{post_id}{channel_text}:",
            reply_markup=get_moderation_keyboard(post_id)
        )
    except Exception as e:
        logger.error(f"Ошибка отправки админу поста #{post_id}: {e}")

# ==================== ПУБЛИКАЦИЯ В КАНАЛ ====================

async def publish_post(post: Dict):
    channel_id = post.get('channel')
    if not channel_id:
        logger.error(f"Пост #{post['id']} без канала")
        return
    
    try:
        content = post['content']
        
        for photo_id in content.get('photos', []):
            await bot.send_photo(channel_id, photo_id)
        
        for video_id in content.get('videos', []):
            await bot.send_video(channel_id, video_id)
        
        await bot.send_message(
            channel_id,
            f"✍️ Автор: @{post['username']}"
        )
        
        if content['type'] == 'livery':
            if content['files'].get('body'):
                await bot.send_document(
                    channel_id,
                    content['files']['body']['file_id'],
                    caption="📁 Кузов"
                )
            if content['files'].get('glass'):
                await bot.send_document(
                    channel_id,
                    content['files']['glass']['file_id'],
                    caption="📁 Стекло"
                )
        
        elif content['type'] == 'sticker':
            if content['files'].get('sticker'):
                await bot.send_document(
                    channel_id,
                    content['files']['sticker']['file_id'],
                    caption="🏷️ Наклейка"
                )
        
        db.mark_published(post['id'])
        await db.save()
        
        channel = db.get_current_channel()
        channel_name = channel.get('title', channel_id) if channel else channel_id
        await bot.send_message(
            ADMIN_ID,
            f"✅ Пост #{post['id']} опубликован в {channel_name}"
        )
        
    except Exception as e:
        logger.error(f"Ошибка публикации поста #{post['id']}: {e}")
        await bot.send_message(
            ADMIN_ID,
            f"❌ Ошибка публикации поста #{post['id']} в канале {channel_id}\n{e}"
        )

# ==================== МОДЕРАЦИЯ И НАВИГАЦИЯ ====================

@dp.callback_query(F.data == "admin_queue")
@error_handler
async def show_queue(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    pending = db.get_pending_posts()
    
    if not pending:
        await callback.message.edit_text(
            "📭 Нет постов на модерации",
            reply_markup=get_start_keyboard(True)
        )
        return
    
    pending.sort(key=lambda x: x['created_at'], reverse=True)
    
    text = "📋 *Ожидают проверки:*\n\n"
    builder = InlineKeyboardBuilder()
    
    for p in pending[:10]:
        channel_info = ""
        if p.get('channel'):
            for ch in db.channels:
                if ch['id'] == p['channel']:
                    channel_info = f" в {ch.get('title', ch['id'])[:10]}"
                    break
        
        type_emoji = {
            'regular': '📤',
            'livery': '👕',
            'sticker': '🏷️'
        }.get(p['content']['type'], '📌')
        
        file_count = 0
        if p['content']['type'] == 'regular':
            file_count = len(p['content'].get('photos', [])) + len(p['content'].get('videos', []))
        else:
            file_count = len(p['content'].get('photos', [])) + 1
        
        short_text = f"{type_emoji} #{p['id']} @{p['username']}{channel_info} ({file_count} 📎)"
        builder.row(InlineKeyboardButton(
            text=short_text,
            callback_data=f"view_post_{p['id']}"
        ))
    
    if len(pending) > 10:
        builder.row(InlineKeyboardButton(
            text=f"📌 Ещё {len(pending) - 10} постов...",
            callback_data="no_action"
        ))
    
    builder.row(
        InlineKeyboardButton(text="🧹 Очистить всё", callback_data="clean_menu"),
        InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_admin")
    )
    
    await callback.message.edit_text(
        text,
        parse_mode='Markdown',
        reply_markup=builder.as_markup()
    )

async def show_post_detail(callback: CallbackQuery, post_id: int):
    post = db.get_post(post_id)
    if not post:
        await callback.answer("❌ Пост не найден", show_alert=True)
        return
    
    pending = db.get_pending_posts()
    total = len(pending)
    
    channel_info = ""
    if post.get('channel'):
        for ch in db.channels:
            if ch['id'] == post['channel']:
                channel_info = f" в {ch.get('title', ch['id'])}"
                break
    
    type_emoji = {
        'regular': '📤',
        'livery': '👕',
        'sticker': '🏷️'
    }.get(post['content']['type'], '📌')
    
    text = f"{type_emoji} *Пост #{post_id}* из {total}\n"
    text += f"👤 От: @{post['username']}{channel_info}\n"
    
    if post['content']['type'] == 'regular':
        if post['content'].get('photos'):
            text += f"📸 Фото: {len(post['content']['photos'])}\n"
        if post['content'].get('videos'):
            text += f"🎥 Видео: {len(post['content']['videos'])}\n"
        total_files = len(post['content'].get('photos', [])) + len(post['content'].get('videos', []))
        text += f"📊 Всего: {total_files}/{LIMITS['regular']}\n"
    elif post['content']['type'] == 'livery':
        text += f"📸 Фото: {len(post['content']['photos'])}/{LIMITS['livery']}\n"
        text += "📁 Кузов: +1 файл\n📁 Стекло: +1 файл\n"
    elif post['content']['type'] == 'sticker':
        text += f"📸 Фото: {len(post['content']['photos'])}/{LIMITS['sticker']}\n"
        text += "🏷️ Наклейка: +1 файл\n"
    
    text += f"\n🕐 Создан: {post['created_at'][:16]}"
    
    try:
        await callback.message.delete()
    except:
        pass
    
    try:
        if post['content'].get('photos'):
            await bot.send_photo(
                callback.from_user.id,
                post['content']['photos'][0],
                caption=text,
                parse_mode='Markdown',
                reply_markup=get_post_navigation_keyboard(post_id, total)
            )
        elif post['content'].get('videos'):
            await bot.send_video(
                callback.from_user.id,
                post['content']['videos'][0],
                caption=text,
                parse_mode='Markdown',
                reply_markup=get_post_navigation_keyboard(post_id, total)
            )
        else:
            await bot.send_message(
                callback.from_user.id,
                text,
                parse_mode='Markdown',
                reply_markup=get_post_navigation_keyboard(post_id, total)
            )
    except Exception as e:
        logger.error(f"Ошибка показа поста #{post_id}: {e}")
        await bot.send_message(
            callback.from_user.id,
            text,
            parse_mode='Markdown',
            reply_markup=get_post_navigation_keyboard(post_id, total)
        )

@dp.callback_query(F.data.startswith("view_post_"))
@error_handler
async def view_post(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    post_id = int(callback.data.split("_")[2])
    await show_post_detail(callback, post_id)

@dp.callback_query(F.data.startswith("nav_"))
@error_handler
async def navigation_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    parts = callback.data.split("_")
    action = parts[1]
    post_id = int(parts[2])
    
    pending = db.get_pending_posts()
    post_ids = [p['id'] for p in pending]
    
    if action == "prev":
        try:
            current_index = post_ids.index(post_id)
            if current_index > 0:
                await show_post_detail(callback, post_ids[current_index - 1])
            else:
                await callback.answer("Это первый пост", show_alert=True)
        except ValueError:
            await callback.answer("Пост не найден", show_alert=True)
    
    elif action == "next":
        try:
            current_index = post_ids.index(post_id)
            if current_index < len(post_ids) - 1:
                await show_post_detail(callback, post_ids[current_index + 1])
            else:
                await callback.answer("Это последний пост", show_alert=True)
        except ValueError:
            await callback.answer("Пост не найден", show_alert=True)
    
    elif action == "approve":
        try:
            await callback.message.delete()
        except:
            pass
        await approve_post_logic(callback, post_id)
    
    elif action == "reject":
        await reject_post_logic(callback, post_id)
    
    elif action == "delete":
        db.delete_post(post_id)
        await db.save()
        await callback.answer("🗑️ Пост удалён", show_alert=True)
        await show_queue(callback)
    
    elif action in ["10sec", "10min", "sched"]:
        try:
            await callback.message.delete()
        except:
            pass
        await set_time_logic(callback, post_id, action)

async def approve_post_logic(callback: CallbackQuery, post_id: int):
    post = db.get_post(post_id)
    if not post:
        await callback.answer("❌ Пост не найден", show_alert=True)
        return
    
    if not db.get_current_channel():
        await bot.send_message(
            callback.from_user.id,
            "⚠️ Сначала добавьте канал в управлении",
            reply_markup=get_start_keyboard(True)
        )
        return
    
    await bot.send_message(
        callback.from_user.id,
        f"⏱ Выбери время для поста #{post_id}:",
        reply_markup=get_time_keyboard(post_id)
    )

async def reject_post_logic(callback: CallbackQuery, post_id: int):
    post = db.get_post(post_id)
    if post:
        try:
            await bot.send_message(
                post['user_id'],
                "😔 Пост не прошёл модерацию, но мы всё равно ценим твою поддержку! 🌟"
            )
            await send_new_post_button(post['user_id'])
        except:
            pass
        
        db.delete_post(post_id)
        await db.save()
    
    await bot.send_message(
        callback.from_user.id,
        "❌ Пост отклонён",
        reply_markup=get_start_keyboard(True)
    )

async def set_time_logic(callback: CallbackQuery, post_id: int, time_type: str):
    now = datetime.now()
    scheduled = None
    
    if time_type == "10sec":
        scheduled = (now + timedelta(seconds=10)).isoformat()
    elif time_type == "10min":
        scheduled = (now + timedelta(minutes=10)).isoformat()
    elif time_type == "sched":
        tomorrow = now + timedelta(days=1)
        scheduled = tomorrow.replace(hour=6, minute=0, second=0).isoformat()
    
    db.approve_post(post_id, scheduled)
    await db.save()
    
    post = db.get_post(post_id)
    if post:
        try:
            await bot.send_message(
                post['user_id'],
                "✅ Пост одобрен! Спасибо огромное за помощь каналу! 🙏✨ Ты делаешь этот канал лучше! 💫"
            )
            await send_new_post_button(post['user_id'])
        except:
            pass
    
    channel = db.get_current_channel()
    channel_name = channel.get('title', db.current_channel) if channel else "канал"
    
    await bot.send_message(
        callback.from_user.id,
        f"✅ Пост #{post_id} добавлен в очередь\n📢 Канал: {channel_name}",
        reply_markup=get_start_keyboard(True)
    )

# ==================== СТАРЫЕ ОБРАБОТЧИКИ МОДЕРАЦИИ ====================

@dp.callback_query(F.data.startswith("approve_"))
@error_handler
async def approve_post(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    post_id = int(callback.data.split("_")[1])
    post = db.get_post(post_id)
    
    if not post:
        await callback.answer("❌ Пост не найден", show_alert=True)
        return
    
    if not db.get_current_channel():
        await callback.message.edit_text(
            "⚠️ Сначала добавьте канал в управлении",
            reply_markup=get_start_keyboard(True)
        )
        return
    
    await callback.message.edit_text(
        f"⏱ Время публикации для поста #{post_id}\n"
        f"Канал: {db.get_current_channel().get('title', db.current_channel)}",
        reply_markup=get_time_keyboard(post_id)
    )

@dp.callback_query(F.data.startswith("reject_"))
@error_handler
async def reject_post(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    post_id = int(callback.data.split("_")[1])
    post = db.get_post(post_id)
    
    if post:
        try:
            await bot.send_message(
                post['user_id'],
                "😔 Пост не прошёл модерацию, но мы всё равно ценим твою поддержку! 🌟"
            )
            await send_new_post_button(post['user_id'])
        except:
            pass
        
        db.delete_post(post_id)
        await db.save()
    
    await callback.message.edit_text(
        "❌ Пост отклонён",
        reply_markup=get_start_keyboard(True)
    )

@dp.callback_query(F.data.startswith("time_"))
@error_handler
async def set_time(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    parts = callback.data.split("_")
    time_type = parts[1]
    post_id = int(parts[2])
    
    now = datetime.now()
    scheduled = None
    
    if time_type == "10sec":
        scheduled = (now + timedelta(seconds=10)).isoformat()
    elif time_type == "10min":
        scheduled = (now + timedelta(minutes=10)).isoformat()
    elif time_type == "schedule":
        tomorrow = now + timedelta(days=1)
        scheduled = tomorrow.replace(hour=6, minute=0, second=0).isoformat()
    
    db.approve_post(post_id, scheduled)
    await db.save()
    
    post = db.get_post(post_id)
    if post:
        try:
            await bot.send_message(
                post['user_id'],
                "✅ Пост одобрен! Спасибо огромное за помощь каналу! 🙏✨ Ты делаешь этот канал лучше! 💫"
            )
            await send_new_post_button(post['user_id'])
        except:
            pass
    
    channel = db.get_current_channel()
    channel_name = channel.get('title', db.current_channel) if channel else "канал"
    
    await callback.message.edit_text(
        f"✅ Пост #{post_id} добавлен в очередь\n"
        f"📢 Канал: {channel_name}",
        reply_markup=get_start_keyboard(True)
    )

@dp.callback_query(F.data == "admin_stats")
@error_handler
async def show_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    stats = db.get_stats()
    
    text = "📊 *Статистика:*\n\n"
    text += f"📝 Всего постов: {stats['total']}\n"
    text += f"⏳ На модерации: {stats['pending']}\n"
    text += f"✅ Одобрено: {stats['approved']}\n"
    text += f"📢 Опубликовано: {stats['published']}\n"
    text += f"\n📢 Каналов: {len(db.channels)}\n"
    
    current = db.get_current_channel()
    current_name = current.get('title', db.current_channel) if current else "не выбран"
    text += f"📍 Текущий: {current_name}"
    
    await callback.message.edit_text(text, parse_mode='Markdown', reply_markup=get_start_keyboard(True))
    await callback.answer()

@dp.callback_query(F.data == "no_action")
@error_handler
async def no_action(callback: CallbackQuery):
    await callback.answer()

# ==================== ПЛАНИРОВЩИК ====================

async def scheduler():
    while True:
        try:
            now = datetime.now()
            
            for post in db.posts:
                if (post['status'] == 'approved' and 
                    post.get('scheduled_time') and
                    datetime.fromisoformat(post['scheduled_time']) <= now):
                    await publish_post(post)
            
            if now.hour == 6 and now.minute == 0:
                next_post = db.get_next_post()
                if next_post and not next_post.get('scheduled_time'):
                    await publish_post(next_post)
            
            if now.hour == 3 and now.minute == 0:
                before = len(db.posts)
                db.clean_old_posts(30)
                after = len(db.posts)
                if before != after:
                    await bot.send_message(
                        ADMIN_ID,
                        f"🧹 Автоматическая очистка выполнена\n"
                        f"Удалено записей: {before - after}\n"
                        f"Осталось: {after}"
                    )
                    await db.save()
        
        except Exception as e:
            logger.error(f"Ошибка в планировщике: {e}")
        
        await asyncio.sleep(60)

# ==================== ЗАПУСК ====================

async def on_startup():
    os.makedirs(MEDIA_DIR, exist_ok=True)
    
    # Запускаем автосохранение
    db.start_auto_save()
    
    # Запускаем очистку временных данных
    asyncio.create_task(clean_temp_data())
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook удалён, бот работает в режиме polling")
    except Exception as e:
        logger.error(f"Ошибка при удалении вебхука: {e}")
    
    asyncio.create_task(scheduler())
    
    channels = db.get_channels_list()
    if channels:
        current = db.get_current_channel()
        current_name = current.get('title', db.current_channel) if current else 'не выбран'
        stats = db.get_stats()
        
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🚀 Бот запущен\n"
                f"📢 Каналов: {len(channels)}\n"
                f"✅ Текущий: {current_name}\n"
                f"📊 Записей в БД: {stats['total']}"
            )
        except Exception as e:
            logger.error(f"Не удалось отправить приветствие админу: {e}")
    else:
        try:
            await bot.send_message(
                ADMIN_ID,
                "🚀 Бот запущен\n"
                "⚠️ Каналы не добавлены. Перейдите в Управление каналами."
            )
        except Exception as e:
            logger.error(f"Не удалось отправить приветствие админу: {e}")
    
    logger.info("Бот запущен")

async def on_shutdown():
    await db.save()
    logger.info("Бот остановлен")

async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Фатальная ошибка: {e}")
