import os
import random
import sqlite3
import base64
import aiohttp
from dotenv import load_dotenv
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, ConversationHandler
from datetime import datetime

# Загрузка переменных окружения
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')

# Константы для ограничений
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
MAX_LINKS = 1000  # Максимальное количество ссылок в файле
ALLOWED_EXTENSIONS = ('.txt', '.csv', '.md', '')  

# Состояния разговора
CAPTCHA, MENU, SETTINGS, TECH_COMMANDS, OTHER_COMMANDS, SUBSCRIPTION = range(6)

# Код администратора
ADMIN_CODE = 'YH8jRnO1Np8wVUZobJfwPIv'

# Пути к директориям
BOT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BOT_DIR, 'bot_users.db')
TEMP_DIR = os.path.join(BOT_DIR, 'temp')
LOG_DIR = os.path.join(BOT_DIR, 'logs')

for directory in [TEMP_DIR, LOG_DIR]:
    if not os.path.exists(directory):
        os.makedirs(directory)

def log_error(user_id, error_message):
    log_file = os.path.join(LOG_DIR, f'error_{datetime.now().strftime("%Y-%m-%d")}.log')
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"[{timestamp}] User {user_id}: {error_message}\n")

def setup_database():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        is_verified BOOLEAN DEFAULT FALSE,
        is_admin BOOLEAN DEFAULT FALSE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS bot_status (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        status TEXT DEFAULT 'enabled'
    )''')
    c.execute('SELECT COUNT(*) FROM bot_status')
    if c.fetchone()[0] == 0:
        c.execute('INSERT INTO bot_status (id, status) VALUES (1, "enabled")')
    conn.commit()
    conn.close()

def is_user_verified(user_id): return sqlite3.connect(DB_PATH).execute('SELECT is_verified FROM users WHERE user_id = ?', (user_id,)).fetchone()[0] if sqlite3.connect(DB_PATH).execute('SELECT is_verified FROM users WHERE user_id = ?', (user_id,)).fetchone() else False
def is_admin(user_id): return sqlite3.connect(DB_PATH).execute('SELECT is_admin FROM users WHERE user_id = ?', (user_id,)).fetchone()[0] if sqlite3.connect(DB_PATH).execute('SELECT is_admin FROM users WHERE user_id = ?', (user_id,)).fetchone() else False
def is_bot_enabled(): return sqlite3.connect(DB_PATH).execute('SELECT status FROM bot_status WHERE id = 1').fetchone()[0] == 'enabled' if sqlite3.connect(DB_PATH).execute('SELECT status FROM bot_status WHERE id = 1').fetchone() else True

def verify_user(user_id, username):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT is_admin FROM users WHERE user_id = ?', (user_id,))
    is_admin_status = c.fetchone()[0] if c.fetchone() else False
    c.execute('INSERT OR REPLACE INTO users (user_id, username, is_verified, is_admin) VALUES (?, ?, TRUE, ?)', (user_id, username, is_admin_status))
    conn.commit()
    conn.close()

def generate_captcha():
    num1, num2 = random.randint(1, 10), random.randint(1, 10)
    operation = random.choice(['+', '-', '*'])
    answer = num1 + num2 if operation == '+' else num1 - num2 if operation == '-' else num1 * num2
    return f"{num1} {operation} {num2} = ?", str(answer)

def get_menu_keyboard(user_id):
    keyboard = [['📤 Обработать файл'], ['🔗 Объединить подписки'], ['ℹ️ Помощь', '📊 Статистика']]
    if is_admin(user_id): keyboard.append(['⚙️ Настройки'])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_enabled() and not is_admin(update.effective_user.id):
        await update.message.reply_text("Бот на техобслуживании.")
        return ConversationHandler.END
    
    user_id = update.effective_user.id
    message_text = update.message.text.strip()
    
    if message_text.startswith('/start admin') and message_text.split('admin')[-1].strip() == ADMIN_CODE:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO users (user_id, username, is_verified, is_admin) VALUES (?, ?, TRUE, TRUE)', (user_id, update.effective_user.username))
        conn.commit()
        conn.close()
        await update.message.reply_text("Вы стали администратором!")
        await show_menu(update, context)
        return MENU
    
    if is_user_verified(user_id):
        await show_menu(update, context)
        return MENU
    
    captcha_question, captcha_answer = generate_captcha()
    context.user_data['captcha_answer'] = captcha_answer
    await update.message.reply_text(f"Решите пример для верификации:\n\n{captcha_question}")
    return CAPTCHA

async def check_captcha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_enabled() and not is_admin(update.effective_user.id):
        await update.message.reply_text("Бот на техобслуживании.")
        return ConversationHandler.END
    
    if update.message.text.strip() == context.user_data.get('captcha_answer'):
        verify_user(update.effective_user.id, update.effective_user.username)
        await show_menu(update, context)
        return MENU
    else:
        captcha_question, captcha_answer = generate_captcha()
        context.user_data['captcha_answer'] = captcha_answer
        await update.message.reply_text(f"Неверно! Новый пример:\n\n{captcha_question}")
        return CAPTCHA

async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Выберите действие:", reply_markup=get_menu_keyboard(update.effective_user.id))
    return MENU

async def handle_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_enabled() and not is_admin(update.effective_user.id):
        await update.message.reply_text("Бот на техобслуживании.")
        return MENU
    
    text = update.message.text
    if text == '📤 Обработать файл':
        await update.message.reply_text('Отправьте файл со ссылками (txt, csv или md).\nОграничения:\n- Макс. размер: 10 MB\n- Макс. ссылок: 1000')
    elif text == '🔗 Объединить подписки':
        await update.message.reply_text('Отправьте подписки в Base64 (каждая с новой строки, минимум 2).\nПример:\nc3Vic2NyaXB0aW9uMQ==\nc3Vic2NyaXB0aW9uMg==')
        return SUBSCRIPTION
    elif text == 'ℹ️ Помощь':
        await update.message.reply_text("Бот обрабатывает файлы со ссылками и объединяет подписки в Base64.\n\nИспользование:\n1. '📤 Обработать файл' - для обработки файлов\n2. '🔗 Объединить подписки' - для объединения подписок")
    elif text == '📊 Статистика':
        verified_users = sqlite3.connect(DB_PATH).execute('SELECT COUNT(*) FROM users WHERE is_verified = TRUE').fetchone()[0]
        await update.message.reply_text(f"📊 Статистика:\nВерифицировано пользователей: {verified_users}")
    elif text == '⚙️ Настройки' and is_admin(update.effective_user.id):
        return await settings_command(update, context)
    return MENU

async def handle_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_enabled() and not is_admin(update.effective_user.id):
        await update.message.reply_text("Бот на техобслуживании.")
        return MENU
    
    subscriptions = update.message.text.strip().split('\n')
    if len(subscriptions) < 2:
        await update.message.reply_text("Отправьте минимум 2 подписки.")
        return SUBSCRIPTION
    
    async def decode_base64(sub): return base64.b64decode(sub.strip()).decode('utf-8') if sub.strip() else None
    async def fetch_content(url):
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url) as resp:
                    return await resp.text() if resp.status == 200 else f"Ошибка HTTP {resp.status}"
            except Exception as e:
                return f"Ошибка: {str(e)}"
    
    decoded_urls = [await decode_base64(sub) for sub in subscriptions]
    if None in decoded_urls or any("Ошибка" in url for url in decoded_urls if url):
        await update.message.reply_text("Ошибка декодирования одной из подписок.")
        return SUBSCRIPTION
    
    contents = await asyncio.gather(*[fetch_content(url) for url in decoded_urls])
    if any("Ошибка" in content for content in contents):
        await update.message.reply_text("Ошибка загрузки одной из подписок.")
        return SUBSCRIPTION
    
    combined_config = "\n".join(contents)
    combined_base64 = base64.b64encode(combined_config.encode('utf-8')).decode('utf-8')
    context.user_data['last_combined'] = combined_base64
    
    await update.message.reply_text(f"Подписки объединены!\nРезультат:\n```\n{combined_base64}\n```")
    await show_menu(update, context)
    return MENU

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[KeyboardButton("Технические команды")], [KeyboardButton("Другое")], [KeyboardButton("Назад")]] if is_admin(update.effective_user.id) else [[KeyboardButton("Назад")]]
    await update.message.reply_text("Настройки:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True))
    return SETTINGS

async def process_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "Назад": return await show_menu(update, context), MENU
    elif text == "Технические команды" and is_admin(update.effective_user.id):
        markup = ReplyKeyboardMarkup([[KeyboardButton("Включить бота")], [KeyboardButton("Выключить бота")], [KeyboardButton("Перезапустить бота")], [KeyboardButton("Назад")]], resize_keyboard=True)
        await update.message.reply_text("Технические команды:", reply_markup=markup)
        return TECH_COMMANDS
    elif text == "Другое" and is_admin(update.effective_user.id):
        markup = ReplyKeyboardMarkup([[KeyboardButton("Написать всем пользователям")], [KeyboardButton("Назад")]], resize_keyboard=True)
        await update.message.reply_text("Другое:", reply_markup=markup)
        return OTHER_COMMANDS
    return SETTINGS

async def process_tech_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return await show_menu(update, context), MENU
    text = update.message.text
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    if text == "Назад": return await settings_command(update, context), SETTINGS
    elif text == "Включить бота": c.execute("UPDATE bot_status SET status='enabled' WHERE id=1"); await update.message.reply_text("Бот включен.")
    elif text == "Выключить бота": c.execute("UPDATE bot_status SET status='disabled' WHERE id=1"); await update.message.reply_text("Бот выключен.")
    elif text == "Перезапустить бота": await update.message.reply_text("Перезапуск..."); os.execv(sys.executable, ['python'] + sys.argv)
    conn.commit(); conn.close()
    return TECH_COMMANDS

async def process_other_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return await show_menu(update, context), MENU
    text = update.message.text
    if text == "Назад": return await settings_command(update, context), SETTINGS
    elif text == "Написать всем пользователям": await update.message.reply_text("Введите сообщение для рассылки:"); return OTHER_COMMANDS
    else:
        conn = sqlite3.connect(DB_PATH)
        users = conn.execute('SELECT user_id FROM users WHERE is_verified = TRUE').fetchall()
        conn.close()
        success_count = 0
        for user_id in users:
            try: await context.bot.send_message(chat_id=user_id[0], text=text); success_count += 1
            except Exception as e: print(f"Ошибка отправки {user_id[0]}: {e}")
        await update.message.reply_text(f"Отправлено {success_count} пользователям.")
        return await settings_command(update, context), SETTINGS

async def process_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_bot_enabled() and not is_admin(update.effective_user.id) or not is_user_verified(update.effective_user.id):
        await update.message.reply_text("Бот на техобслуживании или требуется верификация (/start).")
        return MENU
    
    if not update.message.document:
        await update.message.reply_text("Отправьте текстовый файл.")
        return MENU
    
    document = update.message.document
    if not any(document.file_name.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS):
        await update.message.reply_text(f"Поддерживаются только: {', '.join(ALLOWED_EXTENSIONS)}")
        return MENU
    
    if document.file_size > MAX_FILE_SIZE:
        await update.message.reply_text(f"Макс. размер: {MAX_FILE_SIZE // (1024 * 1024)} MB")
        return MENU
    
    file = await context.bot.get_file(document.file_id)
    content = await file.download_as_bytearray()
    try: content = content.decode('utf-8')
    except: content = content.decode('windows-1251')
    
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if len(lines) > MAX_LINKS or not lines:
        await update.message.reply_text(f"Лимит: {MAX_LINKS} строк" if len(lines) > MAX_LINKS else "Файл пуст.")
        return MENU
    
    last_ten_lines = lines[-10:]
    output_filename = os.path.join(TEMP_DIR, f'lines_{update.effective_user.id}.html')
    try:
        with open(output_filename, 'w', encoding='utf-8') as f: f.write('\n'.join(last_ten_lines))
        with open(output_filename, 'rb') as f:
            await context.bot.send_document(chat_id=update.effective_chat.id, document=f, filename='last_ten_lines.html', caption=f"Найдено {len(lines)} строк. Последние 10.")
    finally:
        if os.path.exists(output_filename): os.remove(output_filename)
    return MENU

def main():
    application = Application.builder().token(TOKEN).build()
    setup_database()
    
    async def restore_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if is_user_verified(update.effective_user.id) and (is_bot_enabled() or is_admin(update.effective_user.id)):
            await show_menu(update, context)
            return MENU
        await update.message.reply_text("Бот на техобслуживании или используйте /start.")
        return ConversationHandler.END
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start), MessageHandler(filters.TEXT & ~filters.COMMAND, restore_menu), MessageHandler(filters.Document.ALL, restore_menu)],
        states={
            CAPTCHA: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_captcha)],
            MENU: [
                MessageHandler(filters.Document.ALL, process_file),
                MessageHandler(filters.Regex('^(📤 Обработать файл|🔗 Объединить подписки|ℹ️ Помощь|📊 Статистика|⚙️ Настройки)$'), handle_menu)
            ],
            SUBSCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_subscription)],
            SETTINGS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_settings)],
            TECH_COMMANDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_tech_commands)],
            OTHER_COMMANDS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_other_commands)]
        },
        fallbacks=[CommandHandler('start', start), MessageHandler(filters.TEXT & ~filters.COMMAND, restore_menu), MessageHandler(filters.Document.ALL, restore_menu)]
    )
    
    application.add_handler(conv_handler)
    print(f"Бот запущен!\nБД: {DB_PATH}\nTemp: {TEMP_DIR}\nЛоги: {LOG_DIR}")
    application.run_polling()

if __name__ == '__main__':
    main()