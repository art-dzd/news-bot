import os
import pytz
from datetime import datetime
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла (если есть)
load_dotenv()

# Настройки Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
USER_ID = os.getenv("USER_ID")  # ID пользователя, которому отправлять сообщения

# Настройки API
API_BASE_URL = os.getenv("API_BASE_URL")  # URL для API эндпоинтов

# Настройки для mos.ru
MOSRU_HEALTH_URL = "https://www.mos.ru/search/newsfeed?hostApplied=false&no_spellcheck=0&page=1&q=&spheres=18299"
MOSRU_DZDRAV_URL = "https://www.mos.ru/dzdrav/news/"

# Настройки для Яндекс Дзен
DZEN_MOSCOW_URL = "https://dzen.ru/topic/19711"

# Настройки для парсера
MAX_NEWS_AGE_DAYS = int(os.getenv("MAX_NEWS_AGE_DAYS", "2"))  # Максимальный возраст новостей для сравнения (в днях)
# Порог схожести для SBERT (косинусное расстояние, 0.0-1.0)
SBERT_SIMILARITY_THRESHOLD = float(os.getenv("SBERT_SIMILARITY_THRESHOLD", "0.79"))

# Настройки кэширования и оптимизации памяти
FORCE_CPU = os.getenv("FORCE_CPU", "false").lower() == "true"  # Использовать CPU вместо GPU
LIMIT_PYTORCH_MEM = os.getenv("LIMIT_PYTORCH_MEM", "true").lower() == "true"  # Включить ограничения памяти для PyTorch
MAX_CACHE_SIZE = int(os.getenv("MAX_CACHE_SIZE", "1000"))  # Максимальный размер кэша эмбеддингов

# Часовой пояс
TIMEZONE = pytz.timezone("Europe/Moscow")

# Вспомогательные функции
def get_current_date():
    """Получение текущей даты в московском часовом поясе."""
    return datetime.now(TIMEZONE).date()

# Настройки логгера
LOG_LEVEL = os.getenv("LOG_LEVEL", "DEBUG") 