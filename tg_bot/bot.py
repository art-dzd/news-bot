import asyncio
import os
import requests
from telegram import Bot, BotCommand
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    CallbackContext,
    ContextTypes,
    filters
)

from utils.logger import logger
from utils.models import NewsItem
from config import TELEGRAM_BOT_TOKEN, USER_ID
from sources.mosru import get_all_mosru_news
from sources.dzen import fetch_dzen_news
from storage.s3 import s3_storage

class NewsBot:
    """
    Telegram-бот для отправки новостей.
    Поддерживает команды для ручного запуска парсера и другие функции.
    """
    
    def __init__(self):
        """Инициализация бота."""
        if not TELEGRAM_BOT_TOKEN:
            logger.error("Telegram bot token not found")
            raise ValueError("TELEGRAM_BOT_TOKEN environment variable is not set")
            
        self.bot = Bot(token=TELEGRAM_BOT_TOKEN)
        self.application = None
        self.authorized_user_id = USER_ID
    
    async def setup(self):
        """
        Асинхронная настройка бота и обработчиков команд.
        """
        # Создаем приложение
        self.application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Добавляем обработчики команд
        self.application.add_handler(CommandHandler("start", self.cmd_start))
        self.application.add_handler(CommandHandler("help", self.cmd_help))
        self.application.add_handler(CommandHandler("fetch", self.cmd_fetch))
        self.application.add_handler(CommandHandler("stats", self.cmd_stats))
        self.application.add_handler(CommandHandler("run", self.cmd_run))
        self.application.add_handler(CommandHandler("stop", self.cmd_stop))
        self.application.add_handler(CommandHandler("restart", self.cmd_restart))
        self.application.add_handler(CommandHandler("logs", self.cmd_logs))
        self.application.add_handler(CommandHandler("logsfile", self.cmd_logsfile))
        
        # Добавляем обработчик обычных сообщений
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_message))
        
        # Устанавливаем команды для отображения в меню
        await self.setup_commands()
        
        logger.info("Telegram bot setup completed")
    
    async def setup_commands(self):
        """Настройка команд бота для отображения в меню."""
        commands = [
            BotCommand("start", "Запустить бота"),
            BotCommand("help", "Получить справку"),
            BotCommand("fetch", "Запустить поиск новостей вручную"),
            BotCommand("stats", "Показать статистику"),
            BotCommand("run", "Запустить парсер в непрерывном режиме"),
            BotCommand("stop", "Остановить парсер"),
            BotCommand("restart", "Перезапустить парсер"),
            BotCommand("logs", "Показать последние строки логов парсера"),
            BotCommand("logsfile", "Отправить файл с логами")
        ]
        
        try:
            # Устанавливаем команды через API телеграма
            await self.bot.set_my_commands(commands)
            logger.info("Bot commands set successfully")
        except TelegramError as e:
            logger.error(f"Failed to set bot commands: {e}")
            try:
                # Пробуем прямой вызов API если стандартный метод не сработал
                token = TELEGRAM_BOT_TOKEN
                url = f"https://api.telegram.org/bot{token}/setMyCommands"
                cmd_json = {"commands": [{"command": cmd.command, "description": cmd.description} for cmd in commands]}
                response = requests.post(url, json=cmd_json)
                if response.status_code == 200:
                    logger.info("Bot commands set successfully via direct API call")
                else:
                    logger.error(f"Failed to set bot commands via direct API: {response.text}")
            except Exception as e:
                logger.error(f"Failed to set bot commands via direct API: {e}")
    
    async def run(self):
        """Запуск бота."""
        await self.setup()
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        
        logger.info("Telegram bot started")
    
    async def stop(self):
        """Остановка бота."""
        if self.application:
            await self.application.stop()
            await self.application.shutdown()
            logger.info("Telegram bot stopped")
    
    async def send_message(self, chat_id, text, parse_mode=ParseMode.HTML):
        """
        Отправка сообщения пользователю.
        
        Args:
            chat_id (str): ID чата для отправки
            text (str): Текст сообщения
            parse_mode: Режим форматирования текста
            
        Returns:
            bool: True если отправка успешна, иначе False
        """
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                disable_web_page_preview=False
            )
            return True
        except TelegramError as e:
            logger.error(f"Error sending message to {chat_id}: {e}")
            return False
    
    async def send_news(self, news_items):
        """
        Отправка новостей указанному пользователю.
        Args:
            news_items (list): Список объектов NewsItem, MosruHistoryItem или DzenHistoryItem для отправки
        Returns:
            int: Количество успешно отправленных новостей
        """
        if not self.authorized_user_id:
            logger.warning("No authorized user ID configured, skipping news sending")
            return 0
        sent_count = 0
        for news in news_items:
            # Универсальный формат: если есть to_telegram_message — используем его
            if hasattr(news, 'to_telegram_message'):
                message = news.to_telegram_message()
            else:
                message = news.to_telegram_message() if isinstance(news, NewsItem) else str(news)
            success = await self.send_message(
                chat_id=self.authorized_user_id,
                text=message
            )
            if success:
                sent_count += 1
                await asyncio.sleep(0.5)
        return sent_count
    
    async def is_authorized(self, user_id):
        """
        Проверка, авторизован ли пользователь для использования бота.
        
        Args:
            user_id (str): ID пользователя
            
        Returns:
            bool: True если пользователь авторизован, иначе False
        """
        return str(user_id) == str(self.authorized_user_id)
    
    async def cmd_start(self, update, context):
        """Обработчик команды /start."""
        user_id = update.effective_user.id
        
        if not await self.is_authorized(user_id):
            await update.message.reply_text("У вас нет доступа к этому боту.")
            logger.warning(f"Unauthorized access attempt from user {user_id}")
            return
        
        await update.message.reply_text(
            "Привет! Я буду присылать тебе новости по темам здравоохранения из "
            "официальных источников и Яндекс.Дзена.\n\n"
            "Используй /help для получения списка команд."
        )
    
    async def cmd_help(self, update, context):
        """Обработчик команды /help."""
        user_id = update.effective_user.id
        
        if not await self.is_authorized(user_id):
            await update.message.reply_text("У вас нет доступа к этому боту.")
            return
        
        help_text = (
            "Доступные команды:\n\n"
            "/fetch - Запустить поиск новостей вручную\n"
            "/stats - Показать статистику по собранным новостям\n"
            "/run - Запустить парсер в непрерывном режиме\n"
            "/stop - Остановить парсер\n"
            "/restart - Перезапустить парсер\n"
            "/logs - Показать последние строки логов\n"
            "/logsfile - Отправить файл с логами\n"
            "/help - Показать это сообщение\n\n"
            "Бот может работать в двух режимах:\n"
            "1. Ручной (/fetch) - однократная проверка новостей\n"
            "2. Непрерывный (/run) - проверка новостей по умному расписанию"
        )
        
        await update.message.reply_text(help_text)
    
    async def cmd_fetch(self, update, context):
        """Обработчик команды /fetch для ручного запуска парсера."""
        user_id = update.effective_user.id
        if not await self.is_authorized(user_id):
            await update.message.reply_text("У вас нет доступа к этому боту.")
            return
        await update.message.reply_text("Запускаю поиск новостей... Это может занять некоторое время.")
        try:
            from scheduler import fetch_and_send_news
            report = await fetch_and_send_news(report_mode=True)
            if report and 'error' in report:
                await update.message.reply_text(f"Произошла ошибка при поиске новостей: {report['error']}")
                return
            msg = []
            if report['mosru_new'] == 0:
                msg.append("Новых новостей с mos.ru не найдено.")
            else:
                msg.append(f"Новых новостей с mos.ru: {report['mosru_new']}")
            if report['dzen_new'] == 0:
                msg.append("Новых новостей с Яндекс.Дзен не найдено.")
            else:
                msg.append(f"Новых новостей с Яндекс.Дзен: {report['dzen_new']}")
            msg.append(f"Ссылок mos.ru проверено: {report['mosru_found']}")
            msg.append(f"Ссылок Дзен проверено: {report['dzen_found']}")
            await update.message.reply_text("\n".join(msg))
        except Exception as e:
            logger.error(f"Ошибка при поиске новостей: {e}")
            await update.message.reply_text("Произошла ошибка при поиске новостей.")
    
    async def cmd_stats(self, update, context):
        """Обработчик команды /stats для показа статистики."""
        user_id = update.effective_user.id
        
        if not await self.is_authorized(user_id):
            await update.message.reply_text("У вас нет доступа к этому боту.")
            return
        
        # Получаем историю отправленных новостей
        mosru_history = s3_storage.load_mosru_history()
        dzen_history = s3_storage.load_dzen_history()
        
        # Получаем информацию о проанализированных URL
        analyzed_urls_count = len(s3_storage.analyzed_urls) if hasattr(s3_storage, 'analyzed_urls') else 0
        
        # Группируем новости Дзена по типу совпадения
        dzen_by_match = {}
        for item in dzen_history:
            if isinstance(item, dict):
                match_type = item.get('match_type', 'unknown')
            else:
                match_type = getattr(item, 'match_type', 'unknown')
            dzen_by_match[match_type] = dzen_by_match.get(match_type, 0) + 1
        
        # Статистика по типам совпадений
        by_sbert = dzen_by_match.get('sbert', 0)
        by_keywords = dzen_by_match.get('keywords', 0)
        
        stats_text = (
            f"📊 Статистика новостей:\n\n"
            f"Всего отправлено с mos.ru: {len(mosru_history)} новостей\n"
            f"Всего отправлено с Дзена: {len(dzen_history)} новостей\n"
            f" - По сходству с mos.ru: {by_sbert}\n"
            f" - По ключевым словам: {by_keywords}\n\n"
            f"Проанализировано URL Дзен: {analyzed_urls_count}"
        )
        await update.message.reply_text(stats_text)
    
    async def cmd_run(self, update, context):
        """Обработчик команды /run для запуска парсера в непрерывном режиме."""
        user_id = update.effective_user.id
        if not await self.is_authorized(user_id):
            await update.message.reply_text("У вас нет доступа к этому боту.")
            return
        try:
            # Импортируем функцию запуска процесса парсера
            from container_entry import start_parser_process
            if start_parser_process():
                await update.message.reply_text(
                    "✅ Парсер запущен в непрерывном режиме.\n"
                    "Теперь он будет автоматически проверять новости по расписанию."
                )
            else:
                await update.message.reply_text("❌ Ошибка при запуске парсера.")
        except Exception as e:
            logger.error(f"Ошибка при запуске парсера: {e}")
            await update.message.reply_text(f"❌ Ошибка при запуске парсера: {e}")
    
    async def cmd_stop(self, update, context):
        """Обработчик команды /stop для остановки парсера."""
        user_id = update.effective_user.id
        if not await self.is_authorized(user_id):
            await update.message.reply_text("У вас нет доступа к этому боту.")
            return
        try:
            # Импортируем функцию остановки процесса парсера
            from container_entry import stop_parser_process
            if stop_parser_process():
                await update.message.reply_text(
                    "✅ Парсер остановлен.\n"
                    "Автоматическая проверка новостей прекращена."
                )
            else:
                await update.message.reply_text("❌ Ошибка при остановке парсера.")
        except Exception as e:
            logger.error(f"Ошибка при остановке парсера: {e}")
            await update.message.reply_text(f"❌ Ошибка при остановке парсера: {e}")
    
    async def cmd_restart(self, update, context):
        """Обработчик команды /restart для перезапуска парсера."""
        user_id = update.effective_user.id
        if not await self.is_authorized(user_id):
            await update.message.reply_text("У вас нет доступа к этому боту.")
            return
        try:
            # Импортируем функции управления процессом парсера
            from container_entry import stop_parser_process, start_parser_process
            
            # Останавливаем парсер
            stop_parser_process()
            
            # Даем немного времени на завершение процесса
            await asyncio.sleep(1)
            
            # Запускаем парсер заново
            if start_parser_process():
                await update.message.reply_text(
                    "✅ Парсер успешно перезапущен.\n"
                    "Автоматическая проверка новостей продолжается."
                )
            else:
                await update.message.reply_text("❌ Ошибка при перезапуске парсера.")
        except Exception as e:
            logger.error(f"Ошибка при перезапуске парсера: {e}")
            await update.message.reply_text(f"❌ Ошибка при перезапуске парсера: {e}")
    
    async def cmd_logs(self, update, context):
        """Обработчик команды /logs для просмотра последних логов парсера."""
        user_id = update.effective_user.id
        if not await self.is_authorized(user_id):
            await update.message.reply_text("У вас нет доступа к этому боту.")
            return
        
        log_file_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'storage/parser_output.log')
        if not os.path.exists(log_file_path):
            await update.message.reply_text("Файл логов не найден.")
            return
        
        # Максимальный размер сообщения в Telegram (4096 символов)
        max_message_size = 4000
        
        try:
            # Получаем последние строки логов
            with open(log_file_path, 'r', encoding='utf-8') as file:
                logs = file.readlines()
            
            # Если логов много, берем только последние
            if len(logs) > 15:
                logs = logs[-15:]
            
            # Формируем сообщение с логами, обрезая до лимита
            log_message = "Последние записи логов:\n\n"
            for line in logs:
                # Если добавление текущей строки превысит лимит
                if len(log_message) + len(line) > max_message_size:
                    log_message += "...\n(логи обрезаны из-за ограничения размера сообщения)"
                    break
                log_message += line
            
            await update.message.reply_text(log_message)
        except Exception as e:
            logger.error(f"Ошибка при чтении логов: {e}")
            await update.message.reply_text(f"Ошибка при чтении логов: {e}")
            
    async def cmd_logsfile(self, update, context):
        """Обработчик команды /logsfile - отправляет файл с логами."""
        user_id = update.effective_user.id
        if not await self.is_authorized(user_id):
            await update.message.reply_text("У вас нет доступа к этому боту.")
            return
        
        log_file_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'storage/parser_output.log')
        if not os.path.exists(log_file_path):
            await update.message.reply_text("⚠️ Файл логов не найден")
            return
        
        try:
            # Отправляем файл с логами
            await update.message.reply_text("📋 Отправляю файл с логами...")
            with open(log_file_path, 'rb') as f:
                await update.message.reply_document(document=f, filename="parser_logs.txt")
        except Exception as e:
            logger.error(f"Ошибка при отправке файла с логами: {e}")
            await update.message.reply_text(f"❌ Ошибка при отправке файла с логами: {e}")
    
    async def handle_message(self, update, context):
        """Обработчик обычных текстовых сообщений."""
        user_id = update.effective_user.id
        
        if not await self.is_authorized(user_id):
            await update.message.reply_text("У вас нет доступа к этому боту.")
            return
        
        await update.message.reply_text(
            "Пожалуйста, используйте команды для взаимодействия с ботом.\n"
            "Отправьте /help для получения списка команд."
        ) 