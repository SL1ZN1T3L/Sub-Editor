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

# Разрешенные типы файлов (расширения)
DEFAULT_ALLOWED_EXTENSIONS = 'txt,pdf,png,jpg,jpeg,gif,doc,docx,xls,xlsx,ppt,pptx,zip,rar,7z,mp3,mp4,avi,mov,mkv'
ALLOWED_EXTENSIONS_STRING = os.getenv('ALLOWED_EXTENSIONS', DEFAULT_ALLOWED_EXTENSIONS)
app.config['ALLOWED_EXTENSIONS'] = set(ALLOWED_EXTENSIONS_STRING.lower().split(','))

# Запрещенные типы файлов (дополнительная безопасность)
DEFAULT_BLOCKED_EXTENSIONS = 'php,exe,sh,bat,cmd,js,vbs,ps1,py,rb,pl'
BLOCKED_EXTENSIONS_STRING = os.getenv('BLOCKED_EXTENSIONS', DEFAULT_BLOCKED_EXTENSIONS)
app.config['BLOCKED_EXTENSIONS'] = set(BLOCKED_EXTENSIONS_STRING.lower().split(','))

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

# Создаем необходимые директории
os.makedirs(TEMP_STORAGE_DIR, exist_ok=True)
os.makedirs(app.config['SESSION_FILE_DIR'], exist_ok=True)  # Создаем директорию для сессий

# Логирование загруженных конфигураций
logger.info(f"Загружена конфигурация из .env файла:")
logger.info(f"MAX_STORAGE_SIZE_MB: {MAX_STORAGE_SIZE_MB} MB")
logger.info(f"MAX_FILE_SIZE_MB: {MAX_FILE_SIZE_MB} MB")
logger.info(f"MAX_FILES_PER_STORAGE: {MAX_FILES_PER_STORAGE} (0 = не ограничено)")
logger.info(f"ALLOWED_EXTENSIONS: {ALLOWED_EXTENSIONS_STRING}")
logger.info(f"BLOCKED_EXTENSIONS: {BLOCKED_EXTENSIONS_STRING}")
logger.info(f"STORAGE_EXPIRATION_DAYS: {STORAGE_EXPIRATION_DAYS}")
logger.info(f"CSRF_PROTECTION_ENABLED: {CSRF_PROTECTION_ENABLED}")

# Инициализация базы данных
async def init_db_async():
    """Асинхронная инициализация базы данных"""
    async with aiosqlite.connect(DB_PATH) as conn:
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
                                
                                # Обновляем запись
                                await conn.execute('UPDATE temp_links SET created_at = ? WHERE link_id = ?', 
                                                (created_at, link_id))
                            except Exception as e:
                                logger.error(f"Ошибка при обновлении created_at для {link_id}: {str(e)}")
                    
                    logger.info("Добавлена колонка created_at в таблицу temp_links")
                except Exception as e:
                    logger.error(f"Ошибка при добавлении колонки created_at: {str(e)}")
        
        await conn.commit()

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
    """Проверяет, что загружаемый файл имеет разрешенное расширение"""
    if '.' not in filename:
        return False
    extension = filename.rsplit('.', 1)[1].lower()
    
    # Сначала проверяем запрещенные расширения (приоритет)
    if extension in app.config['BLOCKED_EXTENSIONS']:
        return False
    
    # Если список разрешенных расширений пуст, разрешаем все (кроме заблокированных)
    if not app.config['ALLOWED_EXTENSIONS']:
        return True
        
    return extension in app.config['ALLOWED_EXTENSIONS']

# CSRF защита
def generate_csrf_token():
    """Генерирует CSRF токен для защиты форм"""
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(16)
    return session['csrf_token']

def check_csrf_token():
    """Проверяет CSRF токен в запросе"""
    token = request.headers.get('X-CSRF-Token')
    if not token or token != session.get('csrf_token'):
        return False
    return True

# Защита маршрутов от CSRF атак
def csrf_protected(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Если CSRF защита отключена, просто выполняем функцию
        if not app.config['CSRF_PROTECTION_ENABLED']:
            return f(*args, **kwargs)
            
        if request.method == 'POST':
            if not check_csrf_token():
                logger.warning("CSRF-атака предотвращена")
                return jsonify({'error': 'Недействительный CSRF токен'}), 403
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

@app.route('/space/<link_id>')
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
                        files.append({
                            'name': filename,
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
        
        try:
            return render_template('temp_storage.html',
                                link_id=link_id,
                                files=files,
                                total_size=total_size,
                                used_space=used_space,
                                used_percent=used_percent,
                                theme=theme,
                                expires_at=expires_at,
                                remaining_time=remaining_time)
        except Exception as e:
            logger.error(f"Ошибка при рендеринге шаблона: {str(e)}")
            return "Произошла ошибка при загрузке страницы. Пожалуйста, попробуйте позже.", 500
        
    except Exception as e:
        logger.error(f"Ошибка при загрузке страницы: {str(e)}")
        return "Произошла ошибка при загрузке страницы. Пожалуйста, попробуйте позже.", 500

@app.route('/space/<link_id>/upload', methods=['POST'])
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
            chunk_number = max(0, int(request.form.get('chunk', '0')))
            total_chunks = max(1, int(request.form.get('chunks', '1')))
            total_size = max(0, int(request.form.get('total_size', '0')))
            upload_session_id = request.form.get('upload_session_id', '')
        except (ValueError, TypeError) as e:
            logger.error(f"Некорректные параметры загрузки: {str(e)}")
            return jsonify({'error': 'Некорректные параметры загрузки'}), 400

        # Проверяем размер файла
        file.seek(0, 2)  # Перемещаемся в конец файла
        chunk_size = file.tell()  # Получаем размер чанка
        file.seek(0)  # Возвращаемся в начало
        
        if chunk_size <= 0:
            logger.error(f"Попытка загрузки файла с некорректным размером чанка: {chunk_size}")
            return jsonify({'error': 'Некорректный размер чанка'}), 400
        
        # Проверка максимального размера файла из конфигурации
        if total_size <= 0 or total_size > app.config['MAX_FILE_SIZE']:
            logger.error(f"Некорректный общий размер файла: {total_size}, максимум: {app.config['MAX_FILE_SIZE']}")
            max_size_mb = app.config['MAX_FILE_SIZE'] / (1024*1024)
            return jsonify({'error': f'Превышен максимальный размер файла ({max_size_mb:.0f} MB)'}), 400
        
        storage_path = get_temp_storage_path(link_id)
        current_size = get_temp_storage_size(link_id)
        
        # Формируем безопасное имя файла
        filename = secure_filename(original_filename)
        
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

@app.route('/space/<link_id>/delete-partial/<filename>', methods=['POST'])
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

@app.route('/space/<link_id>/delete/<filename>', methods=['POST'])
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

@app.route('/space/<link_id>/delete-all', methods=['POST'])
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

@app.route('/space/<link_id>/download/<filename>')
def download_file(link_id, filename):
    """Скачивание файла из временного хранилища"""
    try:
        if not is_temp_storage_valid(link_id):
            return "Временное хранилище не найдено или срок его действия истек", 404
        
        # Безопасное формирование пути к файлу
        safe_filename = secure_filename(filename)
        if not safe_filename:
            logger.error(f"Попытка скачивания файла с пустым или небезопасным именем: {filename}")
            return "Недопустимое имя файла", 400
        
        storage_path = get_temp_storage_path(link_id)
        file_path = os.path.join(storage_path, safe_filename)
        
        # Проверка безопасности пути
        real_file_path = os.path.abspath(file_path)
        real_storage_path = os.path.abspath(storage_path)
        if not real_file_path.startswith(real_storage_path):
            logger.error(f"Попытка скачивания файла вне хранилища: {filename}")
            return "Недопустимый путь к файлу", 403
            
        if os.path.exists(file_path):
            return send_file(
                file_path,
                as_attachment=True,
                download_name=safe_filename
            )
        return "Файл не найден", 404
    except Exception as e:
        logger.error(f"Ошибка при скачивании файла: {str(e)}")
        return "Произошла ошибка при скачивании файла", 500

@app.route('/space/<link_id>/download-multiple', methods=['POST'])
@csrf_protected
def download_multiple_files(link_id):
    """Скачивание нескольких файлов в архиве"""
    try:
        # Проверяем Content-Type
        if not request.is_json:
            logger.warning("Получен запрос с неверным Content-Type")
            return jsonify({'error': 'Ожидался JSON-запрос'}), 400
            
        # Проверяем валидность хранилища
        if not is_temp_storage_valid(link_id):
            logger.error(f"Попытка скачивания из недействительного хранилища: {link_id}")
            return jsonify({'error': 'Временное хранилище не найдено или срок его действия истек'}), 404

        # Получаем список файлов для скачивания
        files = request.json.get('files', [])
        if not files:
            return jsonify({'error': 'Файлы не выбраны'}), 400

        storage_path = get_temp_storage_path(link_id)
        real_storage_path = os.path.abspath(storage_path)
        
        # Создаем объект в памяти для архива
        memory_file = BytesIO()
        
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for filename in files:
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
                
                if os.path.exists(file_path) and os.path.isfile(file_path):
                    # Добавляем файл в архив
                    zf.write(file_path, safe_filename)
                else:
                    logger.warning(f"Файл не найден: {filename}")

        # Перемещаем указатель в начало файла
        memory_file.seek(0)
        
        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'files_{link_id}.zip'
        )

    except Exception as e:
        logger.error(f"Ошибка при скачивании файлов: {str(e)}")
        return jsonify({'error': 'Произошла ошибка при скачивании файлов'}), 500

@app.route('/space/<link_id>/set-theme', methods=['POST'])
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
        
        # Получим текущее локальное время в формате ISO
        from datetime import datetime
        
        # Используем московское время (UTC+3) как основное
        moscow_tz = pytz.timezone('Europe/Moscow')
        now = datetime.now(moscow_tz)
        current_time_iso = now.strftime('%Y-%m-%d %H:%M:%S')
        
        logger.info(f"Текущее время (Москва): {current_time_iso}")
        
        async with aiosqlite.connect(DB_PATH) as conn:
            # Получаем список всех хранилищ с их сроками действия для диагностики
            cursor = await conn.execute('SELECT link_id, expires_at FROM temp_links')
            all_storages = await cursor.fetchall()
            
            # Список истекших хранилищ
            expired_storages = []
            
            for storage in all_storages:
                link_id = storage[0]
                expires_at = storage[1]
                logger.info(f"Хранилище {link_id}, срок действия до: {expires_at}")
                
                # Форматируем дату истечения без микросекунд для корректного сравнения
                expires_clean = expires_at
                if '.' in expires_at:
                    expires_clean = expires_at.split('.')[0]
                
                # Сравниваем даты в строковом формате без микросекунд
                if expires_clean <= current_time_iso:
                    expired_storages.append((link_id,))
                    logger.info(f"Хранилище {link_id} истекло ({expires_clean} <= {current_time_iso})")
            
            logger.info(f"Найдено {len(expired_storages)} истекших хранилищ")
            
            # Удаляем файлы и записи из базы данных
            for storage in expired_storages:
                link_id = storage[0]
                storage_path = get_temp_storage_path(link_id)
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
                
                # Удаляем запись из базы данных
                try:
                    await conn.execute('DELETE FROM temp_links WHERE link_id = ?', (link_id,))
                    logger.info(f"Удалена запись о хранилище {link_id} из базы данных")
                except Exception as e:
                    logger.error(f"Ошибка при удалении записи о хранилище {link_id}: {str(e)}")
            
            await conn.commit()
            logger.info(f"Очищено {len(expired_storages)} истекших хранилищ")
            
    except Exception as e:
        logger.error(f"Ошибка при очистке хранилищ: {str(e)}")
        # Для отладки выводим полный стек ошибки
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
            
            # Проверяем каждую минуту
            time.sleep(60)
        except Exception as e:
            logger.error(f"Ошибка в периодической очистке: {str(e)}")
            # В случае ошибки ждем 30 секунд перед следующей попыткой
            time.sleep(30)

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