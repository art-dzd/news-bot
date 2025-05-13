import asyncio
import time
import signal
import random
from datetime import datetime, timedelta
import os

from utils.logger import logger
from utils.models import NewsItem, MosruHistoryItem, DzenHistoryItem
from utils.similarity import cleanup_cache
from sources.mosru import get_all_mosru_news
from sources.dzen import fetch_dzen_news
from storage.s3 import s3_storage
from tg_bot.bot import NewsBot
from config import TIMEZONE

shutdown_event = asyncio.Event()
running_event = asyncio.Event()  # Новый флаг для контроля работы парсера
news_bot = None

# Переменная для отслеживания времени последней очистки кэша
last_cache_cleanup = None

def handle_shutdown(*args):
    logger.info("Shutdown signal received. Stopping application...")
    shutdown_event.set()

# Функция для определения интервала проверки в зависимости от времени суток
def get_check_interval():
    """
    Возвращает интервал проверки новостей в зависимости от времени суток:
    - с 6:00 до 22:00: 4-6 минут (рандомно)
    - с 22:00 до 6:00: 30-60 минут (рандомно)
    """
    now = datetime.now(TIMEZONE)
    hour = now.hour
    
    # Дневное время (6:00 - 22:00)
    if 6 <= hour < 22:
        # Рандомный интервал от 4 до 6 минут (в секундах)
        return random.randint(4 * 60, 6 * 60)
    else:
        # Ночное время (22:00 - 6:00)
        # Рандомный интервал от 30 до 60 минут (в секундах)
        return random.randint(30 * 60, 60 * 60)

async def cleanup_embeddings_cache():
    """
    Очистка кэша эмбеддингов для экономии памяти.
    Удаляет эмбеддинги старше 3 дней и сохраняет только те, которые используются в последних новостях.
    """
    global last_cache_cleanup
    now = datetime.now(TIMEZONE)
    
    # Выполняем очистку кэша при каждом запуске, но не чаще чем раз в сутки
    if last_cache_cleanup is None or (now - last_cache_cleanup).total_seconds() > 24 * 3600:
        logger.info("Очистка кэша эмбеддингов (удаление записей старше 3 дней)")
        
        try:
            # Загружаем URL из истории, которые нужно сохранить в кэше
            mosru_history = s3_storage.load_mosru_history()
            dzen_history = s3_storage.load_dzen_history()
            
            # Собираем все URL, которые нужно сохранить в кэше
            keep_urls = set()
            for item in mosru_history:
                if isinstance(item, dict):
                    keep_urls.add(item.get('url', ''))
                else:
                    keep_urls.add(getattr(item, 'url', ''))
            
            for item in dzen_history:
                if isinstance(item, dict):
                    keep_urls.add(item.get('url', ''))
                    keep_urls.add(item.get('mosru_source_url', ''))
                else:
                    keep_urls.add(getattr(item, 'url', ''))
                    keep_urls.add(getattr(item, 'mosru_source_url', ''))
            
            # Очищаем кэш, сохраняя только нужные URL и записи не старше 3 дней
            keep_urls = {url for url in keep_urls if url}  # Удаляем пустые URL
            stats = cleanup_cache(keep_urls, max_age_days=3)  # Устанавливаем максимальный возраст в 3 дня
            
            # Обновляем время последней очистки
            last_cache_cleanup = now
            logger.info(f"Очистка кэша завершена: Дзен: {stats['dzen_cleared']} удалено, {stats['dzen_after']} осталось; " +
                       f"Mos.ru: {stats['mosru_cleared']} удалено, {stats['mosru_after']} осталось")
        except Exception as e:
            logger.error(f"Ошибка при очистке кэша эмбеддингов: {e}")

async def fetch_and_send_news(report_mode=False):
    """
    Основная функция получения и отправки новостей.
    Получает новости с mos.ru и Яндекс Дзена, фильтрует и отправляет в Telegram.
    report_mode: если True — формировать расширенный отчёт (для ручного запуска)
    """
    logger.info("Starting news fetch process")
    try:
        # Очистка кэша эмбеддингов, если необходимо
        await cleanup_embeddings_cache()
        
        global news_bot
        if news_bot is None:
            news_bot = NewsBot()
        # Загружаем истории
        mosru_history = [MosruHistoryItem(**item) for item in s3_storage.load_mosru_history()]
        
        # Загружаем историю Дзена, добавляя отсутствующие поля при необходимости
        dzen_raw_history = s3_storage.load_dzen_history()
        now_iso = datetime.now(TIMEZONE).isoformat()
        dzen_history = []
        for item in dzen_raw_history:
            # Проверяем наличие обязательного поля added_at и добавляем его, если отсутствует
            if 'added_at' not in item:
                item['added_at'] = now_iso
            
            # Добавляем остальные отсутствующие поля со значениями по умолчанию
            if 'mosru_source_url' not in item:
                item['mosru_source_url'] = None
            if 'mosru_title' not in item:
                item['mosru_title'] = None  
            if 'mosru_snippet' not in item:
                item['mosru_snippet'] = None
                
            # Создаем объект DzenHistoryItem
            dzen_history.append(DzenHistoryItem(**item))
        
        mosru_urls = set(item.url for item in mosru_history)
        dzen_urls = set(item.url for item in dzen_history)
        
        # Получаем новости с mos.ru
        logger.info("Fetching news from mos.ru")
        mosru_news, mosru_new_items = await get_all_mosru_news()
        logger.info(f"Found {len(mosru_news)} news items from mos.ru")
        # Для отправки — только те, которых нет в истории
        new_mosru_news = [news for news in mosru_news if news.url not in mosru_urls]
        logger.info(f"Found {len(new_mosru_news)} new news items from mos.ru")
        logger.debug(f"New mos.ru news URLs: {[n.url for n in new_mosru_news]}")
        # Добавляем только уникальные в историю
        new_mosru_history = [item for item in mosru_new_items if item.url not in mosru_urls]
        mosru_history.extend(new_mosru_history)
        mosru_urls.update(item.url for item in new_mosru_history)
        # Получаем новости с Дзена
        logger.info("Fetching news from Yandex Dzen")
        dzen_history_urls = set(item.url for item in dzen_history)
        dzen_news, dzen_new_items = await fetch_dzen_news(mosru_news, mosru_history, dzen_history_urls)
        logger.info(f"Found {len(dzen_news)} news items from Dzen after filtering")
        # Для отправки — только те, которых нет в истории
        new_dzen_news = [news for news in dzen_news if news.url not in dzen_urls]
        logger.info(f"Found {len(new_dzen_news)} new news items from Dzen after filtering")
        logger.debug(f"New Dzen news URLs: {[n.url for n in new_dzen_news]}")
        # Обновляем in_dzen в mosru_history, если совпала новость
        dzen_mosru_urls = set(item.mosru_source_url for item in dzen_new_items if getattr(item, 'mosru_source_url', None))
        mosru_updated = False
        for item in mosru_history:
            if item.url in dzen_mosru_urls:
                if not item.in_dzen:  # Проверяем, чтобы не делать лишних изменений
                    item.in_dzen = True
                    mosru_updated = True
                    logger.info(f"Обновлен флаг in_dzen для новости mos.ru: {item.title}")
        # Добавляем только уникальные в историю
        new_dzen_history = [item for item in dzen_new_items if item.url not in dzen_urls]
        dzen_history.extend(new_dzen_history)
        dzen_urls.update(item.url for item in new_dzen_history)
        # Сохраняем обновлённые истории только если есть новые элементы
        if new_mosru_history or mosru_updated:
            s3_storage.save_mosru_history([item.__dict__ for item in mosru_history])
        if new_dzen_history:
            s3_storage.save_dzen_history([item.__dict__ for item in dzen_history])
        # Отправляем только новые уникальные новости
        total_sent = 0
        if new_mosru_news:
            logger.info(f"Sending {len(new_mosru_news)} news from mos.ru")
            sent_count = await news_bot.send_news(new_mosru_news)
            logger.info(f"Successfully sent {sent_count} news from mos.ru")
            total_sent += sent_count
        if new_dzen_news:
            logger.info(f"Sending {len(new_dzen_news)} news from Dzen")
            sent_count = await news_bot.send_news(new_dzen_news)
            logger.info(f"Successfully sent {sent_count} news from Dzen")
            total_sent += sent_count
        logger.info(f"News fetch process completed. Total sent: {total_sent}")
        # Если нужен отчёт — вернуть данные для формирования отчёта в боте
        if report_mode:
            return {
                "mosru_found": len(mosru_news),
                "mosru_new": len(new_mosru_news),
                "dzen_found": len(dzen_news),
                "dzen_new": len(new_dzen_news),
                "sent": total_sent
            }
    except Exception as e:
        logger.error(f"Error in fetch_and_send_news: {e}")
        if report_mode:
            return {"error": str(e)}

async def run_scheduler():
    logger.info("Scheduler triggered (single run for Cloud Function)")
    try:
        await fetch_and_send_news(report_mode=False)
    except Exception as e:
        logger.error(f"Error in scheduler: {e}")

async def run_continuous_scheduler():
    """Запускает парсер новостей в режиме постоянной работы с умным расписанием"""
    logger.info("Запуск парсера в непрерывном режиме с умным расписанием")
    
    while not shutdown_event.is_set():
        if running_event.is_set():
            try:
                # Запускаем парсер
                logger.info("Выполняем парсинг новостей...")
                await fetch_and_send_news(report_mode=False)
                
                # Определяем следующий интервал проверки
                interval = get_check_interval()
                now = datetime.now(TIMEZONE)
                next_time = now + timedelta(seconds=interval)
                logger.info(f"Следующая проверка новостей в {next_time.strftime('%H:%M:%S')} (через {interval//60} мин {interval%60} сек)")
                
                # Ожидаем до следующей проверки, но можем прерваться при shutdown
                try:
                    await asyncio.wait_for(
                        asyncio.gather(shutdown_event.wait(), running_event.wait()), 
                        timeout=interval
                    )
                except asyncio.TimeoutError:
                    # Нормальное истечение таймаута
                    pass
                
                # Если событие остановлено, выходим из цикла
                if not running_event.is_set() or shutdown_event.is_set():
                    logger.info("Парсер остановлен по команде")
                    break
                    
            except Exception as e:
                logger.error(f"Ошибка при выполнении парсера: {e}")
                # В случае ошибки делаем короткую паузу и продолжаем
                await asyncio.sleep(60)
        else:
            # Если режим остановлен, ждем сигнал запуска или остановки
            logger.info("Парсер находится в режиме ожидания запуска...")
            await asyncio.wait([running_event.wait(), shutdown_event.wait()], return_when=asyncio.FIRST_COMPLETED)
            
            if shutdown_event.is_set():
                break
            
            if running_event.is_set():
                logger.info("Парсер запущен - начинаю работу")

    logger.info("Непрерывный режим парсера остановлен")

async def run_bot():
    try:
        global news_bot
        if news_bot is None:
            news_bot = NewsBot()
        await news_bot.run()
        logger.info("Bot started successfully")
    except Exception as e:
        logger.error(f"Error starting bot: {e}")

async def shutdown():
    global news_bot
    logger.info("Shutting down application...")
    if news_bot is not None:
        await news_bot.stop()
    logger.info("Shutdown complete.")

async def main():
    logger.info("Starting application")
    # Устанавливаем обработчики сигналов
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_shutdown)
        except NotImplementedError:
            # Windows
            pass

    # Запускаем Telegram-бот, если env WITHOUT_BOT не установлен
    if os.environ.get("WITHOUT_BOT") != "1":
        bot_task = asyncio.create_task(run_bot())
        logger.info("Telegram-бот запущен")
    else:
        logger.info("WITHOUT_BOT установлен, Telegram-бот не запускается")

    # Запускаем планировщик парсера в непрерывном режиме
    scheduler_task = asyncio.create_task(run_continuous_scheduler())

    # Ожидаем сигнал завершения
    await shutdown_event.wait()

    logger.info("Shutdown event triggered. Cancelling tasks...")
    # Отменяем задачи
    if os.environ.get("WITHOUT_BOT") != "1":
        bot_task.cancel()
    scheduler_task.cancel()
    await shutdown()

# Функции для управления парсером через команды бота
def start_parser():
    """Запускает непрерывный парсер"""
    logger.info("Установка флага running_event в True")
    running_event.set()
    logger.info("Парсер запущен в непрерывном режиме")
    return True

def stop_parser():
    """Останавливает непрерывный парсер"""
    running_event.clear()
    logger.info("Парсер остановлен")
    return True

def is_parser_running():
    """Проверяет, запущен ли непрерывный парсер"""
    return running_event.is_set()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application stopped by user")
    except Exception as e:
        logger.error(f"Unhandled exception: {e}") 