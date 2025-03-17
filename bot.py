import os
import random
import sqlite3
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler
import sys
from datetime import datetime

# Загрузка переменных окружения
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')

# Константы для ограничений
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_LINKS = 1000  # Максимальное количество ссылок в файле
ALLOWED_EXTENSIONS = ('.txt', '.csv', '.md', '')  # Добавлено пустое расширение
DEFAULT_LINES_TO_KEEP = 10  # Количество строк по умолчанию

# Состояния разговора
CAPTCHA, MENU, SETTINGS, TECH_COMMANDS, OTHER_COMMANDS, USER_MANAGEMENT, MERGE_FILES, SET_LINES, PROCESS_FILE = range(9)

# Код администратора
ADMIN_CODE = 'YH8jRnO1Np8wVUZobJfwPIv'

# Определяем путь к директории бота
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BOT_DIR, 'bot_users.db')
TEMP_DIR = os.path.join(BOT_DIR, 'temp')
LOG_DIR = os.path.join(BOT_DIR, 'logs')

# Создаем директории если их нет
for directory in [TEMP_DIR, LOG_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)

def log_error(user_id, error_message):
    """Логирование ошибок в файл"""
    log_file = os.path.join(LOG_DIR, f'error_{datetime.now().strftime("%Y-%m-%d")}.log')
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] User {user_id}: {error_message}\n")

# Создание базы данных
def setup_database():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Создаем таблицу users если её нет
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            is_verified BOOLEAN DEFAULT FALSE,
            is_admin BOOLEAN DEFAULT FALSE,
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
    c.execute('SELECT is_admin FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else False

def verify_user(user_id, username):
    """Верификация пользователя в базе данных"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Проверяем, существует ли пользователь и является ли он админом
    c.execute('SELECT is_admin FROM users WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    is_admin_status = result[0] if result else False
    
    # Обновляем или добавляем пользователя, сохраняя статус админа
    c.execute('''
        INSERT OR REPLACE INTO users (user_id, username, is_verified, is_admin)
        VALUES (?, ?, TRUE, ?)
    ''', (user_id, username, is_admin_status))
    
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
        ['ℹ️ Помощь', '📊 Статистика'],
        ['⚙️ Настройки']  # Теперь доступно всем
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_enabled() and not is_admin(update.effective_user.id):
        await update.message.reply_text("Бот находится на техническом обслуживании. Пожалуйста, подождите.")
        return ConversationHandler.END
    
    user_id = update.effective_user.id
    
    # Проверяем команду на код администратора
    message_text = update.message.text.strip()
    if message_text.startswith('/start admin'):
        admin_code = message_text.split('admin')[-1].strip()
        if admin_code == ADMIN_CODE:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('''
                INSERT OR REPLACE INTO users (user_id, username, is_verified, is_admin)
                VALUES (?, ?, TRUE, TRUE)
            ''', (user_id, update.effective_user.username))
            conn.commit()
            conn.close()
            await update.message.reply_text("Вы успешно стали администратором!")
            await show_menu(update, context)
            return MENU
    
    # Проверяем, верифицирован ли пользователь
    if is_user_verified(user_id):
        await show_menu(update, context)
        return MENU
    
    # Генерируем капчу
    captcha_question, captcha_answer = generate_captcha()
    context.user_data['captcha_answer'] = captcha_answer
    
    await update.message.reply_text(
        f"Добро пожаловать! Для начала работы, пожалуйста, решите простой пример:\n\n{captcha_question}"
    )
    return CAPTCHA

async def check_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_enabled() and not is_admin(update.effective_user.id):
        await update.message.reply_text("Бот находится на техническом обслуживании. Пожалуйста, подождите.")
        return ConversationHandler.END
    
    user_answer = update.message.text.strip()
    correct_answer = context.user_data.get('captcha_answer')
    
    if user_answer == correct_answer:
        # Верифицируем пользователя
        verify_user(update.effective_user.id, update.effective_user.username)
        await show_menu(update, context)
        return MENU
    else:
        # Генерируем новую капчу
        captcha_question, captcha_answer = generate_captcha()
        context.user_data['captcha_answer'] = captcha_answer
        await update.message.reply_text(
            f"Неверно! Попробуйте еще раз:\n\n{captcha_question}"
        )
        return CAPTCHA

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
    
    text = update.message.text
    
    if text == '📤 Обработать файл':
        lines_to_keep = get_user_lines_to_keep(update.effective_user.id)
        await update.message.reply_text(
            f'Отправьте мне файл со ссылками (txt, csv или md), '
            f'и я верну вам последние {lines_to_keep} ссылок в формате HTML.\n'
            'Ограничения:\n'
            '- Максимальный размер файла: 10 MB\n'
            '- Максимальное количество ссылок: 1000\n'
            '- Поддерживаемые форматы: .txt, .csv, .md'
        )
    elif text == 'ℹ️ Помощь':
        lines_to_keep = get_user_lines_to_keep(update.effective_user.id)
        await update.message.reply_text(
            "Этот бот помогает обрабатывать файлы со ссылками.\n\n"
            "Как использовать:\n"
            "1. Нажмите '📤 Обработать файл'\n"
            f"2. Отправьте файл со ссылками\n"
            f"3. Получите обработанный файл с последними {lines_to_keep} ссылками\n\n"
            "Поддерживаемые форматы: .txt, .csv, .md"
        )
    elif text == '📊 Статистика':
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM users WHERE is_verified = TRUE')
        verified_users = c.fetchone()[0]
        conn.close()
        
        await update.message.reply_text(
            f"📊 Статистика бота:\n"
            f"Верифицированных пользователей: {verified_users}"
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
        current_lines = get_user_lines_to_keep(update.effective_user.id)
        await update.message.reply_text(
            f"Текущее количество строк: {current_lines}\n"
            f"Введите новое количество (от 1 до {MAX_LINKS}):"
        )
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
                [KeyboardButton(text="Объединить подписки")],
                [KeyboardButton(text="Настройка количества строк")],
                [KeyboardButton(text="Назад")]
            ],
            resize_keyboard=True
        )
        await update.message.reply_text("Другое:", reply_markup=markup)
        return OTHER_COMMANDS
    else:
        await update.message.reply_text("Пожалуйста, выберите действие из меню.")
        return SETTINGS

async def process_tech_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет доступа к этой функции.")
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
        os.execv(sys.executable, ['python'] + sys.argv)
    
    return TECH_COMMANDS

async def process_other_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет доступа к этой функции.")
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
        users = get_all_users()
        if not users:
            await update.message.reply_text("В базе данных нет пользователей.")
            return OTHER_COMMANDS
        
        user_list = "Список пользователей:\n\n"
        for user in users:
            user_list += f"ID: {user[0]}\nИмя: {user[1] or 'Не указано'}\nВерифицирован: {'Да' if user[2] else 'Нет'}\nАдмин: {'Да' if user[3] else 'Нет'}\n\n"
        
        await update.message.reply_text(user_list + "Введите ID пользователя для удаления:")
        return USER_MANAGEMENT
    elif text == "Объединить подписки":
        await update.message.reply_text("Отправьте первый файл для объединения:")
        return MERGE_FILES
    elif text == "Настройка количества строк":
        current_lines = get_user_lines_to_keep(update.effective_user.id)
        await update.message.reply_text(f"Текущее количество строк: {current_lines}\nВведите новое количество (от 1 до {MAX_LINKS}):")
        return SET_LINES
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

async def process_user_management(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(update.message.text)
        if user_id == update.effective_user.id:
            await update.message.reply_text("Вы не можете удалить себя.")
            return USER_MANAGEMENT
        
        remove_user(user_id)
        await update.message.reply_text(f"Пользователь с ID {user_id} удален.")
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите корректный ID пользователя.")
    except Exception as e:
        await update.message.reply_text(f"Ошибка при удалении пользователя: {str(e)}")
    
    await settings_command(update, context)
    return SETTINGS

async def process_merge_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        await update.message.reply_text("Пожалуйста, отправьте файл.")
        return MERGE_FILES
    
    if 'first_file' not in context.user_data:
        # Сохраняем первый файл
        file = await context.bot.get_file(update.message.document.file_id)
        context.user_data['first_file'] = await file.download_as_bytearray()
        context.user_data['first_filename'] = os.path.splitext(update.message.document.file_name)[0]
        await update.message.reply_text("Отправьте второй файл для объединения:")
        return MERGE_FILES
    else:
        # Получаем второй файл и объединяем
        file = await context.bot.get_file(update.message.document.file_id)
        second_file = await file.download_as_bytearray()
        second_filename = os.path.splitext(update.message.document.file_name)[0]
        
        try:
            # Декодируем файлы
            content1 = context.user_data['first_file'].decode('utf-8')
            content2 = second_file.decode('utf-8')
        except UnicodeDecodeError:
            try:
                content1 = context.user_data['first_file'].decode('windows-1251')
                content2 = second_file.decode('windows-1251')
            except UnicodeDecodeError:
                await update.message.reply_text("Ошибка при чтении файлов. Убедитесь, что файлы в кодировке UTF-8 или Windows-1251.")
                return MERGE_FILES
        
        # Объединяем строки и удаляем дубликаты
        lines1 = [line.strip() for line in content1.splitlines() if line.strip()]
        lines2 = [line.strip() for line in content2.splitlines() if line.strip()]
        merged_lines = list(set(lines1 + lines2))
        
        # Сортируем и берем последние N строк
        merged_lines.sort()
        lines_to_keep = get_user_lines_to_keep(update.effective_user.id)
        last_lines = merged_lines[-lines_to_keep:]
        
        # Создаем имя выходного файла на основе оригинальных имен
        output_filename = os.path.join(TEMP_DIR, f'merged_{context.user_data["first_filename"]}_{second_filename}_{update.effective_user.id}.txt')
        try:
            with open(output_filename, 'w', encoding='utf-8') as f:
                f.write('\n'.join(last_lines))
            
            with open(output_filename, 'rb') as f:
                await context.bot.send_document(
                    chat_id=update.effective_chat.id,
                    document=f,
                    filename=f'merged_{context.user_data["first_filename"]}_{second_filename}.txt',
                    caption=f"Объединено {len(merged_lines)} строк. Показаны последние {lines_to_keep}."
                )
        finally:
            if os.path.exists(output_filename):
                os.remove(output_filename)
        
        # Очищаем данные
        context.user_data.clear()
        await settings_command(update, context)
        return SETTINGS

async def process_set_lines(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        lines = int(update.message.text)
        if 1 <= lines <= MAX_LINKS:
            if is_admin(update.effective_user.id):
                # Админ меняет глобальные настройки
                set_lines_to_keep(lines)
                await update.message.reply_text(f"Глобальное количество строк установлено: {lines}")
            else:
                # Обычный пользователь меняет свои настройки
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

async def process_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            return MENU

        document = update.message.document
        
        # Проверка расширения файла
        file_name = document.file_name.lower()
        if not any(file_name.endswith(ext) for ext in ALLOWED_EXTENSIONS):
            await update.message.reply_text(
                f"Неподдерживаемый формат файла. Разрешены только: {', '.join(ALLOWED_EXTENSIONS)}"
            )
            return MENU

        # Проверка размера файла
        if document.file_size > MAX_FILE_SIZE:
            await update.message.reply_text(
                f"Файл слишком большой. Максимальный размер: {MAX_FILE_SIZE // (1024 * 1024)} MB"
            )
            return MENU

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
                return MENU

        # Получаем строки из файла
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        
        # Проверка количества строк
        if len(lines) > MAX_LINKS:
            await update.message.reply_text(
                f"Слишком много строк в файле. Максимально допустимо: {MAX_LINKS}"
            )
            return MENU

        if not lines:
            await update.message.reply_text(
                "Файл пуст или не содержит текстовых строк."
            )
            return MENU

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
                    filename=f'{original_name}.html',  # Используем оригинальное имя
                    caption=f"Найдено {len(lines)} строк. Показаны последние {lines_to_keep}."
                )
        finally:
            # Удаляем временный файл
            if os.path.exists(output_filename):
                os.remove(output_filename)
        
    except Exception as e:
        error_message = f"Произошла ошибка при обработке файла: {str(e)}"
        log_error(update.effective_user.id, error_message)
        print(f"Error for user {update.effective_user.id}: {error_message}")
        await update.message.reply_text(
            "Произошла ошибка при обработке файла. Пожалуйста, попробуйте снова."
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

def get_all_users():
    """Получение списка всех пользователей"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT user_id, username, is_verified, is_admin FROM users')
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

def main():
    # Создаем и настраиваем приложение
    application = Application.builder().token(TOKEN).build()
    
    # Создаем базу данных
    setup_database()
    
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
                MessageHandler(filters.Document.ALL, process_file),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu)
            ],
            SETTINGS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_settings)],
            TECH_COMMANDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_tech_commands)],
            OTHER_COMMANDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_other_commands)],
            USER_MANAGEMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_user_management)],
            MERGE_FILES: [MessageHandler(filters.Document.ALL, process_merge_files)],
            SET_LINES: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_set_lines)]
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
    application.run_polling()

if __name__ == '__main__':
    main() 