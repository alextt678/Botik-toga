import asyncio
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging
import json

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = "7078059729:AAG4JvDdzbHV-3ga-LfjEziTA7W3NMmgnZY"
ADMIN_USERNAME = "JDD452"
ADMIN_ID = 5138605368

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

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== СОСТОЯНИЯ ====================
class PostStates(StatesGroup):
    collecting_media = State()
    collecting_livery_photo = State()
    waiting_livery_body_file = State()
    waiting_livery_glass_file = State()
    collecting_sticker_photo = State()
    waiting_sticker_file = State()
    confirm_post = State()

# ==================== ПРОСТАЯ БАЗА ДАННЫХ ====================
class SimpleDB:
    def __init__(self):
        self.posts = []
        self.channels = []
        self.current_channel = None
        self.load()
    
    def load(self):
        try:
            if os.path.exists("posts.json"):
                with open("posts.json", "r") as f:
                    self.posts = json.load(f)
        except:
            self.posts = []
        
        try:
            if os.path.exists("channels.json"):
                with open("channels.json", "r") as f:
                    data = json.load(f)
                    self.channels = data.get("channels", [])
                    self.current_channel = data.get("current_channel")
        except:
            self.channels = []
    
    def save(self):
        try:
            with open("posts.json", "w") as f:
                json.dump(self.posts, f, indent=2)
            with open("channels.json", "w") as f:
                json.dump({
                    "channels": self.channels,
                    "current_channel": self.current_channel
                }, f, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")
    
    def add_post(self, user_id, username, content):
        post_id = len(self.posts) + 1
        post = {
            "id": post_id,
            "user_id": user_id,
            "username": username,
            "content": content,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "channel": self.current_channel
        }
        self.posts.append(post)
        self.save()
        return post_id
    
    def get_pending_posts(self):
        return [p for p in self.posts if p["status"] == "pending"]
    
    def get_post(self, post_id):
        for p in self.posts:
            if p["id"] == post_id:
                return p
        return None
    
    def approve_post(self, post_id, scheduled_time=None):
        post = self.get_post(post_id)
        if post:
            post["status"] = "approved"
            post["scheduled_time"] = scheduled_time
            self.save()
    
    def delete_post(self, post_id):
        self.posts = [p for p in self.posts if p["id"] != post_id]
        self.save()
    
    def add_channel(self, channel_id, title=None):
        for ch in self.channels:
            if ch["id"] == channel_id:
                return False
        self.channels.append({
            "id": channel_id,
            "title": title or channel_id
        })
        if len(self.channels) == 1:
            self.current_channel = channel_id
        self.save()
        return True
    
    def remove_channel(self, channel_id):
        self.channels = [ch for ch in self.channels if ch["id"] != channel_id]
        if self.current_channel == channel_id:
            self.current_channel = self.channels[0]["id"] if self.channels else None
        self.save()
    
    def set_current_channel(self, channel_id):
        for ch in self.channels:
            if ch["id"] == channel_id:
                self.current_channel = channel_id
                self.save()
                return True
        return False
    
    def get_current_channel(self):
        for ch in self.channels:
            if ch["id"] == self.current_channel:
                return ch
        return None

db = SimpleDB()

# ==================== ИНИЦИАЛИЗАЦИЯ ====================
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def is_admin(username):
    return username == ADMIN_USERNAME if username else False

def is_txt_file(file_name):
    return file_name and file_name.lower().endswith('.txt')

def check_limit(post_type, current_count):
    limit = LIMITS.get(post_type, 4)
    return current_count < limit

# Временные данные
temp_data = {}
temp_channel_add = {}

# ==================== КЛАВИАТУРЫ ====================
def get_start_keyboard(is_admin_user):
    builder = InlineKeyboardBuilder()
    if is_admin_user:
        builder.button(text="📋 Очередь", callback_data="admin_queue")
        builder.button(text="📊 Статистика", callback_data="admin_stats")
        builder.button(text="📢 Управление каналами", callback_data="manage_channels")
        builder.button(text="🧹 Очистка", callback_data="clean_menu")
        current = db.get_current_channel()
        if current:
            builder.button(text=f"✅ Текущий: {current.get('title', current['id'])}", callback_data="no_action")
    else:
        builder.button(text="📤 Обычный пост", callback_data="new_regular")
        builder.button(text="👕 Ливрея", callback_data="new_livery")
        builder.button(text="🏷️ Наклейка", callback_data="new_sticker")
    builder.adjust(1)
    return builder.as_markup()

def get_cancel_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ Отмена", callback_data="cancel_post")
    return builder.as_markup()

def get_confirm_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Да, отправить", callback_data="confirm_send")
    builder.button(text="🔄 Нет, заново", callback_data="confirm_redo")
    builder.button(text="❌ Отмена", callback_data="cancel_post")
    builder.adjust(1)
    return builder.as_markup()

def get_content_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Готово", callback_data="content_done")
    builder.button(text="❌ Отмена", callback_data="cancel_post")
    return builder.as_markup()

def get_channels_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="➕ Добавить канал", callback_data="add_channel")
    for ch in db.channels:
        title = ch.get('title', ch['id'])
        is_current = "✅ " if ch['id'] == db.current_channel else ""
        builder.button(text=f"{is_current}{title}", callback_data=f"select_channel_{ch['id']}")
    builder.button(text="◀️ Назад", callback_data="back_to_admin")
    builder.adjust(1)
    return builder.as_markup()

def get_channel_actions_keyboard(channel_id):
    builder = InlineKeyboardBuilder()
    if channel_id != db.current_channel:
        builder.button(text="✅ Сделать текущим", callback_data=f"set_current_{channel_id}")
    builder.button(text="❌ Удалить канал", callback_data=f"delete_channel_{channel_id}")
    builder.button(text="◀️ Назад к списку", callback_data="manage_channels")
    builder.adjust(1)
    return builder.as_markup()

def get_moderation_keyboard(post_id):
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Одобрить", callback_data=f"approve_{post_id}")
    builder.button(text="❌ Отклонить", callback_data=f"reject_{post_id}")
    builder.button(text="🔙 В админ-меню", callback_data="back_to_admin")
    builder.adjust(2, 1)
    return builder.as_markup()

def get_time_keyboard(post_id):
    builder = InlineKeyboardBuilder()
    builder.button(text="⏱️ 10 секунд", callback_data=f"time_10sec_{post_id}")
    builder.button(text="⏰ 10 минут", callback_data=f"time_10min_{post_id}")
    builder.button(text="📅 Завтра 9:00", callback_data=f"time_schedule_{post_id}")
    builder.button(text="🔙 В админ-меню", callback_data="back_to_admin")
    builder.adjust(1)
    return builder.as_markup()

def get_clean_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🧹 Удалить опубликованные", callback_data="clean_published")
    builder.button(text="🗑️ Удалить старше 30 дней", callback_data="clean_30days")
    builder.button(text="📊 Статистика", callback_data="clean_stats")
    builder.button(text="◀️ Назад", callback_data="back_to_admin")
    builder.adjust(1)
    return builder.as_markup()

# ==================== КОМАНДЫ ====================
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user = message.from_user
    admin_user = is_admin(user.username)
    
    await state.clear()
    if user.id in temp_data:
        del temp_data[user.id]
    
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
        await message.answer(text, reply_markup=get_start_keyboard(True))
    else:
        text = (
            "👋 Привет! Что хочешь отправить?\n\n"
            "📤 Обычный пост - фото/видео (максимум 4 файла)\n"
            "👕 Ливрея - только фото (максимум 4 фото) + 2 файла .txt\n"
            "🏷️ Наклейка - только 1 фото + 1 файл .txt\n\n"
            "⚠️ Файлы .txt должны быть в формате .txt"
        )
        await message.answer(text, reply_markup=get_start_keyboard(False))

# ==================== ОТМЕНА ====================
@dp.callback_query(F.data == "cancel_post")
async def cancel_post(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    
    if user_id in temp_data:
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
    
    await callback.message.answer(
        text,
        reply_markup=get_start_keyboard(is_admin(callback.from_user.username))
    )
    await callback.answer("❌ Отменено")

# ==================== СОЗДАНИЕ ПОСТОВ ====================
@dp.callback_query(F.data == "new_regular")
async def new_regular(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    await state.set_state(PostStates.collecting_media)
    
    temp_data[user_id] = {
        'photos': [],
        'videos': [],
        'type': 'regular'
    }
    
    try:
        await callback.message.delete()
    except:
        pass
    
    await callback.message.answer(
        "📤 Отправляй фото или видео (максимум 4 файла)\nКогда закончишь - нажми Готово",
        reply_markup=get_content_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "new_livery")
async def new_livery(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    await state.set_state(PostStates.collecting_livery_photo)
    
    temp_data[user_id] = {
        'photos': [],
        'body_file': None,
        'glass_file': None,
        'type': 'livery'
    }
    
    try:
        await callback.message.delete()
    except:
        pass
    
    await callback.message.answer(
        "👕 Создание ливреи\n\nОтправь фото ливреи (максимум 4 фото)\nКогда закончишь - нажми Готово",
        reply_markup=get_content_keyboard()
    )
    await callback.answer()

@dp.callback_query(F.data == "new_sticker")
async def new_sticker(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    await state.set_state(PostStates.collecting_sticker_photo)
    
    temp_data[user_id] = {
        'photos': [],
        'sticker_file': None,
        'type': 'sticker'
    }
    
    try:
        await callback.message.delete()
    except:
        pass
    
    await callback.message.answer(
        "🏷️ Создание наклейки\n\nОтправь фото наклейки (только 1 фото)\nКогда отправишь - нажми Готово",
        reply_markup=get_content_keyboard()
    )
    await callback.answer()

# ==================== СБОР МЕДИА ====================
@dp.message(PostStates.collecting_media)
async def collect_regular_media(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id not in temp_data:
        await state.clear()
        return
    
    data = temp_data[user_id]
    current_count = len(data.get('photos', [])) + len(data.get('videos', []))
    
    if message.photo and check_limit('regular', current_count):
        data['photos'].append(message.photo[-1].file_id)
        await message.reply(f"✅ Фото добавлено ({current_count + 1}/{LIMITS['regular']})")
    elif message.video and check_limit('regular', current_count):
        data['videos'].append(message.video.file_id)
        await message.reply(f"✅ Видео добавлено ({current_count + 1}/{LIMITS['regular']})")
    else:
        await message.reply(get_limit_text('regular'))

@dp.message(PostStates.collecting_livery_photo)
async def collect_livery_photo(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id not in temp_data:
        await state.clear()
        return
    
    if not message.photo:
        await message.reply("❌ Для ливреи можно отправлять только фото!")
        return
    
    data = temp_data[user_id]
    current_count = len(data.get('photos', []))
    
    if check_limit('livery', current_count):
        data['photos'].append(message.photo[-1].file_id)
        await message.reply(f"✅ Фото добавлено ({current_count + 1}/{LIMITS['livery']})")
    else:
        await message.reply(get_limit_text('livery'))

@dp.message(PostStates.collecting_sticker_photo)
async def collect_sticker_photo(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id not in temp_data:
        await state.clear()
        return
    
    if not message.photo:
        await message.reply("❌ Для наклейки можно отправлять только фото!")
        return
    
    data = temp_data[user_id]
    current_count = len(data.get('photos', []))
    
    if check_limit('sticker', current_count):
        data['photos'].append(message.photo[-1].file_id)
        await message.reply(f"✅ Фото добавлено ({current_count + 1}/{LIMITS['sticker']})")
    else:
        await message.reply(get_limit_text('sticker'))

# ==================== ГОТОВО ====================
@dp.callback_query(F.data == "content_done")
async def content_done(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    current_state = await state.get_state()
    
    if user_id not in temp_data:
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    
    data = temp_data[user_id]
    
    if current_state == PostStates.collecting_media.state:
        if not data.get('photos') and not data.get('videos'):
            await callback.answer("❌ Сначала отправь файлы", show_alert=True)
            return
        
        total = len(data.get('photos', [])) + len(data.get('videos', []))
        text = f"📋 Проверь содержимое:\n📸 Фото: {len(data.get('photos', []))}\n🎥 Видео: {len(data.get('videos', []))}\n📊 Всего: {total}/{LIMITS['regular']}\n\nВсё верно?"
        await state.set_state(PostStates.confirm_post)
        await callback.message.edit_text(text, reply_markup=get_confirm_keyboard())
    
    elif current_state == PostStates.collecting_livery_photo.state:
        if not data.get('photos'):
            await callback.answer("❌ Сначала отправь фото", show_alert=True)
            return
        await state.set_state(PostStates.waiting_livery_body_file)
        await callback.message.edit_text("📁 Отправь файл на КУЗОВ (только .txt)", reply_markup=get_cancel_keyboard())
    
    elif current_state == PostStates.collecting_sticker_photo.state:
        if not data.get('photos'):
            await callback.answer("❌ Сначала отправь фото", show_alert=True)
            return
        await state.set_state(PostStates.waiting_sticker_file)
        await callback.message.edit_text("📁 Отправь файл с наклейкой (только .txt)", reply_markup=get_cancel_keyboard())
    
    await callback.answer()

# ==================== ФАЙЛЫ ====================
@dp.message(PostStates.waiting_livery_body_file, F.document)
async def get_livery_body_file(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id not in temp_data:
        await state.clear()
        return
    
    if not is_txt_file(message.document.file_name):
        await message.reply("❌ Файл должен быть в формате .txt")
        return
    
    temp_data[user_id]['body_file'] = {
        'file_id': message.document.file_id,
        'file_name': message.document.file_name
    }
    
    await state.set_state(PostStates.waiting_livery_glass_file)
    await message.answer("✅ Файл кузова получен\n📁 Теперь отправь файл на СТЕКЛО (только .txt)", reply_markup=get_cancel_keyboard())

@dp.message(PostStates.waiting_livery_glass_file, F.document)
async def get_livery_glass_file(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id not in temp_data:
        await state.clear()
        return
    
    if not is_txt_file(message.document.file_name):
        await message.reply("❌ Файл должен быть в формате .txt")
        return
    
    temp_data[user_id]['glass_file'] = {
        'file_id': message.document.file_id,
        'file_name': message.document.file_name
    }
    
    data = temp_data[user_id]
    text = f"📋 Проверь содержимое ливреи:\n📸 Фото: {len(data['photos'])}/{LIMITS['livery']}\n📁 Кузов: {data['body_file']['file_name']}\n📁 Стекло: {data['glass_file']['file_name']}\n\nВсё верно?"
    
    await state.set_state(PostStates.confirm_post)
    await message.answer(text, reply_markup=get_confirm_keyboard())

@dp.message(PostStates.waiting_sticker_file, F.document)
async def get_sticker_file(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id not in temp_data:
        await state.clear()
        return
    
    if not is_txt_file(message.document.file_name):
        await message.reply("❌ Файл должен быть в формате .txt")
        return
    
    temp_data[user_id]['sticker_file'] = {
        'file_id': message.document.file_id,
        'file_name': message.document.file_name
    }
    
    data = temp_data[user_id]
    text = f"📋 Проверь содержимое наклейки:\n📸 Фото: {len(data['photos'])}/{LIMITS['sticker']}\n🏷️ Файл: {data['sticker_file']['file_name']}\n\nВсё верно?"
    
    await state.set_state(PostStates.confirm_post)
    await message.answer(text, reply_markup=get_confirm_keyboard())

# ==================== ОТПРАВКА ====================
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
    else:
        content = {
            'type': 'sticker',
            'photos': data.get('photos', []),
            'files': {
                'sticker': data['sticker_file']
            }
        }
    
    post_id = db.add_post(user_id, username, content)
    
    # Отправка админу
    current_channel = db.get_current_channel()
    channel_text = f" для {current_channel.get('title', db.current_channel)}" if current_channel else ""
    
    type_names = {'regular': '📤 Обычный пост', 'livery': '👕 Ливрея', 'sticker': '🏷️ Наклейка'}
    
    for photo_id in content.get('photos', []):
        await bot.send_photo(ADMIN_ID, photo_id, caption=f"{type_names[data['type']]} #{post_id} от @{username}{channel_text}")
    
    for video_id in content.get('videos', []):
        await bot.send_video(ADMIN_ID, video_id, caption=f"{type_names[data['type']]} #{post_id} от @{username}{channel_text}")
    
    if data['type'] == 'livery':
        if content['files'].get('body'):
            await bot.send_document(ADMIN_ID, content['files']['body']['file_id'], caption=f"📁 КУЗОВ для поста #{post_id}")
        if content['files'].get('glass'):
            await bot.send_document(ADMIN_ID, content['files']['glass']['file_id'], caption=f"📁 СТЕКЛО для поста #{post_id}")
    elif data['type'] == 'sticker' and content['files'].get('sticker'):
        await bot.send_document(ADMIN_ID, content['files']['sticker']['file_id'], caption=f"🏷️ Наклейка для поста #{post_id}")
    
    await bot.send_message(ADMIN_ID, f"🔍 {type_names[data['type']]} #{post_id}{channel_text}:", reply_markup=get_moderation_keyboard(post_id))
    
    del temp_data[user_id]
    await state.clear()
    
    await callback.message.edit_text(f"✅ {type_names[data['type']]} отправлен на проверку!")
    await callback.answer()

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
        await callback.message.edit_text("📤 Отправляй фото или видео заново:", reply_markup=get_content_keyboard())
    elif data['type'] == 'livery':
        data['photos'] = []
        data['body_file'] = None
        data['glass_file'] = None
        await state.set_state(PostStates.collecting_livery_photo)
        await callback.message.edit_text("👕 Отправь фото ливреи заново:", reply_markup=get_content_keyboard())
    else:
        data['photos'] = []
        data['sticker_file'] = None
        await state.set_state(PostStates.collecting_sticker_photo)
        await callback.message.edit_text("🏷️ Отправь фото наклейки заново:", reply_markup=get_content_keyboard())
    
    await callback.answer()

# ==================== УПРАВЛЕНИЕ КАНАЛАМИ ====================
@dp.callback_query(F.data == "manage_channels")
async def manage_channels(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    text = "📢 Список каналов:\n✅ - текущий канал" if db.channels else "📢 У вас нет добавленных каналов."
    await callback.message.edit_text(text, reply_markup=get_channels_keyboard())
    await callback.answer()

@dp.callback_query(F.data == "add_channel")
async def add_channel_start(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    temp_channel_add[callback.from_user.id] = True
    await callback.message.edit_text(
        "📝 Отправьте ссылку на канал или его ID\nПримеры: @channel, -1001234567890\n❗️ Бот должен быть администратором!",
        reply_markup=InlineKeyboardBuilder().button(text="◀️ Отмена", callback_data="manage_channels").as_markup()
    )
    await callback.answer()

@dp.message(F.text)
async def handle_channel_input(message: types.Message):
    user_id = message.from_user.id
    
    if user_id in temp_channel_add and is_admin(message.from_user.username):
        channel_input = message.text.strip()
        
        if 't.me/' in channel_input:
            channel_input = '@' + channel_input.split('t.me/')[-1].split('/')[0]
        
        try:
            chat = await bot.get_chat(channel_input)
            await bot.send_message(channel_input, "🔍 Проверка...")
            
            db.add_channel(channel_input, chat.title)
            await message.answer(f"✅ Канал {chat.title} добавлен!", reply_markup=get_channels_keyboard())
        except:
            await message.answer("❌ Ошибка! Проверьте:\n1. Бот админ канала\n2. ID правильный", reply_markup=get_channels_keyboard())
        
        del temp_channel_add[user_id]

@dp.callback_query(F.data.startswith("select_channel_"))
async def select_channel(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    channel_id = callback.data.replace("select_channel_", "")
    channel = next((ch for ch in db.channels if ch['id'] == channel_id), None)
    
    if channel:
        text = f"📢 Канал: {channel.get('title', channel['id'])}\nID: {channel['id']}"
        if channel_id == db.current_channel:
            text += "\n\n✅ Это текущий канал"
        await callback.message.edit_text(text, reply_markup=get_channel_actions_keyboard(channel_id))
    await callback.answer()

@dp.callback_query(F.data.startswith("set_current_"))
async def set_current_channel(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    channel_id = callback.data.replace("set_current_", "")
    if db.set_current_channel(channel_id):
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
    await callback.answer("✅ Канал удалён")
    await manage_channels(callback)

@dp.callback_query(F.data == "back_to_admin")
async def back_to_admin(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    current = db.get_current_channel()
    text = f"🔑 Панель администратора\n📢 Текущий канал: {current.get('title', current['id'])}" if current else "🔑 Панель администратора\n⚠️ Канал не выбран!"
    
    try:
        await callback.message.delete()
    except:
        pass
    
    await callback.message.answer(text, reply_markup=get_start_keyboard(True))
    await callback.answer()

# ==================== МОДЕРАЦИЯ ====================
@dp.callback_query(F.data == "admin_queue")
async def show_queue(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    pending = db.get_pending_posts()
    
    if not pending:
        await callback.message.edit_text("📭 Нет постов на модерации", reply_markup=get_start_keyboard(True))
        return
    
    text = "📋 Ожидают проверки:\n\n"
    for p in pending[:5]:
        emoji = {'regular': '📤', 'livery': '👕', 'sticker': '🏷️'}.get(p['content']['type'], '📌')
        text += f"{emoji} #{p['id']} @{p['username']}\n"
    
    await callback.message.edit_text(text, reply_markup=get_start_keyboard(True))
    await callback.answer()

@dp.callback_query(F.data.startswith("approve_"))
async def approve_post(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    post_id = int(callback.data.split("_")[1])
    
    if not db.get_current_channel():
        await callback.message.edit_text("⚠️ Сначала добавьте канал!", reply_markup=get_start_keyboard(True))
        return
    
    await callback.message.edit_text(f"⏱ Время для поста #{post_id}:", reply_markup=get_time_keyboard(post_id))

@dp.callback_query(F.data.startswith("reject_"))
async def reject_post(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    post_id = int(callback.data.split("_")[1])
    post = db.get_post(post_id)
    
    if post:
        try:
            await bot.send_message(post['user_id'], "😔 Пост не прошёл модерацию, но мы ценим твою поддержку! 🌟")
            await bot.send_message(post['user_id'], "👋 Что хочешь отправить?", reply_markup=get_start_keyboard(False))
        except:
            pass
        db.delete_post(post_id)
    
    await callback.message.edit_text("❌ Пост отклонён", reply_markup=get_start_keyboard(True))

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
        scheduled = (now + timedelta(days=1)).replace(hour=6, minute=0).isoformat()
    
    db.approve_post(post_id, scheduled)
    
    post = db.get_post(post_id)
    if post:
        try:
            await bot.send_message(post['user_id'], "✅ Пост одобрен! Спасибо за помощь! 🙏")
            await bot.send_message(post['user_id'], "👋 Что хочешь отправить?", reply_markup=get_start_keyboard(False))
        except:
            pass
    
    channel = db.get_current_channel()
    channel_name = channel.get('title', db.current_channel) if channel else "канал"
    
    await callback.message.edit_text(f"✅ Пост #{post_id} добавлен в очередь\n📢 Канал: {channel_name}", reply_markup=get_start_keyboard(True))

# ==================== СТАТИСТИКА И ОЧИСТКА ====================
@dp.callback_query(F.data == "admin_stats")
async def show_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    total = len(db.posts)
    pending = len([p for p in db.posts if p['status'] == 'pending'])
    approved = len([p for p in db.posts if p['status'] == 'approved'])
    published = len([p for p in db.posts if p['status'] == 'published'])
    
    text = f"📊 Статистика:\n\n📝 Всего: {total}\n⏳ На модерации: {pending}\n✅ Одобрено: {approved}\n📢 Опубликовано: {published}\n\n📢 Каналов: {len(db.channels)}"
    
    current = db.get_current_channel()
    current_name = current.get('title', db.current_channel) if current else "не выбран"
    text += f"\n📍 Текущий: {current_name}"
    
    await callback.message.edit_text(text, reply_markup=get_start_keyboard(True))

@dp.callback_query(F.data == "clean_menu")
async def clean_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await callback.message.edit_text("🧹 Меню очистки:", reply_markup=get_clean_keyboard())

@dp.callback_query(F.data == "clean_published")
async def clean_published(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    before = len(db.posts)
    db.posts = [p for p in db.posts if p['status'] != 'published']
    db.save()
    after = len(db.posts)
    
    await callback.message.edit_text(f"🧹 Удалено опубликованных: {before - after}\nОсталось: {after}", reply_markup=get_clean_keyboard())

@dp.callback_query(F.data == "clean_30days")
async def clean_30days(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    now = datetime.now()
    before = len(db.posts)
    db.posts = [p for p in db.posts if datetime.fromisoformat(p['created_at']) > now - timedelta(days=30)]
    db.save()
    after = len(db.posts)
    
    await callback.message.edit_text(f"🧹 Удалено старых: {before - after}\nОсталось: {after}", reply_markup=get_clean_keyboard())

@dp.callback_query(F.data == "clean_stats")
async def clean_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.username):
        await callback.answer("⛔ Доступ запрещён", show_alert=True)
        return
    
    await show_stats(callback)

@dp.callback_query(F.data == "no_action")
async def no_action(callback: CallbackQuery):
    await callback.answer()

# ==================== ПУБЛИКАЦИЯ ====================
async def publish_scheduled():
    while True:
        await asyncio.sleep(60)
        try:
            now = datetime.now()
            for post in db.posts:
                if post['status'] == 'approved' and post.get('scheduled_time'):
                    try:
                        if datetime.fromisoformat(post['scheduled_time']) <= now:
                            channel_id = post.get('channel')
                            if channel_id:
                                content = post['content']
                                for photo_id in content.get('photos', []):
                                    await bot.send_photo(channel_id, photo_id)
                                for video_id in content.get('videos', []):
                                    await bot.send_video(channel_id, video_id)
                                await bot.send_message(channel_id, f"✍️ Автор: @{post['username']}")
                                
                                if content['type'] == 'livery':
                                    if content['files'].get('body'):
                                        await bot.send_document(channel_id, content['files']['body']['file_id'], caption="📁 Кузов")
                                    if content['files'].get('glass'):
                                        await bot.send_document(channel_id, content['files']['glass']['file_id'], caption="📁 Стекло")
                                elif content['type'] == 'sticker' and content['files'].get('sticker'):
                                    await bot.send_document(channel_id, content['files']['sticker']['file_id'], caption="🏷️ Наклейка")
                                
                                post['status'] = 'published'
                                db.save()
                                
                                channel = db.get_current_channel()
                                channel_name = channel.get('title', channel_id) if channel else channel_id
                                await bot.send_message(ADMIN_ID, f"✅ Пост #{post['id']} опубликован в {channel_name}")
                    except Exception as e:
                        logger.error(f"Ошибка публикации: {e}")
        except Exception as e:
            logger.error(f"Ошибка в планировщике: {e}")

# ==================== ЗАПУСК ====================
async def main():
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(publish_scheduled())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
