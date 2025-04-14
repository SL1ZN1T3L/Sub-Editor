from flask import Flask, send_file, request, render_template, jsonify, session
from flask_session import Session
import sqlite3
import aiosqlite
import asyncio
import os
import logging
from datetime import datetime, timedelta
import shutil
import secrets
from werkzeug.utils import secure_filename
import threading
import time
import tempfile
import zipfile
from io import BytesIO
from functools import wraps
import pytz
import re
from dotenv import load_dotenv
import html
import hashlib
from collections import defaultdict

# Загружаем переменные окружения из .env файла
load_dotenv()

# Функция для выполнения асинхронных задач в синхронном контексте Flask
def run_async(func):
    """Декоратор для запуска асинхронных функций в синхронном контексте Flask"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(func(*args, **kwargs))
        finally:
            loop.close()
    return wrapper

# Создаем необходимые директории
os.makedirs('logs', exist_ok=True)

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join('logs', 'web_server.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Пути к файлам и директориям
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(BASE_DIR, 'templates')
DB_PATH = os.path.join(BASE_DIR, 'bot_users.db')
TEMP_STORAGE_DIR = os.path.join(BASE_DIR, 'temp_storage')

app = Flask(__name__, template_folder=TEMPLATE_DIR)

# Загрузка конфигурации из .env файла
# Общий лимит хранилища (по умолчанию 500 MB)
MAX_STORAGE_SIZE_MB = int(os.getenv('MAX_STORAGE_SIZE_MB', 500))
app.config['MAX_STORAGE_SIZE'] = MAX_STORAGE_SIZE_MB * 1024 * 1024

# Максимальный размер загружаемого файла (по умолчанию 500 MB)
MAX_FILE_SIZE_MB = int(os.getenv('MAX_FILE_SIZE_MB', 500))
app.config['MAX_FILE_SIZE'] = MAX_FILE_SIZE_MB * 1024 * 1024

# Максимальное количество файлов в хранилище (по умолчанию не ограничено)
MAX_FILES_PER_STORAGE = int(os.getenv('MAX_FILES_PER_STORAGE', 0))
app.config['MAX_FILES_PER_STORAGE'] = MAX_FILES_PER_STORAGE

# Директория для временного хранилища
app.config['UPLOAD_FOLDER'] = os.getenv('UPLOAD_FOLDER', 'temp_storage')

# Разрешенные типы файлов (расширения, белый список)
DEFAULT_ALLOWED_EXTENSIONS = 'txt,pdf,png,jpg,jpeg,gif,doc,docx,xls,xlsx,ppt,pptx,zip,rar,7z,mp3,mp4,avi,mov,mkv'
ALLOWED_EXTENSIONS_STRING = os.getenv('ALLOWED_EXTENSIONS', DEFAULT_ALLOWED_EXTENSIONS)
app.config['ALLOWED_EXTENSIONS'] = set(ALLOWED_EXTENSIONS_STRING.lower().split(','))

# Настройка секретного ключа для сессий и CSRF защиты
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', secrets.token_hex(32))

# Срок действия временных хранилищ (в днях, по умолчанию 7 дней)
STORAGE_EXPIRATION_DAYS = int(os.getenv('STORAGE_EXPIRATION_DAYS', 7))
app.config['STORAGE_EXPIRATION_DAYS'] = STORAGE_EXPIRATION_DAYS

# Включение/отключение проверки CSRF (для отладки можно отключить)
CSRF_PROTECTION_ENABLED = os.getenv('CSRF_PROTECTION_ENABLED', 'true').lower() == 'true'
app.config['CSRF_PROTECTION_ENABLED'] = CSRF_PROTECTION_ENABLED

# Настройка серверных сессий
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = os.path.join(BASE_DIR, 'flask_session')
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_COOKIE_SECURE'] = os.getenv('SESSION_COOKIE_SECURE', 'true').lower() == 'true'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=1)

# Защита от брут-форса и rate limiting
# ИСПРАВЛЕНО: Увеличены лимиты для предотвращения ложных блокировок
app.config['MAX_REQUESTS_PER_MINUTE'] = int(os.getenv('MAX_REQUESTS_PER_MINUTE', 180))  # Было 60, стало 180
app.config['MAX_FAILED_ATTEMPTS'] = int(os.getenv('MAX_FAILED_ATTEMPTS', 10))  # Было 5, стало 10
app.config['BLOCK_TIME_SECONDS'] = int(os.getenv('BLOCK_TIME_SECONDS', 180))  # Было 300 (5 минут), стало 180 (3 минуты)

# Отключаем кэширование ответов для предотвращения устаревших данных
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# Инициализация сессий
Session(app)

# Отключаем ограничение на размер запроса
app.config['MAX_CONTENT_LENGTH'] = None

# Добавляем конфигурацию для загрузки файлов
app.config['UPLOAD_CHUNK_SIZE'] = int(os.getenv('UPLOAD_CHUNK_SIZE', 64)) * 1024  # по умолчанию 64 KB
app.config['MAX_CHUNK_SIZE'] = int(os.getenv('MAX_CHUNK_SIZE', 2)) * 1024 * 1024  # по умолчанию 2 MB

# Константа для количества строк по умолчанию
DEFAULT_LINES_TO_KEEP = int(os.getenv('DEFAULT_LINES_TO_KEEP', 10))

# Хранилище для rate limiting и защиты от брут-форса
request_counters = defaultdict(lambda: {'count': 0, 'reset_time': time.time() + 60})
failed_attempts = defaultdict(lambda: {'count': 0, 'blocked_until': 0})

# Rate limiting и защита от брут-форса
def check_rate_limit(ip_address):
    """Проверяет ограничение частоты запросов для IP-адреса"""
    current_time = time.time()
    counter = request_counters[ip_address]
    
    # Сбрасываем счетчик каждую минуту
    if current_time > counter['reset_time']:
        counter['count'] = 1
        counter['reset_time'] = current_time + 60
        return True
    
    # Увеличиваем счетчик
    counter['count'] += 1
    
    # ИСПРАВЛЕНО: Добавляем исключения для static-файлов и ресурсов, чтобы не блокировать обычных пользователей
    if request.path.startswith('/static/') or request.path.endswith(('.js', '.css', '.ico', '.png', '.jpg', '.jpeg', '.gif')):
        return True
        
    # Проверяем превышение лимита
    if counter['count'] > app.config['MAX_REQUESTS_PER_MINUTE']:
        # ИСПРАВЛЕНО: Добавляем логирование превышения лимита
        logger.warning(f"IP {ip_address} превысил лимит запросов: {counter['count']} запросов за минуту")
        return False
    
    return True

def is_ip_blocked(ip_address):
    """Проверяет, заблокирован ли IP-адрес"""
    current_time = time.time()
    data = failed_attempts[ip_address]
    
    # Если время блокировки прошло, снимаем блокировку
    if data['blocked_until'] > 0 and current_time > data['blocked_until']:
        data['blocked_until'] = 0
        data['count'] = 0
        return False
    
    return data['blocked_until'] > 0

def record_failed_attempt(ip_address):
    """Записывает неудачную попытку для IP-адреса"""
    data = failed_attempts[ip_address]
    data['count'] += 1
    
    # Если превышено максимальное количество попыток, блокируем IP
    if data['count'] >= app.config['MAX_FAILED_ATTEMPTS']:
        data['blocked_until'] = time.time() + app.config['BLOCK_TIME_SECONDS']
        logger.warning(f"IP {ip_address} заблокирован на {app.config['BLOCK_TIME_SECONDS']} секунд после {data['count']} неудачных попыток")

def rate_limit_middleware():
    """Middleware для применения rate limiting к запросам"""
    ip_address = request.remote_addr
    
    # Проверяем блокировку IP
    if is_ip_blocked(ip_address):
        return "Слишком много запросов. Пожалуйста, повторите позже.", 429
    
    # Проверяем rate limit
    if not check_rate_limit(ip_address):
        # Записываем неудачную попытку
        record_failed_attempt(ip_address)
        return "Превышен лимит запросов. Пожалуйста, повторите позже.", 429
    
    return None

@app.before_request
def before_request_middleware():
    """Middleware, выполняемый перед каждым запросом"""
    # Сохраняем время начала запроса для измерения длительности
    request.start_time = time.time()
    
    # ИСПРАВЛЕНО: Пропускаем проверки для статических файлов
    if request.path.startswith('/static/'):
        return None
    
    # Применяем rate limiting
    rate_limit_result = rate_limit_middleware()
    if rate_limit_result:
        return rate_limit_result
    
    # Добавляем случайный идентификатор сессии для предотвращения атак на фиксацию сессии
    if 'session_id' not in session:
        session['session_id'] = secrets.token_hex(16)
    
    # Периодическое обновление CSRF-токена
    if 'csrf_last_updated' not in session or time.time() - session.get('csrf_last_updated', 0) > 3600:
        session['csrf_token'] = secrets.token_hex(16)
        session['csrf_last_updated'] = time.time()

# Добавляем заголовки безопасности для всех ответов
@app.after_request
def add_security_headers(response):
    """Добавляет заголовки безопасности к каждому ответу"""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; style-src 'self' 'unsafe-inline' https://netdna.bootstrapcdn.com https://cdnjs.cloudflare.com https://cdn.jsdelivr.net; img-src 'self' data:; font-src 'self' https://cdnjs.cloudflare.com;"
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    
    # Устанавливаем заголовок Referrer-Policy для контроля утечек Referrer
    response.headers['Referrer-Policy'] = 'same-origin'
    
    # Устанавливаем Feature-Policy для ограничения опасных функций
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
    
    # Для API-ответов с JSON не нужны некоторые заголовки
    if response.mimetype == 'application/json':
        # Для JSON-ответов некоторые CSP-директивы не применимы
        response.headers['Content-Security-Policy'] = "default-src 'none'"
    
    # Для файлов разного типа нужны разные заголовки
    elif response.mimetype.startswith('image/'):
        response.headers['Content-Disposition'] = 'inline'
    elif response.mimetype in ['application/octet-stream', 'application/zip']:
        # Для загружаемых файлов устанавливаем Content-Disposition: attachment
        if 'Content-Disposition' not in response.headers:
            response.headers['Content-Disposition'] = 'attachment'
    
    return response

@app.after_request
def log_request(response):
    """Логирует информацию о запросе после его завершения"""
    # Вычисляем время выполнения запроса
    duration = 0
    if hasattr(request, 'start_time'):
        duration = time.time() - request.start_time
    
    # Получаем IP-адрес с учётом возможных прокси
    ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
    if ip_address and ',' in ip_address:
        # Если передано несколько IP через X-Forwarded-For, берём первый
        ip_address = ip_address.split(',')[0].strip()
    
    # Ограничиваем длину User-Agent для предотвращения атак
    user_agent = request.headers.get('User-Agent', '')[:255]
    
    # Логируем запрос в файл
    log_message = f"{ip_address} - {request.method} {request.path} - {response.status_code} - {duration:.2f}s - {user_agent}"
    if response.status_code >= 400:
        logger.warning(log_message)
    else:
        logger.info(log_message)
        
    # Асинхронно сохраняем информацию о запросе в базу данных
    @run_async
    async def log_to_db():
        try:
            current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            path = request.path[:255]  # Ограничиваем длину пути
            method = request.method[:10]  # Ограничиваем длину метода
            
            async with aiosqlite.connect(DB_PATH) as conn:
                await conn.execute(
                    'INSERT INTO access_log (timestamp, ip_address, user_agent, request_path, request_method, status_code, response_time) VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (current_time, ip_address, user_agent, path, method, response.status_code, duration)
                )
                await conn.commit()
        except Exception as e:
            logger.error(f"Ошибка при логировании запроса в БД: {str(e)}")
    
    # Запускаем асинхронное логирование, только если это не обращение к статическим файлам
    if not request.path.startswith('/static/'):
        try:
            log_to_db()
        except Exception as e:
            logger.error(f"Ошибка при запуске асинхронного логирования: {str(e)}")
    
    return response

# Создаем необходимые директории
os.makedirs(TEMP_STORAGE_DIR, exist_ok=True)
os.makedirs(app.config['SESSION_FILE_DIR'], exist_ok=True)  # Создаем директорию для сессий

# Логирование загруженных конфигураций
logger.info(f"Загружена конфигурация из .env файла:")
logger.info(f"MAX_STORAGE_SIZE_MB: {MAX_STORAGE_SIZE_MB} MB")
logger.info(f"MAX_FILE_SIZE_MB: {MAX_FILE_SIZE_MB} MB")
logger.info(f"MAX_FILES_PER_STORAGE: {MAX_FILES_PER_STORAGE} (0 = не ограничено)")
logger.info(f"ALLOWED_EXTENSIONS: {ALLOWED_EXTENSIONS_STRING}")
logger.info(f"STORAGE_EXPIRATION_DAYS: {STORAGE_EXPIRATION_DAYS}")
logger.info(f"CSRF_PROTECTION_ENABLED: {CSRF_PROTECTION_ENABLED}")

# Инициализация базы данных
async def init_db_async():
    """Асинхронная инициализация базы данных"""
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
            # Включаем защиту от внешних ключей
            await conn.execute("PRAGMA foreign_keys = ON")
            
            # Включаем WAL-режим для улучшения конкурентности и безопасности
            await conn.execute("PRAGMA journal_mode = WAL")
            
            # Создаем таблицу настроек пользователя, если её нет
            await conn.execute('''CREATE TABLE IF NOT EXISTS user_settings
                         (user_id INTEGER PRIMARY KEY,
                          lines_to_keep INTEGER DEFAULT 10,
                          theme TEXT DEFAULT 'dark')''')
            
            # Проверяем, есть ли колонка theme
            cursor = await conn.execute("PRAGMA table_info(user_settings)")
            columns_raw = await cursor.fetchall()
            columns = [column[1] for column in columns_raw]
            
            # Если колонки theme нет, добавляем её
            if 'theme' not in columns:
                await conn.execute('ALTER TABLE user_settings ADD COLUMN theme TEXT DEFAULT "light"')
            
            # Проверяем, есть ли таблица temp_links
            cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='temp_links'")
            table_exists = await cursor.fetchone()
            
            # Если таблица не существует, создаем её
            if not table_exists:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS temp_links (
                        link_id TEXT PRIMARY KEY,
                        expires_at TEXT NOT NULL,
                        user_id INTEGER,
                        created_at TEXT
                    )
                ''')
                logger.info("Создана таблица temp_links")
            
            # Если таблица существует, проверяем наличие колонки created_at
            else:
                cursor = await conn.execute("PRAGMA table_info(temp_links)")
                columns_raw = await cursor.fetchall()
                columns = [column[1] for column in columns_raw]
                
                # Если колонки created_at нет, добавляем её
                if 'created_at' not in columns:
                    try:
                        # Добавляем колонку created_at
                        await conn.execute('ALTER TABLE temp_links ADD COLUMN created_at TEXT')
                        
                        # Обновляем значения created_at на основе expires_at
                        # Предполагаем, что хранилище было создано за 7 дней до истечения срока
                        cursor = await conn.execute('SELECT link_id, expires_at FROM temp_links')
                        links = await cursor.fetchall()
                        
                        for link in links:
                            link_id, expires_at = link
                            if expires_at:
                                # Очищаем от микросекунд
                                if '.' in expires_at:
                                    expires_at = expires_at.split('.')[0]
                                    
                                try:
                                    # Преобразуем в datetime и вычитаем 7 дней
                                    expires_datetime = datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S')
                                    created_datetime = expires_datetime - timedelta(days=7)
                                    created_at = created_datetime.strftime('%Y-%m-%d %H:%M:%S')
                                    
                                    # Обновляем запись с использованием параметризованного запроса
                                    await conn.execute('UPDATE temp_links SET created_at = ? WHERE link_id = ?', 
                                                    (created_at, link_id))
                                except Exception as e:
                                    logger.error(f"Ошибка при обновлении created_at для {link_id}: {str(e)}")
                        
                        logger.info("Добавлена колонка created_at в таблицу temp_links")
                    except Exception as e:
                        logger.error(f"Ошибка при добавлении колонки created_at: {str(e)}")
            
            # Добавляем индекс для ускорения поиска по expires_at
            try:
                await conn.execute('CREATE INDEX IF NOT EXISTS idx_temp_links_expires_at ON temp_links(expires_at)')
                logger.info("Создан индекс на колонку expires_at")
            except Exception as e:
                logger.error(f"Ошибка при создании индекса: {str(e)}")
                
            # Создаем таблицу для логирования доступа, если её нет
            try:
                await conn.execute('''
                    CREATE TABLE IF NOT EXISTS access_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        ip_address TEXT,
                        user_agent TEXT,
                        request_path TEXT,
                        request_method TEXT,
                        status_code INTEGER,
                        response_time REAL
                    )
                ''')
                logger.info("Создана таблица access_log")
            except Exception as e:
                logger.error(f"Ошибка при создании таблицы access_log: {str(e)}")
                
            await conn.commit()
    except Exception as e:
        logger.error(f"Ошибка при инициализации базы данных: {str(e)}")
        raise

# Синхронная обертка для инициализации базы данных
def init_db():
    """Инициализация базы данных"""
    return run_async(init_db_async)()

# Инициализируем базу данных при запуске
init_db()

def get_temp_storage_path(link_id):
    """Получение пути к временному хранилищу"""
    return os.path.join(TEMP_STORAGE_DIR, link_id)

def get_temp_storage_size(link_id):
    """Получение размера временного хранилища"""
    storage_path = get_temp_storage_path(link_id)
    if not os.path.exists(storage_path):
        return 0
    total_size = 0
    for dirpath, _, filenames in os.walk(storage_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            total_size += os.path.getsize(fp)
    return max(0, total_size)  # Гарантируем положительное значение

def check_temp_storage_limit(link_id):
    """Проверка лимита временного хранилища (500 MB)"""
    return get_temp_storage_size(link_id) < 500 * 1024 * 1024

async def is_temp_storage_valid_async(link_id):
    """Асинхронная проверка валидности временного хранилища"""
    try:
        # Получаем текущее московское время
        from datetime import datetime
        moscow_tz = pytz.timezone('Europe/Moscow')
        now = datetime.now(moscow_tz)
        current_time_iso = now.strftime('%Y-%m-%d %H:%M:%S')
        
        async with aiosqlite.connect(DB_PATH) as conn:
            # Сначала проверяем существование хранилища
            cursor = await conn.execute('SELECT expires_at FROM temp_links WHERE link_id = ?', (link_id,))
            result = await cursor.fetchone()
            
            if not result:
                logger.info(f"Хранилище {link_id} не найдено")
                return False
                
            # Получаем дату истечения и убираем микросекунды, если они есть
            expires_at = result[0]
            if '.' in expires_at:
                expires_at = expires_at.split('.')[0]
                
            # Обновляем срок действия хранилища, если изменилась настройка STORAGE_EXPIRATION_DAYS
            if app.config['STORAGE_EXPIRATION_DAYS'] != 7:  # Если отличается от дефолтной
                try:
                    # Проверяем наличие поля created_at в таблице temp_links
                    cursor = await conn.execute("PRAGMA table_info(temp_links)")
                    columns = await cursor.fetchall()
                    column_names = [column[1] for column in columns]
                    
                    has_created_at = 'created_at' in column_names
                    
                    if has_created_at:
                        # Получаем время создания хранилища
                        cursor = await conn.execute('SELECT created_at FROM temp_links WHERE link_id = ?', (link_id,))
                        created_result = await cursor.fetchone()
                        
                        if created_result and created_result[0]:
                            created_at = created_result[0]
                            # Если есть поле created_at, вычисляем новое expires_at
                            if '.' in created_at:
                                created_at = created_at.split('.')[0]
                                
                            # Преобразуем строку в объект datetime
                            created_datetime = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
                            created_datetime = moscow_tz.localize(created_datetime)
                            
                            # Вычисляем новую дату истечения
                            new_expires_datetime = created_datetime + timedelta(days=app.config['STORAGE_EXPIRATION_DAYS'])
                            new_expires_at = new_expires_datetime.strftime('%Y-%m-%d %H:%M:%S')
                            
                            # Проверяем, отличается ли новая дата истечения от текущей
                            if new_expires_at != expires_at:
                                logger.info(f"Обновляем срок действия хранилища {link_id} с {expires_at} на {new_expires_at}")
                                await conn.execute('UPDATE temp_links SET expires_at = ? WHERE link_id = ?', 
                                                (new_expires_at, link_id))
                                await conn.commit()
                                expires_at = new_expires_at
                    else:
                        logger.warning(f"Поле created_at не найдено в таблице temp_links. Срок действия хранилища {link_id} не обновлен.")
                except Exception as e:
                    logger.error(f"Ошибка при обновлении срока действия хранилища {link_id}: {str(e)}")
                    
            # Сравниваем даты в строковом формате
            is_valid = expires_at > current_time_iso
            
            if not is_valid:
                logger.info(f"Хранилище {link_id} истекло ({expires_at} <= {current_time_iso})")
            else:
                logger.info(f"Хранилище {link_id} действительно ({expires_at} > {current_time_iso})")
                
            return is_valid
                
    except Exception as e:
        logger.error(f"Ошибка при проверке срока действия хранилища {link_id}: {str(e)}")
        return False

def is_temp_storage_valid(link_id):
    """Проверка валидности временного хранилища (синхронная обертка)"""
    return run_async(is_temp_storage_valid_async)(link_id)

async def get_user_theme_async(user_id):
    """Асинхронное получение темы пользователя"""
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
            cursor = await conn.execute('SELECT theme FROM user_settings WHERE user_id = ?', (user_id,))
            result = await cursor.fetchone()
            return result[0] if result and result[0] else 'light'
    except Exception as e:
        logger.error(f"Ошибка при получении темы пользователя: {str(e)}")
        return 'light'

def get_user_theme(user_id):
    """Получение темы пользователя (синхронная обертка)"""
    return run_async(get_user_theme_async)(user_id)

async def set_user_theme_async(user_id, theme):
    """Асинхронная установка темы пользователя"""
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute('''INSERT OR REPLACE INTO user_settings 
                         (user_id, theme, lines_to_keep) 
                         VALUES (?, ?, 
                                COALESCE((SELECT lines_to_keep FROM user_settings WHERE user_id = ?), 
                                ?))''', 
                     (user_id, theme, user_id, DEFAULT_LINES_TO_KEEP))
            await conn.commit()
            return True
    except Exception as e:
        logger.error(f"Ошибка при установке темы пользователя: {str(e)}")
        return False

def set_user_theme(user_id, theme):
    """Установка темы пользователя (синхронная обертка)"""
    return run_async(set_user_theme_async)(user_id, theme)

# Функция для проверки валидности расширения файла
def allowed_file(filename):
    """Проверяет, что загружаемый файл имеет разрешенное расширение из белого списка"""
    # Проверяем на валидность имени файла
    if not filename or '.' not in filename:
        return False
        
    # Нормализуем имя файла
    filename = filename.lower()
    
    # Проверяем на потенциально опасные последовательности символов в имени
    dangerous_patterns = [
        '../', './', '/..',  # Path traversal
        '\\', '%00',         # Null byte injection
        '<?', '<?php',       # PHP tags
        '<script', 'javascript:', 'vbscript:',  # XSS
        'cmd.exe', 'powershell', 'bash'  # Command execution
    ]
    
    for pattern in dangerous_patterns:
        if pattern in filename:
            return False
    
    # Дополнительная проверка на очень длинные имена файлов
    if len(filename) > 255:
        return False
    
    # Получаем все расширения файла (защита от двойных расширений)
    extensions = filename.split('.')
    
    # Проверяем основное расширение
    extension = extensions[-1]
    
    # Проверяем по белому списку (если он есть)
    if not app.config['ALLOWED_EXTENSIONS']:
        # Если белый список пуст - все файлы запрещены
        return False
        
    # Разрешены только файлы с расширениями из белого списка
    return extension in app.config['ALLOWED_EXTENSIONS']

# CSRF защита
def generate_csrf_token():
    """Генерирует CSRF токен для защиты форм"""
    # Если токен уже существует и не нуждается в обновлении, используем его
    if 'csrf_token' in session and 'csrf_last_updated' in session:
        last_updated = session.get('csrf_last_updated', 0)
        # Обновляем токен каждый час
        if time.time() - last_updated < 3600:
            return session['csrf_token']
    
    # Генерируем новый токен с использованием криптостойкого генератора случайных чисел
    token = secrets.token_hex(32)
    session['csrf_token'] = token
    session['csrf_last_updated'] = time.time()
    return token

def check_csrf_token():
    """Проверяет CSRF токен в запросе"""
    # Пробуем получить токен из разных источников
    token = None
    
    # Из заголовка X-CSRF-Token
    header_token = request.headers.get('X-CSRF-Token')
    if header_token:
        token = header_token
    
    # Из формы (form data)
    form_token = request.form.get('csrf_token')
    if not token and form_token:
        token = form_token
    
    # Из JSON-данных
    if not token and request.is_json:
        json_data = request.get_json(silent=True)
        if json_data and isinstance(json_data, dict):
            json_token = json_data.get('csrf_token')
            if json_token:
                token = json_token
    
    # Проверяем токен
    if not token or not session.get('csrf_token') or not secrets.compare_digest(token, session.get('csrf_token')):
        # Записываем попытку CSRF-атаки
        ip = request.remote_addr
        logger.warning(f"CSRF-атака предотвращена с IP {ip}")
        record_failed_attempt(ip)  # Записываем неудачную попытку для IP
        return False
    
    return True

# Защита маршрутов от CSRF атак
def csrf_protected(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Если CSRF защита отключена, просто выполняем функцию
        if not app.config['CSRF_PROTECTION_ENABLED']:
            return f(*args, **kwargs)
            
        if request.method in ['POST', 'PUT', 'DELETE', 'PATCH']:
            if not check_csrf_token():
                logger.warning("CSRF-атака предотвращена")
                return jsonify({'error': 'Недействительный CSRF токен. Пожалуйста, обновите страницу и повторите попытку.'}), 403
        return f(*args, **kwargs)
    return decorated_function

# Внедряем CSRF токен в шаблоны
@app.context_processor
def inject_csrf_token():
    return dict(csrf_token=generate_csrf_token())

# Добавляем кастомные фильтры в Jinja2
@app.template_filter('escapejs')
def escapejs_filter(value):
    """Экранирует строку для использования в JavaScript"""
    replacements = {
        '\\': '\\\\',
        '\n': '\\n',
        '\r': '\\r',
        '\t': '\\t',
        '\f': '\\f',
        '\b': '\\b',
        '"': '\\"',
        "'": "\\'"
    }
    result = str(value)
    for char, replacement in replacements.items():
        result = result.replace(char, replacement)
    return result

# Уникальные идентификаторы сессий загрузки для обработки конкурентной загрузки
upload_sessions = {}

# Безопасная обработка ошибок 
def handle_error(e, default_message="Внутренняя ошибка сервера", log_message=None):
    """Централизованная обработка ошибок с логированием и без утечки системной информации"""
    if log_message:
        logger.error(log_message)
    logger.error(f"Ошибка: {str(e)}")
    
    # Не показываем чувствительную информацию в ответе клиенту
    return default_message

@app.route('/')
def index():
    """Главная страница"""
    return render_template('index.html')

@app.route('/<link_id>')
def temp_storage(link_id):
    """Страница временного хранилища"""
    try:
        # Проверка на безопасность link_id (только буквенно-цифровые символы)
        if not re.match(r'^[a-zA-Z0-9_-]+$', link_id):
            logger.warning(f"Попытка доступа с некорректным link_id: {link_id}")
            return "Временное хранилище не найдено", 404
            
        # Проверяем валидность хранилища
        try:
            is_valid = is_temp_storage_valid(link_id)
        except Exception as e:
            logger.error(f"Ошибка при проверке валидности хранилища {link_id}: {str(e)}")
            return render_template('error.html', 
                                 message="Произошла ошибка при проверке хранилища. Пожалуйста, попробуйте позже."), 500
        
        if not is_valid:
            storage_path = get_temp_storage_path(link_id)
            if os.path.exists(storage_path):
                try:
                    shutil.rmtree(storage_path)
                    logger.info(f"Удалена директория недействительного хранилища: {link_id}")
                except Exception as e:
                    logger.error(f"Ошибка при удалении директории недействительного хранилища {link_id}: {str(e)}")
            
            # Используем асинхронную функцию через декоратор
            @run_async
            async def delete_expired_storage():
                try:
                    async with aiosqlite.connect(DB_PATH) as conn:
                        await conn.execute('DELETE FROM temp_links WHERE link_id = ?', (link_id,))
                        await conn.commit()
                        logger.info(f"Удалена запись о недействительном хранилище: {link_id}")
                except Exception as e:
                    logger.error(f"Ошибка при удалении записи о хранилище {link_id}: {str(e)}")
            
            try:
                delete_expired_storage()
            except Exception as e:
                logger.error(f"Ошибка при удалении записи о хранилище {link_id}: {str(e)}")
                
            return "Временное хранилище не найдено или срок его действия истек", 404

        # Получаем user_id из базы асинхронно
        try:
            @run_async
            async def get_user_id_and_expires_at():
                async with aiosqlite.connect(DB_PATH) as conn:
                    cursor = await conn.execute('SELECT user_id, expires_at FROM temp_links WHERE link_id = ?', (link_id,))
                    result = await cursor.fetchone()
                    return result if result else (None, None)
            
            user_data = get_user_id_and_expires_at()
            user_id, expires_at = user_data if user_data else (None, None)
            theme = get_user_theme(user_id) if user_id else 'light'
        except Exception as e:
            logger.error(f"Ошибка при получении данных пользователя для хранилища {link_id}: {str(e)}")
            user_id, expires_at = None, None
            theme = 'light'
        
        # Рассчитываем оставшееся время до истечения срока хранилища
        remaining_time = None
        if expires_at:
            try:
                # Форматируем дату истечения без микросекунд
                if '.' in expires_at:
                    expires_at = expires_at.split('.')[0]
                
                # Получаем текущее московское время
                moscow_tz = pytz.timezone('Europe/Moscow')
                now = datetime.now(moscow_tz)
                expires_datetime = datetime.strptime(expires_at, '%Y-%m-%d %H:%M:%S')
                expires_datetime = moscow_tz.localize(expires_datetime)
                
                # Рассчитываем оставшееся время
                time_delta = expires_datetime - now
                days = time_delta.days
                hours, remainder = divmod(time_delta.seconds, 3600)
                minutes, _ = divmod(remainder, 60)
                
                if days >= 0:
                    remaining_time = f"{days} дн., {hours} ч., {minutes} мин."
            except Exception as e:
                logger.error(f"Ошибка при расчете оставшегося времени: {str(e)}")
        
        # Получаем список файлов
        storage_path = get_temp_storage_path(link_id)
        if not os.path.exists(storage_path):
            os.makedirs(storage_path)
        
        files = []
        total_size = 0
        
        try:
            for filename in os.listdir(storage_path):
                file_path = os.path.join(storage_path, filename)
                if os.path.isfile(file_path):
                    try:
                        file_size = max(0, os.path.getsize(file_path))
                        total_size += file_size
                        
                        # Экранируем имя файла для предотвращения XSS
                        safe_filename = html.escape(filename)
                        
                        files.append({
                            'name': safe_filename,
                            'raw_name': filename,  # Оригинальное имя для операций с файлами
                            'size': file_size,
                            'modified': datetime.fromtimestamp(os.path.getmtime(file_path))
                        })
                    except (OSError, IOError) as e:
                        logger.error(f"Ошибка при получении информации о файле {filename}: {str(e)}")
        except Exception as e:
            logger.error(f"Ошибка при чтении содержимого директории {storage_path}: {str(e)}")
        
        files.sort(key=lambda x: x['modified'], reverse=True)
        
        # Обеспечиваем безопасные значения
        total_size = max(0, total_size)
        used_space = total_size / (1024 * 1024)  # переводим в MB
        used_percent = min(100, max(0, (total_size / app.config['MAX_STORAGE_SIZE']) * 100))
        
        # Рендерим шаблон с данными о хранилище
        try:
            return render_template('temp_storage.html',
                                link_id=link_id,
                                files=files,
                                used_space=used_space,
                                used_percent=used_percent,
                                theme=theme,
                                expires_at=expires_at,
                                remaining_time=remaining_time,
                                allowed_extensions=ALLOWED_EXTENSIONS_STRING.split(','))
        except Exception as e:
            logger.error(f"Ошибка при рендеринге шаблона: {str(e)}")
            return "Произошла ошибка при загрузке страницы. Пожалуйста, попробуйте позже.", 500
        
    except Exception as e:
        logger.error(f"Ошибка при загрузке страницы: {str(e)}")
        return "Произошла ошибка при загрузке страницы. Пожалуйста, попробуйте позже.", 500

@app.route('/<link_id>/upload', methods=['POST'])
@csrf_protected
def upload_file(link_id):
    """Загрузка файла в временное хранилище"""
    try:
        # Проверка на безопасность link_id
        if not re.match(r'^[a-zA-Z0-9_-]+$', link_id):
            logger.warning(f"Попытка загрузки с некорректным link_id: {link_id}")
            return jsonify({'error': 'Недействительный идентификатор хранилища'}), 400
            
        # Проверяем валидность хранилища
        if not is_temp_storage_valid(link_id):
            logger.error(f"Попытка загрузки в недействительное хранилище: {link_id}")
            return jsonify({'error': 'Временное хранилище не найдено или срок его действия истек'}), 404
            
        if 'file' not in request.files:
            logger.error("Файл не был отправлен в запросе")
            return jsonify({'error': 'Файл не выбран'}), 400
        
        file = request.files['file']
        if file.filename == '':
            logger.error("Пустое имя файла")
            return jsonify({'error': 'Файл не выбран'}), 400
        
        # Проверяем тип файла
        original_filename = file.filename
        if not allowed_file(original_filename):
            logger.warning(f"Попытка загрузить файл недопустимого типа: {original_filename}")
            return jsonify({'error': 'Тип файла не разрешен для загрузки'}), 400

        # Получаем информацию о чанках и сессии загрузки
        try:
            # Используем strip для предотвращения атак с вводом манипулированных данных
            chunk_number_str = request.form.get('chunk', '0').strip()
            total_chunks_str = request.form.get('chunks', '1').strip()
            total_size_str = request.form.get('total_size', '0').strip()
            upload_session_id = request.form.get('upload_session_id', '').strip()
            
            # Строгая проверка на числовые значения
            if not chunk_number_str.isdigit() or not total_chunks_str.isdigit() or not total_size_str.isdigit():
                logger.error(f"Получены некорректные числовые параметры: chunk={chunk_number_str}, chunks={total_chunks_str}, size={total_size_str}")
                return jsonify({'error': 'Параметры загрузки должны быть числовыми значениями'}), 400
                
            chunk_number = max(0, int(chunk_number_str))
            total_chunks = max(1, int(total_chunks_str))
            total_size = max(0, int(total_size_str))
            
            # Проверка разумных пределов
            if total_chunks > 10000:
                logger.error(f"Слишком большое количество чанков: {total_chunks}")
                return jsonify({'error': 'Превышено максимальное количество чанков'}), 400
                
            # Проверка upload_session_id на безопасность (только буквенно-цифровые символы)
            if not re.match(r'^[a-zA-Z0-9_-]*$', upload_session_id):
                logger.warning(f"Некорректный upload_session_id: {upload_session_id}")
                return jsonify({'error': 'Недействительный идентификатор сессии'}), 400
        except (ValueError, TypeError) as e:
            logger.error(f"Некорректные параметры загрузки: {str(e)}")
            return jsonify({'error': 'Некорректные параметры загрузки'}), 400

        # Проверяем размер файла
        file.seek(0, 2)  # Перемещаемся в конец файла
        chunk_size = file.tell()  # Получаем размер чанка
        file.seek(0)  # Возвращаемся в начало
        
        # Добавляем проверку на нулевой размер чанка
        if chunk_size <= 0:
            logger.error(f"Попытка загрузки файла с некорректным размером чанка: {chunk_size}")
            return jsonify({'error': 'Некорректный размер чанка'}), 400
            
        # Проверка на максимальный размер чанка
        if chunk_size > app.config['MAX_CHUNK_SIZE']:
            logger.error(f"Превышен максимальный размер чанка: {chunk_size} > {app.config['MAX_CHUNK_SIZE']}")
            max_chunk_mb = app.config['MAX_CHUNK_SIZE'] / (1024*1024)
            return jsonify({'error': f'Превышен максимальный размер чанка ({max_chunk_mb:.1f} MB)'}), 400
        
        # Проверка максимального размера файла из конфигурации
        if total_size <= 0 or total_size > app.config['MAX_FILE_SIZE']:
            logger.error(f"Некорректный общий размер файла: {total_size}, максимум: {app.config['MAX_FILE_SIZE']}")
            max_size_mb = app.config['MAX_FILE_SIZE'] / (1024*1024)
            return jsonify({'error': f'Превышен максимальный размер файла ({max_size_mb:.0f} MB)'}), 400
        
        storage_path = get_temp_storage_path(link_id)
        current_size = get_temp_storage_size(link_id)
        
        # Формируем безопасное имя файла
        # ИСПРАВЛЕНО: сохраняем имя файла, а не только расширение
        filename = secure_filename(original_filename)
        if not filename or filename == '.':
            logger.error(f"Не удалось создать безопасное имя файла из {original_filename}")
            return jsonify({'error': 'Недопустимое имя файла'}), 400
        
        # Установка и проверка сессии загрузки для обработки конкурентных загрузок
        if chunk_number == 0:
            # Для первого чанка создаем или обновляем запись сессии
            upload_sessions[upload_session_id] = {
                'filename': filename,
                'total_size': total_size,
                'last_update': time.time()
            }
            
            # Проверка на ограничение количества файлов в хранилище
            if app.config['MAX_FILES_PER_STORAGE'] > 0:
                try:
                    # Подсчитываем количество файлов в хранилище
                    if os.path.exists(storage_path):
                        file_count = len([f for f in os.listdir(storage_path) if os.path.isfile(os.path.join(storage_path, f))])
                        
                        # Проверяем, не существует ли уже файл с таким именем
                        file_path = os.path.join(storage_path, filename)
                        file_exists = os.path.exists(file_path)
                        
                        # Если файл с таким именем не существует, и мы превысили лимит
                        if not file_exists and file_count >= app.config['MAX_FILES_PER_STORAGE']:
                            logger.warning(f"Превышен лимит файлов в хранилище {link_id}: {file_count}/{app.config['MAX_FILES_PER_STORAGE']}")
                            return jsonify({
                                'error': f'Превышен лимит файлов в хранилище ({app.config["MAX_FILES_PER_STORAGE"]}). Удалите ненужные файлы.'
                            }), 400
                except Exception as e:
                    logger.error(f"Ошибка при проверке количества файлов в хранилище: {str(e)}")
            
        elif upload_session_id not in upload_sessions:
            logger.error(f"Недействительная сессия загрузки: {upload_session_id}")
            return jsonify({'error': 'Недействительная сессия загрузки'}), 400
        else:
            # Обновляем время последнего обновления
            upload_sessions[upload_session_id]['last_update'] = time.time()
        
        # Проверяем размер только для первого чанка
        if chunk_number == 0:
            remaining_size = max(0, app.config['MAX_STORAGE_SIZE'] - current_size)
            if total_size > remaining_size:
                logger.error(f"Превышен лимит хранилища. Текущий размер: {current_size}, размер файла: {total_size}, доступно: {remaining_size}")
                return jsonify({
                    'error': f'Превышен лимит хранилища ({MAX_STORAGE_SIZE_MB} MB). Использовано: {current_size / (1024*1024):.2f} MB'
                }), 400

        # Дополнительная проверка валидности пути хранилища
        try:
            storage_path = os.path.abspath(storage_path)
            expected_base_path = os.path.abspath(TEMP_STORAGE_DIR)
            if not storage_path.startswith(expected_base_path):
                logger.error(f"Попытка доступа к недопустимой директории: {storage_path}")
                return jsonify({'error': 'Недопустимый путь хранилища'}), 403
        except Exception as e:
            logger.error(f"Ошибка при проверке пути хранилища: {str(e)}")
            return jsonify({'error': 'Ошибка при проверке пути хранилища'}), 500

        # Создаем временный файл для безопасного сохранения чанка
        try:
            # Создаем директорию если её нет
            os.makedirs(storage_path, exist_ok=True)
            
            # Путь к постоянному файлу
            file_path = os.path.join(storage_path, filename)
            
            # Для первого чанка используем временный файл с уникальным именем
            if chunk_number == 0:
                # Если файл существует, удаляем его перед началом новой загрузки
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        logger.info(f"Удален существующий файл {filename} перед новой загрузкой")
                    except Exception as e:
                        logger.error(f"Ошибка при удалении существующего файла {filename}: {str(e)}")
                        return jsonify({'error': 'Не удалось подготовить файл к загрузке'}), 500
            
            # Открываем файл в режиме добавления для всех чанков кроме первого
            mode = 'wb' if chunk_number == 0 else 'ab'
            
            # Создаем временный файл, чтобы избежать гонки условий
            temp_fd, temp_path = tempfile.mkstemp(dir=storage_path)
            try:
                with os.fdopen(temp_fd, 'wb') as temp_file:
                    file.save(temp_file)
                
                # Проверяем размер временного файла
                temp_size = os.path.getsize(temp_path)
                if temp_size != chunk_size:
                    os.unlink(temp_path)
                    logger.error(f"Размер сохраненного чанка не соответствует ожидаемому: {temp_size} != {chunk_size}")
                    return jsonify({'error': 'Ошибка при сохранении файла: неверный размер чанка'}), 500
                
                # Если это первый чанк, просто переименовываем временный файл
                if chunk_number == 0:
                    if os.path.exists(file_path):
                        os.unlink(file_path)
                    os.rename(temp_path, file_path)
                else:
                    # Для последующих чанков добавляем содержимое временного файла к основному
                    with open(file_path, 'ab') as main_file, open(temp_path, 'rb') as t_file:
                        shutil.copyfileobj(t_file, main_file)
                    # Удаляем временный файл
                    os.unlink(temp_path)
                
                # Проверяем размер обновленного файла
                file_size = os.path.getsize(file_path)
                expected_size = (chunk_number + 1) * chunk_size
                
                # Если это последний чанк, используем общий размер файла
                if chunk_number == total_chunks - 1:
                    expected_size = total_size
                    
                    # Для последнего чанка, проверяем итоговый размер
                    if file_size != total_size:
                        logger.error(f"Итоговый размер файла не совпадает: {file_size} != {total_size}")
                        os.remove(file_path)
                        return jsonify({'error': 'Ошибка при сохранении файла: неверный итоговый размер'}), 500
                    
                    # Очищаем информацию о сессии загрузки
                    if upload_session_id in upload_sessions:
                        del upload_sessions[upload_session_id]
                        
                    logger.info(f"Файл {filename} успешно загружен в хранилище {link_id}")
                
                # Для промежуточных чанков проверяем, соответствует ли общий размер ожидаемому
                elif file_size < expected_size * 0.9 or file_size > expected_size * 1.1:
                    logger.error(f"Размер файла после загрузки чанка не соответствует ожидаемому: {file_size} (ожидалось около {expected_size})")
                    os.remove(file_path)
                    return jsonify({'error': 'Ошибка при сохранении файла: неверный размер'}), 500
                
                return jsonify({
                    'success': True,
                    'filename': filename,
                    'chunk': chunk_number,
                    'chunks': total_chunks
                })
                
            except Exception as e:
                # Очищаем временный файл в случае ошибки
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
                raise e
                
        except Exception as e:
            logger.error(f"Ошибка при сохранении чанка {chunk_number} файла {file.filename}: {str(e)}")
            # Чистим файл в случае ошибки
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass
            return jsonify({'error': 'Ошибка при сохранении файла'}), 500
        
    except Exception as e:
        logger.error(f"Общая ошибка при загрузке файла: {str(e)}")
        return jsonify({'error': 'Произошла ошибка при загрузке файла'}), 500

@app.route('/<link_id>/delete-partial/<filename>', methods=['POST'])
@csrf_protected
def delete_partial_file(link_id, filename):
    """Удаление частично загруженного файла"""
    try:
        # Проверка на безопасность link_id
        if not re.match(r'^[a-zA-Z0-9_-]+$', link_id):
            logger.warning(f"Попытка удаления с некорректным link_id: {link_id}")
            return jsonify({'error': 'Недействительный идентификатор хранилища'}), 400
            
        # Проверяем валидность хранилища
        if not is_temp_storage_valid(link_id):
            logger.error(f"Попытка удаления файла из недействительного хранилища: {link_id}")
            return jsonify({'error': 'Временное хранилище не найдено или срок его действия истек'}), 404
        
        # Безопасное формирование пути к файлу
        safe_filename = secure_filename(filename)
        if not safe_filename:
            logger.error(f"Попытка доступа к файлу с пустым или небезопасным именем: {filename}")
            return jsonify({'error': 'Недопустимое имя файла'}), 400
        
        storage_path = get_temp_storage_path(link_id)
        file_path = os.path.join(storage_path, safe_filename)
        
        # Проверка безопасности пути
        real_file_path = os.path.abspath(file_path)
        real_storage_path = os.path.abspath(storage_path)
        if not real_file_path.startswith(real_storage_path):
            logger.error(f"Попытка доступа к файлу вне хранилища: {filename}")
            return jsonify({'error': 'Недопустимый путь к файлу'}), 403
        
        # Удаляем файл если он существует
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Удален частично загруженный файл {filename} из хранилища {link_id}")
                return jsonify({'success': True})
            except Exception as e:
                logger.error(f"Ошибка при удалении частично загруженного файла {filename}: {str(e)}")
                return jsonify({'error': 'Не удалось удалить файл'}), 500
        
        return jsonify({'success': True})  # Возвращаем успех, даже если файла нет
        
    except Exception as e:
        logger.error(f"Ошибка при удалении частично загруженного файла: {str(e)}")
        return jsonify({'error': 'Произошла ошибка при удалении файла'}), 500

@app.route('/<link_id>/delete/<filename>', methods=['POST'])
@csrf_protected
def delete_file(link_id, filename):
    """Удаление файла из временного хранилища"""
    try:
        # Проверяем валидность хранилища
        if not is_temp_storage_valid(link_id):
            logger.error(f"Попытка удаления файла из недействительного хранилища: {link_id}")
            return jsonify({'error': 'Временное хранилище не найдено или срок его действия истек'}), 404
        
        # Безопасное формирование пути к файлу
        safe_filename = secure_filename(filename)
        if not safe_filename:
            logger.error(f"Попытка доступа к файлу с пустым или небезопасным именем: {filename}")
            return jsonify({'error': 'Недопустимое имя файла'}), 400
        
        storage_path = get_temp_storage_path(link_id)
        file_path = os.path.join(storage_path, safe_filename)
        
        # Проверка безопасности пути
        real_file_path = os.path.abspath(file_path)
        real_storage_path = os.path.abspath(storage_path)
        if not real_file_path.startswith(real_storage_path):
            logger.error(f"Попытка доступа к файлу вне хранилища: {filename}")
            return jsonify({'error': 'Недопустимый путь к файлу'}), 403
        
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"Файл {filename} успешно удален из хранилища {link_id}")
                return jsonify({'success': True})
            except Exception as e:
                logger.error(f"Ошибка при удалении файла {filename}: {str(e)}")
                return jsonify({'error': 'Не удалось удалить файл'}), 500
        else:
            logger.warning(f"Попытка удаления несуществующего файла {filename} из хранилища {link_id}")
            return jsonify({'error': 'Файл не найден'}), 404
    except Exception as e:
        logger.error(f"Ошибка при удалении файла {filename}: {str(e)}")
        return jsonify({'error': 'Произошла ошибка при удалении файла'}), 500

@app.route('/<link_id>/delete-all', methods=['POST'])
@csrf_protected
def delete_all_storage(link_id):
    """Удаление всего временного хранилища"""
    try:
        if not is_temp_storage_valid(link_id):
            return jsonify({'error': 'Временное хранилище не найдено или срок его действия истек'}), 404
            
        # Получаем путь к хранилищу
        storage_path = get_temp_storage_path(link_id)
        
        # Удаляем директорию с файлами
        if os.path.exists(storage_path):
            shutil.rmtree(storage_path)
            logger.info(f"Удалено хранилище по запросу пользователя: {link_id}")
            
        # Удаляем запись из базы данных асинхронно
        @run_async
        async def delete_storage_record():
            async with aiosqlite.connect(DB_PATH) as conn:
                await conn.execute('DELETE FROM temp_links WHERE link_id = ?', (link_id,))
                await conn.commit()
        
        delete_storage_record()
        
        return jsonify({'success': True})
    except Exception as e:
        logger.error(f"Ошибка при удалении хранилища: {str(e)}")
        return jsonify({'error': 'Произошла ошибка при удалении хранилища'}), 500

@app.route('/<link_id>/download/<filename>')
def download_file(link_id, filename):
    """Скачивание файла из временного хранилища"""
    try:
        # Проверка на безопасность link_id (только буквенно-цифровые символы)
        if not re.match(r'^[a-zA-Z0-9_-]+$', link_id):
            logger.warning(f"Попытка скачивания с некорректным link_id: {link_id}")
            return "Временное хранилище не найдено", 404
            
        # Проверяем валидность хранилища
        if not is_temp_storage_valid(link_id):
            return "Временное хранилище не найдено или срок его действия истек", 404
        
        # Безопасное формирование пути к файлу
        safe_filename = secure_filename(filename)
        if not safe_filename:
            logger.error(f"Попытка скачивания файла с пустым или небезопасным именем: {filename}")
            return "Недопустимое имя файла", 400
        
        # Проверка на длину имени файла
        if len(safe_filename) > 255:
            logger.error(f"Попытка скачивания файла со слишком длинным именем: {len(safe_filename)} символов")
            return "Недопустимое имя файла", 400
            
        storage_path = get_temp_storage_path(link_id)
        file_path = os.path.join(storage_path, safe_filename)
        
        # Проверка безопасности пути
        real_file_path = os.path.abspath(file_path)
        real_storage_path = os.path.abspath(storage_path)
        if not real_file_path.startswith(real_storage_path):
            logger.error(f"Попытка скачивания файла вне хранилища: {filename}")
            return "Недопустимый путь к файлу", 403
            
        # Проверка существования файла
        if not os.path.exists(file_path):
            logger.warning(f"Попытка скачивания несуществующего файла: {filename}")
            return "Файл не найден", 404
            
        # Проверка что это регулярный файл, а не директория или символическая ссылка
        if not os.path.isfile(file_path):
            logger.warning(f"Попытка скачивания не-файла: {filename}")
            return "Недопустимый тип ресурса", 400
            
        # Проверка размера файла
        try:
            file_size = os.path.getsize(file_path)
            if file_size > app.config['MAX_FILE_SIZE']:
                logger.warning(f"Попытка скачивания слишком большого файла: {filename} ({file_size} байт)")
                return "Файл слишком большой для скачивания", 400
                
            # Проверка нулевого размера файла
            if file_size == 0:
                logger.warning(f"Попытка скачивания пустого файла: {filename}")
                # Разрешаем скачивание пустых файлов, но логируем это
        except Exception as e:
            logger.error(f"Ошибка при проверке размера файла {filename}: {str(e)}")
            
        # Устанавливаем правильные MIME-типы для безопасности
        mime_type = 'application/octet-stream'
        # Определяем некоторые безопасные MIME-типы для распространенных расширений
        extensions_mime = {
            'pdf': 'application/pdf',
            'txt': 'text/plain',
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'gif': 'image/gif',
            'zip': 'application/zip'
        }
        
        # Получаем расширение файла
        if '.' in safe_filename:
            ext = safe_filename.rsplit('.', 1)[1].lower()
            mime_type = extensions_mime.get(ext, 'application/octet-stream')
            
        try:
            return send_file(
                file_path,
                mimetype=mime_type,
                as_attachment=True,
                download_name=safe_filename
            )
        except Exception as e:
            logger.error(f"Ошибка при отправке файла {filename}: {str(e)}")
            return "Произошла ошибка при скачивании файла", 500
            
    except Exception as e:
        logger.error(f"Ошибка при скачивании файла: {str(e)}")
        return "Произошла ошибка при скачивании файла", 500

@app.route('/<link_id>/download-multiple', methods=['POST'])
@csrf_protected
def download_multiple_files(link_id):
    """Скачивание нескольких файлов в архиве"""
    try:
        # Проверяем Content-Type
        if not request.is_json:
            logger.warning("Получен запрос с неверным Content-Type")
            return jsonify({'error': 'Ожидался JSON-запрос'}), 400
            
        # Проверка на безопасность link_id (только буквенно-цифровые символы)
        if not re.match(r'^[a-zA-Z0-9_-]+$', link_id):
            logger.warning(f"Попытка скачивания с некорректным link_id: {link_id}")
            return jsonify({'error': 'Недействительный идентификатор хранилища'}), 400
            
        # Проверяем валидность хранилища
        if not is_temp_storage_valid(link_id):
            logger.error(f"Попытка скачивания из недействительного хранилища: {link_id}")
            return jsonify({'error': 'Временное хранилище не найдено или срок его действия истек'}), 404

        # Получаем список файлов для скачивания
        files = request.json.get('files', [])
        if not files:
            return jsonify({'error': 'Файлы не выбраны'}), 400
            
        # Проверка на максимальное количество файлов для скачивания (защита от DoS)
        if len(files) > 1000:
            logger.warning(f"Попытка скачать слишком много файлов: {len(files)}")
            return jsonify({'error': 'Превышено максимальное количество файлов для скачивания'}), 400

        storage_path = get_temp_storage_path(link_id)
        real_storage_path = os.path.abspath(storage_path)
        
        # Создаем объект в памяти для архива
        memory_file = BytesIO()
        
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for filename in files:
                # Проверка на тип данных
                if not isinstance(filename, str):
                    logger.warning(f"Некорректный тип данных в списке файлов: {type(filename)}")
                    continue
                    
                # Ограничение длины имени файла
                if len(filename) > 255:
                    logger.warning(f"Слишком длинное имя файла: {len(filename)} символов")
                    continue
                
                # Безопасное формирование пути к файлу
                safe_filename = secure_filename(filename)
                if not safe_filename:
                    logger.warning(f"Пропуск файла с небезопасным именем: {filename}")
                    continue
                
                file_path = os.path.join(storage_path, safe_filename)
                real_file_path = os.path.abspath(file_path)
                
                # Проверка безопасности пути
                if not real_file_path.startswith(real_storage_path):
                    logger.warning(f"Попытка доступа к файлу вне хранилища: {filename}")
                    continue
                
                # Дополнительная проверка существования файла и его типа
                if not os.path.exists(file_path) or not os.path.isfile(file_path):
                    logger.warning(f"Файл не найден или не является обычным файлом: {filename}")
                    continue
                    
                # Проверка на максимальный размер файла для архивации (защита от DoS)
                file_size = os.path.getsize(file_path)
                if file_size > 500 * 1024 * 1024:  # 500 MB
                    logger.warning(f"Файл слишком большой для архивации: {filename} ({file_size} байт)")
                    continue
                
                try:
                    # Добавляем файл в архив
                    zf.write(file_path, safe_filename)
                except Exception as e:
                    logger.error(f"Ошибка при добавлении файла {filename} в архив: {str(e)}")

        # Перемещаем указатель в начало файла
        memory_file.seek(0)
        
        # Проверяем, были ли добавлены файлы в архив
        if memory_file.getbuffer().nbytes == 0:
            logger.warning("Не удалось создать архив - нет подходящих файлов")
            return jsonify({'error': 'Не удалось создать архив'}), 400
            
        # Ограничиваем размер архива для предотвращения DoS
        archive_size = memory_file.getbuffer().nbytes
        if archive_size > 1024 * 1024 * 1024:  # 1 GB
            logger.warning(f"Созданный архив слишком большой: {archive_size} байт")
            return jsonify({'error': 'Архив слишком большой. Выберите меньше файлов.'}), 400
        
        # Безопасное имя для архива
        safe_archive_name = f'files_{secure_filename(link_id)}.zip'
        
        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name=safe_archive_name
        )

    except Exception as e:
        logger.error(f"Ошибка при скачивании файлов: {str(e)}")
        return jsonify({'error': 'Произошла ошибка при скачивании файлов'}), 500

@app.route('/<link_id>/set-theme', methods=['POST'])
@csrf_protected
def set_theme(link_id):
    """Установка темы для пользователя"""
    try:
        # Проверяем Content-Type
        if not request.is_json:
            logger.warning("Получен запрос с неверным Content-Type")
            return jsonify({'error': 'Ожидался JSON-запрос'}), 400
            
        theme = request.json.get('theme')
        if theme not in ['light', 'dark']:
            return jsonify({'error': 'Неверная тема'}), 400

        # Получаем user_id из базы данных по link_id асинхронно
        @run_async
        async def get_user_id_for_theme():
            async with aiosqlite.connect(DB_PATH) as conn:
                cursor = await conn.execute('SELECT user_id FROM temp_links WHERE link_id = ?', (link_id,))
                result = await cursor.fetchone()
                return result[0] if result else None
        
        user_id = get_user_id_for_theme()
        
        if not user_id:
            return jsonify({'error': 'Хранилище не найдено'}), 404
            
        # Устанавливаем тему
        if set_user_theme(user_id, theme):
            return jsonify({'success': True})
        else:
            return jsonify({'error': 'Ошибка при установке темы'}), 500
            
    except Exception as e:
        logger.error(f"Ошибка при установке темы: {str(e)}")
        return jsonify({'error': 'Произошла ошибка при установке темы'}), 500

@app.route('/health')
def health_check():
    """Проверка работоспособности сервера"""
    return "OK", 200

async def cleanup_expired_storages_async():
    """Асинхронная очистка истекших временных хранилищ"""
    try:
        logger.info("Начинаем проверку истекших хранилищ")
        
        # Получим текущее московское время в формате ISO
        moscow_tz = pytz.timezone('Europe/Moscow')
        now = datetime.now(moscow_tz)
        current_time_iso = now.strftime('%Y-%m-%d %H:%M:%S')
        
        logger.info(f"Текущее время (Москва): {current_time_iso}")
        
        # Используем блокировку для предотвращения одновременной очистки из разных потоков
        cleanup_lock_file = os.path.join(TEMP_STORAGE_DIR, ".cleanup_lock")
        
        # Проверяем, запущен ли уже процесс очистки
        if os.path.exists(cleanup_lock_file):
            try:
                # Проверяем время создания файла блокировки
                lock_time = os.path.getmtime(cleanup_lock_file)
                current_time = time.time()
                
                # Если блокировка старше 30 минут, считаем ее устаревшей и продолжаем
                if current_time - lock_time > 1800:
                    logger.warning("Обнаружена устаревшая блокировка очистки. Продолжаем процесс.")
                    os.remove(cleanup_lock_file)
                else:
                    logger.info("Процесс очистки хранилищ уже запущен. Пропускаем.")
                    return
            except Exception as e:
                logger.error(f"Ошибка при проверке файла блокировки: {str(e)}")
                return
        
        # Создаем файл блокировки
        try:
            with open(cleanup_lock_file, 'w') as f:
                f.write(str(datetime.now()))
        except Exception as e:
            logger.error(f"Не удалось создать файл блокировки: {str(e)}")
            return
        
        try:
            async with aiosqlite.connect(DB_PATH) as conn:
                # Включаем внешние ключи и WAL режим
                await conn.execute("PRAGMA foreign_keys = ON")
                await conn.execute("PRAGMA journal_mode = WAL")
                
                # Получаем все истекшие хранилища одним запросом
                cursor = await conn.execute(
                    'SELECT link_id FROM temp_links WHERE expires_at <= ?', 
                    (current_time_iso,)
                )
                expired_storages = await cursor.fetchall()
                
                logger.info(f"Найдено {len(expired_storages)} истекших хранилищ")
                
                deleted_count = 0
                
                # Удаляем файлы и записи из базы данных
                for storage in expired_storages:
                    link_id = storage[0]
                    storage_path = get_temp_storage_path(link_id)
                    
                    # Проверяем link_id на безопасность
                    if not re.match(r'^[a-zA-Z0-9_-]+$', link_id):
                        logger.warning(f"Пропуск потенциально небезопасного link_id: {link_id}")
                        continue
                    
                    logger.info(f"Удаляем хранилище {link_id}")
                    
                    # Удаляем файлы хранилища
                    if os.path.exists(storage_path):
                        try:
                            shutil.rmtree(storage_path)
                            logger.info(f"Удалены файлы хранилища: {link_id}")
                        except Exception as e:
                            logger.error(f"Ошибка при удалении файлов хранилища {link_id}: {str(e)}")
                    else:
                        logger.info(f"Директория хранилища {link_id} не существует")
                    
                    # Удаляем запись из базы данных в транзакции
                    try:
                        await conn.execute('DELETE FROM temp_links WHERE link_id = ?', (link_id,))
                        deleted_count += 1
                        logger.info(f"Удалена запись о хранилище {link_id} из базы данных")
                    except Exception as e:
                        logger.error(f"Ошибка при удалении записи о хранилище {link_id}: {str(e)}")
                
                # Фиксируем изменения
                await conn.commit()
                logger.info(f"Очищено {deleted_count} истекших хранилищ из {len(expired_storages)}")
                
                # Проводим VACUUM для освобождения места в базе данных
                try:
                    await conn.execute("VACUUM")
                    logger.info("Выполнена оптимизация базы данных (VACUUM)")
                except Exception as e:
                    logger.error(f"Ошибка при оптимизации базы данных: {str(e)}")
        except Exception as e:
            logger.error(f"Ошибка при очистке хранилищ: {str(e)}")
            # Для отладки выводим полный стек ошибки
            import traceback
            logger.error(traceback.format_exc())
        finally:
            # Удаляем файл блокировки в любом случае
            try:
                if os.path.exists(cleanup_lock_file):
                    os.remove(cleanup_lock_file)
            except Exception as e:
                logger.error(f"Ошибка при удалении файла блокировки: {str(e)}")
    except Exception as e:
        logger.error(f"Критическая ошибка в процессе очистки: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

def cleanup_expired_storages():
    """Очистка истекших временных хранилищ (синхронная обертка)"""
    return run_async(cleanup_expired_storages_async)()

def periodic_cleanup():
    """Периодическая очистка истекших хранилищ"""
    while True:
        try:
            logger.info("Запуск периодической очистки истекших хранилищ")
            cleanup_expired_storages()
            
            # Очистка устаревших сессий загрузки
            cleanup_upload_sessions()
            
            # Очистка старых записей логов
            cleanup_old_logs()
            
            # Проверяем каждую минуту
            time.sleep(60)
        except Exception as e:
            logger.error(f"Ошибка в периодической очистке: {str(e)}")
            # В случае ошибки ждем 30 секунд перед следующей попыткой
            time.sleep(30)

# Очистка старых записей логов
def cleanup_old_logs():
    """Очистка старых записей журнала доступа"""
    logger.info("Запуск очистки старых записей журнала доступа")
    
    async def _cleanup_logs_async():
        try:
            # Оставляем записи только за последние 30 дней
            days_to_keep = 30
            cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).strftime('%Y-%m-%d %H:%M:%S')
            
            async with aiosqlite.connect(DB_PATH) as conn:
                # Получаем количество записей для удаления
                cursor = await conn.execute('SELECT COUNT(*) FROM access_log WHERE timestamp < ?', (cutoff_date,))
                result = await cursor.fetchone()
                count_to_delete = result[0] if result else 0
                
                if count_to_delete > 0:
                    # Удаляем старые записи
                    await conn.execute('DELETE FROM access_log WHERE timestamp < ?', (cutoff_date,))
                    await conn.commit()
                    logger.info(f"Удалено {count_to_delete} старых записей из журнала доступа")
                else:
                    logger.info("Нет устаревших записей для удаления из журнала доступа")
        except Exception as e:
            logger.error(f"Ошибка при очистке старых записей логов: {str(e)}")
    
    # Используем декоратор run_async напрямую на вызов функции
    run_async(_cleanup_logs_async)()

# Периодическая очистка истекших сессий загрузки
def cleanup_upload_sessions():
    """Очистка устаревших сессий загрузки"""
    try:
        current_time = time.time()
        expired_sessions = []
        
        for session_id, session_data in upload_sessions.items():
            # Если сессия не обновлялась более 30 минут, считаем её устаревшей
            if current_time - session_data['last_update'] > 1800:  # 30 минут
                expired_sessions.append(session_id)
        
        # Удаляем устаревшие сессии
        for session_id in expired_sessions:
            del upload_sessions[session_id]
            
        logger.info(f"Очищено {len(expired_sessions)} устаревших сессий загрузки")
    except Exception as e:
        logger.error(f"Ошибка при очистке сессий загрузки: {str(e)}")

# Обработчик ошибки 404 (не найдено)
@app.errorhandler(404)
def page_not_found(e):
    """Обработчик ошибки 404"""
    return "Запрашиваемая страница не найдена", 404

# Обработчик ошибки 500 (внутренняя ошибка сервера)
@app.errorhandler(500)
def internal_server_error(e):
    """Обработчик ошибки 500"""
    logger.error(f"Внутренняя ошибка сервера: {str(e)}")
    return "Произошла внутренняя ошибка сервера", 500

# Обработчик ошибки 405 (метод не разрешен)
@app.errorhandler(405)
def method_not_allowed(e):
    """Обработчик ошибки 405"""
    return "Метод не разрешен", 405

# Обработчик ошибки 403 (доступ запрещен)
@app.errorhandler(403)
def forbidden(e):
    """Обработчик ошибки 403"""
    return "Доступ запрещен", 403

if __name__ == '__main__':
    # Проверяем подключение к базе данных
    @run_async
    async def check_db_connection():
        try:
            async with aiosqlite.connect(DB_PATH) as conn:
                logger.info("Подключение к базе данных успешно")
        except Exception as e:
            logger.error(f"Ошибка подключения к базе данных: {str(e)}")
            raise
    
    try:
        # Инициализация базы данных и проверка подключения
        init_db()
        check_db_connection()
        
        # Проверяем существование и права директорий
        if not os.path.exists(TEMP_STORAGE_DIR):
            os.makedirs(TEMP_STORAGE_DIR, exist_ok=True)
            logger.info(f"Создана директория для временных хранилищ: {TEMP_STORAGE_DIR}")
        
        # Запускаем поток очистки
        cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
        cleanup_thread.start()
        logger.info("Запущен поток периодической очистки истекших хранилищ и сессий")
        
        # Запускаем сервер
        logger.info("Запуск веб-сервера на 0.0.0.0:5000")
        app.run(host='0.0.0.0', port=5000, threaded=True)
    except Exception as e:
        logger.critical(f"Критическая ошибка при запуске сервера: {str(e)}")
        raise