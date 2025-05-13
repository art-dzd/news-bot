from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

@dataclass
class NewsItem:
    """
    Модель для представления новости (для отправки в Telegram).
    """
    title: str  # Заголовок новости
    url: str  # Ссылка на новость
    source: str  # Источник новости (mos.ru, dzen, etc.)
    published_date: Optional[datetime] = None  # Дата публикации (если известна)
    snippet: Optional[str] = None  # Короткое описание/сниппет
    categories: List[str] = None  # Категории/теги новости
    
    def __post_init__(self):
        """Пост-обработка после инициализации."""
        if self.categories is None:
            self.categories = []
    
    def __eq__(self, other):
        """
        Проверка равенства двух новостей.
        Две новости считаются равными, если у них одинаковый URL.
        """
        if not isinstance(other, NewsItem):
            return False
        return self.url == other.url
    
    def __hash__(self):
        """
        Хеш-функция для новости.
        Используется URL в качестве уникального идентификатора.
        """
        return hash(self.url)
    
    def to_telegram_message(self):
        """
        Форматирование новости для отправки в Telegram.
        
        Returns:
            str: Отформатированное сообщение
        """
        message = ""
        if self.source == "Дзен":
            message += "<b>ТОП ДЗЕНА:</b>\n"
        message += f"📰 <b>{self.title}</b>\n"
        
        if self.snippet:
            message += f"{self.snippet}\n\n"
            
        message += f"📎 <a href=\"{self.url}\">Читать на {self.source}</a>"
        
        return message 

@dataclass
class MosruHistoryItem:
    url: str
    title: str
    snippet: str
    added_at: str  # ISO8601
    in_dzen: bool = False

    def to_telegram_message(self):
        message = f"📰 <b>{self.title}</b>\n"
        if self.snippet:
            message += f"{self.snippet}\n\n"
        message += f"📎 <a href=\"{self.url}\">Читать на mos.ru</a>"
        return message

@dataclass
class DzenHistoryItem:
    url: str
    title: str
    added_at: str  # ISO8601
    mosru_source_url: Optional[str] = None
    mosru_title: Optional[str] = None
    mosru_snippet: Optional[str] = None
    match_type: Optional[str] = None  # "sbert" или "keywords"
    similarity_score: Optional[float] = None  # Числовой показатель схожести (0.0-1.0)
    common_words: Optional[int] = None  # Количество общих слов
    matched_keywords: Optional[List[str]] = None  # Список найденных ключевых слов

    def __post_init__(self):
        # Преобразуем None в списки для ListField
        if self.matched_keywords is None:
            self.matched_keywords = []

    def to_telegram_message(self):
        message = "<b>ТОП ДЗЕНА:</b>\n"
        message += f"📰 <b>{self.title}</b>\n"
        if self.mosru_source_url:
            message += f"\n<b>Первоисточник:</b> <a href=\"{self.mosru_source_url}\">{self.mosru_title or 'Читать на mos.ru'}</a>\n"
            
            # Добавляем информацию о схожести, если доступна
            if self.similarity_score:
                message += f"<i>Схожесть: {self.similarity_score:.2f}</i>\n"
        elif self.match_type == "keywords" and self.matched_keywords:
            message += f"\n<i>Ключевые слова: {', '.join(self.matched_keywords[:3])}</i>\n"
            
        message += f"\n📎 <a href=\"{self.url}\">Читать на Дзен</a>"
        return message 