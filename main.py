import sys
import os

# Применяем патч для torch.compiler перед импортом других модулей
try:
    import patch_torch
    print("Патч torch.compiler успешно применен")
except Exception as e:
    print(f"Не удалось применить патч torch.compiler: {e}")

import asyncio
import platform
import signal
import json

from utils.logger import logger
from scheduler import main as run_scheduler
from config import TELEGRAM_BOT_TOKEN
from tg_bot.bot import NewsBot

async def shutdown(signals, loop):
    """Обработчик сигналов для корректного завершения работы"""
    for signal in signals:
        loop.remove_signal_handler(signal)
    logger.info("Получен сигнал завершения. Останавливаю приложение...")
    
    # Останавливаем задачи и завершаем работу
    tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()

def setup_signal_handlers(loop):
    """Настройка обработчиков сигналов завершения"""
    signals = (signal.SIGTERM, signal.SIGINT)
    for sig in signals:
        try:
            loop.add_signal_handler(
                sig, lambda: asyncio.create_task(shutdown(signals, loop))
            )
        except NotImplementedError:
            # Windows не поддерживает POSIX сигналы
            pass

if __name__ == "__main__":
    logger.info(f"Запуск приложения на {platform.system()} {platform.release()}")
    
    # В Windows может потребоваться этот код для корректной работы asyncio
    if platform.system() == "Windows":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        # Получаем текущий event loop
        loop = asyncio.get_event_loop()
        
        # Настраиваем обработчики сигналов
        setup_signal_handlers(loop)
        
        # Активируем парсер, чтобы он начал работать сразу
        from scheduler import start_parser
        start_parser()
        
        # Запускаем основную функцию приложения
        asyncio.run(run_scheduler())
    except KeyboardInterrupt:
        logger.info("Приложение остановлено пользователем")
    except Exception as e:
        logger.error(f"Необработанное исключение: {e}")
        sys.exit(1)

# Обработчик для Cloud Functions (оставлен для совместимости, если потребуется)
def handler(event, context):
    import asyncio
    from scheduler import main as run_scheduler
    from tg_bot.bot import NewsBot

    # Проверяем, является ли это запросом от Telegram webhook
    if event.get('body'):
        try:
            logger.info("Получен webhook-запрос от Telegram")
            update_data = json.loads(event['body'])
            logger.info(f"Содержимое webhook: {update_data}")

            news_bot = NewsBot()
            async def process_update():
                await news_bot.setup()
                await news_bot.application.initialize()
                from telegram import Update
                update = Update.de_json(update_data, news_bot.application.bot)
                await news_bot.application.process_update(update)
            asyncio.run(process_update())
            logger.info(f"Webhook успешно обработан")
            return {
                'statusCode': 200,
                'body': 'ok'
            }
        except Exception as e:
            logger.error(f"Ошибка при обработке webhook: {e}")
            return {
                'statusCode': 500,
                'body': f'Ошибка: {str(e)}'
            }
    else:
        logger.info("Запуск парсера по расписанию")
        asyncio.run(run_scheduler())
        return {
            'statusCode': 200,
            'body': 'Новости проверены и отправлены'
        } 