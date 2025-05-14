import os
import json
import threading
from datetime import datetime
from utils.logger import logger

SENT_CACHE_PATH = os.path.join(os.path.dirname(__file__), 'sent_urls_cache.json')
MAX_SENT_CACHE = 1000

class SentURLCache:
    def __init__(self, path=SENT_CACHE_PATH, max_size=MAX_SENT_CACHE):
        self.path = path
        self.max_size = max_size
        self._lock = threading.RLock()
        self._cache = {}  # url -> sent_at (str)
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            self._cache = {}
            return
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # data: list of {"url": ..., "sent_at": ...}
            self._cache = {item['url']: item['sent_at'] for item in data if 'url' in item and 'sent_at' in item}
        except Exception as e:
            logger.error(f"Ошибка при загрузке sent_urls_cache: {e}")
            self._cache = {}

    def _save(self):
        try:
            items = [
                {"url": url, "sent_at": sent_at}
                for url, sent_at in list(self._cache.items())[-self.max_size:]
            ]
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка при сохранении sent_urls_cache: {e}")

    def is_sent(self, url):
        with self._lock:
            return url in self._cache

    def get_sent_at(self, url):
        with self._lock:
            return self._cache.get(url)

    def add(self, url):
        now = datetime.now().isoformat(timespec='seconds')
        with self._lock:
            self._cache[url] = now
            # Обрезаем кэш до max_size
            if len(self._cache) > self.max_size:
                # Оставляем только последние max_size записей
                items = list(self._cache.items())[-self.max_size:]
                self._cache = dict(items)
            self._save()

sent_url_cache = SentURLCache() 