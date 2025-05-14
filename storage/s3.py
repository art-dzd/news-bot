import json
import os
import traceback
from datetime import datetime, timedelta
from typing import List, Dict, Any, Set, Optional
from utils.logger import logger
from config import TIMEZONE
import threading

MOSRU_HISTORY_PATH = os.path.join(os.path.dirname(__file__), 'mosru_history.json')
DZEN_HISTORY_PATH = os.path.join(os.path.dirname(__file__), 'dzen_history.json')
DZEN_ANALYZED_URLS_PATH = os.path.join(os.path.dirname(__file__), 'dzen_analyzed_urls.json')
PARSER_STATE_PATH = os.path.join(os.path.dirname(__file__), 'parser_state.json')
CACHE_EMBEDDINGS_PATH = os.path.join(os.path.dirname(__file__), 'cache_embeddings.json')
MAX_ANALYZED_URLS = 5000  # Максимальное количество URL для хранения

class S3Storage:
    """
    Класс для работы с локальным хранилищем истории новостей.
    Использует файлы в папке storage/ вместо S3.
    """
    _lock = threading.RLock()
    
    def __init__(self):
        self.analyzed_urls: Set[str] = set()  # Кэш проанализированных URL
        self._load_analyzed_urls()  # Загружаем при инициализации
    
    def load_mosru_history(self):
        return self._load_json(MOSRU_HISTORY_PATH, default=[])
    
    def save_mosru_history(self, history):
        self._save_json(MOSRU_HISTORY_PATH, history)
    
    def load_dzen_history(self):
        return self._load_json(DZEN_HISTORY_PATH, default=[])
    
    def save_dzen_history(self, history):
        self._save_json(DZEN_HISTORY_PATH, history)
    
    def load_parser_state(self):
        return self._load_json(PARSER_STATE_PATH, default={})
    
    def save_parser_state(self, parser_state):
        self._save_json(PARSER_STATE_PATH, parser_state)
    
    def save_cache_embeddings(self, cache_data: Dict):
        """Сохраняет кэш эмбеддингов в файл"""
        self._save_json(CACHE_EMBEDDINGS_PATH, cache_data)
        
    def load_cache_embeddings(self) -> Dict:
        """Загружает кэш эмбеддингов из файла"""
        return self._load_json(CACHE_EMBEDDINGS_PATH, default={})
    
    def is_url_analyzed(self, url: str) -> bool:
        """Проверяет, был ли URL уже проанализирован"""
        with self._lock:
            return url in self.analyzed_urls
    
    def add_analyzed_urls(self, urls: List[str]) -> None:
        """Добавляет список URL в кэш проанализированных и сохраняет в файл"""
        if not urls:
            return
            
        with self._lock:
            # Добавляем новые URL в кэш
            for url in urls:
                self.analyzed_urls.add(url)
            
            # Ограничиваем размер кэша при необходимости
            self._trim_analyzed_urls_if_needed()
            
            # Сохраняем обновленный список в файл
            self._save_analyzed_urls()
    
    def clear_analyzed_urls_cache(self, max_age_days: int = 30) -> None:
        """Очищает кэш проанализированных URL старше указанного количества дней"""
        # В текущей реализации у нас нет информации о времени анализа каждого URL,
        # поэтому мы просто очищаем кэш до определенного размера
        with self._lock:
            if len(self.analyzed_urls) > MAX_ANALYZED_URLS:
                # Преобразуем в список для удаления лишних элементов
                urls_list = list(self.analyzed_urls)
                # Оставляем только MAX_ANALYZED_URLS / 2 элементов (самые новые)
                self.analyzed_urls = set(urls_list[-(MAX_ANALYZED_URLS // 2):])
                # Сохраняем обновленный список
                self._save_analyzed_urls()
                logger.info(f"Очищен кэш проанализированных URL: было {len(urls_list)}, стало {len(self.analyzed_urls)}")
    
    def _load_analyzed_urls(self) -> None:
        """Загружает список проанализированных URL из файла"""
        with self._lock:
            urls = self._load_json(DZEN_ANALYZED_URLS_PATH, default=[])
            self.analyzed_urls = set(urls)
            logger.info(f"Загружено {len(self.analyzed_urls)} проанализированных URL")
    
    def _save_analyzed_urls(self) -> None:
        """Сохраняет список проанализированных URL в файл"""
        with self._lock:
            urls_list = list(self.analyzed_urls)
            self._save_json(DZEN_ANALYZED_URLS_PATH, urls_list)
    
    def _trim_analyzed_urls_if_needed(self) -> None:
        """Ограничивает размер кэша проанализированных URL"""
        if len(self.analyzed_urls) > MAX_ANALYZED_URLS:
            # Преобразуем в список для удаления лишних элементов
            urls_list = list(self.analyzed_urls)
            # Оставляем только MAX_ANALYZED_URLS элементов (самые новые)
            self.analyzed_urls = set(urls_list[-MAX_ANALYZED_URLS:])
            logger.info(f"Кэш проанализированных URL был ограничен до {MAX_ANALYZED_URLS} элементов")
    
    def _save_json(self, path, data):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка при сохранении в {path}: {str(e)}")
            logger.error(traceback.format_exc())
    
    def _load_json(self, path, default=None):
        if default is None:
            default = {}
        try:
            if not os.path.exists(path):
                return default
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Ошибка при загрузке из {path}: {str(e)}")
            logger.error(traceback.format_exc())
            return default

s3_storage = S3Storage() 