# Модуль для утилит проекта
from utils.logger import logger
from utils.models import NewsItem
from utils.similarity import is_similar_title, calculate_similarity, normalize_text

__all__ = [
    'logger',
    'NewsItem',
    'is_similar_title',
    'calculate_similarity',
    'normalize_text'
] 