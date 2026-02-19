import asyncio
import os
from datetime import datetime, timedelta, time
from typing import List, Dict, Optional
import logging
import json
import aiofiles

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = "7078059729:AAG4JvDdzbHV-3ga-LfjEziTA7W3NMmgnZY"
ADMIN_USERNAME = "JDD452"
ADMIN_ID = 5138605368
MEDIA_DIR = "temp_media"

os.makedirs(MEDIA_DIR, exist_ok=True)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== СОСТОЯНИЯ ДЛЯ FSM ====================
class PostStates(StatesGroup):
    choosing_type = State()
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

class Database:
    def __init__(self):
        self.posts: List[Dict] = []
        self.channels: List[Dict] = []
        self.current_channel: Optional[str] = None
        self.load()
    
    def load(self):
        if os.path.exists(DB_FILE):
            try:
                with open(DB_FILE, 'r', encoding='utf-8') as f:
                    self.posts = json.load(f)
            except:
                self.posts = []
        
        if os.path.exists(CHANNELS_FILE):
            try:
                with open(CHANNELS_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.channels = data.get('channels', [])
                    self.current_channel = data.get('current_channel')
            except:
                self.channels = []
                self.current_channel = None
    
    async def save(self):
        async with aiofiles.open(DB_FILE, 'w', encoding='utf-8') as f:
            await f.write(json.dumps(self.posts, ensure_ascii=False, indent=2))
        
        async with aiofiles.open(CHANNELS_FILE, 'w', encoding='utf-8') as f:
            data = {
                'channels': self.channels,
                'current_channel': self.current_channel
            }
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))
    
    def add_post(self, user_id: int, username: str, content: Dict) -> int:
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
    
    def get_post(self, post_id: int) -> Dict | None:
        for p in self.posts:
            if p['id'] == post_id:
                return p
        return None
    
    def approve_post(self, post_id: int, scheduled_time: str = None):
        post = self.get_post(post_id)
        if post:
            post['status'] = 'approved'
            post['scheduled_time'] = scheduled_time
    
    def get_next_post(self) -> Dict | None:
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
        self.posts = [
            p for p in self.posts 
            if datetime.fromisoformat(p['created_at']) > now - timedelta(days=days)
        ]
    
    def clean_published_posts(self):
        self.posts = [p for p in self.posts if p['status'] != 'published']
    
    def get_stats(self) -> Dict:
        return {
            'total': len(self.posts),
            'pending': len([p for p in self.posts if p['status'] == 'pending']),
            'approved': len([p for p in self.posts if p['status'] == 'approved']),
            'published': len([p for p in self.posts if p['status'] == 'published']),
            'oldest': min([datetime.fromisoformat(p['created_at']) for p in self.posts]) if self.posts else None,
            'newest': max([datetime.fromisoformat(p['created_at']) for p in self.posts]) if self.posts else None
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

def is_admin(username: str) -> bool:
    return username == ADMIN_USERNAME

async def check_bot_in_channel(channel_id: str) -> bool:
    try:
        chat = await bot.get_chat(channel_id)
        msg = await bot.send_message(channel_id, "🔍 Проверка связи...")
        await msg.delete()
        return True
    except Exception as e:
        logging.error(f"Ошибка проверки канала {channel_id}: {e}")
        return False

def is_txt_file(file_name: str) -> bool:
    return file_name and file_name.lower().endswith('.txt')

# ==================== ФУНКЦИИ АВТОУДАЛЕНИЯ ====================

async def delete_message_after(chat_id: int, message_id: int, seconds: int = 10):
    await asyncio.sleep(seconds)
    try:
        await bot.delete_message(chat_id, message_id)
    except:
        pass

# ==================== ВРЕМЕННОЕ ХРАНИЛИЩЕ ====================
temp_data = {}
temp_channel_add = {}

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

def get_post_navigation_keyboard(post_id: int, total: int, post_data: Dict) -> InlineKeyboardMarkup:
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
        InlineKeyboardButton(text="📋 К списку", callback_data="admin_queue"),
        InlineKeyboardButton(text="🗑️ Удалить пост", callback_data=f"nav_delete_{post_id}")
    )
    
    return builder.as_markup()

def get_moderation_keyboard(post_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Одобрить", callback_data=f"approve_{post_id}")
    builder.button(text="❌ Отклонить", callback_data=f"reject_{post_id}")
    builder.adjust(2)
    return builder.as_markup()

def get_time_keyboard(post_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="⏱️ 10 секунд", callback_data=f"time_10sec_{post_id}")
    builder.button(text="⏰ 10 минут", callback_data=f"time_10min_{post_id}")
    builder.button(text="📅 Завтра 9:00", callback_data=f"time_schedule_{post_id}")
    builder.adjust(1)
    return builder.as_markup()

def get_new_post_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="📤 Обычный пост", callback_data="new_regular")
    builder.button(text="👕 Ливрея", callback_data="new_livery")
    builder.button(text="🏷️ Наклейка", callback_data="new_sticker")
    builder.adjust(1)
    return builder.as_markup()

# ==================== ОБРАБОТЧИК ОТМЕНЫ ====================

@dp.callback_query(F.data == "cancel_post")
async def cancel_post(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    # Очищаем временные данные
    if user_id in temp_data:
        if temp_data[user_id].get('msg_id'):
            try:
                await bot.delete_message(user_id, temp_data[user_id]['msg_id'])
            except:
                pass
        del temp_data[user_id]
    
    # Сбрасываем состояние
    await state.clear()
    
    # Удаляем сообщение с кнопками отмены
    try:
        await callback.message.delete()
    except:
        pass
    
    # Отправляем НОВОЕ приветственное сообщение с кнопками
    text = (
        "👋 Привет! Что хочешь отправить?\n\n"
        "📤 Обычный пост - фото/видео\n"
        "👕 Ливрея - фото + 2 файла (.txt) на кузов и стекло\n"
        "🏷️ Наклейка - фото + 1 файл (.txt)\n\n"
        "⚠️ Файлы должны быть в формате .txt"
    )
    
    await bot.send_message(
        user_id,
        text,
        reply_markup=get_start_keyboard(False)
    )
    
    await callback.answer("❌ Создание поста отменено")

# ==================== ОБРАБОТЧИКИ КОМАНД ====================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = message.from_user
    admin_user = is_admin(user.username)
    
    if admin_user:
        current = db.get_current_channel()
        if current:
            text = f"🔑 Панель администратора\n📢 Текущий канал: {current.get('title', current['id'])}"
        else:
            text = "🔑 Панель администратора\n⚠️ Канал не выбран! Добавьте канал в управлении."
        
        await message.answer(text, reply_markup=get_start_keyboard(True))
    else:
        text = (
            "👋 Привет! Что хочешь отправить?\n\n"
            "📤 Обычный пост - фото/видео\n"
            "👕 Ливрея - фото + 2 файла (.txt) на кузов и стекло\n"
            "🏷️ Наклейка - фото + 1 файл (.txt)\n\n"
            "⚠️ Файлы должны быть в формате .txt"
        )
        await message.answer(text, reply_markup=get_start_keyboard(False))

@dp.message(Command("clean"))
async def cmd_clean(message: types.Message):
    if not is_admin(message.from_user.username):
        await message.answer("⛔ Доступ запрещён")
        return
    
    await message.answer("🧹 Меню очистки:", reply_markup=get_clean_keyboard())

# ==================== УПРАВЛЕНИЕ КАНАЛАМИ ====================

@dp.callback_query(F.data == "manage_channels")
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
async def back_to_admin(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current = db.get_current_channel()
    if current:
        text = f"🔑 Панель администратора\n📢 Текущий канал: {current.get('title', current['id'])}"
    else:
        text = "🔑 Панель администратора\n⚠️ Канал не выбран!"
    
    await callback.message.edit_text(text, reply_markup=get_start_keyboard(True))
    await callback.answer()

# ==================== УПРАВЛЕНИЕ ОЧИСТКОЙ ====================

@dp.callback_query(F.data == "clean_menu")
async def clean_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await callback.message.edit_text("🧹 Меню очистки:", reply_markup=get_clean_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "clean_published")
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
async def new_regular(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    
    if callback.from_user.id in temp_data:
        await callback.message.answer("⏳ Сначала дождись проверки предыдущего поста!")
        return
    
    await state.set_state(PostStates.collecting_media)
    
    temp_data[callback.from_user.id] = {
        'photos': [], 
        'videos': [], 
        'type': 'regular'
    }
    
    try:
        await callback.message.delete()
    except:
        pass
    
    msg = await callback.message.answer(
        "📤 Отправляй фото или видео\n"
        "Можно отправить несколько файлов одним сообщением\n"
        "Когда закончишь - нажми кнопку",
        reply_markup=get_content_keyboard()
    )
    temp_data[callback.from_user.id]['msg_id'] = msg.message_id

@dp.callback_query(F.data == "new_livery")
async def new_livery(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    
    if callback.from_user.id in temp_data:
        await callback.message.answer("⏳ Сначала дождись проверки предыдущего поста!")
        return
    
    await state.set_state(PostStates.collecting_livery_photo)
    
    temp_data[callback.from_user.id] = {
        'photos': [], 
        'body_file': None, 
        'glass_file': None, 
        'type': 'livery'
    }
    
    try:
        await callback.message.delete()
    except:
        pass
    
    msg = await callback.message.answer(
        "👕 Создание ливреи\n\n"
        "1. Отправь фото ливреи (можно несколько одним сообщением)\n"
        "2. После фото я попрошу отправить файл на КУЗОВ (.txt)\n"
        "3. Затем файл на СТЕКЛО (.txt)\n\n"
        "⚠️ Файлы должны быть строго в формате .txt",
        reply_markup=get_content_keyboard()
    )
    temp_data[callback.from_user.id]['msg_id'] = msg.message_id

@dp.callback_query(F.data == "new_sticker")
async def new_sticker(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    
    if callback.from_user.id in temp_data:
        await callback.message.answer("⏳ Сначала дождись проверки предыдущего поста!")
        return
    
    await state.set_state(PostStates.collecting_sticker_photo)
    
    temp_data[callback.from_user.id] = {
        'photos': [], 
        'sticker_file': None, 
        'type': 'sticker'
    }
    
    try:
        await callback.message.delete()
    except:
        pass
    
    msg = await callback.message.answer(
        "🏷️ Создание наклейки\n\n"
        "1. Отправь фото наклейки (можно несколько одним сообщением)\n"
        "2. После фото отправь файл с наклейкой (.txt)\n\n"
        "⚠️ Файл должен быть в формате .txt",
        reply_markup=get_content_keyboard()
    )
    temp_data[callback.from_user.id]['msg_id'] = msg.message_id

# ==================== СБОР МЕДИА ====================

@dp.message(PostStates.collecting_media, F.photo | F.video)
async def collect_regular_media(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id not in temp_data:
        await message.reply("Сначала выбери тип поста через /start")
        return
    
    added = False
    
    if message.photo:
        photo = message.photo[-1]
        temp_data[user_id]['photos'].append(photo.file_id)
        added = True
    
    elif message.video:
        temp_data[user_id]['videos'].append(message.video.file_id)
        added = True
    
    if added:
        total = len(temp_data[user_id]['photos']) + len(temp_data[user_id]['videos'])
        reply_msg = await message.reply(f"✅ Добавлено ({total})")
        asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 3))
    
    if temp_data[user_id].get('msg_id'):
        try:
            await bot.delete_message(user_id, temp_data[user_id]['msg_id'])
        except:
            pass
    
    total = len(temp_data[user_id]['photos']) + len(temp_data[user_id]['videos'])
    
    msg = await message.answer(
        f"📦 Собрано: {total} файлов\n"
        "Можешь добавить ещё или нажать Готово",
        reply_markup=get_content_keyboard()
    )
    temp_data[user_id]['msg_id'] = msg.message_id

@dp.message(PostStates.collecting_livery_photo, F.photo)
async def collect_livery_photo(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id not in temp_data:
        await message.reply("Сначала выбери тип поста через /start")
        return
    
    if message.photo:
        photo = message.photo[-1]
        temp_data[user_id]['photos'].append(photo.file_id)
        reply_msg = await message.reply(f"✅ Фото добавлено ({len(temp_data[user_id]['photos'])})")
        asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 3))
    
    if temp_data[user_id].get('msg_id'):
        try:
            await bot.delete_message(user_id, temp_data[user_id]['msg_id'])
        except:
            pass
    
    msg = await message.answer(
        f"📦 Собрано фото: {len(temp_data[user_id]['photos'])}\n"
        "Можешь добавить ещё или нажать Готово",
        reply_markup=get_content_keyboard()
    )
    temp_data[user_id]['msg_id'] = msg.message_id

@dp.message(PostStates.collecting_sticker_photo, F.photo)
async def collect_sticker_photo(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id not in temp_data:
        await message.reply("Сначала выбери тип поста через /start")
        return
    
    if message.photo:
        photo = message.photo[-1]
        temp_data[user_id]['photos'].append(photo.file_id)
        reply_msg = await message.reply(f"✅ Фото добавлено ({len(temp_data[user_id]['photos'])})")
        asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 3))
    
    if temp_data[user_id].get('msg_id'):
        try:
            await bot.delete_message(user_id, temp_data[user_id]['msg_id'])
        except:
            pass
    
    msg = await message.answer(
        f"📦 Собрано фото: {len(temp_data[user_id]['photos'])}\n"
        "Можешь добавить ещё или нажать Готово",
        reply_markup=get_content_keyboard()
    )
    temp_data[user_id]['msg_id'] = msg.message_id

# ==================== ОБРАБОТКА НАЖАТИЯ "ГОТОВО" ====================

@dp.callback_query(F.data == "content_done")
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
        text += "\nВсё верно?"
        
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
        await state.set_state(PostStates.waiting_sticker_file)
        await callback.message.edit_text(
            "📁 Отправь файл с наклейкой (только .txt)\n"
            "Можно отправить только один файл",
            reply_markup=get_cancel_keyboard()
        )
    
    await callback.answer()

# ==================== ПОДТВЕРЖДЕНИЕ ОТПРАВКИ ====================

@dp.callback_query(F.data == "confirm_send")
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
    
    if data.get('msg_id'):
        try:
            await bot.delete_message(user_id, data['msg_id'])
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
async def confirm_redo(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    if user_id not in temp_data:
        await callback.answer("❌ Ошибка", show_alert=True)
        await state.clear()
        return
    
    data = temp_data[user_id]
    
    if data['type'] == 'regular':
        data['photos'] = []
        data['videos'] = []
        await state.set_state(PostStates.collecting_media)
        await callback.message.edit_text(
            "📤 Отправляй фото или видео заново:",
            reply_markup=get_content_keyboard()
        )
    elif data['type'] == 'livery':
        data['photos'] = []
        data['body_file'] = None
        data['glass_file'] = None
        await state.set_state(PostStates.collecting_livery_photo)
        await callback.message.edit_text(
            "👕 Отправь фото ливреи заново:",
            reply_markup=get_content_keyboard()
        )
    elif data['type'] == 'sticker':
        data['photos'] = []
        data['sticker_file'] = None
        await state.set_state(PostStates.collecting_sticker_photo)
        await callback.message.edit_text(
            "🏷️ Отправь фото наклейки заново:",
            reply_markup=get_content_keyboard()
        )

# ==================== СБОР ФАЙЛОВ ДЛЯ ЛИВРЕИ ====================

@dp.message(PostStates.waiting_livery_body_file, F.document)
async def get_livery_body_file(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if not message.document:
        await message.reply("❌ Отправь файл в формате .txt")
        return
    
    file_name = message.document.file_name
    if not is_txt_file(file_name):
        await message.reply("❌ Файл должен быть в формате .txt")
        return
    
    temp_data[user_id]['body_file'] = {
        'file_id': message.document.file_id,
        'file_name': file_name
    }
    
    await state.set_state(PostStates.waiting_livery_glass_file)
    await message.answer(
        "✅ Файл кузова получен\n\n"
        "📁 Теперь отправь файл на СТЕКЛО (только .txt)",
        reply_markup=get_cancel_keyboard()
    )

@dp.message(PostStates.waiting_livery_glass_file, F.document)
async def get_livery_glass_file(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if not message.document:
        await message.reply("❌ Отправь файл в формате .txt")
        return
    
    file_name = message.document.file_name
    if not is_txt_file(file_name):
        await message.reply("❌ Файл должен быть в формате .txt")
        return
    
    temp_data[user_id]['glass_file'] = {
        'file_id': message.document.file_id,
        'file_name': file_name
    }
    
    data = temp_data[user_id]
    text = "📋 *Проверь содержимое ливреи:*\n\n"
    text += f"📸 Фото: {len(data['photos'])}\n"
    text += f"📁 Кузов: {data['body_file']['file_name']}\n"
    text += f"📁 Стекло: {data['glass_file']['file_name']}\n"
    text += "\nВсё верно?"
    
    await state.set_state(PostStates.confirm_post)
    await message.answer(text, parse_mode='Markdown', reply_markup=get_confirm_keyboard())

# ==================== СБОР ФАЙЛА ДЛЯ НАКЛЕЙКИ ====================

@dp.message(PostStates.waiting_sticker_file, F.document)
async def get_sticker_file(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if not message.document:
        await message.reply("❌ Отправь файл в формате .txt")
        return
    
    file_name = message.document.file_name
    if not is_txt_file(file_name):
        await message.reply("❌ Файл должен быть в формате .txt")
        return
    
    temp_data[user_id]['sticker_file'] = {
        'file_id': message.document.file_id,
        'file_name': file_name
    }
    
    data = temp_data[user_id]
    text = "📋 *Проверь содержимое наклейки:*\n\n"
    text += f"📸 Фото: {len(data['photos'])}\n"
    text += f"🏷️ Файл: {data['sticker_file']['file_name']}\n"
    text += "\nВсё верно?"
    
    await state.set_state(PostStates.confirm_post)
    await message.answer(text, parse_mode='Markdown', reply_markup=get_confirm_keyboard())

# ==================== ФУНКЦИЯ ОТПРАВКИ КНОПКИ ПОЛЬЗОВАТЕЛЮ ====================

async def send_new_post_button(user_id: int):
    try:
        text = (
            "👋 Привет! Что хочешь отправить?\n\n"
            "📤 Обычный пост - фото/видео\n"
            "👕 Ливрея - фото + 2 файла (.txt) на кузов и стекло\n"
            "🏷️ Наклейка - фото + 1 файл (.txt)\n\n"
            "⚠️ Файлы должны быть в формате .txt"
        )
        await bot.send_message(
            user_id,
            text,
            reply_markup=get_new_post_keyboard()
        )
    except Exception as e:
        logging.error(f"Не удалось отправить кнопку пользователю {user_id}: {e}")

# ==================== ОТПРАВКА АДМИНУ ====================

async def send_to_admin(post_id: int, content: Dict, username: str):
    current_channel = db.get_current_channel()
    channel_text = f" для {current_channel.get('title', db.current_channel)}" if current_channel else ""
    
    post_type_text = {
        'regular': '📤 Обычный пост',
        'livery': '👕 Ливрея',
        'sticker': '🏷️ Наклейка'
    }.get(content['type'], '📌 Пост')
    
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

# ==================== ПУБЛИКАЦИЯ В КАНАЛ ====================

async def publish_post(post: Dict):
    channel_id = post.get('channel')
    if not channel_id:
        logging.error(f"Пост #{post['id']} без канала")
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
        logging.error(f"Ошибка публикации поста #{post['id']}: {e}")
        await bot.send_message(
            ADMIN_ID,
            f"❌ Ошибка публикации поста #{post['id']} в канале {channel_id}\n{e}"
        )

# ==================== МОДЕРАЦИЯ И НАВИГАЦИЯ ====================

@dp.callback_query(F.data == "admin_queue")
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
    elif post['content']['type'] == 'livery':
        text += f"📸 Фото: {len(post['content']['photos'])}\n"
        text += "📁 Кузов: +1 файл\n📁 Стекло: +1 файл"
    elif post['content']['type'] == 'sticker':
        text += f"📸 Фото: {len(post['content']['photos'])}\n"
        text += "🏷️ Наклейка: +1 файл"
    
    text += f"\n🕐 Создан: {post['created_at'][:16]}"
    
    await callback.message.delete()
    
    if post['content'].get('photos'):
        await bot.send_photo(
            callback.from_user.id,
            post['content']['photos'][0],
            caption=text,
            parse_mode='Markdown',
            reply_markup=get_post_navigation_keyboard(post_id, total, post)
        )
    elif post['content'].get('videos'):
        await bot.send_video(
            callback.from_user.id,
            post['content']['videos'][0],
            caption=text,
            parse_mode='Markdown',
            reply_markup=get_post_navigation_keyboard(post_id, total, post)
        )

@dp.callback_query(F.data.startswith("view_post_"))
async def view_post(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    post_id = int(callback.data.split("_")[2])
    await show_post_detail(callback, post_id)

@dp.callback_query(F.data.startswith("nav_"))
async def navigation_handler(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    action = callback.data.split("_")[1]
    post_id = int(callback.data.split("_")[2])
    
    pending = db.get_pending_posts()
    post_ids = [p['id'] for p in pending]
    
    if action == "prev":
        current_index = post_ids.index(post_id)
        if current_index > 0:
            await show_post_detail(callback, post_ids[current_index - 1])
        else:
            await callback.answer("Это первый пост", show_alert=True)
    
    elif action == "next":
        current_index = post_ids.index(post_id)
        if current_index < len(post_ids) - 1:
            await show_post_detail(callback, post_ids[current_index + 1])
        else:
            await callback.answer("Это последний пост", show_alert=True)
    
    elif action == "approve":
        await callback.message.delete()
        await approve_post_logic(callback, post_id)
    
    elif action == "reject":
        await reject_post_logic(callback, post_id)
    
    elif action == "delete":
        db.delete_post(post_id)
        await db.save()
        await callback.answer("🗑️ Пост удалён", show_alert=True)
        await show_queue(callback)
    
    elif action in ["10sec", "10min", "sched"]:
        await callback.message.delete()
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
async def no_action(callback: CallbackQuery):
    await callback.answer()

# ==================== ПЛАНИРОВЩИК ====================

async def scheduler():
    while True:
        now = datetime.now()
        
        try:
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
                await bot.send_message(
                    ADMIN_ID,
                    f"🧹 Автоматическая очистка выполнена\n"
                    f"Удалено записей: {before - after}\n"
                    f"Осталось: {after}"
                )
                await db.save()
        
        except Exception as e:
            logging.error(f"Ошибка в планировщике: {e}")
        
        await asyncio.sleep(60)

# ==================== ЗАПУСК ====================

async def on_startup():
    os.makedirs(MEDIA_DIR, exist_ok=True)
    
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("Webhook удалён, бот работает в режиме polling")
    except Exception as e:
        logging.error(f"Ошибка при удалении вебхука: {e}")
    
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
            logging.error(f"Не удалось отправить приветствие админу: {e}")
    else:
        try:
            await bot.send_message(
                ADMIN_ID,
                "🚀 Бот запущен\n"
                "⚠️ Каналы не добавлены. Перейдите в Управление каналами."
            )
        except Exception as e:
            logging.error(f"Не удалось отправить приветствие админу: {e}")
    
    logging.info("Бот запущен")

async def on_shutdown():
    await db.save()
    logging.info("Бот остановлен")

async def main():
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
