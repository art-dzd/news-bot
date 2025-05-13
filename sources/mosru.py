# Универсальный парсер для mos.ru (newsfeed и dzdrav)
from sources.playwright_parser import fetch_mosru_news
from config import MOSRU_HEALTH_URL, MOSRU_DZDRAV_URL

async def get_all_mosru_news():
    """
    Возвращает кортеж:
    - news_items: список NewsItem для отправки в Telegram
    - history_items: список MosruHistoryItem для хранения в истории
    """
    urls = [
        MOSRU_HEALTH_URL,
        MOSRU_DZDRAV_URL,
        # Добавь сюда другие mos.ru ссылки, если появятся
    ]
    all_news = []
    all_history = []
    for url in urls:
        news, history = await fetch_mosru_news(url)
        all_news.extend(news)
        all_history.extend(history)
    # Фильтрация дублей по нормализованному URL
    unique_news = {}
    for item in all_news:
        norm_url = item.url.rstrip('/') + '/'
        if norm_url not in unique_news:
            unique_news[norm_url] = item
    unique_history = {}
    for item in all_history:
        norm_url = item.url.rstrip('/') + '/'
        if norm_url not in unique_history:
            unique_history[norm_url] = item
    return list(unique_news.values()), list(unique_history.values()) 