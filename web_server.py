from flask import Flask, send_file, abort, request, render_template, jsonify, redirect, url_for
import sqlite3
import os
import logging
from datetime import datetime
import traceback
import zipfile
import io
import shutil
from werkzeug.utils import secure_filename

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('web_server.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024  # 500 MB
app.config['UPLOAD_FOLDER'] = 'temp_storage'

# Пути к файлам и директориям
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'bot_users.db')
TEMP_STORAGE_DIR = os.path.join(BASE_DIR, 'temp_storage')

# Создаем необходимые директории
os.makedirs(TEMP_STORAGE_DIR, exist_ok=True)

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

def is_temp_storage_valid(link_id):
    """Проверка валидности временного хранилища"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        c.execute('''
            SELECT expires_at 
            FROM temp_links 
            WHERE link_id = ? AND expires_at > datetime('now')
        ''', (link_id,))
        return c.fetchone() is not None
    finally:
        conn.close()

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
            return "Временное хранилище не найдено или срок его действия истек", 404
        
        # Получаем список файлов
        storage_path = get_temp_storage_path(link_id)
        if not os.path.exists(storage_path):
            os.makedirs(storage_path)
        
        files = []
        for filename in os.listdir(storage_path):
            file_path = os.path.join(storage_path, filename)
            if os.path.isfile(file_path):
                files.append({
                    'name': filename,
                    'size': os.path.getsize(file_path),
                    'modified': datetime.fromtimestamp(os.path.getmtime(file_path))
                })
        
        # Сортируем файлы по дате изменения
        files.sort(key=lambda x: x['modified'], reverse=True)
        
        # Получаем статистику хранилища
        total_size = get_temp_storage_size(link_id)
        used_percent = (total_size / (500 * 1024 * 1024)) * 100
        
        return render_template('temp_storage.html',
                             link_id=link_id,
                             files=files,
                             total_size=total_size,
                             used_percent=used_percent)
        
    except Exception as e:
        logger.error(f"Ошибка при загрузке страницы: {str(e)}")
        return "Внутренняя ошибка сервера", 500

@app.route('/space/<link_id>/upload', methods=['POST'])
def upload_file(link_id):
    """Загрузка файла в временное хранилище"""
    try:
        if not is_temp_storage_valid(link_id):
            return jsonify({'error': 'Временное хранилище не найдено или срок его действия истек'}), 404
            
        if 'file' not in request.files:
            return jsonify({'error': 'Файл не выбран'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'Файл не выбран'}), 400
        
        # Проверяем лимит хранилища
        if not check_temp_storage_limit(link_id):
            return jsonify({'error': 'Превышен лимит хранилища (500 MB)'}), 400
        
        # Сохраняем файл
        filename = secure_filename(file.filename)
        storage_path = get_temp_storage_path(link_id)
        os.makedirs(storage_path, exist_ok=True)
        file_path = os.path.join(storage_path, filename)
        file.save(file_path)
        
        return jsonify({
            'success': True,
            'filename': filename,
            'size': os.path.getsize(file_path)
        })
        
    except Exception as e:
        logger.error(f"Ошибка при загрузке файла: {str(e)}")
        return jsonify({'error': 'Внутренняя ошибка сервера'}), 500

@app.route('/space/<link_id>/delete/<filename>', methods=['POST'])
def delete_file(link_id, filename):
    """Удаление файла из временного хранилища"""
    try:
        if not is_temp_storage_valid(link_id):
            return jsonify({'error': 'Временное хранилище не найдено или срок его действия истек'}), 404
            
        file_path = os.path.join(get_temp_storage_path(link_id), secure_filename(filename))
        if os.path.exists(file_path):
            os.remove(file_path)
            return jsonify({'success': True})
        return jsonify({'error': 'Файл не найден'}), 404
    except Exception as e:
        logger.error(f"Ошибка при удалении файла: {str(e)}")
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

@app.route('/health')
def health_check():
    """Проверка работоспособности сервера"""
    return "OK", 200

def cleanup_expired_storages():
    """Очистка истекших временных хранилищ"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Получаем список истекших хранилищ
        c.execute('''
            SELECT link_id 
            FROM temp_links 
            WHERE expires_at <= datetime('now')
        ''')
        
        expired_storages = c.fetchall()
        
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
            c.execute('DELETE FROM temp_links WHERE link_id = ?', (link_id,))
        
        conn.commit()
        logger.info(f"Очищено {len(expired_storages)} истекших хранилищ")
        
    except Exception as e:
        logger.error(f"Ошибка при очистке хранилищ: {str(e)}")
    finally:
        conn.close()

# Добавляем очистку при запуске сервера
cleanup_expired_storages()

if __name__ == '__main__':
    # Проверяем подключение к базе данных
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.close()
        logger.info("Подключение к базе данных успешно")
    except Exception as e:
        logger.error(f"Ошибка подключения к базе данных: {str(e)}")
        raise
    
    # Запускаем сервер
    app.run(host='0.0.0.0', port=5000) 