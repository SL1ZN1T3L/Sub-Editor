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
import hashlib
from pathlib import Path
import logging

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

# Константы для ограничений
MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_LINKS = 1000  # Максимальное количество ссылок в файле
ALLOWED_EXTENSIONS = ('.txt', '.csv', '.md', '')  # Добавлено пустое расширение
DEFAULT_LINES_TO_KEEP = 10  # Количество строк по умолчанию
MAX_TEMP_LINK_HOURS = 24  # Максимальное время хранения файла в часах

# Состояния разговора
CAPTCHA, MENU, SETTINGS, TECH_COMMANDS, OTHER_COMMANDS, USER_MANAGEMENT, MERGE_FILES, SET_LINES, PROCESS_FILE, QR_TYPE, QR_DATA, TEMP_LINK, TEMP_LINK_DURATION = range(13)

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

def ensure_directories():
    """Создание необходимых директорий с обработкой ошибок"""
    directories = [TEMP_DIR, LOG_DIR, TEMP_LINKS_DIR]
    for directory in directories:
        try:
            if not os.path.exists(directory):
                os.makedirs(directory)
                logger.info(f"Создана директория: {directory}")
                # Проверяем права на запись
                test_file = os.path.join(directory, 'test.txt')
                with open(test_file, 'w') as f:
                    f.write('test')
                os.remove(test_file)
                logger.info(f"Проверка прав доступа к директории {directory} успешна")
        except Exception as e:
            error_message = f"Ошибка при создании директории {directory}: {e}"
            logger.error(error_message)
            print(error_message)
            sys.exit(1)

def log_error(user_id, error_message):
    """Логирование ошибок в файл"""
    try:
        log_file = os.path.join(LOG_DIR, f'error_{datetime.now().strftime("%Y-%m-%d")}.log')
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(f"[{timestamp}] User {user_id}: {error_message}\n")
    except Exception as e:
        print(f"Ошибка при логировании: {str(e)}")
        print(f"[{timestamp}] User {user_id}: {error_message}")

def setup_database():
    """Создание и проверка базы данных"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Создаем таблицу users с полем role вместо is_admin
        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                is_verified BOOLEAN DEFAULT FALSE,
                role TEXT DEFAULT 'user',
                usage_count INTEGER DEFAULT 0,
                merged_count INTEGER DEFAULT 0,
                qr_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        logger.info("Таблица users создана или уже существует")
        
        # Создаем таблицу для статуса бота если её нет
        c.execute('''
            CREATE TABLE IF NOT EXISTS bot_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                status TEXT DEFAULT 'enabled',
                lines_to_keep INTEGER DEFAULT 10
            )
        ''')
        logger.info("Таблица bot_status создана или уже существует")
        
        # Создаем таблицу для персональных настроек пользователей
        c.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY,
                lines_to_keep INTEGER DEFAULT 10
            )
        ''')
        logger.info("Таблица user_settings создана или уже существует")

        # Создаем таблицу для временных ссылок
        c.execute('''
            CREATE TABLE IF NOT EXISTS temp_links (
                link_id TEXT PRIMARY KEY,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        logger.info("Таблица temp_links создана или уже существует")
        
        # Создаем таблицу для файлов временных ссылок
        c.execute('''
            CREATE TABLE IF NOT EXISTS temp_link_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                link_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                original_name TEXT NOT NULL,
                FOREIGN KEY (link_id) REFERENCES temp_links(link_id) ON DELETE CASCADE
            )
        ''')
        logger.info("Таблица temp_link_files создана или уже существует")
        
        # Проверяем, есть ли запись о статусе бота
        c.execute('SELECT COUNT(*) FROM bot_status')
        if c.fetchone()[0] == 0:
            c.execute('INSERT INTO bot_status (id, status, lines_to_keep) VALUES (1, "enabled", 10)')
            logger.info("Добавлена запись о статусе бота")
        
        conn.commit()
        logger.info("База данных успешно инициализирована")
        
    except sqlite3.Error as e:
        conn.rollback()
        error_message = f"Ошибка при создании базы данных: {str(e)}"
        logger.error(error_message)
        raise
        
    finally:
        conn.close()

def is_user_verified(user_id):
    """Проверка верификации пользователя"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Создаем таблицу users с полем role вместо is_admin
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            is_verified BOOLEAN DEFAULT FALSE,
            role TEXT DEFAULT 'user',
            usage_count INTEGER DEFAULT 0,
            merged_count INTEGER DEFAULT 0,
            qr_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Создаем таблицу для статуса бота если её нет
    c.execute('''
        CREATE TABLE IF NOT EXISTS bot_status (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            status TEXT DEFAULT 'enabled',
            lines_to_keep INTEGER DEFAULT 10
        )
    ''')
    
    # Создаем таблицу для персональных настроек пользователей
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            lines_to_keep INTEGER DEFAULT 10
        )
    ''')

    # Создаем таблицу для временных ссылок
    c.execute('''
        CREATE TABLE IF NOT EXISTS temp_links (
            link_id TEXT PRIMARY KEY,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Создаем таблицу для файлов временных ссылок
    c.execute('''
        CREATE TABLE IF NOT EXISTS temp_link_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            link_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            original_name TEXT NOT NULL,
            FOREIGN KEY (link_id) REFERENCES temp_links(link_id) ON DELETE CASCADE
        )
    ''')
    
    # Проверяем, есть ли запись о статусе бота
    c.execute('SELECT COUNT(*) FROM bot_status')
    if c.fetchone()[0] == 0:
        c.execute('INSERT INTO bot_status (id, status, lines_to_keep) VALUES (1, "enabled", 10)')
    
    conn.commit()
    conn.close()

def is_user_verified(user_id):
    """Проверка верификации пользователя"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT is_verified FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else False

def is_admin(user_id):
    """Проверка прав администратора"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT role FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] == UserRole.ADMIN if result else False

def verify_user(user_id, username, role=UserRole.USER):
    """Верификация пользователя в базе данных"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Проверяем существующие данные пользователя
    c.execute('SELECT role, usage_count, merged_count, qr_count FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    
    if result:
        existing_role, usage_count, merged_count, qr_count = result
        # Не понижаем роль существующего пользователя
        if role == UserRole.USER:
            role = existing_role
    else:
        usage_count, merged_count, qr_count = 0, 0, 0
    
    c.execute('''
        INSERT OR REPLACE INTO users 
        (user_id, username, is_verified, role, usage_count, merged_count, qr_count)
        VALUES (?, ?, TRUE, ?, ?, ?, ?)
    ''', (user_id, username, role, usage_count, merged_count, qr_count))
    
    conn.commit()
    conn.close()

def is_bot_enabled():
    """Проверка, включен ли бот"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT status FROM bot_status WHERE id = 1')
    result = c.fetchone()
    conn.close()
    return result[0] == 'enabled' if result else True

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
        ['ℹ️ Помощь', '📊 Статистика'],
        ['⚙️ Настройки']
    ]
    
    # Добавляем кнопку временных ссылок только для User+ и Админов
    if check_user_plus_rights(user_id):
        keyboard.insert(2, ['🔗 Создать временную ссылку'])
    
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
                verify_user(user_id, username, UserRole.ADMIN)
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
                verify_user(user_id, username, UserRole.USER_PLUS)
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
            verify_user(
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
    elif text == '🔗 Создать временную ссылку':
        if not check_user_plus_rights(update.effective_user.id):
            await update.message.reply_text("У вас нет прав для использования этой функции.")
            return MENU
        # Создаем клавиатуру с выбором срока хранения
        keyboard = [
            ['1 час', '6 часов'],
            ['12 часов', '24 часа'],
            ['Назад']
        ]
        await update.message.reply_text(
            "Выберите срок хранения временного хранилища:",
            reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
        )
        return TEMP_LINK_DURATION
    elif text == 'ℹ️ Помощь':
        await update.message.reply_text(
            "📚 *Помощь по использованию бота*\n\n"
            "📤 *Обработать файл* - загрузите файл со ссылками, и бот вернет последние N ссылок\n"
            "🔄 *Объединить подписки* - объединяет несколько подписок в одну\n"
            "📱 *Создать QR-код* - создает QR-код для различных типов данных\n"
            "🔗 *Создать временную ссылку* - создает временное хранилище для файлов\n"
            "📊 *Статистика* - показывает вашу статистику использования\n"
            "⚙️ *Настройки* - настройки бота\n\n"
            "Для начала работы просто выберите нужную функцию в меню.",
            parse_mode='Markdown'
        )
        return MENU
    elif text == '📊 Статистика':
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT usage_count, merged_count, qr_count FROM users WHERE user_id = ?', 
                 (update.effective_user.id,))
        stats = c.fetchone()
        usage_count = stats[0] if stats else 0
        merged_count = stats[1] if stats else 0
        qr_count = stats[2] if stats else 0
        lines_to_keep = get_user_lines_to_keep(update.effective_user.id)
        conn.close()
        
        await update.message.reply_text(
            f"📊 Ваша статистика:\n\n"
            f"1. Обработано файлов: {usage_count}\n"
            f"2. Объединено подписок: {merged_count}\n"
            f"3. Создано QR-кодов: {qr_count}\n"
            f"4. Выбранное количество строк: {lines_to_keep}"
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
        if is_admin(update.effective_user.id):
            # Для админов показываем выбор между личными и глобальными настройками
            keyboard = []
            keyboard.append([KeyboardButton(text="Изменить для себя")])
            keyboard.append([KeyboardButton(text="Изменить для всех")])
            keyboard.append([KeyboardButton(text="Назад")])
            markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            await update.message.reply_text(
                "Выберите действие:",
                reply_markup=markup
            )
            return SET_LINES
        else:
            # Для обычных пользователей сразу запрашиваем количество строк
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
    elif text == "Изменить для себя":
        current_lines = get_user_lines_to_keep(update.effective_user.id)
        await update.message.reply_text(
            f"Текущее количество строк: {current_lines}\n"
            f"Введите новое количество (от 1 до {MAX_LINKS}):"
        )
        context.user_data['setting_type'] = 'personal'
        return SET_LINES
    elif text == "Изменить для всех" and is_admin(update.effective_user.id):
        current_lines = get_lines_to_keep()
        await update.message.reply_text(
            f"Текущее глобальное количество строк: {current_lines}\n"
            f"Введите новое количество (от 1 до {MAX_LINKS}):"
        )
        context.user_data['setting_type'] = 'global'
        return SET_LINES
    elif text.isdigit():
        try:
            lines = int(text)
            if 1 <= lines <= MAX_LINKS:
                setting_type = context.user_data.get('setting_type')
                if setting_type == 'global' and is_admin(update.effective_user.id):
                    # Админ меняет глобальные настройки
                    set_lines_to_keep(lines)  # Обновляем глобальные настройки
                    await update.message.reply_text(f"Глобальное количество строк установлено: {lines}")
                elif setting_type == 'personal':
                    # Пользователь меняет свои настройки
                    set_user_lines_to_keep(update.effective_user.id, lines)
                    await update.message.reply_text(f"Ваше персональное количество строк установлено: {lines}")
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
    if not check_admin_rights(update.effective_user.id):
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
        # Завершаем процесс, systemd или другой менеджер процессов перезапустит бот
        sys.exit(0)
    
    return TECH_COMMANDS

async def process_other_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not check_admin_rights(update.effective_user.id):
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
    users = get_all_users()
    if not users:
        await update.message.reply_text("В базе данных нет пользователей.")
        return OTHER_COMMANDS
    
    verified_count = sum(1 for user in users if user[2])
    
    user_list = f"Всего верифицированных пользователей: {verified_count}\n\nСписок пользователей:\n\n"
    
    # Создаем клавиатуру с именами пользователей
    keyboard = []
    users_dict = {}
    
    for user in users:
        user_id = user[0]
        username = user[1] or f"ID: {user_id}"
        role = 'Пользователь' if user[3]=='user' else 'Пользователь+' if user[3]=='user_plus' else 'Админ'
        
        user_list += (
            f"ID: `{user[0]}`\n"
            f"Имя: {user[1] or 'Не указано'}\n"
            f"Верифицирован: {'Да' if user[2] else 'Нет'}\n"
            f"Роль: {'Пользователь' if user[3]=='user' else 'Пользователь+' if user[3]=='user_plus' else 'Админ'}\n"           
            f"Обработано файлов: {user[4]}\n"
            f"Объединено подписок: {user[5]}\n"
            f"Создано QR-кодов: {user[6]}\n\n"
        )
        
        button_text = f"{username} ({role})"
        keyboard.append([KeyboardButton(text=button_text)])
        users_dict[button_text] = user_id

    keyboard.append([KeyboardButton(text="Назад")])
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    # Сохраняем информацию о пользователях в контексте
    context.user_data['users_info'] = users_dict
    
    await update.message.reply_text(
        user_list + "\nВыберите пользователя для управления:", 
        reply_markup=markup,
        parse_mode='Markdown'
    )
    
    return USER_MANAGEMENT

async def process_user_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if text == "Назад":
        await settings_command(update, context)
        return SETTINGS
        
    if text in ["Убрать из базы", "Выдать пользователя", "Выдать пользователя+", "Выдать админа"]:
        user_id = context.user_data.get('selected_user_id')
        if not user_id:
            await update.message.reply_text("Сначала выберите пользователя.")
            return USER_MANAGEMENT
            
        if text == "Убрать из базы":
            if user_id == update.effective_user.id:
                await update.message.reply_text("Вы не можете удалить себя.")
            else:
                try:
                    remove_user(user_id)
                    await update.message.reply_text("Пользователь удален.")
                    return await show_users_list(update, context)
                except Exception as e:
                    await update.message.reply_text(f"Ошибка при удалении пользователя: {str(e)}")
        else:
            role = {
                "Выдать пользователя": UserRole.USER,
                "Выдать пользователя+": UserRole.USER_PLUS,
                "Выдать админа": UserRole.ADMIN
            }[text]
            
            try:
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute('UPDATE users SET role = ? WHERE user_id = ?', (role, user_id))
                conn.commit()
                conn.close()
                await update.message.reply_text("Роль пользователя обновлена.")
                return await show_users_list(update, context)
            except Exception as e:
                await update.message.reply_text(f"Ошибка при обновлении роли: {str(e)}")
    
    elif text in context.user_data.get('users_info', {}):
        # Пользователь выбран, показываем действия
        user_id = context.user_data['users_info'][text]
        context.user_data['selected_user_id'] = user_id
        
        keyboard = [
            [KeyboardButton(text="Убрать из базы")],
            [KeyboardButton(text="Выдать пользователя")],
            [KeyboardButton(text="Выдать пользователя+")],
            [KeyboardButton(text="Выдать админа")],
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
        log_error(update.effective_user.id, error_message)
        await update.message.reply_text("Произошла ошибка. Пожалуйста, попробуйте снова.")
        return MERGE_FILES

def merge_vless_subscriptions(subscriptions):
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
            increment_merge_count(update.effective_user.id)
            
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

def increment_merge_count(user_id):
    """Увеличение счетчика объединенных подписок"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE users SET merged_count = merged_count + 1 WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

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
            increment_qr_count(update.effective_user.id)
            
            # Очищаем данные пользователя
            context.user_data.clear()
            
            return MENU
            
        except Exception as e:
            error_message = f"Ошибка при создании QR-кода: {str(e)}"
            log_error(update.effective_user.id, error_message)
            if temp_filename and os.path.exists(temp_filename):
                os.remove(temp_filename)
            await update.message.reply_text(
                "Произошла ошибка. Пожалуйста, проверьте формат данных и попробуйте снова.",
                reply_markup=get_qr_type_keyboard()
            )
            return QR_TYPE

    except Exception as e:
        error_message = f"Ошибка при создании QR-кода: {str(e)}"
        log_error(update.effective_user.id, error_message)
        if temp_filename and os.path.exists(temp_filename):
            os.remove(temp_filename)
        await update.message.reply_text(
            "Произошла ошибка. Пожалуйста, проверьте формат данных и попробуйте снова.",
            reply_markup=get_qr_type_keyboard()
        )
        return QR_TYPE

def increment_qr_count(user_id):
    """Увеличение счетчика созданных QR-кодов"""
    conn = safe_db_connect()
    if not conn:
        return
    
    try:
        c = conn.cursor()
        c.execute('UPDATE users SET qr_count = qr_count + 1 WHERE user_id = ?', (user_id,))
        conn.commit()
    except sqlite3.Error as e:
        print(f"Ошибка при обновлении счетчика QR-кодов: {e}")
    finally:
        conn.close()

def safe_db_connect():
    """Безопасное подключение к базе данных"""
    try:
        return sqlite3.connect(DB_PATH)
    except sqlite3.Error as e:
        print(f"Ошибка подключения к базе данных: {e}")
        return None

def get_user_role(user_id):
    """Получение роли пользователя"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT role FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else UserRole.USER

def check_admin_rights(user_id):
    """Проверка прав администратора"""
    return get_user_role(user_id) == UserRole.ADMIN

def check_user_plus_rights(user_id):
    """Проверка прав привилегированного пользователя"""
    role = get_user_role(user_id)
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

def generate_temp_link_id():
    """Генерация уникального ID для временной ссылки"""
    # Используем только буквы и цифры для более короткого ID
    chars = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    return ''.join(random.choice(chars) for _ in range(4))

def save_temp_link(file_path, original_name, duration_hours):
    """Сохранение информации о временной ссылке"""
    try:
        link_id = generate_temp_link_id()
        expires_at = datetime.now() + timedelta(hours=duration_hours)
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        try:
            # Создаем запись о временной ссылке
            c.execute('''
                INSERT INTO temp_links (link_id, expires_at)
                VALUES (?, ?)
            ''', (link_id, expires_at))
            
            # Добавляем информацию о файле
            c.execute('''
                INSERT INTO temp_link_files (link_id, file_path, original_name)
                VALUES (?, ?, ?)
            ''', (link_id, file_path, original_name))
            
            conn.commit()
            return link_id
            
        except sqlite3.Error as e:
            conn.rollback()
            error_message = f"Ошибка базы данных при сохранении временной ссылки: {str(e)}"
            log_error(None, error_message)
            raise
            
        finally:
            conn.close()
            
    except Exception as e:
        error_message = f"Критическая ошибка при сохранении временной ссылки: {str(e)}"
        log_error(None, error_message)
        raise

def get_temp_link_info(link_id):
    """Получение информации о временной ссылке"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Проверяем срок действия ссылки
    c.execute('''
        SELECT expires_at
        FROM temp_links
        WHERE link_id = ? AND expires_at > datetime('now')
    ''', (link_id,))
    
    result = c.fetchone()
    if not result:
        conn.close()
        return None
    
    # Получаем список файлов
    c.execute('''
        SELECT file_path, original_name
        FROM temp_link_files
        WHERE link_id = ?
    ''', (link_id,))
    
    files = c.fetchall()
    conn.close()
    
    return {
        'expires_at': result[0],
        'files': files
    }

def cleanup_expired_links():
    """Очистка истекших временных ссылок"""
    try:
        # Проверяем и создаем директорию для временных ссылок
        if not os.path.exists(TEMP_LINKS_DIR):
            os.makedirs(TEMP_LINKS_DIR)
            logger.info(f"Создана директория для временных ссылок: {TEMP_LINKS_DIR}")
            
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        try:
            # Получаем список истекших ссылок
            c.execute('''
                SELECT link_id
                FROM temp_links
                WHERE expires_at <= datetime('now')
            ''')
            
            expired_links = c.fetchall()
            logger.info(f"Найдено {len(expired_links)} истекших ссылок")
            
            # Удаляем файлы и записи из базы данных
            for link_id in expired_links:
                # Получаем список файлов для удаления
                c.execute('SELECT file_path FROM temp_link_files WHERE link_id = ?', (link_id[0],))
                files = c.fetchall()
                
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
                c.execute('DELETE FROM temp_links WHERE link_id = ?', (link_id[0],))
            
            conn.commit()
            
        except sqlite3.Error as e:
            conn.rollback()
            error_message = f"Ошибка базы данных при очистке истекших ссылок: {str(e)}"
            logger.error(error_message)
            
        finally:
            conn.close()
            
    except Exception as e:
        error_message = f"Критическая ошибка при очистке истекших ссылок: {str(e)}"
        logger.error(error_message)

def get_temp_link_keyboard():
    """Создание клавиатуры для меню временных ссылок"""
    keyboard = [
        ['✅ Завершить'],
        ['Назад']
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def process_temp_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка создания временной ссылки"""
    if not check_user_plus_rights(update.effective_user.id):
        await update.message.reply_text("У вас нет прав для использования этой функции.")
        return await show_menu(update, context)
    
    # Создаем клавиатуру с выбором срока хранения
    keyboard = [
        ['1 час', '6 часов'],
        ['12 часов', '24 часа'],
        ['Назад']
    ]
    
    await update.message.reply_text(
        "Выберите срок хранения временного хранилища:",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    
    return TEMP_LINK_DURATION

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
        '24 часа': 24
    }
    
    if update.message.text not in duration_map:
        await update.message.reply_text(
            "Пожалуйста, выберите срок хранения из предложенных вариантов."
        )
        return TEMP_LINK_DURATION
    
    duration_hours = duration_map[update.message.text]
    
    try:
        # Создаем временное хранилище
        link_id = generate_temp_link_id()
        expires_at = datetime.now() + timedelta(hours=duration_hours)
        
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        try:
            # Создаем запись о временном хранилище
            c.execute('''
                INSERT INTO temp_links (link_id, expires_at)
                VALUES (?, ?)
            ''', (link_id, expires_at))
            
            conn.commit()
            
            # Формируем URL для доступа к хранилищу
            storage_url = f"{TEMP_LINK_DOMAIN}/space/{link_id}"
            
            # Отправляем пользователю ссылку
            await update.message.reply_text(
                f"✅ Временное хранилище создано!\n\n"
                f"🔗 Ссылка: {storage_url}\n"
                f"⏱ Срок действия: {duration_hours} {'час' if duration_hours == 1 else 'часа' if 1 < duration_hours < 5 else 'часов'}\n\n"
                f"⚠️ Хранилище будет доступно до {expires_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"Вы можете загружать файлы через веб-интерфейс.",
                reply_markup=get_menu_keyboard(update.effective_user.id)
            )
            
            return MENU
            
        except sqlite3.Error as e:
            conn.rollback()
            error_message = f"Ошибка базы данных при создании временного хранилища: {str(e)}"
            logger.error(error_message)
            raise
            
        finally:
            conn.close()
            
    except Exception as e:
        error_message = f"Ошибка при создании временного хранилища: {str(e)}"
        logger.error(error_message)
        
        await update.message.reply_text(
            "Произошла ошибка при создании временного хранилища. Попробуйте снова.",
            reply_markup=get_menu_keyboard(update.effective_user.id)
        )
        return MENU

def get_user_lines_to_keep(user_id):
    """Получение персонального количества строк для пользователя"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT lines_to_keep FROM user_settings WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else DEFAULT_LINES_TO_KEEP

def set_user_lines_to_keep(user_id, lines):
    """Установка персонального количества строк для пользователя"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO user_settings (user_id, lines_to_keep) VALUES (?, ?)', 
              (user_id, lines))
    conn.commit()
    conn.close()

def get_lines_to_keep():
    """Получение количества строк для сохранения"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT lines_to_keep FROM bot_status WHERE id = 1')
    result = c.fetchone()
    conn.close()
    return result[0] if result else DEFAULT_LINES_TO_KEEP

def set_lines_to_keep(lines):
    """Установка количества строк для сохранения"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE bot_status SET lines_to_keep = ? WHERE id = 1', (lines,))
    conn.commit()
    conn.close()

def get_all_users():
    """Получение списка всех пользователей"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT user_id, username, is_verified, role, 
                 usage_count, merged_count, qr_count FROM users''')
    users = c.fetchall()
    conn.close()
    return users

def remove_user(user_id):
    """Удаление пользователя из базы данных"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM users WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

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
            increment_usage_count(update.effective_user.id)
        finally:
            # Удаляем временный файл
            if os.path.exists(output_filename):
                os.remove(output_filename)
        
        # Возвращаемся в главное меню
        await show_menu(update, context)
        return MENU
        
    except Exception as e:
        error_message = f"Произошла ошибка при обработке файла: {str(e)}"
        log_error(update.effective_user.id, error_message)
        print(f"Error for user {update.effective_user.id}: {error_message}")
        await update.message.reply_text(
            "Произошла ошибка при обработке файла. Пожалуйста, попробуйте снова."
        )
        return PROCESS_FILE

def increment_usage_count(user_id):
    """Увеличение счетчика обработанных файлов"""
    conn = safe_db_connect()
    if not conn:
        return
    
    try:
        c = conn.cursor()
        c.execute('UPDATE users SET usage_count = usage_count + 1 WHERE user_id = ?', (user_id,))
        conn.commit()
    except sqlite3.Error as e:
        print(f"Ошибка при обновлении счетчика обработанных файлов: {e}")
    finally:
        conn.close()

def main():
    try:
        # Создаем необходимые директории
        ensure_directories()
        
        # Создаем и настраиваем приложение
        application = Application.builder().token(TOKEN).build()
        
        # Создаем базу данных
        setup_database()
        
        # Запускаем очистку истекших ссылок
        cleanup_expired_links()
        
        async def restore_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
            """Восстановление меню для верифицированных пользователей"""
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
                    MessageHandler(filters.Document.ALL | filters.TEXT & ~filters.COMMAND, process_temp_link)
                ],
                TEMP_LINK_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_temp_link_duration)]
            },
            fallbacks=[
                CommandHandler('start', start),
                MessageHandler(filters.TEXT & ~filters.COMMAND, restore_menu),
                MessageHandler(filters.Document.ALL, restore_menu)
            ]
        )
        
        # Добавляем обработчик разговора
        application.add_handler(conv_handler)
        
        # Запускаем бота
        print(f"Бот запущен и готов к работе!")
        print(f"База данных: {DB_PATH}")
        print(f"Временные файлы: {TEMP_DIR}")
        print(f"Логи: {LOG_DIR}")
        print(f"Временные ссылки: {TEMP_LINKS_DIR}")
        application.run_polling()

    except Exception as e:
        print(f"Критическая ошибка при запуске бота: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main() 