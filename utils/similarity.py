import re
import os
import yaml
import numpy as np
import torch
from transformers import AutoTokenizer, AutoModel
from collections import Counter, OrderedDict
import gc
import time
from datetime import datetime, timedelta
import threading

from utils.logger import logger
from config import SBERT_SIMILARITY_THRESHOLD, MAX_CACHE_SIZE, FORCE_CPU, LIMIT_PYTORCH_MEM

# Список стоп-слов (предлоги, союзы, местоимения)
STOP_WORDS = {
    'в', 'на', 'по', 'с', 'о', 'и', 'за', 'от', 'для', 'к', 'у', 'из', 'под', 'над', 'до', 'после', 'при', 'об', 'без', 'через', 'про',
    'а', 'но', 'или', 'же', 'да', 'то', 'также', 'тоже', 'еще', 'уже', 'все', 'этот', 'тот', 'такой', 'так', 'там', 'тут', 'здесь', 'где',
    'когда', 'почему', 'как', 'кто', 'что', 'какой', 'какая', 'какое', 'какие', 'мой', 'твой', 'его', 'ее', 'их', 'наш', 'ваш', 'свой',
    'город', 'москва', 'столица', 'новый', 'работа', 'быть', 'есть', 'нет', 'ли', 'он', 'она', 'оно', 'они', 'мы', 'вы', 'я', 'ты', 'вы',
    'этого', 'этой', 'этих', 'эти', 'тот', 'та', 'те', 'того', 'той', 'тех', 'один', 'два', 'три', 'четыре', 'пять', 'шесть', 'семь', 'восемь', 'девять', 'десять',
    'об', 'который', 'которая', 'которое', 'которые', 'вот', 'очень'
}

# Слова, которые не должны считаться при определении совпадающих слов для бонусирования.
# Это частые слова и формы, которые присутствуют во многих новостях, но не несут смысловой нагрузки.
COMMON_WORD_STOPLIST = {
    'рассказал', 'рассказала', 'рассказало', 'рассказали', 'сообщил', 'сообщила', 'сообщило', 'сообщили',
    'заявил', 'заявила', 'заявило', 'заявили', 'отметил', 'отметила', 'отметило', 'отметили',
    'уточнил', 'уточнила', 'уточнило', 'уточнили', 'указал', 'указала', 'указало', 'указали',
    'подчеркнул', 'подчеркнула', 'подчеркнуло', 'подчеркнули', 'добавил', 'добавила', 'добавило', 'добавили',
    'прокомментировал', 'прокомментировала', 'прокомментировало', 'прокомментировали',
    'написал', 'написала', 'написало', 'написали', 'сказал', 'сказала', 'сказало', 'сказали',
    'собя', 'моск', 'ново'
}

# Глобальная переменная для кэширования ключевых слов
KEYWORDS = None

# --- Стоп-список для бонуса по keywords ---
KEYWORD_BONUS_STOPWORDS = set([
    'пациент'
])

# --- Кэширование эмбеддингов (LRU кэширование) ---
# Максимальный размер кэша из конфигурации

class LRUCache(OrderedDict):
    """
    Кэш, который автоматически удаляет старые записи при достижении максимального размера
    """
    def __init__(self, capacity):
        super().__init__()
        self.capacity = capacity
        
    def __getitem__(self, key):
        if key in self:
            self.move_to_end(key)
        return super().__getitem__(key)
    
    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > self.capacity:
            oldest = next(iter(self))
            del self[oldest]

# Заменяем обычные словари на LRU кэши
DZEN_EMB_CACHE = LRUCache(MAX_CACHE_SIZE)
MOSRU_EMB_CACHE = LRUCache(MAX_CACHE_SIZE)

# Для блокировки во время загрузки модели
SBERT_LOCK = threading.Lock()
SBERT_LOADING = False

def cleanup_cache(keep_urls=None, max_age_days=3):
    """
    Очистка кэша эмбеддингов, сохраняя указанные URL и удаляя старые (старше max_age_days дней)
    
    Args:
        keep_urls (set, optional): Набор URL, которые нужно оставить в кэше
        max_age_days (int, optional): Максимальный возраст эмбеддингов в днях, по умолчанию 3 дня
        
    Returns:
        dict: Статистика очистки кэша
    """
    global DZEN_EMB_CACHE, MOSRU_EMB_CACHE
    
    if keep_urls is None:
        keep_urls = set()
    
    # Текущее время для сравнения
    current_time = datetime.now().timestamp()
    max_age_seconds = max_age_days * 24 * 3600  # Максимальный возраст в секундах
    
    dzen_before = len(DZEN_EMB_CACHE)
    mosru_before = len(MOSRU_EMB_CACHE)
    logger.debug(f"Очистка кэша эмбеддингов. Размер до очистки: DZEN={dzen_before}, MOSRU={mosru_before}")
    
    # Сколько URL мы сохраним
    dzen_keep_count = len(set(DZEN_EMB_CACHE.keys()) & keep_urls)
    mosru_keep_count = len(set(MOSRU_EMB_CACHE.keys()) & keep_urls)
    logger.debug(f"URL для сохранения: DZEN={dzen_keep_count}, MOSRU={mosru_keep_count}")
    
    # Создаем новые кэши
    new_dzen_cache = LRUCache(MAX_CACHE_SIZE)
    new_mosru_cache = LRUCache(MAX_CACHE_SIZE)
    
    # Копируем URL из списка keep_urls и удаляем старые записи
    for url, data in DZEN_EMB_CACHE.items():
        if url in keep_urls or (current_time - data.get('timestamp', 0) < max_age_seconds):
            new_dzen_cache[url] = data
    
    for url, data in MOSRU_EMB_CACHE.items():
        if url in keep_urls or (current_time - data.get('timestamp', 0) < max_age_seconds):
            new_mosru_cache[url] = data
    
    # Заменяем старые кэши новыми
    DZEN_EMB_CACHE = new_dzen_cache
    MOSRU_EMB_CACHE = new_mosru_cache
    
    # Статистика по очистке
    dzen_cleared = dzen_before - len(DZEN_EMB_CACHE)
    mosru_cleared = mosru_before - len(MOSRU_EMB_CACHE)
    
    logger.debug(f"Кэш эмбеддингов очищен. Удалено: DZEN={dzen_cleared}, MOSRU={mosru_cleared}")
    logger.debug(f"Новый размер: DZEN={len(DZEN_EMB_CACHE)}, MOSRU={len(MOSRU_EMB_CACHE)}")
    
    # Запуск сборщика мусора для освобождения памяти
    gc.collect()
    
    return {
        'dzen_before': dzen_before,
        'mosru_before': mosru_before,
        'dzen_after': len(DZEN_EMB_CACHE),
        'mosru_after': len(MOSRU_EMB_CACHE),
        'dzen_cleared': dzen_cleared,
        'mosru_cleared': mosru_cleared
    }

# --- SBERT инициализация ---
SBERT_MODEL_NAME = "ai-forever/sbert_large_nlu_ru"
tokenizer = None
model = None

def optimize_memory_usage():
    """
    Оптимизация использования памяти для работы на сервере с ограниченным ОЗУ
    """
    # Освобождение неиспользуемой памяти Python
    gc.collect()
    
    # Если возможно, ограничиваем использование CUDA
    if torch.cuda.is_available():
        # Ограничиваем выделение памяти для CUDA
        torch.cuda.empty_cache()
        
        # Для серверов с малым количеством ОЗУ лучше использовать CPU
        if FORCE_CPU:
            logger.info("Использование CPU для SBERT вместо GPU (FORCE_CPU=true)")
            os.environ["CUDA_VISIBLE_DEVICES"] = ""
    
    # Установка ограничений памяти для PyTorch
    if LIMIT_PYTORCH_MEM:
        try:
            torch.backends.cuda.matmul.allow_tf32 = False  # Более экономное использование памяти
            if hasattr(torch.backends, 'cudnn'):
                torch.backends.cudnn.benchmark = False
                torch.backends.cudnn.deterministic = True
            logger.info("Установлены ограничения памяти для PyTorch")
        except Exception as e:
            logger.warning(f"Не удалось установить ограничения памяти для PyTorch: {e}")

def init_sbert():
    """
    Инициализация модели SBERT при первом использовании
    """
    global tokenizer, model, SBERT_LOADING
    
    # Если модель уже загружена, просто возвращаем управление
    if tokenizer is not None and model is not None:
        return

    # Используем блокировку для предотвращения одновременной загрузки из разных потоков
    with SBERT_LOCK:
        # Второй раз проверяем, что модель все еще не загружена (могла загрузиться за время получения блокировки)
        if tokenizer is not None and model is not None:
            return
            
        # Отмечаем, что модель загружается
        if SBERT_LOADING:
            logger.info("Модель SBERT уже загружается в другом потоке, ожидаем...")
            return
            
        SBERT_LOADING = True
        
        try:
            logger.info(f"Инициализация SBERT модели {SBERT_MODEL_NAME}")
            
            # Оптимизация памяти перед загрузкой модели
            optimize_memory_usage()
            
            # Загружаем токенизатор
            tokenizer = AutoTokenizer.from_pretrained(SBERT_MODEL_NAME)
            
            # Проверяем версию PyTorch для совместимости
            torch_version = getattr(torch, "__version__", "0.0.0")
            logger.info(f"Используется PyTorch версии: {torch_version}")
            
            # Загружаем модель с базовыми параметрами, совместимыми со старыми версиями PyTorch
            model_kwargs = {
                "torch_dtype": torch.float32,  # Используем float32 вместо float16 для стабильности
            }
            
            try:
                # Проверяем, установлен ли accelerate
                import importlib
                accelerate_spec = importlib.util.find_spec("accelerate")
                if accelerate_spec is not None:
                    logger.info("Библиотека accelerate найдена, используем low_cpu_mem_usage=True")
                    model = AutoModel.from_pretrained(
                        SBERT_MODEL_NAME,
                        **model_kwargs,
                        low_cpu_mem_usage=True  # Экономия памяти CPU
                    )
                else:
                    logger.warning("Библиотека accelerate не найдена, загружаем модель без low_cpu_mem_usage")
                    model = AutoModel.from_pretrained(
                        SBERT_MODEL_NAME,
                        **model_kwargs
                    )
            except (TypeError, ImportError) as e:
                # Если low_cpu_mem_usage не поддерживается или требует accelerate, загружаем без него
                logger.warning(f"Параметр low_cpu_mem_usage не поддерживается или требует accelerate: {e}, загружаем модель без него")
                model = AutoModel.from_pretrained(
                    SBERT_MODEL_NAME,
                    **model_kwargs
                )
            
            # Переводим модель в режим evaluation для экономии памяти
            model.eval()
            
            logger.info("SBERT модель успешно загружена")
        except Exception as e:
            logger.error(f"Ошибка инициализации SBERT: {e}")
            # Записываем полный стек-трейс для отладки
            import traceback
            logger.error(f"Трассировка: {traceback.format_exc()}")
            raise
        finally:
            # Снимаем флаг загрузки в любом случае
            SBERT_LOADING = False

def mean_pooling(model_output, attention_mask):
    """
    Получение эмбеддинга из выхода SBERT модели
    """
    token_embeddings = model_output[0]
    input_mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
    sum_embeddings = torch.sum(token_embeddings * input_mask_expanded, 1)
    sum_mask = torch.clamp(input_mask_expanded.sum(1), min=1e-9)
    return sum_embeddings / sum_mask

def get_sentence_embedding(text):
    """
    Получение эмбеддинга для текста
    """
    init_sbert()
    encoded_input = tokenizer([text], padding=True, truncation=True, max_length=32, return_tensors='pt')
    with torch.no_grad():
        model_output = model(**encoded_input)
    embedding = mean_pooling(model_output, encoded_input['attention_mask'])
    return embedding[0].numpy()

def get_dzen_embedding(url, title):
    """
    Получение и кэширование эмбеддинга для заголовка новости Дзена
    """
    if url in DZEN_EMB_CACHE:
        # Возвращаем только эмбеддинг, не обновляя timestamp
        return DZEN_EMB_CACHE[url]['embedding']
    
    emb = get_sentence_embedding(title)
    # Сохраняем эмбеддинг вместе с временной меткой
    DZEN_EMB_CACHE[url] = {
        'embedding': emb,
        'timestamp': datetime.now().timestamp()
    }
    return emb

def get_mosru_embeddings(item):
    """
    Получение и кэширование эмбеддингов для новости mos.ru
    """
    url = getattr(item, 'url', None)
    if url in MOSRU_EMB_CACHE:
        # Возвращаем только эмбеддинги, не обновляя timestamp
        return MOSRU_EMB_CACHE[url]['embeddings']
    
    title = item.title
    snippet = getattr(item, 'snippet', '') or ''
    
    emb_title = get_sentence_embedding(title)
    if snippet:
        emb_title_snippet = get_sentence_embedding(title + '. ' + snippet)
        emb_snippet = get_sentence_embedding(snippet)
    else:
        emb_title_snippet = emb_title
        emb_snippet = np.zeros_like(emb_title)
    
    embeddings = {
        'title': emb_title,
        'title_snippet': emb_title_snippet,
        'snippet': emb_snippet
    }
    
    # Сохраняем эмбеддинги вместе с временной меткой
    MOSRU_EMB_CACHE[url] = {
        'embeddings': embeddings,
        'timestamp': datetime.now().timestamp()
    }
    
    return embeddings

def normalize_text_simple(text):
    """
    Простая нормализация текста без лемматизации
    """
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r'[^а-яa-z0-9ё\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def normalize_for_match(text):
    """
    Нормализация для подсчета совпадающих слов
    """
    text = normalize_text_simple(text)
    # Исключаем стоп-слова и слова короче 3 символов
    return set(word[:4] for word in text.split() if len(word) > 2 and word.lower() not in COMMON_WORD_STOPLIST)

def count_common_words(title1, title2):
    """
    Подсчет общих слов в заголовках, исключая стоп-слова и другие общие слова
    """
    set1 = normalize_for_match(title1)
    set2 = normalize_for_match(title2)
    common_words = set1 & set2
    
    # Дополнительное логирование для отладки
    if common_words:
        logger.debug(f"Общие слова в заголовках (после фильтрации): {common_words}")
    
    return len(common_words)

def load_keywords():
    """
    Загружает ключевые слова из filters/keywords.yaml
    """
    global KEYWORDS
    if KEYWORDS is not None:
        return KEYWORDS
    
    keywords_path = os.path.join(os.path.dirname(__file__), '../filters/keywords.yaml')
    with open(keywords_path, 'r', encoding='utf-8') as f:
        keywords_data = yaml.safe_load(f)
    raw_keywords = keywords_data.get('topics', [])
    norm_keywords = set()
    
    for kw in raw_keywords:
        if not isinstance(kw, str):
            continue
        norm_keywords.add(normalize_text_simple(kw))
    
    KEYWORDS = norm_keywords
    return KEYWORDS

def has_keyword_phrase_in_both(title1, title2):
    """
    Проверка на наличие одинаковых ключевых фраз в обоих заголовках
    """
    try:
        norm1 = normalize_text_simple(title1)
        norm2 = normalize_text_simple(title2)
        
        logger.debug(f"Проверка ключевых фраз для заголовков:")
        logger.debug(f"  Заголовок 1: '{title1}' -> '{norm1}'")
        logger.debug(f"  Заголовок 2: '{title2}' -> '{norm2}'")
        
        keywords_path = os.path.join(os.path.dirname(__file__), '../filters/keywords.yaml')
        with open(keywords_path, 'r', encoding='utf-8') as f:
            keywords_data = yaml.safe_load(f)
        
        # Расширенное логирование для отладки проблемы с неправильным определением
        matched_words = []
        
        for kw in keywords_data.get('topics', []):
            if not isinstance(kw, str):
                continue
            norm_kw = normalize_text_simple(kw)
            if ' ' in norm_kw:
                # Фраза из нескольких слов
                if norm_kw in norm1 and norm_kw in norm2:
                    logger.debug(f"СОВПАДЕНИЕ: Ключевая фраза '{norm_kw}' найдена в обоих заголовках")
                    matched_words.append(norm_kw)
                    return True
                elif norm_kw in norm1:
                    logger.debug(f"Ключевая фраза '{norm_kw}' найдена только в заголовке 1")
                elif norm_kw in norm2:
                    logger.debug(f"Ключевая фраза '{norm_kw}' найдена только в заголовке 2")
            else:
                # Одно слово — только если есть как отдельное слово
                if f' {norm_kw} ' in f' {norm1} ' and f' {norm_kw} ' in f' {norm2} ':
                    logger.debug(f"СОВПАДЕНИЕ: Ключевое слово '{norm_kw}' найдено в обоих заголовках как отдельное слово")
                    matched_words.append(norm_kw)
                    return True
                elif f' {norm_kw} ' in f' {norm1} ':
                    logger.debug(f"Ключевое слово '{norm_kw}' найдено только в заголовке 1 как отдельное слово")
                elif f' {norm_kw} ' in f' {norm2} ':
                    logger.debug(f"Ключевое слово '{norm_kw}' найдено только в заголовке 2 как отдельное слово")
        
        # Отладочная информация, если есть общие слова
        common = count_common_words(title1, title2)
        if common >= 3:
            logger.debug(f"Обнаружено {common} общих слов в заголовках")
            # Дополнительная отладка - вывод общих слов
            words1 = normalize_for_match(title1)
            words2 = normalize_for_match(title2)
            common_words = words1.intersection(words2)
            logger.debug(f"Общие слова: {common_words}")
        
        return False
    except Exception as e:
        logger.error(f"Ошибка при проверке ключевых фраз: {e}")
        return False

def contains_keyword(text):
    """
    Проверка на наличие ключевых слов в тексте
    """
    norm = normalize_text_simple(text)
    keywords = load_keywords()
    
    for kw in keywords:
        # Проверка по подстроке
        if kw in norm:
            return True
        # Проверка по словам (например, "скорая помощь" в "скорой помощи")
        kw_words = kw.split()
        norm_words = norm.split()
        if len(kw_words) > 1:
            for i in range(len(norm_words) - len(kw_words) + 1):
                if all(norm_words[i+j].startswith(kw_words[j][:4]) for j in range(len(kw_words))):
                    return True
    return False

def normalize_text(text):
    """
    Обертка над normalize_text_simple для обратной совместимости
    """
    return normalize_text_simple(text)

def calculate_similarity(text1, text2, snippet2=None, mosru_history=None):
    """
    Обертка над calculate_similarity_sbert для обратной совместимости
    """
    # Временный URL для кэширования
    temp_url = f"temp_similarity_{hash(text1)}"
    
    # Создаем простой объект для передачи в calculate_similarity_sbert
    class SimpleItem:
        def __init__(self, title, snippet):
            self.title = title
            self.snippet = snippet
            self.url = f"temp_url_{hash(title)}"
    
    item = SimpleItem(text2, snippet2 or "")
    
    # Вычисляем схожесть
    score = calculate_similarity_sbert(temp_url, text1, item)
    
    # Очистка временного кэша
    if temp_url in DZEN_EMB_CACHE:
        del DZEN_EMB_CACHE[temp_url]
    if item.url in MOSRU_EMB_CACHE:
        del MOSRU_EMB_CACHE[item.url]
    
    return score

def calculate_similarity_sbert(dzen_url, dzen_title, mosru_item):
    """
    Подсчет семантической схожести с использованием SBERT
    """
    try:
        dzen_emb = get_dzen_embedding(dzen_url, dzen_title)
        mosru_embs = get_mosru_embeddings(mosru_item)
        
        # Добавляем защиту от деления на ноль
        score_title = float(np.dot(dzen_emb, mosru_embs['title']) / (np.linalg.norm(dzen_emb) * np.linalg.norm(mosru_embs['title']) + 1e-9))
        score_title_snippet = float(np.dot(dzen_emb, mosru_embs['title_snippet']) / (np.linalg.norm(dzen_emb) * np.linalg.norm(mosru_embs['title_snippet']) + 1e-9))
        
        avg_score = (score_title + score_title_snippet) / 2
        
        # Определение совпадающих слов
        n_common = count_common_words(dzen_title, mosru_item.title)
        
        # Проверка, чтобы исключить фальшивые совпадения из-за общих частотных слов
        has_matching_keywords = has_keyword_phrase_in_both(dzen_title, mosru_item.title)
        
        bonus = 0.0
        bonus_reason = ""
        
        # Применяем бонус только если:
        # 1. Найдено не менее 3 общих слов (после фильтрации стоп-слов и общих слов)
        # 2. ИЛИ есть совпадение по ключевым фразам из keywords.yaml
        if n_common >= 3:
            # Дополнительная проверка - совпадения должны быть среди значимых слов
            # Если семантическое сходство уже высокое (>0.7), то бонус не так критичен
            if avg_score >= 0.7:
                bonus = 0.1  # Меньший бонус для высоких сходств
                bonus_reason = f"бонус за {n_common} общих слов (высокое сходство)"
            else:
                bonus = 0.15
                bonus_reason = f"бонус за {n_common} общих слов"
        elif has_matching_keywords:
            bonus = 0.15
            bonus_reason = "бонус за совпадение ключевой фразы"
        
        final_score = min(avg_score * (1 + bonus), 1.0)
        
        # Улучшенное логирование для отладки
        log_msg = f"SBERT similarity для '{dzen_title}' и '{mosru_item.title}': title={score_title:.3f}, title+snippet={score_title_snippet:.3f}, avg={avg_score:.3f}"
        if bonus > 0:
            log_msg += f", {bonus_reason}={bonus}, final={final_score:.3f}"
        else:
            log_msg += f", общих слов={n_common}, совпадение ключевых фраз={has_matching_keywords}, bonus=0, final={final_score:.3f}"
        
        logger.debug(log_msg)
        
        return final_score
    except Exception as e:
        logger.error(f"Ошибка при расчете схожести с SBERT: {e}")
        return 0.0

def is_similar_title(title1, title2, threshold=None, snippet1=None, snippet2=None, mosru_history=None):
    """
    Проверка схожести заголовков с использованием SBERT
    Для обратной совместимости с остальным кодом
    """
    if threshold is None:
        threshold = SBERT_SIMILARITY_THRESHOLD
    
    score = calculate_similarity(title1, title2, snippet2=snippet2, mosru_history=mosru_history)
    return score >= threshold

def find_best_match(dzen_title, mosru_items):
    """
    Поиск наиболее схожей новости mos.ru для новости Дзена с использованием SBERT
    """
    best_item = None
    best_score = 0.0
    
    # Временный URL для кэширования эмбеддинга
    temp_url = f"temp_{hash(dzen_title)}"
    
    for item in mosru_items:
        score = calculate_similarity_sbert(temp_url, dzen_title, item)
        if score > best_score:
            best_score = score
            best_item = item
    
    # Очищаем временный эмбеддинг для экономии памяти
    if temp_url in DZEN_EMB_CACHE:
        del DZEN_EMB_CACHE[temp_url]
    
    return best_item, best_score 