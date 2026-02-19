import asyncio
import os
from datetime import datetime, timedelta, time
from typing import List, Dict, Optional
import logging
import json
import aiofiles

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
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
dp = Dispatcher()

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
    
    def add_post(self, user_id: int, username: str, content: List[Dict]) -> int:
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

# ==================== ФУНКЦИИ АВТОУДАЛЕНИЯ ====================

async def delete_message_after(chat_id: int, message_id: int, seconds: int = 10):
    """Удаляет сообщение через указанное количество секунд"""
    await asyncio.sleep(seconds)
    try:
        await bot.delete_message(chat_id, message_id)
    except:
        pass

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
        builder.button(text="📤 Отправить пост", callback_data="new_post")
    
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
    return builder.as_markup()

def get_post_navigation_keyboard(post_id: int, total: int, post_data: Dict) -> InlineKeyboardMarkup:
    """Клавиатура для навигации по постам с действиями"""
    builder = InlineKeyboardBuilder()
    
    # Кнопки навигации
    nav_row = []
    if post_id > 1:
        nav_row.append(InlineKeyboardButton(text="◀️", callback_data=f"nav_prev_{post_id}"))
    nav_row.append(InlineKeyboardButton(text=f"{post_id}/{total}", callback_data="no_action"))
    if post_id < total:
        nav_row.append(InlineKeyboardButton(text="▶️", callback_data=f"nav_next_{post_id}"))
    
    if nav_row:
        builder.row(*nav_row)
    
    # Кнопки действий
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

# ==================== ХРАНИЛИЩЕ ВРЕМЕННЫХ ДАННЫХ ====================
temp_posts = {}
temp_channel_add = {}

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
        await message.answer("👋 Отправь фото, видео или музыку для канала",
                           reply_markup=get_start_keyboard(False))

@dp.message(Command("clean"))
async def cmd_clean(message: types.Message):
    if not is_admin(message.from_user.username):
        await message.answer("⛔ Доступ запрещён")
        return
    
    await message.answer("🧹 Меню очистки:", reply_markup=get_clean_keyboard())

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

# ==================== ОБРАБОТЧИКИ ПОСТОВ ====================

@dp.callback_query(F.data == "new_post")
async def new_post(callback: CallbackQuery):
    await callback.answer()
    user_id = callback.from_user.id
    
    # Удаляем предыдущее сообщение с кнопками, если оно было
    if user_id in temp_posts and temp_posts[user_id]['msg_id']:
        try:
            await bot.delete_message(user_id, temp_posts[user_id]['msg_id'])
        except:
            pass
    
    temp_posts[user_id] = {'content': [], 'msg_id': None}
    
    msg = await callback.message.edit_text(
        "📤 Отправляй файлы\n"
        "Когда закончишь - нажми кнопку",
        reply_markup=get_content_keyboard()
    )
    temp_posts[user_id]['msg_id'] = msg.message_id

@dp.callback_query(F.data == "content_done")
async def content_done(callback: CallbackQuery):
    user_id = callback.from_user.id
    
    if user_id not in temp_posts or not temp_posts[user_id]['content']:
        await callback.answer("❌ Нет контента", show_alert=True)
        return
    
    current_channel = db.get_current_channel()
    if is_admin(callback.from_user.username) and not current_channel:
        await callback.message.edit_text(
            "⚠️ Сначала добавьте канал в управлении",
            reply_markup=get_start_keyboard(True)
        )
        return
    
    username = callback.from_user.username or f"id{user_id}"
    post_id = db.add_post(user_id, username, temp_posts[user_id]['content'])
    await db.save()
    
    if is_admin(callback.from_user.username):
        await send_to_admin(post_id, temp_posts[user_id]['content'], username, is_admin=True)
    else:
        await send_to_admin(post_id, temp_posts[user_id]['content'], username)
    
    del temp_posts[user_id]
    
    # Кнопка для повторной отправки
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="📤 Отправить ещё", callback_data="new_post")
    
    await callback.message.edit_text(
        "✅ Отправлено на проверку!\n\nМожешь отправить ещё один пост 👇",
        reply_markup=keyboard.as_markup()
    )

@dp.message(F.photo | F.video | F.audio)
async def handle_media(message: types.Message):
    user_id = message.from_user.id
    
    if user_id not in temp_posts:
        await message.reply("Сначала нажми /start и выбери 'Отправить пост'")
        return
    
    content_item = {}
    
    if message.photo:
        photo = message.photo[-1]
        content_item = {
            'type': 'photo',
            'file_id': photo.file_id,
            'caption': message.caption
        }
    elif message.video:
        content_item = {
            'type': 'video',
            'file_id': message.video.file_id,
            'caption': message.caption
        }
    elif message.audio:
        content_item = {
            'type': 'audio',
            'file_id': message.audio.file_id,
            'caption': message.caption
        }
    
    if content_item:
        temp_posts[user_id]['content'].append(content_item)
        reply_msg = await message.reply(f"✅ Добавлено ({len(temp_posts[user_id]['content'])})")
        # Автоудаление через 3 секунды
        asyncio.create_task(delete_message_after(reply_msg.chat.id, reply_msg.message_id, 3))
    
    if temp_posts[user_id]['msg_id']:
        try:
            await bot.delete_message(user_id, temp_posts[user_id]['msg_id'])
        except:
            pass
    
    msg = await message.answer(
        f"📦 {len(temp_posts[user_id]['content'])} файлов",
        reply_markup=get_content_keyboard()
    )
    temp_posts[user_id]['msg_id'] = msg.message_id

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
    
    # Сортируем по времени (новые сверху)
    pending.sort(key=lambda x: x['created_at'], reverse=True)
    
    text = "📋 *Ожидают проверки:*\n\n"
    builder = InlineKeyboardBuilder()
    
    for p in pending[:10]:  # Показываем только первые 10
        channel_info = ""
        if p.get('channel'):
            for ch in db.channels:
                if ch['id'] == p['channel']:
                    channel_info = f" в {ch.get('title', ch['id'])[:10]}"
                    break
        
        # Краткое описание
        short_text = f"#{p['id']} @{p['username']}{channel_info} ({len(p['content'])} 📎)"
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
    """Показывает детали одного поста"""
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
    
    text = f"📌 *Пост #{post_id}* из {total}\n"
    text += f"👤 От: @{post['username']}{channel_info}\n"
    text += f"📎 Файлов: {len(post['content'])}\n"
    text += f"🕐 Создан: {post['created_at'][:16]}\n"
    
    if post['content'] and post['content'][0].get('caption'):
        text += f"\n📝 Подпись: {post['content'][0]['caption']}"
    
    # Отправляем первый файл как превью
    await callback.message.delete()
    if post['content']:
        item = post['content'][0]
        if item['type'] == 'photo':
            await bot.send_photo(
                callback.from_user.id,
                item['file_id'],
                caption=text,
                parse_mode='Markdown',
                reply_markup=get_post_navigation_keyboard(post_id, total, post)
            )
        elif item['type'] == 'video':
            await bot.send_video(
                callback.from_user.id,
                item['file_id'],
                caption=text,
                parse_mode='Markdown',
                reply_markup=get_post_navigation_keyboard(post_id, total, post)
            )
        elif item['type'] == 'audio':
            await bot.send_audio(
                callback.from_user.id,
                item['file_id'],
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
        # Запускаем процесс одобрения
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
    
    # Отправляем сообщение с выбором времени
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
        except:
            pass
    
    channel = db.get_current_channel()
    channel_name = channel.get('title', db.current_channel) if channel else "канал"
    
    await bot.send_message(
        callback.from_user.id,
        f"✅ Пост #{post_id} добавлен в очередь\n📢 Канал: {channel_name}",
        reply_markup=get_start_keyboard(True)
    )

# ==================== СТАРЫЕ ОБРАБОТЧИКИ МОДЕРАЦИИ (ОСТАВЛЯЕМ ДЛЯ СОВМЕСТИМОСТИ) ====================

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

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

async def send_to_admin(post_id: int, content: List[Dict], username: str, is_admin: bool = False):
    current_channel = db.get_current_channel()
    channel_text = f" для {current_channel.get('title', db.current_channel)}" if current_channel else ""
    
    for item in content:
        if item['type'] == 'photo':
            caption = f"📸 Пост #{post_id} от @{username}{channel_text}"
            if item.get('caption'):
                caption += f"\n\n{item['caption']}"
            await bot.send_photo(ADMIN_ID, item['file_id'], caption=caption)
            
        elif item['type'] == 'video':
            caption = f"🎥 Пост #{post_id} от @{username}{channel_text}"
            if item.get('caption'):
                caption += f"\n\n{item['caption']}"
            await bot.send_video(ADMIN_ID, item['file_id'], caption=caption)
            
        elif item['type'] == 'audio':
            caption = f"🎵 Отправил трек: @{username}{channel_text}"
            if item.get('caption'):
                caption += f"\n\n{item['caption']}"
            await bot.send_audio(ADMIN_ID, item['file_id'], caption=caption)
    
    await bot.send_message(
        ADMIN_ID,
        f"🔍 Пост #{post_id}{channel_text}:",
        reply_markup=get_moderation_keyboard(post_id)
    )

async def publish_post(post: Dict):
    channel_id = post.get('channel')
    if not channel_id:
        logging.error(f"Пост #{post['id']} без канала")
        return
    
    try:
        for item in post['content']:
            if item['type'] == 'photo':
                await bot.send_photo(channel_id, item['file_id'])
            elif item['type'] == 'video':
                await bot.send_video(channel_id, item['file_id'])
            elif item['type'] == 'audio':
                await bot.send_audio(channel_id, item['file_id'])
        
        await bot.send_message(
            channel_id,
            f"✍️ Автор: @{post['username']}"
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
    
    # ===== КРИТИЧЕСКИ ВАЖНО: принудительно удаляем вебхук =====
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