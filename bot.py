import os
import random
import sqlite3
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler
import sys
from datetime import datetime, timedelta
import base64
import aiohttp
import qrcode
import operator
import logging
import shutil
import time
import asyncio
import aiosqlite
import aiofiles
import pytz
import requests
from uuid import uuid4

# Определяем путь к директории бота
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BOT_DIR, 'bot_users.db')
TEMP_DIR = os.path.join(BOT_DIR, 'temp')
LOG_DIR = os.path.join(BOT_DIR, 'logs')
TEMP_LINKS_DIR = os.path.join(BOT_DIR, 'temp_links')
TEMP_LINKS_DB = os.path.join(BOT_DIR, 'temp_links.db')

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'bot.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    raise ValueError("Не указан токен бота в файле .env (BOT_TOKEN)")

# Получаем админский код из .env
ADMIN_CODE = os.getenv('ADMIN_CODE')
if not ADMIN_CODE:
    raise ValueError("Не указан код администратора в файле .env (ADMIN_CODE)")

# Получаем код user_plus из .env
USER_PLUS_CODE = os.getenv('USER_PLUS_CODE')
if not USER_PLUS_CODE:
    raise ValueError("Не указан код привилегированного пользователя в файле .env (USER_PLUS_CODE)")

# Получаем домен для временных ссылок
TEMP_LINK_DOMAIN = os.getenv('TEMP_LINK_DOMAIN', 'https://your-domain.com')

# Получаем URL веб-сервера и токен администратора
WEB_SERVER_URL = os.getenv('WEB_SERVER_URL')
ADMIN_TOKEN = os.getenv('ADMIN_TOKEN')

async def set_user_lines_to_keep(user_id, lines):
    """Асинхронная установка количества строк для пользователя"""
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute(
                'INSERT OR REPLACE INTO user_settings (user_id, lines_to_keep) VALUES (?, ?)',
                (user_id, lines)
            )
            await conn.commit()
            logger.info(f"Установлено количество строк {lines} для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка при установке количества строк для пользователя {user_id}: {e}")
        # Fallback к синхронной версии
        set_user_lines_to_keep_sync(user_id, lines)

# Константы для ограничений
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_LINKS = 1000  # Максимальное количество ссылок в файле
ALLOWED_EXTENSIONS = ('.txt', '.csv', '.md', '')  # Добавлено пустое расширение
DEFAULT_LINES_TO_KEEP = 10  # Количество строк по умолчанию
MAX_TEMP_LINK_HOURS = 720  # Максимальное время хранения файла в часах (30 дней)

# Состояния разговора
CAPTCHA, MENU, SETTINGS, TECH_COMMANDS, OTHER_COMMANDS, USER_MANAGEMENT, MERGE_FILES, SET_LINES, PROCESS_FILE, QR_TYPE, QR_DATA, TEMP_LINK, TEMP_LINK_DURATION, TEMP_LINK_EXTEND, STORAGE_MANAGEMENT = range(15)

# Добавим константы для ролей
class UserRole:
    ADMIN = "admin"
    USER_PLUS = "user_plus"
    USER = "user"

# Добавим константы для операторов
OPERATORS = {
    '+': operator.add,
    '-': operator.sub,
    '*': operator.mul
}

# Глобальные переменные для защиты от спама
user_action_times = {}  # Словарь для хранения времени последних действий
user_action_counts = {}  # Словарь для подсчета действий за период
user_spam_warnings = {}  # Словарь для хранения предупреждений о спаме
user_ban_list = set()  # Множество для хранения ID заблокированных пользователей
SPAM_COOLDOWN = 0.5  # Минимальное время между действиями в секундах
MAX_ACTIONS_PER_MINUTE = 20  # Максимальное количество действий в минуту
BAN_THRESHOLD = 50  # Порог для автоматической блокировки
WARNING_THRESHOLD = 10  # Порог для предупреждения администратора (уменьшен)
ADMIN_NOTIFICATION_INTERVAL = 60  # Интервал между уведомлениями администратора в секундах

async def ensure_directories():
    """Создание необходимых директорий с обработкой ошибок"""
    directories = [TEMP_DIR, LOG_DIR, TEMP_LINKS_DIR]
    for directory in directories:
        try:
            if not os.path.exists(directory):
                os.makedirs(directory)
                logger.info(f"Создана директория: {directory}")
                # Проверяем права на запись
                test_file = os.path.join(directory, 'test.txt')
                async with aiofiles.open(test_file, 'w') as f:
                    await f.write('test')
                os.remove(test_file)
                logger.info(f"Проверка прав доступа к директории {directory} успешна")
        except Exception as e:
            error_message = f"Ошибка при создании директории {directory}: {e}"
            logger.error(error_message)
            print(error_message)
            sys.exit(1)

async def log_error(user_id, error_message):
    """Логирование ошибок в файл"""
    try:
        log_file = os.path.join(LOG_DIR, f'error_{datetime.now().strftime("%Y-%m-%d")}.log')
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        async with aiofiles.open(log_file, 'a', encoding='utf-8') as f:
            await f.write(f"[{timestamp}] User {user_id}: {error_message}\n")
    except Exception as e:
        print(f"Ошибка при логировании: {str(e)}")
        print(f"[{timestamp}] User {user_id}: {error_message}")

async def setup_database():
    """Настройка базы данных"""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY,
                  username TEXT,
                  is_verified BOOLEAN DEFAULT FALSE,
                  role TEXT DEFAULT 'user',
                  usage_count INTEGER DEFAULT 0,
                  merged_count INTEGER DEFAULT 0,
                  qr_count INTEGER DEFAULT 0,
                  last_action_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  is_banned BOOLEAN DEFAULT FALSE)''')
        
        # Проверка наличия поля is_banned и его добавление, если отсутствует
        cursor = await conn.execute("PRAGMA table_info(users)")
        columns = [column[1] for column in await cursor.fetchall()]
        if 'is_banned' not in columns:
            await conn.execute("ALTER TABLE users ADD COLUMN is_banned BOOLEAN DEFAULT FALSE")
        
        # Создание таблицы bot_status
        await conn.execute('''CREATE TABLE IF NOT EXISTS bot_status
                 (id INTEGER PRIMARY KEY,
                  status TEXT DEFAULT 'enabled',
                  lines_to_keep INTEGER DEFAULT 10)''')
        
        # Проверяем наличие записи с id=1
        cursor = await conn.execute('SELECT COUNT(*) FROM bot_status WHERE id = 1')
        count = await cursor.fetchone()
        if count[0] == 0:
            await conn.execute('INSERT INTO bot_status (id, status, lines_to_keep) VALUES (1, "enabled", 10)')
        
        # Создание таблицы user_settings
        await conn.execute('''CREATE TABLE IF NOT EXISTS user_settings
                 (user_id INTEGER PRIMARY KEY,
                  language TEXT DEFAULT 'ru',
                  lines_to_keep INTEGER DEFAULT 10,
                  theme TEXT DEFAULT 'dark',
                  FOREIGN KEY (user_id) REFERENCES users(user_id))''')
        
        # Проверяем наличие колонок в user_settings
        cursor = await conn.execute("PRAGMA table_info(user_settings)")
        columns = [column[1] for column in await cursor.fetchall()]
        if 'lines_to_keep' not in columns:
            await conn.execute("ALTER TABLE user_settings ADD COLUMN lines_to_keep INTEGER DEFAULT 10")
        if 'theme' not in columns:
            await conn.execute("ALTER TABLE user_settings ADD COLUMN theme TEXT DEFAULT 'dark'")
        
        # Создание таблицы temp_links
        await conn.execute('''CREATE TABLE IF NOT EXISTS temp_links
                 (link_id TEXT PRIMARY KEY,
                  user_id INTEGER,
                  expires_at TIMESTAMP,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  extension_count INTEGER DEFAULT 0,
                  capacity_mb INTEGER DEFAULT 500,
                  FOREIGN KEY (user_id) REFERENCES users(user_id))''')
        
        # Проверка наличия поля extension_count и его добавление, если отсутствует
        cursor = await conn.execute("PRAGMA table_info(temp_links)")
        columns = [column[1] for column in await cursor.fetchall()]
        if 'extension_count' not in columns:
            await conn.execute("ALTER TABLE temp_links ADD COLUMN extension_count INTEGER DEFAULT 0")
        if 'capacity_mb' not in columns:
            await conn.execute("ALTER TABLE temp_links ADD COLUMN capacity_mb INTEGER DEFAULT 500")
        
        # Создание таблицы temp_link_files
        await conn.execute('''CREATE TABLE IF NOT EXISTS temp_link_files
                 (file_id TEXT PRIMARY KEY,
                  link_id TEXT,
                  file_path TEXT,
                  original_name TEXT,
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY (link_id) REFERENCES temp_links(link_id))''')
        
        await conn.commit()
    
    logger.info("База данных успешно инициализирована")

def is_user_verified(user_id):
    """Проверка верификации пользователя (синхронная версия)"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('SELECT is_verified FROM users WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        return result[0] if result else False
    finally:
        conn.close()

def is_admin(user_id):
    """Проверка, является ли пользователь администратором"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('SELECT role FROM users WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        return result and result[0] == UserRole.ADMIN
    finally:
        conn.close()

async def verify_user(user_id, username, role=UserRole.USER):
    """Верификация пользователя в базе данных"""
    async with aiosqlite.connect(DB_PATH) as conn:
        # Проверяем существующие данные пользователя
        cursor = await conn.execute('SELECT role, usage_count, merged_count, qr_count FROM users WHERE user_id = ?', (user_id,))
        result = await cursor.fetchone()
        
        if result:
            existing_role, usage_count, merged_count, qr_count = result
            # Не понижаем роль существующего пользователя
            if role == UserRole.USER:
                role = existing_role
        else:
            usage_count, merged_count, qr_count = 0, 0, 0
        
        await conn.execute('''
            INSERT OR REPLACE INTO users 
            (user_id, username, is_verified, role, usage_count, merged_count, qr_count)
            VALUES (?, ?, TRUE, ?, ?, ?, ?)
        ''', (user_id, username, role, usage_count, merged_count, qr_count))
        
        await conn.commit()

def is_bot_enabled():
    """Проверка, включен ли бот"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('SELECT status FROM bot_status WHERE id = 1')
        result = c.fetchone()
        return result[0] == 'enabled' if result else True
    finally:
        conn.close()

def generate_captcha():
    """Генерация простой математической капчи"""
    num1 = random.randint(1, 10)
    num2 = random.randint(1, 10)
    operation = random.choice(['+', '-', '*'])
    if operation == '+':
        answer = num1 + num2
    elif operation == '-':
        answer = num1 - num2
    else:
        answer = num1 * num2
    question = f"{num1} {operation} {num2} = ?"
    return question, str(answer)

def get_menu_keyboard(user_id):
    """Создание клавиатуры с меню"""
    keyboard = [
        ['📤 Обработать файл'],
        ['🔄 Объединить подписки', '📱 Создать QR-код'],
        ['🔗 Создать временное хранилище'],
        ['ℹ️ Помощь', '📊 Статистика'],
        ['⚙️ Настройки']
    ]   
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_qr_type_keyboard():
    """Создание клавиатуры выбора типа QR-кода"""
    keyboard = [
        ['🔗 Ссылка', '📝 Текст'],
        ['📧 Электронная почта', '📍 Местоположение'],
        ['📞 Телефон', '✉️ СМС'],
        ['📱 WhatsApp', '📶 Wi-Fi'],
        ['👤 Визитка'],
        ['Назад']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_temp_link_duration_keyboard(user_id):
    """Создание клавиатуры выбора срока хранения временного хранилища"""
    keyboard = [
        ['1 час', '6 часов'],
        ['12 часов', '24 часа'],
        ['3 дня', '7 дней'],
        ['14 дней', '30 дней'],
        ['♾️ Бесконечное']
    ]
    if is_admin(user_id):
        keyboard.append(['Бесконечное']) # Добавляем кнопку для админов
    keyboard.append(['Назад'])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_access(update, context):
        return ConversationHandler.END
        
    user_id = update.effective_user.id
    username = update.effective_user.username
    
    # Проверяем аргументы команды
    args = context.args
    if args:
        command = args[0]
        
        # Проверяем на admin код
        if command.startswith('admin'):
            code = command[5:]  # Получаем код после 'admin'
            if code == ADMIN_CODE:
                await verify_user(user_id, username, UserRole.ADMIN)
                await update.message.reply_text("Вы успешно авторизованы как администратор! 👑")
                return await show_menu(update, context)
            else:
                await update.message.reply_text("❌ Неверный код администратора.")
                return MENU
                
        # Проверяем на user_plus код
        elif command.startswith('user_plus'):
            code = command[9:]  # Получаем код после 'user_plus'
            user_plus_code = os.getenv('USER_PLUS_CODE')
            if user_plus_code and code == user_plus_code:
                await verify_user(user_id, username, UserRole.USER_PLUS)
                await update.message.reply_text("Вы успешно авторизованы как привилегированный пользователь! ⭐")
                return await show_menu(update, context)
            else:
                await update.message.reply_text("❌ Неверный код привилегированного пользователя.")
                return MENU
    
    # Проверяем, верифицирован ли пользователь
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT is_verified FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    
    if result and result[0]:
        return await show_menu(update, context)
    
    # Если пользователь не верифицирован, отправляем капчу
    return await send_captcha(update, context)

async def send_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправка капчи пользователю"""
    # Генерируем случайные числа и оператор
    num1 = random.randint(1, 10)
    num2 = random.randint(1, 10)
    op = random.choice(list(OPERATORS.keys()))
    
    # Вычисляем правильный ответ
    correct_answer = OPERATORS[op](num1, num2)
    
    # Сохраняем ответ в контексте пользователя
    context.user_data['captcha_answer'] = correct_answer
    
    # Отправляем капчу пользователю
    await update.message.reply_text(
        f"Для верификации, пожалуйста, решите пример:\n"
        f"{num1} {op} {num2} = ?"
    )
    
    return CAPTCHA

async def check_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка ответа на капчу"""
    try:
        user_answer = int(update.message.text)
        correct_answer = context.user_data.get('captcha_answer')
        
        if user_answer == correct_answer:
            # Верифицируем пользователя
            await verify_user(
                update.effective_user.id,
                update.effective_user.username,
                UserRole.USER
            )
            await update.message.reply_text("Верификация успешно пройдена!")
            return await show_menu(update, context)
        else:
            # Отправляем новую капчу
            await update.message.reply_text("Неверный ответ. Попробуйте еще раз.")
            return await send_captcha(update, context)
            
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите число.")
        return await send_captcha(update, context)

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Выберите действие:",
        reply_markup=get_menu_keyboard(update.effective_user.id)
    )
    return MENU

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_user_access(update, context):
        return MENU
        
    if not is_bot_enabled() and not is_admin(update.effective_user.id):
        await update.message.reply_text("Бот находится на техническом обслуживании. Пожалуйста, подождите.")
        return MENU
    
    # Проверяем, отправлен ли файл
    if update.message.document:
        await update.message.reply_text(
            "Для обработки файла сначала нажмите кнопку '📤 Обработать файл'",
            reply_markup=get_menu_keyboard(update.effective_user.id)
        )
        return MENU
    
    text = update.message.text
    
    # Проверяем, является ли текст ссылкой
    if text and text.startswith('http'):
        await update.message.reply_text(
            "Для объединения подписок сначала нажмите кнопку '🔄 Объединить подписки'",
            reply_markup=get_menu_keyboard(update.effective_user.id)
        )
        return MENU
    
    if text == '📤 Обработать файл':
        lines_to_keep = get_user_lines_to_keep(update.effective_user.id)
        await update.message.reply_text(
            f'Отправьте мне файл со ссылками (txt, csv или md), '
            f'и я верну вам последние {lines_to_keep} ссылок в формате HTML.\n'
            'Ограничения:\n'
            '- Максимальный размер файла: 50 MB\n'
            '- Максимальное количество ссылок: 1000\n'
            '- Поддерживаемые форматы: .txt, .csv, .md'
        )
        return PROCESS_FILE
    elif text == '🔄 Объединить подписки':
        keyboard = [
            [KeyboardButton("Объединить")],
            [KeyboardButton("Назад")]
        ]
        reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(
            "Отправьте ссылки на подписки для объединения:",
            reply_markup=reply_markup
        )
        return MERGE_FILES
    elif text == '📱 Создать QR-код':
        await update.message.reply_text(
            "Выберите тип QR-кода:",
            reply_markup=get_qr_type_keyboard()
        )
        return QR_TYPE
    elif text == '🔗 Создать временное хранилище':
        user_id = update.effective_user.id
        if not (await check_user_plus_rights(user_id) or is_admin(user_id)):
             await update.message.reply_text(
                 "Эта функция доступна только для привилегированных пользователей и администраторов.",
                 reply_markup=get_menu_keyboard(user_id)
             )
             return MENU

        try:
            # Проверяем наличие активного хранилища
            async with aiosqlite.connect(DB_PATH) as conn:
                cursor = await conn.execute('''
                    SELECT link_id, expires_at
                    FROM temp_links
                    WHERE user_id = ? AND (expires_at IS NULL OR expires_at > datetime('now'))
                    ORDER BY created_at DESC
                    LIMIT 1
                ''', (user_id,))
                active_storage = await cursor.fetchone()

            if active_storage:
                link_id, expires_at = active_storage
                storage_url = f"{TEMP_LINK_DOMAIN}/{link_id}"

                # Создаем клавиатуру с опцией удаления
                keyboard = [
                    [KeyboardButton("🗑️ Удалить хранилище")]
                ]
                # Кнопка продления только если срок не бесконечный
                if expires_at is not None:
                     keyboard[0].append(KeyboardButton("🔄 Продлить срок хранилища"))
                keyboard.append([KeyboardButton("Назад")])

                markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

                await update.message.reply_text(
                    f"У вас уже есть активное временное хранилище!\n\n"
                    f"🔗 Ссылка: {storage_url}\n"
                    f"⏱ Срок действия до: {format_datetime(expires_at)}\n\n"
                    f"Вы можете продолжать использовать это хранилище или удалить его.",
                    reply_markup=markup
                )
                context.user_data['current_storage'] = link_id
                return TEMP_LINK

            # Если активного хранилища нет, предлагаем создать новое
            await update.message.reply_text(
                "Выберите срок хранения временного хранилища:",
                reply_markup=get_temp_link_duration_keyboard(user_id) # Используем новую функцию
            )
            return TEMP_LINK_DURATION

        except Exception as e:
            logger.error(f"Ошибка при проверке хранилища: {str(e)}")
            await update.message.reply_text(
                "Произошла ошибка при проверке хранилища. Попробуйте позже.",
                reply_markup=get_menu_keyboard(user_id)
            )
            return MENU
        finally:
            # Закрытие соединения больше не нужно из-за 'async with'
            pass
    elif text == 'ℹ️ Помощь':
        await update.message.reply_text(
            "📚 *Помощь по использованию бота*\n\n"
            "📤 *Обработать файл* - загрузите файл со ссылками, и бот вернет последние N ссылок\n"
            "🔄 *Объединить подписки* - объединяет несколько подписок в одну\n"
            "📱 *Создать QR-код* - создает QR-код для различных типов данных\n"
            "🔗 *Создать временное хранилище* - создает хранилище для файлов с возможностью выбора срока хранения от 1 часа до 30 дней, с возможностью продления\n"
            "📊 *Статистика* - показывает вашу статистику использования\n"
            "⚙️ *Настройки* - настройки бота\n\n"
            "Для начала работы просто выберите нужную функцию в меню.",
            parse_mode='Markdown'
        )
    elif text == '📊 Статистика':
        user_id = update.effective_user.id
        async with aiosqlite.connect(DB_PATH) as conn:
            cursor = await conn.execute('SELECT usage_count, merged_count, qr_count FROM users WHERE user_id = ?', (user_id,))
            stats = await cursor.fetchone()
            usage_count, merged_count, qr_count = stats if stats else (0, 0, 0)
        lines_to_keep = get_user_lines_to_keep(user_id)
        await update.message.reply_text(
            f"📊 *Ваша статистика*\n\n"
            f"1. Обработано файлов: {usage_count}\n"
            f"2. Объединено подписок: {merged_count}\n"
            f"3. Создано QR-кодов: {qr_count}\n"
            f"4. Выбранное количество строк: {lines_to_keep}",
            parse_mode='Markdown'
        )
    elif text == '⚙️ Настройки':
        return await settings_command(update, context)
    
    return MENU

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_enabled() and not is_admin(update.effective_user.id):
        await update.message.reply_text("Бот находится на техническом обслуживании. Пожалуйста, подождите.")
        return MENU
    
    keyboard = []
    
    # Настройка строк доступна всем
    keyboard.append([KeyboardButton(text="Настройка количества строк")])
    
    # Дополнительные команды только для администраторов
    if is_admin(update.effective_user.id):
        keyboard.extend([
            [KeyboardButton(text="Технические команды")],
            [KeyboardButton(text="Другое")]
        ])
    
    keyboard.append([KeyboardButton(text="Назад")])
    
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    await update.message.reply_text("Настройки:", reply_markup=markup)
    return SETTINGS

async def process_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if text == "Назад":
        await show_menu(update, context)
        return MENU
    elif text == "Настройка количества строк":
        # Напрямую запрашиваем новое количество строк для пользователя
        current_lines = get_user_lines_to_keep(update.effective_user.id)
        await update.message.reply_text(
            f"Текущее количество строк: {current_lines}\n"
            f"Введите новое количество (от 1 до {MAX_LINKS}):"
        )
        context.user_data['setting_type'] = 'personal'
        return SET_LINES
    elif text == "Технические команды" and is_admin(update.effective_user.id):
        markup = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Включить бота")],
                [KeyboardButton(text="Выключить бота")],
                [KeyboardButton(text="Перезапустить бота")],
                [KeyboardButton(text="Назад")]
            ],
            resize_keyboard=True
        )
        await update.message.reply_text("Технические команды:", reply_markup=markup)
        return TECH_COMMANDS
    elif text == "Другое" and is_admin(update.effective_user.id):
        markup = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="Написать всем пользователям")],
                [KeyboardButton(text="Управление пользователями")],
                [KeyboardButton(text="Управление хранилищами")],
                [KeyboardButton(text="Назад")]
            ],
            resize_keyboard=True
        )
        await update.message.reply_text("Другое:", reply_markup=markup)
        return OTHER_COMMANDS
    else:
        await update.message.reply_text("Пожалуйста, выберите действие из меню.")
        return SETTINGS

async def process_set_lines(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if text == "Назад":
        await settings_command(update, context)
        return SETTINGS
    elif text == "Настройка количества строк":
        current_lines = get_user_lines_to_keep(update.effective_user.id)
        await update.message.reply_text(
            f"Текущее количество строк: {current_lines}\n"
            f"Введите новое количество (от 1 до {MAX_LINKS}):"
        )
        context.user_data['setting_type'] = 'personal'
        return SET_LINES
    elif text.isdigit():
        try:
            lines = int(text)
            if 1 <= lines <= MAX_LINKS:
                # Пользователь меняет свои настройки
                await set_user_lines_to_keep(update.effective_user.id, lines)
                await update.message.reply_text(f"Количество строк установлено: {lines}")
            else:
                await update.message.reply_text(f"Введите число от 1 до {MAX_LINKS}")
                return SET_LINES
        except ValueError:
            await update.message.reply_text("Пожалуйста, введите корректное число.")
            return SET_LINES
        
        await settings_command(update, context)
        return SETTINGS
    else:
        await update.message.reply_text("Пожалуйста, выберите действие из меню.")
        return SET_LINES

async def process_tech_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_admin_rights(update.effective_user.id):
        await update.message.reply_text("У вас нет прав для выполнения этой команды.")
        await show_menu(update, context)
        return MENU
    
    text = update.message.text
    
    if text == "Назад":
        await settings_command(update, context)
        return SETTINGS
    elif text == "Включить бота":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE bot_status SET status='enabled' WHERE id=1")
        conn.commit()
        conn.close()
        await update.message.reply_text("Бот включен.")
    elif text == "Выключить бота":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE bot_status SET status='disabled' WHERE id=1")
        conn.commit()
        conn.close()
        await update.message.reply_text("Бот выключен. Теперь он на техническом обслуживании.")
    elif text == "Перезапустить бота":
        await update.message.reply_text("Перезапуск бота...")
        # Отправляем сигнал SIGTERM для корректного завершения работы
        import os
        import signal
        logger.info("Перезапуск бота по команде администратора")
        os.kill(os.getpid(), signal.SIGTERM)
    
    return TECH_COMMANDS

async def process_other_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_admin_rights(update.effective_user.id):
        await update.message.reply_text("У вас нет прав для выполнения этой команды.")
        await show_menu(update, context)
        return MENU
    
    text = update.message.text
    
    if text == "Назад":
        await settings_command(update, context)
        return SETTINGS
    elif text == "Написать всем пользователям":
        await update.message.reply_text("Введите сообщение для рассылки:")
        return OTHER_COMMANDS
    elif text == "Управление пользователями":
        return await show_users_list(update, context)
    elif text == "Управление хранилищами":
        return await show_storage_list(update, context)
    else:
        # Отправляем сообщение всем пользователям
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT user_id FROM users WHERE is_verified = TRUE')
        users = c.fetchall()
        conn.close()
        
        success_count = 0
        for user_id in users:
            try:
                await context.bot.send_message(chat_id=user_id[0], text=text)
                success_count += 1
            except Exception as e:
                print(f"Ошибка отправки сообщения пользователю {user_id[0]}: {e}")
        
        await update.message.reply_text(f"Сообщение отправлено {success_count} пользователям.")
        await settings_command(update, context)
        return SETTINGS

async def show_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список пользователей с кнопками"""
    if not await check_user_access(update, context):
        return USER_MANAGEMENT
        
    users = await get_all_users_async()
    if not users:
        await update.message.reply_text("В базе данных нет пользователей.")
        return OTHER_COMMANDS
    
    verified_count = sum(1 for user in users if user[2])
    banned_count = sum(1 for user in users if user[7])  # is_banned поле
    
    user_list = f"Всего верифицированных пользователей: {verified_count}\n"
    user_list += f"Заблокированных пользователей: {banned_count}\n\nСписок пользователей:\n\n"
    
    # Создаем клавиатуру с именами пользователей
    keyboard = []
    users_dict = {}
    
    for user in users:
        user_id = user[0]
        username = user[1] or f"ID: {user_id}"
        role = 'Пользователь' if user[3]=='user' else 'Пользователь+' if user[3]=='user_plus' else 'Админ'
        is_banned = user[7]  # is_banned поле
        
        user_list += (
            f"ID: {user[0]}\n"
            f"Имя: {user[1] or 'Не указано'}\n"
            f"Верифицирован: {'Да' if user[2] else 'Нет'}\n"
            f"Роль: {role}\n"
            f"Статус: {'Заблокирован' if is_banned else 'Активен'}\n"
            f"Обработано файлов: {user[4]}\n"
            f"Объединено подписок: {user[5]}\n"
            f"Создано QR-кодов: {user[6]}\n\n"
        )
        
        button_text = f"{username} ({role}){' [ЗАБЛОКИРОВАН]' if is_banned else ''}"
        keyboard.append([KeyboardButton(text=button_text)])
        users_dict[button_text] = user_id

    keyboard.append([KeyboardButton(text="Назад")])
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # Сохраняем информацию о пользователях в контексте
    context.user_data['users_info'] = users_dict
    
    await update.message.reply_text(
        user_list + "\nВыберите пользователя для управления:", 
        reply_markup=markup
    )
    
    return USER_MANAGEMENT

async def process_user_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка управления пользователями"""
    # Для администраторов пропускаем проверку спама
    if is_admin(update.effective_user.id):
        return await _process_user_management(update, context)
    
    if not await check_user_access(update, context):
        return USER_MANAGEMENT
    
    return await _process_user_management(update, context)

async def _process_user_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Внутренняя функция обработки управления пользователями"""
    text = update.message.text
    
    if text == "Назад":
        await settings_command(update, context)
        return SETTINGS
        
    if text in ["Убрать из базы", "Выдать пользователя", "Выдать пользователя+", "Выдать админа", "Заблокировать", "Разблокировать"]:
        user_id = context.user_data.get('selected_user_id')
        if not user_id:
            await update.message.reply_text("Сначала выберите пользователя.")
            return USER_MANAGEMENT
            
        if text == "Убрать из базы":
            if user_id == update.effective_user.id:
                await update.message.reply_text("Вы не можете удалить себя.")
            else:
                try:
                    await remove_user(user_id)
                    await update.message.reply_text("Пользователь удален.")
                    return await show_users_list(update, context)
                except Exception as e:
                    await update.message.reply_text(f"Ошибка при удалении пользователя: {str(e)}")
        elif text == "Заблокировать":
            if user_id == update.effective_user.id:
                await update.message.reply_text("Вы не можете заблокировать себя.")
            else:
                try:
                    if await ban_user(context.bot, user_id, True):
                        await update.message.reply_text("Пользователь заблокирован.")
                    else:
                        await update.message.reply_text("Ошибка при блокировке пользователя.")
                    return await show_users_list(update, context)
                except Exception as e:
                    await update.message.reply_text(f"Ошибка при блокировке пользователя: {str(e)}")
        elif text == "Разблокировать":
            try:
                if await ban_user(context.bot, user_id, False):
                    await update.message.reply_text("Пользователь разблокирован.")
                else:
                    await update.message.reply_text("Ошибка при разблокировке пользователя.")
                return await show_users_list(update, context)
            except Exception as e:
                await update.message.reply_text(f"Ошибка при разблокировке пользователя: {str(e)}")
        else:
            role = {
                "Выдать пользователя": UserRole.USER,
                "Выдать пользователя+": UserRole.USER_PLUS,
                "Выдать админа": UserRole.ADMIN
            }[text]
            
            try:
                async with aiosqlite.connect(DB_PATH) as conn:
                    await conn.execute('UPDATE users SET role = ? WHERE user_id = ?', (role, user_id))
                    await conn.commit()
                await update.message.reply_text("Роль пользователя обновлена.")
                return await show_users_list(update, context)
            except Exception as e:
                await update.message.reply_text(f"Ошибка при обновлении роли: {str(e)}")
    
    elif text in context.user_data.get('users_info', {}):
        # Пользователь выбран, показываем действия
        user_id = context.user_data['users_info'][text]
        context.user_data['selected_user_id'] = user_id
        
        # Проверяем статус блокировки пользователя
        is_banned = is_user_banned(user_id)
        
        keyboard = [
            [KeyboardButton(text="Убрать из базы")],
            [KeyboardButton(text="Выдать пользователя")],
            [KeyboardButton(text="Выдать пользователя+")],
            [KeyboardButton(text="Выдать админа")],
            [KeyboardButton(text="Заблокировать" if not is_banned else "Разблокировать")],
            [KeyboardButton(text="Назад")]
        ]
        markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        await update.message.reply_text(
            f"Выберите действие для пользователя {text}:",
            reply_markup=markup
        )
    
    return USER_MANAGEMENT

async def fetch_subscription(url):
    """Получение содержимого подписки по URL"""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                encoded_content = await response.text()
                # Декодируем из Base64 в VLESS-конфигурацию
                try:
                    decoded_content = base64.b64decode(encoded_content.strip()).decode('utf-8')
                    return decoded_content
                except:
                    raise ValueError("Ошибка декодирования подписки")
            else:
                raise ValueError(f"Ошибка получения подписки: {response.status}")

async def process_merge_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if 'subscriptions' not in context.user_data:
        context.user_data['subscriptions'] = []
        context.user_data['count'] = 0

    try:
        # Получаем URL подписки
        if update.message.text and update.message.text not in ["Объединить", "Назад"]:
            url = update.message.text.strip()
            if not url.startswith('http'):
                await update.message.reply_text("Отправьте URL подписки.")
                return MERGE_FILES
        elif update.message.document:
            file = await context.bot.get_file(update.message.document.file_id)
            downloaded_file = await file.download_as_bytearray()
            try:
                url = downloaded_file.decode('utf-8').strip()
            except UnicodeDecodeError:
                await update.message.reply_text("Ошибка при чтении файла.")
                return MERGE_FILES
        else:
            await update.message.reply_text("Отправьте URL подписки.")
            return MERGE_FILES

        # Получаем и декодируем подписку
        try:
            vless_config = await fetch_subscription(url)
            if not vless_config.startswith('vless://'):
                await update.message.reply_text("Неверный формат подписки. Ожидается VLESS-конфигурация.")
                return MERGE_FILES
            
            context.user_data['subscriptions'].append(vless_config)
            context.user_data['count'] += 1
            
            await update.message.reply_text(
                f"Получено подписок: {context.user_data['count']}\n"
                "Отправьте еще подписки или нажмите 'Объединить' для завершения."
            )

            if context.user_data['count'] >= 2:
                markup = ReplyKeyboardMarkup([
                    ["Объединить"],
                    ["Назад"]
                ], resize_keyboard=True)
                await update.message.reply_text("Можно объединить подписки:", reply_markup=markup)

        except Exception as e:
            await update.message.reply_text(f"Ошибка при получении подписки: {str(e)}")
            return MERGE_FILES

        return MERGE_FILES

    except Exception as e:
        error_message = f"Ошибка при обработке подписок: {str(e)}"
        await log_error(update.effective_user.id, error_message)
        await update.message.reply_text("Произошла ошибка. Пожалуйста, попробуйте снова.")
        return MERGE_FILES

async def merge_vless_subscriptions(subscriptions):
    """Объединение VLESS-подписок"""
    merged_configs = []
    
    for i, sub in enumerate(subscriptions, 1):
        try:
            # Проверяем формат VLESS
            if not sub.startswith('vless://'):
                continue
                
            # Добавляем подписку в список, обновляя название
            # Сохраняем оригинальное название, если есть
            if '#' in sub:
                base_sub, name = sub.rsplit('#', 1)
                new_sub = f"{base_sub}#Merged-{i}-{name}"
            else:
                new_sub = f"{sub}#Merged-{i}"
            
            merged_configs.append(new_sub)
            
        except Exception as e:
            print(f"Ошибка при обработке подписки: {str(e)}")
            continue
    
    # Объединяем все подписки в одну строку
    return '\n'.join(merged_configs)

async def process_merge_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "Объединить":
        if context.user_data.get('count', 0) < 2:
            await update.message.reply_text("Необходимо как минимум 2 подписки для объединения.")
            return MERGE_FILES

        try:
            # Объединяем VLESS-конфигурации
            merged_config = merge_vless_subscriptions(context.user_data['subscriptions'])
            
            # Кодируем обратно в Base64
            encoded_config = base64.b64encode(merged_config.encode('utf-8')).decode('utf-8')
            
            # Увеличиваем счетчик объединений
            await increment_merge_count(update.effective_user.id)
            
            await update.message.reply_text(
                f"Объединенная подписка (нажмите, чтобы скопировать):\n\n"
                f"`{encoded_config}`",
                parse_mode='Markdown'
            )

            # Очищаем данные
            context.user_data.clear()
            await show_menu(update, context)
            return MENU
            
        except Exception as e:
            await update.message.reply_text(f"Ошибка при объединении подписок: {str(e)}")
            return MERGE_FILES
            
    elif update.message.text == "Назад":
        context.user_data.clear()
        await show_menu(update, context)
        return MENU
    else:
        return await process_merge_files(update, context)

async def increment_merge_count(user_id):
    """Увеличение счетчика объединений"""
    async with await safe_db_connect() as conn:
        if not conn:
            return
        
        try:
            await conn.execute('UPDATE users SET merged_count = merged_count + 1 WHERE user_id = ?', (user_id,))
            await conn.commit()
        except sqlite3.Error as e:
            print(f"Ошибка при обновлении счетчика объединений: {e}")

async def process_qr_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if text == "Назад":
        await show_menu(update, context)
        return MENU
    
    qr_types = {
        '🔗 Ссылка': ('Отправьте URL:', 'URL'),
        '📝 Текст': ('Отправьте текст:', 'TEXT'),
        '📧 Электронная почта': ('Отправьте email и тему (через пробел):', 'EMAIL'),
        '📍 Местоположение': ('Отправьте координаты (широта пробел долгота):', 'GEO'),
        '📞 Телефон': ('Отправьте номер телефона:', 'TEL'),
        '✉️ СМС': ('Отправьте номер телефона и текст (через пробел):', 'SMS'),
        '📱 WhatsApp': ('Отправьте номер WhatsApp и сообщение (через пробел):', 'WHATSAPP'),
        '📶 Wi-Fi': ('Отправьте SSID и пароль (через пробел):', 'WIFI'),
        '👤 Визитка': ('Отправьте данные в формате: ФИО Телефон Email Компания Должность', 'VCARD')
    }
    
    if text in qr_types:
        context.user_data['qr_type'] = qr_types[text][1]
        keyboard = ReplyKeyboardMarkup([['Назад']], resize_keyboard=True)
        await update.message.reply_text(qr_types[text][0], reply_markup=keyboard)
        return QR_DATA
    
    await update.message.reply_text("Пожалуйста, выберите тип QR-кода из меню.")
    return QR_TYPE

async def process_qr_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    temp_filename = None
    try:
        if update.message.text == "Назад":
            await update.message.reply_text(
                "Выберите тип QR-кода:",
                reply_markup=get_qr_type_keyboard()
            )
            return QR_TYPE
        
        try:
            qr_type = context.user_data.get('qr_type')
            data = update.message.text.strip()
            
            # Формируем содержимое QR-кода в зависимости от типа
            if qr_type == 'URL':
                qr_content = data if data.startswith(('http://', 'https://')) else f'https://{data}'
            elif qr_type == 'TEXT':
                qr_content = data
            elif qr_type == 'EMAIL':
                email, *subject = data.split()
                qr_content = f'mailto:{email}?subject={"+".join(subject)}'
            elif qr_type == 'GEO':
                lat, lon = data.split()
                qr_content = f'geo:{lat},{lon}'
            elif qr_type == 'TEL':
                qr_content = f'tel:{data.replace(" ", "")}'
            elif qr_type == 'SMS':
                phone, *message = data.split()
                qr_content = f'smsto:{phone}:{" ".join(message)}'
            elif qr_type == 'WHATSAPP':
                phone, *message = data.split()
                qr_content = f'whatsapp://send?phone={phone.replace("+", "")}&text={"+".join(message)}'
            elif qr_type == 'WIFI':
                ssid, password = data.split(maxsplit=1)
                qr_content = f'WIFI:S:{ssid};T:WPA;P:{password};;'
            elif qr_type == 'VCARD':
                name, phone, email, company, title = data.split(maxsplit=4)
                qr_content = f'BEGIN:VCARD\nVERSION:3.0\nN:{name}\nTEL:{phone}\nEMAIL:{email}\nORG:{company}\nTITLE:{title}\nEND:VCARD'
            
            # Создаем QR-код
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(qr_content)
            qr.make(fit=True)
            
            # Создаем изображение
            img = qr.make_image(fill_color="black", back_color="white")
            
            # Создаем временный файл для QR-кода
            temp_filename = os.path.join(TEMP_DIR, f'qr_{update.effective_user.id}.png')
            
            # Сохраняем изображение в файл
            img.save(temp_filename)
            
            # Отправляем изображение
            with open(temp_filename, 'rb') as f:
                await update.message.reply_photo(
                    photo=f,
                    caption="Ваш QR-код готов!",
                    reply_markup=get_menu_keyboard(update.effective_user.id)
                )
            
            # Увеличиваем счетчик созданных QR-кодов
            await increment_qr_count(update.effective_user.id)
            
            # Очищаем данные пользователя
            context.user_data.clear()
            
            return MENU
            
        except Exception as e:
            error_message = f"Ошибка при создании QR-кода: {str(e)}"
            await log_error(update.effective_user.id, error_message)
            if temp_filename and os.path.exists(temp_filename):
                os.remove(temp_filename)
            await update.message.reply_text(
                "Произошла ошибка. Пожалуйста, проверьте формат данных и попробуйте снова.",
                reply_markup=get_qr_type_keyboard()
            )
            return QR_TYPE

    except Exception as e:
        error_message = f"Ошибка при создании QR-кода: {str(e)}"
        await log_error(update.effective_user.id, error_message)
        if temp_filename and os.path.exists(temp_filename):
            os.remove(temp_filename)
        await update.message.reply_text(
            "Произошла ошибка. Пожалуйста, проверьте формат данных и попробуйте снова.",
            reply_markup=get_qr_type_keyboard()
        )
        return QR_TYPE

async def increment_qr_count(user_id):
    """Увеличение счетчика созданных QR-кодов"""
    async with await safe_db_connect() as conn:
        if not conn:
            return
        
        try:
            await conn.execute('UPDATE users SET qr_count = qr_count + 1 WHERE user_id = ?', (user_id,))
            await conn.commit()
        except sqlite3.Error as e:
            print(f"Ошибка при обновлении счетчика QR-кодов: {e}")

async def safe_db_connect():
    """Безопасное подключение к базе данных"""
    try:
        return await aiosqlite.connect(DB_PATH)
    except sqlite3.Error as e:
        print(f"Ошибка подключения к базе данных: {e}")
        return None

async def get_user_role(user_id):
    """Получение роли пользователя"""
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute('SELECT role FROM users WHERE user_id = ?', (user_id,))
        result = await cursor.fetchone()
    return result[0] if result else UserRole.USER

async def check_admin_rights(user_id):
    """Проверка прав администратора"""
    return await get_user_role(user_id) == UserRole.ADMIN

async def check_user_plus_rights(user_id):
    """Проверка прав привилегированного пользователя"""
    role = await get_user_role(user_id)
    return role in [UserRole.ADMIN, UserRole.USER_PLUS]

async def show_admin_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
🔑 *Команды администратора:*

📝 Основные команды:
• `/start admin[код]` - Получить права администратора
• `/start user_plus[код]` - Получить права привилегированного пользователя

⚙️ Технические команды:
• Включить/выключить бота
• Перезапустить бота
• Просмотр статистики

👥 Управление пользователями:
• Просмотр списка пользователей
• Блокировка/разблокировка пользователей
• Массовая рассылка сообщений

💡 Примеры использования:
• `/start adminYH8jRnO1Np8wVUZobJfwPIv`
• `/start user_plusUj9kLmP2Qw3Er4Ty5`
"""
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def generate_temp_link_id():
    """Генерация уникального ID для временной ссылки"""
    # Используем только буквы и цифры для более короткого ID
    chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    while True:
        link_id = ''.join(random.choice(chars) for _ in range(4))
        # Проверяем, не существует ли уже такой ID
        async with aiosqlite.connect(DB_PATH) as conn:
            cursor = await conn.execute('SELECT COUNT(*) FROM temp_links WHERE link_id = ?', (link_id,))
            count = await cursor.fetchone()
            if count[0] == 0:
                return link_id

async def save_temp_link(file_path, original_name, duration_hours, user_id):
    """Сохранение информации о временной ссылке"""
    try:
        link_id = await generate_temp_link_id()
        
        # Создаем дату без микросекунд
        expires_dt = datetime.now() + timedelta(hours=duration_hours)
        expires_at = expires_dt.strftime('%Y-%m-%d %H:%M:%S')  # Форматирование без микросекунд
        
        async with aiosqlite.connect(DB_PATH) as conn:
            try:
                # Создаем запись о временной ссылке
                await conn.execute('''
                    INSERT INTO temp_links (link_id, expires_at, user_id, extension_count)
                    VALUES (?, ?, ?, 0)
                ''', (link_id, expires_at, user_id))
                
                # Добавляем информацию о файле
                await conn.execute('''
                    INSERT INTO temp_link_files (link_id, file_path, original_name)
                    VALUES (?, ?, ?)
                ''', (link_id, file_path, original_name))
                
                await conn.commit()
                return link_id
                
            except sqlite3.Error as e:
                error_message = f"Ошибка базы данных при сохранении временной ссылки: {str(e)}"
                await log_error(None, error_message)
                raise
                
    except Exception as e:
        error_message = f"Критическая ошибка при сохранении временной ссылки: {str(e)}"
        await log_error(None, error_message)
        raise

async def get_temp_link_info(link_id):
    """Получение информации о временной ссылке"""
    try:
        # Получаем текущее московское время
        import pytz
        moscow_tz = pytz.timezone('Europe/Moscow')
        now = datetime.now(moscow_tz)
        current_time = now.strftime('%Y-%m-%d %H:%M:%S')
        
        async with aiosqlite.connect(DB_PATH) as conn:
            # Получаем информацию о ссылке
            cursor = await conn.execute('SELECT expires_at FROM temp_links WHERE link_id = ?', (link_id,))
            result = await cursor.fetchone()
            
            if not result:
                return None
                
            expires_at = result[0]
            
            # Очищаем дату от микросекунд, если они есть
            clean_expires_at = expires_at
            if '.' in expires_at:
                clean_expires_at = expires_at.split('.')[0]
                
            # Проверяем, не истек ли срок действия
            if clean_expires_at <= current_time:
                logger.info(f"Ссылка {link_id} истекла ({clean_expires_at} <= {current_time})")
                return None
            
            # Получаем список файлов
            cursor = await conn.execute('''
                SELECT file_path, original_name
                FROM temp_link_files
                WHERE link_id = ?
            ''', (link_id,))
            
            files = await cursor.fetchall()
            
            return {
                'expires_at': clean_expires_at,
                'files': files
            }
    except Exception as e:
        logger.error(f"Ошибка при получении информации о ссылке {link_id}: {e}")
        return None

async def cleanup_expired_links(context=None):
    """Очистка истекших временных ссылок"""
    try:
        # Проверяем и создаем директорию для временных ссылок
        if not os.path.exists(TEMP_LINKS_DIR):
            os.makedirs(TEMP_LINKS_DIR)
            logger.info(f"Создана директория для временных ссылок: {TEMP_LINKS_DIR}")
        
        # Получаем текущее московское время
        import pytz
        moscow_tz = pytz.timezone('Europe/Moscow')
        now = datetime.now(moscow_tz)
        current_time = now.strftime('%Y-%m-%d %H:%M:%S')
        
        logger.info(f"Очистка истекших ссылок, текущее время (Москва): {current_time}")
            
        async with aiosqlite.connect(DB_PATH) as conn:
            try:
                # Получаем список всех ссылок для проверки
                cursor = await conn.execute('SELECT link_id, expires_at FROM temp_links')
                all_links = await cursor.fetchall()
                
                expired_links = []
                for link in all_links:
                    link_id, expires_at = link
                    
                    # Очищаем дату от микросекунд, если они есть
                    clean_expires_at = expires_at
                    if '.' in expires_at:
                        clean_expires_at = expires_at.split('.')[0]
                    
                    # Проверяем, истек ли срок действия
                    if clean_expires_at <= current_time:
                        expired_links.append((link_id,))
                        logger.info(f"Найдена истекшая ссылка {link_id}, срок действия до: {clean_expires_at}")
                
                logger.info(f"Найдено {len(expired_links)} истекших ссылок")
                
                # Удаляем файлы и записи из базы данных
                for link_id in expired_links:
                    # Получаем список файлов для удаления
                    cursor = await conn.execute('SELECT file_path FROM temp_link_files WHERE link_id = ?', (link_id[0],))
                    files = await cursor.fetchall()
                    
                    # Удаляем файлы
                    for file_path in files:
                        try:
                            if os.path.exists(file_path[0]):
                                os.remove(file_path[0])
                                logger.info(f"Удален файл: {file_path[0]}")
                        except Exception as e:
                            error_message = f"Ошибка при удалении файла {file_path[0]}: {str(e)}"
                            logger.error(error_message)
                    
                    # Удаляем записи из базы данных
                    await conn.execute('DELETE FROM temp_link_files WHERE link_id = ?', (link_id[0],))
                    await conn.execute('DELETE FROM temp_links WHERE link_id = ?', (link_id[0],))
                    logger.info(f"Удалены записи о хранилище {link_id[0]} из базы данных")
                
                await conn.commit()
                
            except sqlite3.Error as e:
                await conn.rollback()
                error_message = f"Ошибка базы данных при очистке истекших ссылок: {str(e)}"
                logger.error(error_message)
            
    except Exception as e:
        error_message = f"Критическая ошибка при очистке истекших ссылок: {str(e)}"
        logger.error(error_message)

async def process_temp_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка создания временной ссылки"""
    try:
        # Получаем список активных хранилищ пользователя
        storage_list = await get_user_active_storage(update.effective_user.id)
        
        if storage_list:
            # Берем первое активное хранилище
            storage = storage_list[0]
            storage_url = f"{TEMP_LINK_DOMAIN}/{storage['link_id']}"
            await update.message.reply_text(
                f"У вас уже есть активное временное хранилище!\n\n"
                f"🔗 Ссылка: {storage_url}\n"
                f"⏱ Срок действия до: {format_datetime(storage['expires_at'])}\n\n"
                f"Вы можете продолжать использовать это хранилище или дождаться окончания его срока действия для создания нового.",
                reply_markup=get_menu_keyboard(update.effective_user.id)
            )
            return MENU
        
        # Создаем клавиатуру с выбором срока хранения
        keyboard = [
            ['1 час', '6 часов'],
            ['12 часов', '24 часа'],
            ['3 дня', '7 дней'],
            ['14 дней', '30 дней'],
            ['♾️ Бесконечное'],
            ['Назад']
        ]
        
        await update.message.reply_text(
            "Выберите срок хранения временного хранилища:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        
        return TEMP_LINK_DURATION
        
    except Exception as e:
        logger.error(f"Ошибка при проверке хранилища: {str(e)}")
        await update.message.reply_text(
            "Произошла ошибка при проверке хранилища. Попробуйте позже.",
            reply_markup=get_menu_keyboard(update.effective_user.id)
        )
        return MENU

async def process_temp_link_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора срока хранения временного хранилища"""
    if update.message.text == "Назад":
        await show_menu(update, context)
        return MENU
    
    # Определяем срок хранения в часах
    duration_map = {
        '1 час': 1,
        '6 часов': 6,
        '12 часов': 12,
        '24 часа': 24,
        '3 дня': 72,
        '7 дней': 168,
        '14 дней': 336,
        '30 дней': 720
    }
    
    if update.message.text not in duration_map and update.message.text != "♾️ Бесконечное":
        await update.message.reply_text(
            "Пожалуйста, выберите срок хранения из предложенных вариантов."
        )
        return TEMP_LINK_DURATION
    
    try:
        # Проверяем еще раз наличие активного хранилища
        storage_list = await get_user_active_storage(update.effective_user.id)
        
        if storage_list:
            # Берем первое активное хранилище
            storage = storage_list[0]
            storage_url = f"{TEMP_LINK_DOMAIN}/{storage['link_id']}"
            
            # Создаем клавиатуру с опцией удаления
            keyboard = [
                [KeyboardButton("🗑️ Удалить хранилище"), KeyboardButton("🔄 Продлить срок хранилища")],
                [KeyboardButton("Назад")]
            ]
            markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            await update.message.reply_text(
                f"У вас уже есть активное временное хранилище!\n\n"
                f"🔗 Ссылка: {storage_url}\n"
                f"⏱ Срок действия до: {format_datetime(storage['expires_at'])}\n\n"
                f"Вы можете продолжать использовать это хранилище или удалить его.",
                reply_markup=markup
            )
            context.user_data['current_storage'] = storage['link_id']
            return TEMP_LINK
            
        if update.message.text == "♾️ Бесконечное":
            expires_at = None
            capacity_mb = -1
            duration_text = "Бессрочно"
        else:
            duration_hours = duration_map[update.message.text]
            expires_at = datetime.now() + timedelta(hours=duration_hours)
            capacity_mb = 500
            duration_text = update.message.text
        
        # Создаем временное хранилище
        link_id = await generate_temp_link_id()
        
        # Создаем запись о временном хранилище
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute('''
                INSERT INTO temp_links (link_id, expires_at, user_id, created_at, extension_count, capacity_mb)
                VALUES (?, ?, ?, datetime('now'), 0, ?)
            ''', (link_id, expires_at, update.effective_user.id, capacity_mb))
            
            await conn.commit()
        
        # Формируем URL для доступа к хранилищу
        storage_url = f"{TEMP_LINK_DOMAIN}/{link_id}"
        
        # Отправляем пользователю ссылку с кнопкой удаления
        keyboard = [
            [KeyboardButton("🗑️ Удалить хранилище")],
            [KeyboardButton("Назад")]
        ]
        if expires_at is not None:
            keyboard[0].append(KeyboardButton("🔄 Продлить срок хранилища"))
        markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        # Форматируем текст о сроке действия
        if expires_at is None:
            success_message = (
                f"✅ Бесконечное временное хранилище успешно создано!\n\n"
                f"🔗 Ссылка: {storage_url}\n"
                f"💾 Емкость: Бесконечная\n"
                f"⏱ Срок действия: Бессрочно\n\n"
                f"Теперь вы можете перейти по ссылке и загрузить файлы."
            )
        else:
            success_message = (
                f"✅ Временное хранилище успешно создано!\n\n"
                f"🔗 Ссылка: {storage_url}\n"
                f"💾 Емкость: {capacity_mb} MB\n"
                f"⏱ Срок действия: {duration_text} (до {format_datetime(expires_at)})\n\n"
                f"Теперь вы можете перейти по ссылке и загрузить файлы."
            )
        
        await update.message.reply_text(
            success_message,
            reply_markup=markup
        )
        
        context.user_data['current_storage'] = link_id
        return TEMP_LINK
            
    except Exception as e:
        error_message = f"Ошибка при создании временного хранилища: {str(e)}"
        logger.error(error_message)
        
        await update.message.reply_text(
            "Произошла ошибка при создании временного хранилища. Попробуйте снова.",
            reply_markup=get_menu_keyboard(update.effective_user.id)
        )
        return MENU

async def extend_storage_duration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Продление срока хранения временного хранилища"""
    if update.message.text == "Назад":
        # Возвращаемся к управлению хранилищем
        link_id = context.user_data.get('extend_storage')
        if link_id:
            # Создаем клавиатуру для управления хранилищем
            keyboard = [
                [KeyboardButton("🗑️ Удалить хранилище"), KeyboardButton("🔄 Продлить срок хранилища")],
                [KeyboardButton("Назад")]
            ]
            markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            # Получаем информацию о хранилище
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('SELECT expires_at, extension_count, capacity_mb FROM temp_links WHERE link_id = ? AND user_id = ?', 
                     (link_id, update.effective_user.id))
            result = c.fetchone()
            
            if not result:
                await update.message.reply_text(
                    "Хранилище не найдено или уже удалено.", 
                    reply_markup=get_menu_keyboard(update.effective_user.id)
                )
                conn.close()
                return MENU
                
            expires_at, extension_count, capacity_mb = result
            extension_count = extension_count or 0  # Если None, то считаем как 0
            
            # Проверяем, достигнут ли лимит продлений
            max_extensions = 1  # Максимально допустимое количество продлений
            if extension_count >= max_extensions:
                await update.message.reply_text(
                    f"Достигнут лимит продлений хранилища (максимум {max_extensions}). Создайте новое хранилище.", 
                    reply_markup=get_menu_keyboard(update.effective_user.id)
                )
                conn.close()
                return MENU
                
            # Разбираем дату
            current_expires_at = datetime.strptime(format_datetime(expires_at), '%Y-%m-%d %H:%M:%S')
            
            # Проверяем, не истек ли срок хранилища
            if current_expires_at <= datetime.now():
                await update.message.reply_text(
                    "Срок действия хранилища уже истек. Создайте новое хранилище.", 
                    reply_markup=get_menu_keyboard(update.effective_user.id)
                )
                conn.close()
                return MENU
                
            # Рассчитываем новый срок действия
            duration_hours = duration_map[update.message.text]
            new_expires_at = current_expires_at + timedelta(hours=duration_hours)
            
            # Обновляем срок действия и счетчик продлений в базе данных
            c.execute('UPDATE temp_links SET expires_at = ?, extension_count = extension_count + 1 WHERE link_id = ?', 
                     (new_expires_at, link_id))
            conn.commit()
            
            # Форматируем текст о сроке продления
            duration_text = ""
            if duration_hours < 24:
                duration_text = f"{duration_hours} {'час' if duration_hours == 1 else 'часа' if 1 < duration_hours < 5 else 'часов'}"
            elif duration_hours < 48:
                duration_text = "1 день"
            else:
                days = duration_hours // 24
                duration_text = f"{days} {'день' if days == 1 else 'дня' if 1 < days < 5 else 'дней'}"
            
            # Создаем клавиатуру для управления хранилищем
            keyboard = [
                [KeyboardButton("🗑️ Удалить хранилище"), KeyboardButton("🔄 Продлить срок хранилища")],
                [KeyboardButton("Назад")]
            ]
            markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            storage_url = f"{TEMP_LINK_DOMAIN}/{link_id}"
            
            await update.message.reply_text(
                f"✅ Срок действия хранилища успешно продлен на {duration_text}!\n\n"
                f"🔗 Ссылка: {storage_url}\n"
                f"⏱ Новый срок действия до: {format_datetime(new_expires_at)}\n"
                f"🔄 Осталось продлений: {max_extensions - (extension_count + 1)}\n\n"
                f"Выберите действие:",
                reply_markup=markup
            )
            
            # Обновляем данные пользователя
            context.user_data['current_storage'] = link_id
            if 'extend_storage' in context.user_data:
                del context.user_data['extend_storage']
            
            return TEMP_LINK
        
        # Если нет активного хранилища, возвращаемся в главное меню
        await show_menu(update, context)
        return MENU
    
    # Определяем срок продления в часах
    duration_map = {
        '1 час': 1,
        '6 часов': 6,
        '12 часов': 12,
        '24 часа': 24,
        '3 дня': 72,
        '7 дней': 168,
        '14 дней': 336,
        '30 дней': 720
    }
    
    if update.message.text not in duration_map:
        await update.message.reply_text(
            "Пожалуйста, выберите срок продления из предложенных вариантов."
        )
        return TEMP_LINK_EXTEND
    
    try:
        link_id = context.user_data.get('extend_storage')
        if not link_id:
            await update.message.reply_text(
                "Не найдено активное хранилище для продления.", 
                reply_markup=get_menu_keyboard(update.effective_user.id)
            )
            return MENU
            
        # Получаем текущий срок действия
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT expires_at, extension_count, capacity_mb FROM temp_links WHERE link_id = ? AND user_id = ?', 
                 (link_id, update.effective_user.id))
        result = c.fetchone()
        
        if not result:
            await update.message.reply_text(
                "Хранилище не найдено или уже удалено.", 
                reply_markup=get_menu_keyboard(update.effective_user.id)
            )
            conn.close()
            return MENU
            
        expires_at, extension_count, capacity_mb = result
        extension_count = extension_count or 0  # Если None, то считаем как 0
        
        # Проверяем, достигнут ли лимит продлений
        max_extensions = 1  # Максимально допустимое количество продлений
        if extension_count >= max_extensions:
            await update.message.reply_text(
                f"Достигнут лимит продлений хранилища (максимум {max_extensions}). Создайте новое хранилище.", 
                reply_markup=get_menu_keyboard(update.effective_user.id)
            )
            conn.close()
            return MENU
            
        # Разбираем дату
        current_expires_at = datetime.strptime(format_datetime(expires_at), '%Y-%m-%d %H:%M:%S')
        
        # Проверяем, не истек ли срок хранилища
        if current_expires_at <= datetime.now():
            await update.message.reply_text(
                "Срок действия хранилища уже истек. Создайте новое хранилище.", 
                reply_markup=get_menu_keyboard(update.effective_user.id)
            )
            conn.close()
            return MENU
            
        # Рассчитываем новый срок действия
        duration_hours = duration_map[update.message.text]
        new_expires_at = current_expires_at + timedelta(hours=duration_hours)
        
        # Обновляем срок действия и счетчик продлений в базе данных
        c.execute('UPDATE temp_links SET expires_at = ?, extension_count = extension_count + 1 WHERE link_id = ?', 
                 (new_expires_at, link_id))
        conn.commit()
        
        # Форматируем текст о сроке продления
        duration_text = ""
        if duration_hours < 24:
            duration_text = f"{duration_hours} {'час' if duration_hours == 1 else 'часа' if 1 < duration_hours < 5 else 'часов'}"
        elif duration_hours < 48:
            duration_text = "1 день"
        else:
            days = duration_hours // 24
            duration_text = f"{days} {'день' if days == 1 else 'дня' if 1 < days < 5 else 'дней'}"
        
        # Создаем клавиатуру для управления хранилищем
        keyboard = [
            [KeyboardButton("🗑️ Удалить хранилище"), KeyboardButton("🔄 Продлить срок хранилища")],
            [KeyboardButton("Назад")]
        ]
        markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        storage_url = f"{TEMP_LINK_DOMAIN}/{link_id}"
        
        await update.message.reply_text(
            f"✅ Срок действия хранилища успешно продлен на {duration_text}!\n\n"
            f"🔗 Ссылка: {storage_url}\n"
            f"⏱ Новый срок действия до: {format_datetime(new_expires_at)}\n"
            f"🔄 Осталось продлений: {max_extensions - (extension_count + 1)}\n\n"
            f"Выберите действие:",
            reply_markup=markup
        )
        
        # Обновляем данные пользователя
        context.user_data['current_storage'] = link_id
        if 'extend_storage' in context.user_data:
            del context.user_data['extend_storage']
        
        return TEMP_LINK
        
    except Exception as e:
        if 'conn' in locals():
            conn.rollback()
        error_message = f"Ошибка при продлении срока хранилища: {str(e)}"
        logger.error(error_message)
        
        await update.message.reply_text(
            "Произошла ошибка при продлении срока хранилища. Попробуйте снова.",
            reply_markup=get_menu_keyboard(update.effective_user.id)
        )
        return MENU
        
    finally:
        if 'conn' in locals():
            conn.close()

async def delete_user_storage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление временного хранилища пользователя"""
    if update.message.text == "Назад":
        await show_menu(update, context)
        return MENU
    
    if update.message.text == "🔄 Продлить срок хранилища":
        # Переходим к выбору срока продления
        link_id = context.user_data.get('current_storage')
        if not link_id:
            await update.message.reply_text(
                "Не найдено активное хранилище для продления.", 
                reply_markup=get_menu_keyboard(update.effective_user.id)
            )
            return MENU
        
        # Создаем клавиатуру с выбором срока продления
        keyboard = [
            ['1 час', '6 часов'],
            ['12 часов', '24 часа'],
            ['3 дня', '7 дней'],
            ['14 дней', '30 дней'],
            ['Назад']
        ]
        
        await update.message.reply_text(
            "Выберите срок, на который хотите продлить хранилище:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        
        # Переходим к состоянию продления срока
        context.user_data['extend_storage'] = link_id
        return TEMP_LINK_EXTEND
    
    if update.message.text != "🗑️ Удалить хранилище":
        await show_menu(update, context)
        return MENU
    
    link_id = context.user_data.get('current_storage')
    if not link_id:
        await update.message.reply_text(
            "Не найдено активное хранилище для удаления.", 
            reply_markup=get_menu_keyboard(update.effective_user.id)
        )
        return MENU
    
    try:
        # Проверяем существование хранилища
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            SELECT link_id FROM temp_links 
            WHERE link_id = ? AND user_id = ?
        ''', (link_id, update.effective_user.id))
        
        if not c.fetchone():
            await update.message.reply_text(
                "Хранилище не найдено или уже удалено.", 
                reply_markup=get_menu_keyboard(update.effective_user.id)
            )
            return MENU
        
        # Удаляем файлы хранилища
        storage_path = os.path.join(BOT_DIR, 'temp_storage', link_id)
        if os.path.exists(storage_path):
            shutil.rmtree(storage_path)
        
        # Удаляем запись из базы данных
        c.execute('DELETE FROM temp_links WHERE link_id = ?', (link_id,))
        conn.commit()
        
        await update.message.reply_text(
            "✅ Хранилище успешно удалено!", 
            reply_markup=get_menu_keyboard(update.effective_user.id)
        )
        del context.user_data['current_storage']
        return MENU
        
    except Exception as e:
        if 'conn' in locals():
            conn.rollback()
        error_message = f"Ошибка при удалении хранилища: {str(e)}"
        logger.error(error_message)
        
        await update.message.reply_text(
            "Произошла ошибка при удалении хранилища. Попробуйте снова.",
            reply_markup=get_menu_keyboard(update.effective_user.id)
        )
        return MENU
        
    finally:
        if 'conn' in locals():
            conn.close()

def get_user_lines_to_keep(user_id):
    """Получение количества строк для пользователя"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('SELECT lines_to_keep FROM user_settings WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        if not result:
            c.execute('SELECT lines_to_keep FROM bot_status WHERE id = 1')
            result = c.fetchone()
        return result[0] if result else 10
    finally:
        conn.close()

def get_lines_to_keep():
    """Получение глобального количества строк"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT lines_to_keep FROM bot_status WHERE id = 1')
    result = c.fetchone()
    conn.close()
    return result[0] if result else 10

def set_lines_to_keep(lines):
    """Установка глобального количества строк"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('UPDATE bot_status SET lines_to_keep = ? WHERE id = 1', (lines,))
        conn.commit()
    finally:
        conn.close()

def get_all_users():
    """Получение списка всех пользователей"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT user_id, username, is_verified, role, 
                 usage_count, merged_count, qr_count, is_banned FROM users''')
    users = c.fetchall()
    conn.close()
    return users

async def get_all_users_async():
    """Получение списка всех пользователей"""
    async with aiosqlite.connect(DB_PATH) as conn:
        cursor = await conn.execute('''SELECT user_id, username, is_verified, role, 
                 usage_count, merged_count, qr_count, is_banned FROM users''')
        users = await cursor.fetchall()
    return users

async def remove_user(user_id):
    """Удаление пользователя из базы данных"""
    async with aiosqlite.connect(DB_PATH) as conn:
        await conn.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
        await conn.execute('DELETE FROM user_settings WHERE user_id = ?', (user_id,))
        await conn.commit()

async def process_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка файла"""
    # Проверяем верификацию пользователя и статус бота
    if not is_bot_enabled() and not is_admin(update.effective_user.id):
        await update.message.reply_text("Бот находится на техническом обслуживании. Пожалуйста, подождите.")
        return MENU
    
    if not is_user_verified(update.effective_user.id):
        await update.message.reply_text(
            "Пожалуйста, пройдите верификацию с помощью команды /start"
        )
        return MENU

    try:
        # Проверка наличия документа
        if not update.message.document:
            await update.message.reply_text("Пожалуйста, отправьте текстовый файл.")
            return PROCESS_FILE

        document = update.message.document
        
        # Проверка расширения файла
        file_name = document.file_name.lower()
        if not any(file_name.endswith(ext) for ext in ALLOWED_EXTENSIONS):
            await update.message.reply_text(
                f"Неподдерживаемый формат файла. Разрешены только: {', '.join(ALLOWED_EXTENSIONS)}"
            )
            return PROCESS_FILE

        # Проверка размера файла
        if document.file_size > MAX_FILE_SIZE:
            await update.message.reply_text(
                f"Файл слишком большой. Максимальный размер: {MAX_FILE_SIZE // (1024 * 1024)} MB"
            )
            return PROCESS_FILE

        # Скачиваем файл
        file = await context.bot.get_file(document.file_id)
        downloaded_file = await file.download_as_bytearray()
        
        try:
            content = downloaded_file.decode('utf-8')
        except UnicodeDecodeError:
            try:
                content = downloaded_file.decode('windows-1251')
            except UnicodeDecodeError:
                await update.message.reply_text(
                    "Ошибка при чтении файла. Убедитесь, что файл в кодировке UTF-8 или Windows-1251."
                )
                return PROCESS_FILE

        # Получаем строки из файла
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        
        # Проверка количества строк
        if len(lines) > MAX_LINKS:
            await update.message.reply_text(
                f"Слишком много строк в файле. Максимально допустимо: {MAX_LINKS}"
            )
            return PROCESS_FILE

        if not lines:
            await update.message.reply_text(
                "Файл пуст или не содержит текстовых строк."
            )
            return PROCESS_FILE

        # Получаем количество строк для конкретного пользователя
        lines_to_keep = get_user_lines_to_keep(update.effective_user.id)
        
        # Берем последние N строк
        last_lines = lines[-lines_to_keep:]
        
        # Создаем имя выходного файла на основе оригинального имени
        original_name = os.path.splitext(document.file_name)[0]  # Получаем имя без расширения
        output_filename = os.path.join(TEMP_DIR, f'{original_name}_{update.effective_user.id}.html')
        
        try:
            with open(output_filename, 'w', encoding='utf-8') as f:
                f.write('\n'.join(last_lines))
            
            # Отправляем файл
            with open(output_filename, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f'{original_name}.html',
                    caption=f"Найдено {len(lines)} строк. Показаны последние {lines_to_keep}."
                )
            # Увеличиваем счетчик после успешной отправки
            await increment_usage_count(update.effective_user.id)
        finally:
            # Удаляем временный файл
            if os.path.exists(output_filename):
                os.remove(output_filename)
        
        # Возвращаемся в главное меню
        await show_menu(update, context)
        return MENU
        
    except Exception as e:
        error_message = f"Произошла ошибка при обработке файла: {str(e)}"
        await log_error(update.effective_user.id, error_message)
        print(f"Error for user {update.effective_user.id}: {error_message}")
        await update.message.reply_text(
            "Произошла ошибка при обработке файла. Пожалуйста, попробуйте снова."
        )
        return PROCESS_FILE

async def increment_usage_count(user_id):
    """Увеличение счетчика использования бота"""
    async with await safe_db_connect() as conn:
        if not conn:
            return
        
        try:
            await conn.execute('UPDATE users SET usage_count = usage_count + 1 WHERE user_id = ?', (user_id,))
            await conn.commit()
        except sqlite3.Error as e:
            print(f"Ошибка при обновлении счетчика использования: {e}")

async def get_user_active_storage(user_id):
    """Получение активных временных ссылок пользователя"""
    try:
        # Получаем текущее московское время в формате без микросекунд
        import pytz
        moscow_tz = pytz.timezone('Europe/Moscow')
        now = datetime.now(moscow_tz)
        current_time = now.strftime('%Y-%m-%d %H:%M:%S')
        
        logger.info(f"Получение хранилищ пользователя {user_id}, текущее время (Москва): {current_time}")
        
        # Проверяем, является ли пользователь администратором
        is_user_admin = is_admin(user_id)
        
        async with aiosqlite.connect(DB_PATH) as conn:
            # Если пользователь - админ, получаем все хранилища, иначе только его собственные
            if is_user_admin:
                cursor = await conn.execute('''
                    SELECT tl.link_id, tl.expires_at, COUNT(tlf.file_id) as file_count, tl.user_id, u.username
                    FROM temp_links tl
                    LEFT JOIN temp_link_files tlf ON tl.link_id = tlf.link_id
                    LEFT JOIN users u ON tl.user_id = u.user_id
                    GROUP BY tl.link_id
                    ORDER BY tl.expires_at ASC
                ''')
            else:
                cursor = await conn.execute('''
                    SELECT tl.link_id, tl.expires_at, COUNT(tlf.file_id) as file_count, tl.user_id, u.username
                    FROM temp_links tl
                    LEFT JOIN temp_link_files tlf ON tl.link_id = tlf.link_id
                    LEFT JOIN users u ON tl.user_id = u.user_id
                    WHERE tl.user_id = ?
                    GROUP BY tl.link_id
                    ORDER BY tl.expires_at ASC
                ''', (user_id,))
            
            result = await cursor.fetchall()
            
            # Получаем детали по каждой ссылке
            storage_list = []
            for link_id, expires_at, file_count, creator_id, creator_username in result:
                # Очищаем дату от микросекунд, если они есть
                clean_expires_at = expires_at
                if '.' in expires_at:
                    clean_expires_at = expires_at.split('.')[0]
                
                # Проверяем, не истек ли срок действия
                if clean_expires_at <= current_time:
                    logger.info(f"Хранилище {link_id} истекло ({clean_expires_at} <= {current_time})")
                    continue
                
                # Получаем имена файлов
                cursor = await conn.execute('''
                    SELECT original_name 
                    FROM temp_link_files 
                    WHERE link_id = ?
                ''', (link_id,))
                
                files = await cursor.fetchall()
                file_names = [file[0] for file in files]
                
                # Форматируем оставшееся время
                expires_date = datetime.strptime(clean_expires_at, '%Y-%m-%d %H:%M:%S')
                time_left = expires_date - now.replace(tzinfo=None)
                days = time_left.days
                hours, remainder = divmod(time_left.seconds, 3600)
                minutes, _ = divmod(remainder, 60)
                
                time_str = f"{days}д {hours}ч {minutes}м" if days > 0 else f"{hours}ч {minutes}м"
                
                # Определяем имя создателя
                creator_name = creator_username or f"ID: {creator_id}"
                
                storage_list.append({
                    'link_id': link_id,
                    'expires_at': clean_expires_at,
                    'file_count': file_count,
                    'file_names': file_names,
                    'time_left': time_str,
                    'creator_id': creator_id,
                    'creator_name': creator_name
                })
                
                logger.info(f"Найдено активное хранилище {link_id}, срок действия до: {clean_expires_at}, создатель: {creator_name}")
            
            return storage_list
            
    except Exception as e:
        logger.error(f"Ошибка при получении списка хранилища: {e}")
        return []

def format_datetime(dt):
    """Форматирование даты без миллисекунд"""
    if isinstance(dt, str):
        if '.' in dt:
            dt = dt.split('.')[0]
        return dt
    return dt.strftime('%Y-%m-%d %H:%M:%S')

async def show_storage_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список активных хранилищ"""
    try:
        # Получаем список активных хранилищ
        storage_list = await get_user_active_storage(update.effective_user.id)
        
        if not storage_list:
            await update.message.reply_text(
                "Активных хранилищ не найдено.",
                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True)
            )
            return STORAGE_MANAGEMENT
        
        # Создаем клавиатуру с хранилищами
        keyboard = []
        storage_info = {}
        
        for storage in storage_list:
            link_id = storage['link_id']
            expires_at = storage['expires_at']
            file_count = storage['file_count']
            time_left = storage['time_left']
            creator_name = storage['creator_name']
            
            # Получаем количество продлений для этого хранилища
            async with aiosqlite.connect(DB_PATH) as conn:
                cursor = await conn.execute('SELECT extension_count FROM temp_links WHERE link_id = ?', 
                    (link_id,))
                result = await cursor.fetchone()
                extension_count = result[0] if result and result[0] is not None else 0
                # Максимум 1 продление
                max_extensions = 1  # Максимально допустимое количество продлений
                extensions_left = max_extensions - extension_count
            
            # Добавляем информацию о создателе в текст кнопки
            storage_text = f"Хранилище {link_id[:8]}... ({file_count} файлов, {time_left}) от {creator_name}"
            keyboard.append([KeyboardButton(text=storage_text)])
            storage_info[storage_text] = {
                'link_id': link_id,
                'expires_at': expires_at,
                'file_names': storage['file_names'],
                'creator_name': creator_name,
                'creator_id': storage['creator_id'],
                'extensions_left': extensions_left
            }
        
        keyboard.append([KeyboardButton(text="Назад")])
        markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        # Сохраняем информацию о хранилищах в контексте
        context.user_data['storage_info'] = storage_info
        
        await update.message.reply_text(
            "Выберите хранилище для управления:",
            reply_markup=markup
        )
        
        return STORAGE_MANAGEMENT
        
    except Exception as e:
        error_message = f"Ошибка при получении списка хранилищ: {str(e)}"
        logger.error(error_message)
        await update.message.reply_text(
            "Произошла ошибка при получении списка хранилищ.",
            reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True)
        )
        return STORAGE_MANAGEMENT

async def process_storage_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка управления хранилищами"""
    text = update.message.text
    
    if text == "Назад":
        await settings_command(update, context)
        return SETTINGS
    
    storage_info = context.user_data.get('storage_info', {})
    
    if text in storage_info:
        # Хранилище выбрано, показываем действия
        storage_data = storage_info[text]
        context.user_data['selected_storage'] = storage_data
        
        keyboard = [
            [KeyboardButton(text="🗑️ Удалить хранилище")],
            [KeyboardButton(text="🔄 Продлить срок")],
            [KeyboardButton(text="Назад")]
        ]
        markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        
        await update.message.reply_text(
            f"Управление хранилищем:\n\n"
            f"🔗 ID: {storage_data['link_id']}\n"
            f"👤 Создатель: {storage_data['creator_name']}\n"
            f"⏱ Срок действия до: {format_datetime(storage_data['expires_at'])}\n"
            f"🔄 Осталось продлений: {storage_data.get('extensions_left', 0)}\n\n"
            f"Выберите действие:",
            reply_markup=markup
        )
        
        return STORAGE_MANAGEMENT
    
    elif text == "🗑️ Удалить хранилище":
        storage_data = context.user_data.get('selected_storage')
        if not storage_data:
            await update.message.reply_text("Сначала выберите хранилище.")
            return await show_storage_list(update, context)
        
        try:
            # Удаляем файлы хранилища
            storage_path = os.path.join(BOT_DIR, 'temp_storage', storage_data['link_id'])
            if os.path.exists(storage_path):
                shutil.rmtree(storage_path)
            
            # Удаляем запись из базы данных асинхронно
            async with aiosqlite.connect(DB_PATH) as conn:
                await conn.execute('DELETE FROM temp_links WHERE link_id = ?', (storage_data['link_id'],))
                await conn.commit()
            
            await update.message.reply_text(
                "✅ Хранилище успешно удалено!",
                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True)
            )
            
            # Очищаем выбранное хранилище
            if 'selected_storage' in context.user_data:
                del context.user_data['selected_storage']
            
            return await show_storage_list(update, context)
            
        except Exception as e:
            error_message = f"Ошибка при удалении хранилища: {str(e)}"
            logger.error(error_message)
            await update.message.reply_text(
                "Произошла ошибка при удалении хранилища.",
                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True)
            )
            return STORAGE_MANAGEMENT
    
    elif text == "🔄 Продлить срок":
        storage_data = context.user_data.get('selected_storage')
        if not storage_data:
            await update.message.reply_text("Сначала выберите хранилище.")
            return await show_storage_list(update, context)
        
        # Создаем клавиатуру с выбором срока продления
        keyboard = [
            ['1 час', '6 часов'],
            ['12 часов', '24 часа'],
            ['3 дня', '7 дней'],
            ['14 дней', '30 дней'],
            ['Назад']
        ]
        
        await update.message.reply_text(
            "Выберите срок, на который хотите продлить хранилище:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        
        return STORAGE_MANAGEMENT
    
    elif text in ['1 час', '6 часов', '12 часов', '24 часа', '3 дня', '7 дней', '14 дней', '30 дней']:
        storage_data = context.user_data.get('selected_storage')
        if not storage_data:
            await update.message.reply_text("Сначала выберите хранилище.")
            return await show_storage_list(update, context)
        
        # Определяем срок продления в часах
        duration_map = {
            '1 час': 1,
            '6 часов': 6,
            '12 часов': 12,
            '24 часа': 24,
            '3 дня': 72,
            '7 дней': 168,
            '14 дней': 336,
            '30 дней': 720
        }
        
        try:
            duration_hours = duration_map[text]
            
            # Получаем текущее количество продлений хранилища
            async with aiosqlite.connect(DB_PATH) as conn:
                cursor = await conn.execute('SELECT extension_count FROM temp_links WHERE link_id = ?', 
                         (storage_data['link_id'],))
                result = await cursor.fetchone()
                extension_count = result[0] if result and result[0] is not None else 0
                
                # Проверяем, достигнут ли лимит продлений
                max_extensions = 1  # Максимально допустимое количество продлений
                if extension_count >= max_extensions:
                    await update.message.reply_text(
                        f"Достигнут лимит продлений хранилища ({max_extensions}). Создайте новое хранилище.",
                        reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True)
                    )
                    return STORAGE_MANAGEMENT
            
                # Создаем и форматируем новую дату без микросекунд
                expires_dt = datetime.now() + timedelta(hours=duration_hours)
                new_expires_at = expires_dt.strftime('%Y-%m-%d %H:%M:%S')
                
                # Обновляем срок действия и счетчик продлений в базе данных
                await conn.execute('UPDATE temp_links SET expires_at = ?, extension_count = extension_count + 1 WHERE link_id = ?', 
                         (new_expires_at, storage_data['link_id']))
                await conn.commit()
            
            # Получаем обновленное количество продлений
            cursor = await conn.execute('SELECT extension_count FROM temp_links WHERE link_id = ?', 
                     (storage_data['link_id'],))
            result = await cursor.fetchone()
            extension_count = result[0] if result and result[0] is not None else 0
            # Максимум 1 продление, но нужно учесть что продление уже произошло
            max_extensions = 1  # Максимально допустимое количество продлений
            extensions_left = max_extensions - (extension_count + 1)  
            
            # Форматируем текст о сроке продления
            duration_text = ""
            if duration_hours < 24:
                duration_text = f"{duration_hours} {'час' if duration_hours == 1 else 'часа' if 1 < duration_hours < 5 else 'часов'}"
            elif duration_hours < 48:
                duration_text = "1 день"
            else:
                days = duration_hours // 24
                duration_text = f"{days} {'день' if days == 1 else 'дня' if 1 < days < 5 else 'дней'}"
            
            await update.message.reply_text(
                f"✅ Срок действия хранилища успешно продлен на {duration_text}!\n\n"
                f"⏱ Новый срок действия до: {new_expires_at}\n"
                f"🔄 Осталось продлений: {extensions_left}",
                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True)
            )
            
            return await show_storage_list(update, context)
            
        except Exception as e:
            error_message = f"Ошибка при продлении срока хранилища: {str(e)}"
            logger.error(error_message)
            await update.message.reply_text(
                "Произошла ошибка при продлении срока хранилища.",
                reply_markup=ReplyKeyboardMarkup([['Назад']], resize_keyboard=True)
            )
            return STORAGE_MANAGEMENT
    
    return STORAGE_MANAGEMENT

def is_user_banned(user_id):
    """Проверка блокировки пользователя"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('SELECT is_banned FROM users WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        return result[0] if result else False
    finally:
        conn.close()

async def ban_user(bot, user_id, ban=True):
    """Блокировка/разблокировка пользователя"""
    try:
        # Блокируем/разблокируем в базе данных
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('UPDATE users SET is_banned = ? WHERE user_id = ?', (ban, user_id))
        conn.commit()
        conn.close()
        
        # Обновляем множество заблокированных пользователей
        if ban:
            user_ban_list.add(user_id)
        else:
            user_ban_list.discard(user_id)
            
        logger.info(f"Пользователь {user_id} {'заблокирован' if ban else 'разблокирован'}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при блокировке/разблокировке пользователя {user_id}: {e}")
        return False

async def notify_admins_about_spam(bot, user_id, username, action_count):
    """Отправка уведомления администраторам о спаме"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT user_id FROM users WHERE role = ?', (UserRole.ADMIN,))
        admins = c.fetchall()
        conn.close()
        
        message = (
            f"⚠️ *Обнаружен спам!*\n\n"
            f"👤 Пользователь: {username or f'ID: {user_id}'}\n"
            f"🆔 ID: `{user_id}`\n"
            f"📊 Количество действий: {action_count}\n"
            f"⏰ Время: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
            f"Рекомендуется проверить активность пользователя."
        )
        
        for admin in admins:
            try:
                await bot.send_message(
                    chat_id=admin[0],
                    text=message,
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.error(f"Ошибка при отправке уведомления администратору {admin[0]}: {e}")
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомлений администраторам: {e}")

async def check_action_cooldown(user_id):
    """Проверка защиты от спама"""
    current_time = time.time()
    
    # Если пользователь не в кэше, добавляем его
    if user_id not in user_action_times:
        user_action_times[user_id] = current_time
        user_action_counts[user_id] = 1
        return True
    
    # Проверяем последнее время действия
    last_action_time = user_action_times[user_id]
    time_diff = current_time - last_action_time
    
    # Если прошло менее 2 секунд, увеличиваем счетчик и проверяем
    if time_diff < 2:
        user_action_counts[user_id] = user_action_counts.get(user_id, 0) + 1
        
        # Если больше 5 быстрых действий, считаем это спамом
        if user_action_counts[user_id] > 5:
            # Добавляем предупреждение
            user_spam_warnings[user_id] = user_spam_warnings.get(user_id, 0) + 1
            
            # Если больше 3 предупреждений - банить
            if user_spam_warnings[user_id] > 3:
                # Банить пользователя
                user_ban_list.add(user_id)
                
                # Записываем в базу данных
                async with aiosqlite.connect(DB_PATH) as conn:
                    await conn.execute('UPDATE users SET is_banned = 1 WHERE user_id = ?', (user_id,))
                    await conn.commit()
                
                # Логируем бан
                logger.warning(f"Пользователь {user_id} заблокирован за спам")
                return False
            
            # Сбрасываем счетчик
            user_action_counts[user_id] = 0
            
            # Логируем попытку спама
            logger.warning(f"Обнаружена попытка спама от пользователя {user_id}")
            return False
    
    # Обновляем время действия
    user_action_times[user_id] = current_time
    
    # Если прошло больше 10 секунд, сбрасываем счетчик
    if time_diff > 10:
        user_action_counts[user_id] = 1
    
    return True

async def cleanup_spam_protection(context=None):
    """Очистка старых записей в кэше защиты от спама"""
    current_time = time.time()
    # Удаляем записи старше 5 минут
    for user_id in list(user_action_times.keys()):
        if current_time - user_action_times[user_id] > 300:
            del user_action_times[user_id]
            if user_id in user_action_counts:
                del user_action_counts[user_id]
            if user_id in user_spam_warnings:
                del user_spam_warnings[user_id]

async def check_user_access(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка доступа пользователя с защитой от спама"""
    user_id = update.effective_user.id
    
    # Проверяем блокировку в памяти
    if user_id in user_ban_list:
        return False
    
    # Проверяем блокировку в базе данных
    if is_user_banned(user_id):
        user_ban_list.add(user_id)
        return False
    
    # Для администраторов не применяем ограничения
    if is_admin(user_id):
        return True
    
    # Проверяем спам
    if not await check_action_cooldown(user_id):
        await update.message.reply_text(
            "Слишком много запросов. Пожалуйста, подождите..."
        )
        return False
    
    return True

async def create_infinite_storage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.text.split()[-1]
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав для выполнения этой команды.")
        return

    if not user_id.isdigit():
        await update.message.reply_text("Пожалуйста, укажите корректный ID пользователя.")
        return

    response = requests.post(
        f"{WEB_SERVER_URL}/create_infinite_storage",
        json={'user_id': user_id},
        headers={'Authorization': f"Bearer {ADMIN_TOKEN}"}
    )

    if response.status_code == 200:
        await update.message.reply_text("Хранилище с бесконечным сроком и емкостью успешно создано.")
    else:
        await update.message.reply_text(f"Ошибка: {response.json().get('error', 'Неизвестная ошибка')}")

async def make_infinite_storage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /make_infinite <user_id> для администраторов."""
    user_id_to_modify = None
    admin_user_id = update.effective_user.id

    # 1. Проверка прав администратора
    if not is_admin(admin_user_id):
        await update.message.reply_text("У вас нет прав для выполнения этой команды.")
        return

    # 2. Извлечение и валидация user_id из аргументов
    if not context.args:
        await update.message.reply_text("Пожалуйста, укажите ID пользователя после команды. Пример: `/make_infinite 123456789`")
        return

    try:
        user_id_to_modify = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID пользователя должен быть числом. Пример: `/make_infinite 123456789`")
        return

    # 3. Отправка запроса на веб-сервер
    try:
        headers = {'Authorization': f'Bearer {ADMIN_TOKEN}'}
        payload = {'user_id': user_id_to_modify}
        api_url = f"{WEB_SERVER_URL}/create_infinite_storage"

        logger.info(f"Администратор {admin_user_id} отправляет запрос на {api_url} для пользователя {user_id_to_modify}")

        response = requests.post(api_url, json=payload, headers=headers, timeout=10) # Добавляем таймаут

        # 4. Обработка ответа от сервера
        if response.status_code == 200:
            response_data = response.json()
            if response_data.get('success'):
                await update.message.reply_text(f"✅ Хранилище пользователя {user_id_to_modify} успешно обновлено на бесконечное.")
            else:
                # Это не должно произойти при status_code 200, но на всякий случай
                await update.message.reply_text(f"⚠️ Сервер вернул успех, но без флага success: {response_data.get('message', 'Нет сообщения')}")
        elif response.status_code == 404:
             response_data = response.json()
             await update.message.reply_text(f"❌ Ошибка: {response_data.get('error', 'Пользователь не найден')}")
        elif response.status_code == 401:
             await update.message.reply_text("❌ Ошибка аутентификации с веб-сервером. Проверьте ADMIN_SECRET_TOKEN.")
        elif response.status_code == 400:
             response_data = response.json()
             await update.message.reply_text(f"❌ Ошибка в запросе: {response_data.get('error', 'Неверный формат данных')}")
        else:
            # Другие ошибки сервера (5xx и т.д.)
            error_message = f"❌ Ошибка сервера ({response.status_code})."
            try:
                response_data = response.json()
                error_message += f" Детали: {response_data.get('error', response.text)}"
            except requests.exceptions.JSONDecodeError:
                error_message += f" Ответ: {response.text}"
            await update.message.reply_text(error_message)
            logger.error(f"Ошибка при запросе к /create_infinite_storage: {response.status_code} - {response.text}")

    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка сети при запросе к /create_infinite_storage: {e}")
        await update.message.reply_text(f"❌ Ошибка сети при обращении к веб-серверу: {e}")
    except Exception as e:
        logger.error(f"Непредвиденная ошибка в make_infinite_storage_command: {e}")
        await update.message.reply_text(f"❌ Произошла непредвиденная ошибка.")

if __name__ == '__main__':
    try:
        print("Запуск бота...")
        
        # Настройка и запуск бота стандартным методом библиотеки
        # Не используем asyncio.run() чтобы избежать проблем с циклом событий
        app = Application.builder().token(TOKEN).build()
        
        print("Приложение создано, настраиваем цикл событий...")
        try:
            # Получаем текущий цикл событий или создаем новый, если его нет
            try:
                loop = asyncio.get_event_loop()
                print(f"Получен существующий цикл событий: {loop}")
            except RuntimeError:
                print("Нет текущего цикла событий, создаем новый...")
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                print(f"Создан новый цикл событий: {loop}")
                
            # Инициализируем базу данных и создаем директории
            print("Инициализация базы данных и директорий...")
            loop.run_until_complete(ensure_directories())
            loop.run_until_complete(setup_database())
            print("Инициализация базы данных завершена")
        except Exception as e:
            print(f"Ошибка при настройке цикла событий: {e}")
            raise
        
        # Запускаем очистку истекших ссылок через планировщик
        app.job_queue.run_repeating(cleanup_expired_links, interval=3600, first=10)
        
        # Запускаем очистку кэша защиты от спама
        app.job_queue.run_repeating(cleanup_spam_protection, interval=300, first=300)
        
        print("Настройка обработчиков...")
        
        async def restore_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Восстановление меню для верифицированных пользователей"""
            if not await check_user_access(update, context):
                return ConversationHandler.END
                
            if is_user_verified(update.effective_user.id):
                if not is_bot_enabled() and not is_admin(update.effective_user.id):
                    await update.message.reply_text("Бот находится на техническом обслуживании. Пожалуйста, подождите.")
                    return ConversationHandler.END
                await show_menu(update, context)
                return MENU
            else:
                await update.message.reply_text("Пожалуйста, используйте команду /start для начала работы.")
                return ConversationHandler.END
        
        # Создаем обработчик разговора
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler('start', start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, restore_menu),
                MessageHandler(filters.Document.ALL, restore_menu)
            ],
            states={
                CAPTCHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_captcha)],
                MENU: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND | filters.Document.ALL, handle_menu)
                ],
                PROCESS_FILE: [
                    MessageHandler(filters.Document.ALL, process_file),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu)
                ],
                SETTINGS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_settings)],
                TECH_COMMANDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_tech_commands)],
                OTHER_COMMANDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_other_commands)],
                USER_MANAGEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_user_management)],
                MERGE_FILES: [
                    MessageHandler(filters.Document.ALL | filters.TEXT & ~filters.COMMAND, process_merge_command)
                ],
                SET_LINES: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_set_lines)],
                QR_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_qr_type)],
                QR_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_qr_data)],
                TEMP_LINK: [
                    MessageHandler(filters.Document.ALL | filters.TEXT & ~filters.COMMAND, delete_user_storage)
                ],
                TEMP_LINK_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_temp_link_duration)],
                TEMP_LINK_EXTEND: [MessageHandler(filters.TEXT & ~filters.COMMAND, extend_storage_duration)],
                STORAGE_MANAGEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_storage_management)],
            },
            fallbacks=[
                CommandHandler('start', start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, restore_menu),
                MessageHandler(filters.Document.ALL, restore_menu)
            ]
        )
        
        # Добавляем обработчик разговора
        app.add_handler(conv_handler)
        app.add_handler(CommandHandler("create_infinite_storage", create_infinite_storage))
        app.add_handler(CommandHandler("make_infinite", make_infinite_storage_command))

        # Выводим информацию о запуске
        print(f"Бот запущен и готов к работе!")
        print(f"База данных: {DB_PATH}")
        print(f"Временные файлы: {TEMP_DIR}")
        print(f"Логи: {LOG_DIR}")
        print(f"Временные ссылки: {TEMP_LINKS_DIR}")
        
        # Запускаем бота через asyncio для правильной обработки остановки
        loop = asyncio.get_event_loop()
        
        # Правильная последовательность инициализации и запуска
        loop.run_until_complete(app.initialize())
        loop.run_until_complete(app.updater.initialize())
        loop.run_until_complete(app.start())
        loop.run_until_complete(app.updater.start_polling(allowed_updates=Update.ALL_TYPES))
        
        try:
            # Запускаем бесконечный цикл для поддержания работы бота
            loop.run_forever()
        except (KeyboardInterrupt, SystemExit):
            # При остановке корректно завершаем работу
            loop.run_until_complete(app.stop())
            loop.run_until_complete(app.shutdown())
        
        print("Бот остановлен.")
        
    except (KeyboardInterrupt, SystemExit):
        print("Бот остановлен пользователем или системой.")
    except Exception as e:
        print(f"Критическая ошибка при запуске бота: {e}")
        # Выводим трассировку стека для отладки
        import traceback
        traceback.print_exc()
        sys.exit(1)

def import_aiolibs():
    """Проверка и импорт асинхронных библиотек"""
    try:
        import aiosqlite
        import aiofiles
        logger.info("Асинхронные библиотеки успешно импортированы")
        return True
    except ImportError:
        logger.warning("Асинхронные библиотеки не найдены, будут использованы синхронные версии")
        return False

def set_user_lines_to_keep_sync(user_id, lines):
    """Синхронная установка количества строк для пользователя"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute(
            'INSERT OR REPLACE INTO user_settings (user_id, lines_to_keep) VALUES (?, ?)',
            (user_id, lines)
        )
        conn.commit()
    finally:
        conn.close()



async def get_user_lines_to_keep(user_id):
    """Асинхронное получение количества строк для пользователя"""
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
            cursor = await conn.execute('SELECT lines_to_keep FROM user_settings WHERE user_id = ?', (user_id,))
            result = await cursor.fetchone()
            if not result:
                cursor = await conn.execute('SELECT lines_to_keep FROM bot_status WHERE id = 1')
                result = await cursor.fetchone()
            return result[0] if result else 10
    except Exception as e:
        logger.error(f"Ошибка при получении количества строк для пользователя {user_id}: {e}")
        # Fallback к синхронной версии
        return get_user_lines_to_keep_sync(user_id)

def get_user_lines_to_keep_sync(user_id):
    """Получение количества строк для пользователя (синхронная версия)"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('SELECT lines_to_keep FROM user_settings WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        if not result:
            c.execute('SELECT lines_to_keep FROM bot_status WHERE id = 1')
            result = c.fetchone()
        return result[0] if result else 10
    finally:
        conn.close()
        conn.close()