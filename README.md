# Telegram Бот для Обработки Подписок, Файлов и QR-кодов

Этот бот предназначен для обработки VLESS-подписок, текстовых файлов и создания QR-кодов. Бот имеет систему верификации пользователей, административную панель и различные настройки безопасности.

## Основные возможности

- ✅ Обработка текстовых файлов (txt, csv, md)
- 🔗 Объединение VLESS-подписок
- 📱 Создание различных типов QR-кодов
- 🔗 Создание временных ссылок на файлы (только для User+ и Админов)
- 🔒 Система верификации пользователей через капчу
- 👥 Административная панель управления
- 📊 Статистика использования
- 🔄 Поддержка различных кодировок (UTF-8, Windows-1251)
- ⚙️ Возможность включения/выключения бота
- 📨 Рассылка сообщений всем пользователям
- 👤 Управление пользователями (для администраторов)
- ⚙️ Персональные настройки количества сохраняемых строк

## Требования

- Python 3.7+
- python-telegram-bot==20.7
- python-dotenv==1.0.0
- aiohttp==3.9.3
- qrcode==7.4.2
- pillow==10.2.0

## Установка

1. Клонируйте репозиторий:
```bash
git clone <ваш-репозиторий>
cd <папка-проекта>
```

2. Установите зависимости:
```bash
pip install -r requirements.txt
```

3. Создайте файл `.env` в корневой директории проекта:
```env
BOT_TOKEN=ваш_токен_бота
ADMIN_CODE=ваш_код_администратора
USER_PLUS_CODE=ваш_код_привилегированного_пользователя
TEMP_LINK_DOMAIN=https://ваш-домен.com
```

## Запуск

```bash
python bot.py
```

## Структура проекта

```
├── bot.py          # Основной файл бота
├── .env            # Файл с переменными окружения
├── .env.example    # Пример файла с переменными окружения
├── requirements.txt # Зависимости проекта
├── bot_users.db    # База данных SQLite
├── temp_links.db   # База данных для временных ссылок
├── temp/           # Временные файлы
├── temp_links/     # Файлы временных ссылок
└── logs/           # Логи ошибок
```

## Использование

### Обычные пользователи

1. Начните диалог с ботом командой `/start`
2. Пройдите капчу (решите простой математический пример)
3. После верификации вам станет доступно меню:
   - 📤 Обработать файл
   - 🔄 Объединить подписки
   - 📱 Создать QR-код
   - ℹ️ Помощь
   - 📊 Статистика
   - ⚙️ Настройки

### Временные ссылки (только для User+ и Админов)

1. Нажмите кнопку "🔗 Создать временную ссылку"
2. Отправьте файл (максимальный размер: 10 MB)
3. Выберите срок хранения (1, 6, 12 или 24 часа)
4. Получите ссылку на скачивание файла

Особенности:
- Ссылка действительна только в течение выбранного срока
- Файл автоматически удаляется после истечения срока
- Ссылка доступна для скачивания через веб-интерфейс
- Поддерживаются файлы любого типа

### Типы QR-кодов

Бот поддерживает создание следующих типов QR-кодов:
- 🔗 URL-ссылки
- 📝 Текст
- 📧 Электронная почта
- 📍 Местоположение (координаты)
- 📞 Телефонный номер
- ✉️ СМС
- 📱 WhatsApp
- 📶 Wi-Fi
- 👤 Визитка (vCard)

### Уровни пользователей

1. Обычный пользователь
- Базовый функционал
- Персональные настройки
- Просмотр личной статистики

2. Привилегированный пользователь (user_plus)
```
/start user_plusКОД
```
- Расширенный функционал
- Увеличенные лимиты
- Создание временных ссылок
- Дополнительные настройки

3. Администратор
```
/start adminКОД
```
- Полный доступ ко всем функциям
- Управление пользователями
- Технические команды
- Глобальные настройки

## Административные функции

### Технические команды
- ⚙️ Включение/выключение бота
- 🔄 Перезапуск бота
- 📊 Просмотр системной статистики

### Управление пользователями
- 👥 Просмотр списка всех пользователей
- 🚫 Удаление пользователей
- 📨 Массовая рассылка сообщений

### Настройки
- ⚡ Глобальные настройки количества строк
- 🔑 Управление правами пользователей
- 🛠️ Системные параметры

## Статистика

### Индивидуальная статистика
- Количество обработанных файлов
- Количество объединенных подписок
- Количество созданных QR-кодов
- Текущие настройки

### Административная статистика
- Общее количество пользователей
- Количество верифицированных пользователей
- Статистика использования функций
- Системные показатели

## Безопасность

- 🔒 Математическая капча при первой авторизации
- 👤 Три уровня доступа с разными правами
- 🛡️ Защита от спама и флуда
- 📝 Проверка размера и формата файлов
- 📊 Логирование всех ошибок
- ⚙️ Режим технического обслуживания

## Логирование

### Формат логов
```
[YYYY-MM-DD HH:MM:SS] User ID: Сообщение об ошибке
```

### Расположение
- Директория: `logs/`
- Имя файла: `error_YYYY-MM-DD.log`
- Автоматическая ротация логов по дням

## Устранение неполадок

При возникновении проблем:

1. Проверьте логи в директории `logs/`
2. Убедитесь в корректности настроек в `.env`
3. Проверьте права доступа к директориям:
   - `temp/`
   - `temp_links/`
   - `logs/`
   - `bot_users.db`
   - `temp_links.db`
4. Убедитесь, что все зависимости установлены корректно

## Обновление

1. Обновите репозиторий:
```bash
git pull origin main
```

2. Обновите зависимости:
```bash
pip install -r requirements.txt --upgrade
```

3. Перезапустите бота:
```bash
python bot.py
```

## Лицензия

Этот проект распространяется под лицензией MIT. Подробности смотрите в файле LICENSE.