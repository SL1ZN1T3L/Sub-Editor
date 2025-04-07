from flask import Flask, send_file, request, render_template, jsonify
import sqlite3
import aiosqlite
import asyncio
import os
import logging
from datetime import datetime, timedelta
import shutil
from werkzeug.utils import secure_filename
import threading
import time
import tempfile
import zipfile
from io import BytesIO
from functools import wraps

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
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join('logs', 'web_server.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Общий лимит хранилища
app.config['MAX_STORAGE_SIZE'] = 500 * 1024 * 1024  # 500 MB
app.config['UPLOAD_FOLDER'] = 'temp_storage'

# Отключаем ограничение на размер запроса
app.config['MAX_CONTENT_LENGTH'] = None

# Добавляем конфигурацию для загрузки файлов
app.config['UPLOAD_CHUNK_SIZE'] = 64 * 1024  # 64 KB chunks for file reading
app.config['MAX_CHUNK_SIZE'] = 2 * 1024 * 1024  # 2 MB maximum chunk size

# Константа для количества строк по умолчанию
DEFAULT_LINES_TO_KEEP = 10

# Пути к файлам и директориям
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'bot_users.db')
TEMP_STORAGE_DIR = os.path.join(BASE_DIR, 'temp_storage')

# Создаем необходимые директории
os.makedirs(TEMP_STORAGE_DIR, exist_ok=True)

# Инициализация базы данных
async def init_db_async():
    """Асинхронная инициализация базы данных"""
    async with aiosqlite.connect(DB_PATH) as conn:
        # Создаем таблицу настроек пользователя, если её нет
        await conn.execute('''CREATE TABLE IF NOT EXISTS user_settings
                     (user_id INTEGER PRIMARY KEY,
                      lines_to_keep INTEGER DEFAULT 10,
                      theme TEXT DEFAULT 'light')''')
        
        # Проверяем, есть ли колонка theme
        cursor = await conn.execute("PRAGMA table_info(user_settings)")
        columns_raw = await cursor.fetchall()
        columns = [column[1] for column in columns_raw]
        
        # Если колонки theme нет, добавляем её
        if 'theme' not in columns:
            await conn.execute('ALTER TABLE user_settings ADD COLUMN theme TEXT DEFAULT "light"')
        
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
    return total_size

def check_temp_storage_limit(link_id):
    """Проверка лимита временного хранилища (500 MB)"""
    return get_temp_storage_size(link_id) < 500 * 1024 * 1024

async def is_temp_storage_valid_async(link_id):
    """Асинхронная проверка валидности временного хранилища"""
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
            # Проверяем существование и срок действия хранилища через SQL
            cursor = await conn.execute('''
                SELECT COUNT(*) 
                FROM temp_links 
                WHERE link_id = ? 
                AND datetime(expires_at) > datetime('now')
            ''', (link_id,))
            
            result = await cursor.fetchone()
            count = result[0] if result else 0
            return count > 0
                
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

@app.route('/')
def index():
    """Главная страница"""
    return render_template('index.html')

@app.route('/space/<link_id>')
def temp_storage(link_id):
    """Страница временного хранилища"""
    try:
        # Проверяем валидность хранилища
        if not is_temp_storage_valid(link_id):
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
                async with aiosqlite.connect(DB_PATH) as conn:
                    await conn.execute('DELETE FROM temp_links WHERE link_id = ?', (link_id,))
                    await conn.commit()
                    logger.info(f"Удалена запись о недействительном хранилище: {link_id}")
            
            try:
                delete_expired_storage()
            except Exception as e:
                logger.error(f"Ошибка при удалении записи о хранилище {link_id}: {str(e)}")
                
            return "Временное хранилище не найдено или срок его действия истек", 404

        # Получаем user_id из базы асинхронно
        @run_async
        async def get_user_id():
            async with aiosqlite.connect(DB_PATH) as conn:
                cursor = await conn.execute('SELECT user_id FROM temp_links WHERE link_id = ?', (link_id,))
                result = await cursor.fetchone()
                return result[0] if result else None
        
        user_id = get_user_id()
        theme = get_user_theme(user_id) if user_id else 'light'
        
        # Получаем список файлов
        storage_path = get_temp_storage_path(link_id)
        if not os.path.exists(storage_path):
            os.makedirs(storage_path)
        
        files = []
        total_size = 0
        for filename in os.listdir(storage_path):
            file_path = os.path.join(storage_path, filename)
            if os.path.isfile(file_path):
                file_size = os.path.getsize(file_path)
                total_size += file_size
                files.append({
                    'name': filename,
                    'size': file_size,
                    'modified': datetime.fromtimestamp(os.path.getmtime(file_path))
                })
        
        files.sort(key=lambda x: x['modified'], reverse=True)
        
        used_space = total_size / (1024 * 1024)
        used_percentage = (total_size / app.config['MAX_STORAGE_SIZE']) * 100
        
        return render_template('temp_storage.html',
                             link_id=link_id,
                             files=files,
                             used_space=used_space,
                             used_percentage=used_percentage,
                             theme=theme)
        
    except Exception as e:
        logger.error(f"Ошибка при загрузке страницы: {str(e)}")
        return "Внутренняя ошибка сервера", 500

@app.route('/space/<link_id>/upload', methods=['POST'])
def upload_file(link_id):
    """Загрузка файла в временное хранилище"""
    try:
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

        # Получаем информацию о чанках
        chunk_number = int(request.form.get('chunk', '0'))
        total_chunks = int(request.form.get('chunks', '1'))
        total_size = int(request.form.get('total_size', '0'))

        # Проверяем размер файла
        file.seek(0, 2)  # Перемещаемся в конец файла
        chunk_size = file.tell()  # Получаем размер чанка
        file.seek(0)  # Возвращаемся в начало
        
        storage_path = get_temp_storage_path(link_id)
        current_size = get_temp_storage_size(link_id)
        
        # Проверяем размер только для первого чанка
        if chunk_number == 0:
            if current_size + total_size > app.config['MAX_STORAGE_SIZE']:
                logger.error(f"Превышен лимит хранилища. Текущий размер: {current_size}, размер файла: {total_size}")
                return jsonify({
                    'error': f'Превышен лимит хранилища (500 MB). Использовано: {current_size / (1024*1024):.2f} MB'
                }), 400

        try:
            # Создаем директорию если её нет
            os.makedirs(storage_path, exist_ok=True)
            
            # Формируем имя файла
            filename = secure_filename(file.filename)
            file_path = os.path.join(storage_path, filename)
            
            # Для первого чанка
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
            with open(file_path, mode) as f:
                file.save(f)
            
            # Проверяем размер сохраненного чанка
            chunk_file_size = os.path.getsize(file_path)
            expected_size = chunk_size if chunk_number == 0 else (chunk_number + 1) * chunk_size
            
            # Если это последний чанк, используем общий размер файла
            if chunk_number == total_chunks - 1:
                expected_size = total_size
            
            if chunk_file_size > expected_size:
                logger.error(f"Неверный размер файла после загрузки чанка: {chunk_file_size} > {expected_size}")
                os.remove(file_path)
                return jsonify({'error': 'Ошибка при сохранении файла: неверный размер'}), 500
            
            # Если это последний чанк, проверяем итоговый размер
            if chunk_number == total_chunks - 1:
                if chunk_file_size != total_size:
                    logger.error(f"Итоговый размер файла не совпадает: {chunk_file_size} != {total_size}")
                    os.remove(file_path)
                    return jsonify({'error': 'Ошибка при сохранении файла: неверный итоговый размер'}), 500
                logger.info(f"Файл {filename} успешно загружен в хранилище {link_id}")
            
            return jsonify({
                'success': True,
                'filename': filename,
                'chunk': chunk_number,
                'chunks': total_chunks
            })
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении чанка {chunk_number} файла {file.filename}: {str(e)}")
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass
            return jsonify({'error': f'Ошибка при сохранении файла: {str(e)}'}), 500
        
    except Exception as e:
        logger.error(f"Общая ошибка при загрузке файла: {str(e)}")
        return jsonify({'error': f'Внутренняя ошибка сервера: {str(e)}'}), 500

@app.route('/space/<link_id>/delete/<filename>', methods=['POST'])
def delete_file(link_id, filename):
    """Удаление файла из временного хранилища"""
    try:
        # Проверяем валидность хранилища
        if not is_temp_storage_valid(link_id):
            logger.error(f"Попытка удаления файла из недействительного хранилища: {link_id}")
            return jsonify({'error': 'Временное хранилище не найдено или срок его действия истек'}), 404
            
        file_path = os.path.join(get_temp_storage_path(link_id), secure_filename(filename))
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
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/space/<link_id>/delete-all', methods=['POST'])
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
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/space/<link_id>/download/<filename>')
def download_file(link_id, filename):
    """Скачивание файла из временного хранилища"""
    try:
        if not is_temp_storage_valid(link_id):
            return "Временное хранилище не найдено или срок его действия истек", 404
            
        file_path = os.path.join(get_temp_storage_path(link_id), secure_filename(filename))
        if os.path.exists(file_path):
            return send_file(
                file_path,
                as_attachment=True,
                download_name=filename
            )
        return "Файл не найден", 404
    except Exception as e:
        logger.error(f"Ошибка при скачивании файла: {str(e)}")
        return "Внутренняя ошибка сервера", 500

@app.route('/space/<link_id>/download-multiple', methods=['POST'])
def download_multiple_files(link_id):
    """Скачивание нескольких файлов в архиве"""
    try:
        # Проверяем валидность хранилища
        if not is_temp_storage_valid(link_id):
            logger.error(f"Попытка скачивания из недействительного хранилища: {link_id}")
            return jsonify({'error': 'Временное хранилище не найдено или срок его действия истек'}), 404

        # Получаем список файлов для скачивания
        files = request.json.get('files', [])
        if not files:
            return jsonify({'error': 'Файлы не выбраны'}), 400

        storage_path = get_temp_storage_path(link_id)
        
        # Создаем объект в памяти для архива
        memory_file = BytesIO()
        
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for filename in files:
                file_path = os.path.join(storage_path, secure_filename(filename))
                if os.path.exists(file_path) and os.path.isfile(file_path):
                    # Добавляем файл в архив
                    zf.write(file_path, filename)
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
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/space/<link_id>/set-theme', methods=['POST'])
def set_theme(link_id):
    """Установка темы для пользователя"""
    try:
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
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/health')
def health_check():
    """Проверка работоспособности сервера"""
    return "OK", 200

async def cleanup_expired_storages_async():
    """Асинхронная очистка истекших временных хранилищ"""
    try:
        async with aiosqlite.connect(DB_PATH) as conn:
            # Получаем список истекших хранилищ
            cursor = await conn.execute('''
                SELECT link_id 
                FROM temp_links 
                WHERE datetime(expires_at) <= datetime('now')
            ''')
            
            expired_storages = await cursor.fetchall()
            
            # Удаляем файлы и записи из базы данных
            for storage in expired_storages:
                link_id = storage[0]
                storage_path = get_temp_storage_path(link_id)
                
                # Удаляем файлы хранилища
                if os.path.exists(storage_path):
                    try:
                        shutil.rmtree(storage_path)
                        logger.info(f"Удалено временное хранилище: {link_id}")
                    except Exception as e:
                        logger.error(f"Ошибка при удалении хранилища {link_id}: {str(e)}")
                
                # Удаляем запись из базы данных
                await conn.execute('DELETE FROM temp_links WHERE link_id = ?', (link_id,))
            
            await conn.commit()
            logger.info(f"Очищено {len(expired_storages)} истекших хранилищ")
            
    except Exception as e:
        logger.error(f"Ошибка при очистке хранилищ: {str(e)}")

def cleanup_expired_storages():
    """Очистка истекших временных хранилищ (синхронная обертка)"""
    return run_async(cleanup_expired_storages_async)()

def periodic_cleanup():
    """Периодическая очистка истекших хранилищ"""
    while True:
        try:
            cleanup_expired_storages()
            # Проверяем каждые 5 минут
            time.sleep(300)
        except Exception as e:
            logger.error(f"Ошибка в периодической очистке: {str(e)}")
            # В случае ошибки ждем 1 минуту перед следующей попыткой
            time.sleep(60)

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
        check_db_connection()
    except Exception as e:
        logger.error(f"Ошибка при запуске: {str(e)}")
        raise
    
    # Запускаем поток очистки
    cleanup_thread = threading.Thread(target=periodic_cleanup, daemon=True)
    cleanup_thread.start()
    logger.info("Запущен поток периодической очистки истекших хранилищ")
    
    # Запускаем сервер
    app.run(host='0.0.0.0', port=5000) 