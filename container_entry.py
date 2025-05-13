from fastapi import FastAPI, Request, Response, HTTPException, BackgroundTasks
import asyncio
import json
import logging
import subprocess
import os
import sys
import signal
import traceback
from pathlib import Path
import psutil
import time
from scheduler import fetch_and_send_news, start_parser, stop_parser, is_parser_running
from tg_bot.bot import NewsBot
from telegram import Update
from utils.logger import logger

app = FastAPI(title="News Bot API", description="API для Telegram-бота с парсингом новостей.")

# Файл для хранения PID процесса парсера
PARSER_PID_FILE = os.path.join(os.path.dirname(__file__), "storage", "parser.pid")

# Хранение обработанных webhook-запросов для предотвращения дублирования
PROCESSED_UPDATES = set()
MAX_PROCESSED_UPDATES = 1000  # Максимальное количество хранимых update_id

def ensure_storage_dir():
    """Убедиться, что директория storage существует"""
    os.makedirs(os.path.join(os.path.dirname(__file__), "storage"), exist_ok=True)

def is_parser_running():
    """Проверяет, запущен ли парсер в непрерывном режиме, по PID файлу"""
    if not os.path.exists(PARSER_PID_FILE):
        return False
    
    try:
        with open(PARSER_PID_FILE, 'r') as f:
            pid = int(f.read().strip())
            
        # Проверяем, существует ли процесс с таким PID
        process = psutil.Process(pid)
        # Проверяем, что это действительно процесс python с main.py
        if "python" in process.name().lower() and any("main.py" in cmd for cmd in process.cmdline()):
            return True
        else:
            # Процесс существует, но это не наш парсер - удаляем PID файл
            os.remove(PARSER_PID_FILE)
            return False
    except (ProcessLookupError, psutil.NoSuchProcess):
        # Процесс не существует - удаляем PID файл
        if os.path.exists(PARSER_PID_FILE):
            os.remove(PARSER_PID_FILE)
        return False
    except Exception as e:
        logger.error(f"Ошибка при проверке статуса парсера: {e}")
        return False

def start_parser_process():
    """Запускает процесс парсера в фоновом режиме"""
    if is_parser_running():
        logger.info("Парсер уже запущен")
        return True
    
    try:
        # Создаем директорию для логов, если она не существует
        ensure_storage_dir()
        
        # Полный путь к main.py
        main_script = os.path.join(os.path.dirname(__file__), "main.py")
        
        # Подготавливаем переменные окружения, чтобы запустить только планировщик без бота
        env = os.environ.copy()
        env['WITHOUT_BOT'] = '1'
        process = subprocess.Popen(
            [sys.executable, main_script],
            stdout=open(os.path.join(os.path.dirname(__file__), "storage", "parser_output.log"), "a"),
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True  # Создает новую сессию, чтобы процесс не зависел от текущего
        )
        
        # Записываем PID процесса в файл
        with open(PARSER_PID_FILE, 'w') as f:
            f.write(str(process.pid))
        
        logger.info(f"Запущен процесс парсера с PID {process.pid}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при запуске процесса парсера: {e}")
        logger.error(traceback.format_exc())
        return False

def stop_parser_process():
    """Останавливает процесс парсера"""
    if not os.path.exists(PARSER_PID_FILE):
        logger.info("PID файл не найден, парсер не запущен")
        return True
    
    try:
        with open(PARSER_PID_FILE, 'r') as f:
            pid = int(f.read().strip())
        
        # Пытаемся корректно завершить процесс
        try:
            process = psutil.Process(pid)
            process.terminate()  # SIGTERM
            
            # Ждем до 5 секунд завершения процесса
            for _ in range(10):
                if not process.is_running():
                    break
                time.sleep(0.5)
            
            # Если процесс все еще работает - убиваем его
            if process.is_running():
                process.kill()  # SIGKILL
        except (ProcessLookupError, psutil.NoSuchProcess):
            # Процесс уже не существует
            pass
        
        # Удаляем PID файл
        if os.path.exists(PARSER_PID_FILE):
            os.remove(PARSER_PID_FILE)
            
        logger.info(f"Процесс парсера с PID {pid} остановлен")
        return True
    except Exception as e:
        logger.error(f"Ошибка при остановке процесса парсера: {e}")
        logger.error(traceback.format_exc())
        return False

@app.get("/")
async def health_check():
    """Проверка работоспособности сервиса."""
    return {"status": "ok", "service": "news-bot"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """
    Эндпоинт для Telegram webhook.
    Обрабатывает все обновления от Telegram (команды, сообщения).
    """
    try:
        data = await request.json()
        logger.info(f"Получен webhook-запрос от Telegram")
        
        # Проверка на дубликаты запросов
        update_id = data.get('update_id')
        if update_id is not None:
            # Если такой update_id уже обрабатывался - пропускаем
            if update_id in PROCESSED_UPDATES:
                logger.info(f"Пропуск дубликата webhook запроса с update_id={update_id}")
                return {"status": "ok", "message": "duplicate"}
                
            # Добавляем update_id в множество обработанных
            PROCESSED_UPDATES.add(update_id)
            
            # Ограничиваем размер множества
            if len(PROCESSED_UPDATES) > MAX_PROCESSED_UPDATES:
                # Оставляем только последние 500 элементов
                PROCESSED_UPDATES.clear()
                PROCESSED_UPDATES.update(sorted(list(PROCESSED_UPDATES))[-500:])
                
        logger.debug(f"Данные запроса: {data}")

        news_bot = NewsBot()
        await news_bot.setup()
        await news_bot.application.initialize()
        update = Update.de_json(data, news_bot.application.bot)
        await news_bot.application.process_update(update)

        logger.info("Webhook успешно обработан")
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Ошибка при обработке webhook: {e}")
        logger.error(traceback.format_exc())
        return {"status": "error", "message": str(e)}

@app.post("/cron")
async def run_cron():
    """
    Эндпоинт для запуска по расписанию.
    Парсит новости и отправляет их в Telegram.
    """
    try:
        logger.info("Запуск парсера по расписанию")
        await fetch_and_send_news()
        logger.info("Парсер завершил работу")
        return {"status": "ok", "message": "Новости проверены и отправлены"}
    except Exception as e:
        logger.error(f"Ошибка при запуске парсера: {e}")
        logger.error(traceback.format_exc())
        return {"status": "error", "message": str(e)}

@app.post("/control/start")
async def start_continuous_parser(background_tasks: BackgroundTasks):
    """
    Эндпоинт для запуска непрерывного режима парсера.
    """
    try:
        if is_parser_running():
            return {"status": "ok", "message": "Парсер уже запущен"}
        
        # Запускаем парсер в отдельном процессе
        success = start_parser_process()
        
        if success:
            # Не запускаем тестовую проверку, т.к. основной процесс сам это сделает
            # и это вызывает конфликт загрузки моделей и нехватку памяти
            
            return {"status": "ok", "message": "Парсер запущен в непрерывном режиме"}
        else:
            return {"status": "error", "message": "Ошибка при запуске парсера"}
    except Exception as e:
        logger.error(f"Ошибка при запуске непрерывного режима: {e}")
        logger.error(traceback.format_exc())
        return {"status": "error", "message": str(e)}

@app.post("/control/stop")
async def stop_continuous_parser():
    """
    Эндпоинт для остановки непрерывного режима парсера.
    """
    try:
        if not is_parser_running():
            return {"status": "ok", "message": "Парсер уже остановлен"}
        
        # Останавливаем парсер
        success = stop_parser_process()
        
        if success:
            return {"status": "ok", "message": "Парсер остановлен"}
        else:
            return {"status": "error", "message": "Ошибка при остановке парсера"}
    except Exception as e:
        logger.error(f"Ошибка при остановке парсера: {e}")
        logger.error(traceback.format_exc())
        return {"status": "error", "message": str(e)}

@app.get("/control/status")
async def get_parser_status():
    """
    Эндпоинт для получения статуса непрерывного режима парсера.
    """
    try:
        status = "running" if is_parser_running() else "stopped"
        return {"status": "ok", "parser_status": status}
    except Exception as e:
        logger.error(f"Ошибка при получении статуса парсера: {e}")
        logger.error(traceback.format_exc())
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080) 