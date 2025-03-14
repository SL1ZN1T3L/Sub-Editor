import os
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# Загрузка переменных окружения
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        'Привет! Отправь мне файл со ссылками, и я верну тебе последние 10 ссылок в формате HTML.'
    )

async def process_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.document:
        await update.message.reply_text("Пожалуйста, отправьте текстовый файл.")
        return

    try:
        file = await context.bot.get_file(update.message.document.file_id)
        downloaded_file = await file.download_as_bytearray()
        content = downloaded_file.decode('utf-8')
        
        # Получаем последние 10 ссылок
        links = [line.strip() for line in content.splitlines() if line.strip()]
        last_ten_links = links[-10:]
        
        # Создаем простой текстовый файл с расширением .html
        output_filename = 'last_ten_links.html'
        with open(output_filename, 'w', encoding='utf-8') as f:
            f.write('\n'.join(last_ten_links))
        
        # Отправляем файл
        with open(output_filename, 'rb') as f:
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=f,
                filename=output_filename
            )
        
        # Удаляем временный файл
        os.remove(output_filename)
        
    except Exception as e:
        await update.message.reply_text(f"Произошла ошибка при обработке файла: {str(e)}")

def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.Document.ALL, process_file))
    application.run_polling()

if __name__ == '__main__':
    main() 