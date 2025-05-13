import yaml
from datetime import datetime, timedelta
from utils.logger import logger
from utils.models import NewsItem, DzenHistoryItem, MosruHistoryItem
from utils.similarity import is_similar_title, normalize_text, load_keywords, find_best_match, calculate_similarity_sbert, count_common_words
from config import DZEN_MOSCOW_URL, TIMEZONE, MAX_NEWS_AGE_DAYS, SBERT_SIMILARITY_THRESHOLD
from sources.playwright_parser import normalize_mosru_url
from storage.s3 import s3_storage
import os

async def fetch_dzen_news(mosru_news=None, mosru_history=None, dzen_history_urls=None, max_items=20):
    if mosru_news is None:
        mosru_news = []
    if mosru_history is None:
        mosru_history = []
    if dzen_history_urls is None:
        dzen_history_urls = set()
    # Загрузка ключевых слов
    keywords_path = os.path.join(os.path.dirname(__file__), '../filters/keywords.yaml')
    with open(keywords_path, 'r', encoding='utf-8') as f:
        keywords_data = yaml.safe_load(f)
    raw_keywords = keywords_data.get('topics', [])
    # Лемматизируем ключевые слова
    norm_keywords = set()
    for kw in raw_keywords:
        if not isinstance(kw, str):
            continue
        norm_keywords.add(normalize_text(kw))

    # Фильтруем mosru_history по дате (только последние MAX_NEWS_AGE_DAYS)
    now = datetime.now(TIMEZONE)
    recent_mosru = []
    for item in mosru_history:
        try:
            added_at = datetime.fromisoformat(item.added_at)
            if added_at.tzinfo is None:
                added_at = added_at.replace(tzinfo=TIMEZONE)
        except Exception:
            continue
        if (now - added_at).days <= MAX_NEWS_AGE_DAYS:
            # Нормализуем url для сравнения
            item.url = normalize_mosru_url(item.url)
            recent_mosru.append(item)

    filtered_dzen_news = []
    filtered_dzen_history = []
    url_set = set()
    filtered_out_urls = []  # URL, которые не прошли фильтры
    already_analyzed_count = 0

    # --- Новый блок: подготовка истории Дзена по нормализованным заголовкам ---
    dzen_history_raw = s3_storage.load_dzen_history()
    # Словарь: нормализованный заголовок -> DzenHistoryItem
    dzen_title_map = {}
    for item in dzen_history_raw:
        norm_title = normalize_text(item['title'])
        dzen_title_map[norm_title] = item
    # --- ---

    from playwright.async_api import async_playwright
    headless_env = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower()
    headless = headless_env == "true"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu"
        ])
        page = await browser.new_page()

        try:
            await page.goto(DZEN_MOSCOW_URL, timeout=60000)
            await page.wait_for_timeout(4000)
        except Exception as e:
            print(f"[PLAYWRIGHT ERROR] Ошибка при переходе на {DZEN_MOSCOW_URL}: {e}")
            await browser.close()
            return [], []
        cards = await page.query_selector_all('div[data-testid="news-item"]')
        found_news_count = len(cards)
        logger.info(f"Найдено {found_news_count} новостей на странице Дзен")
        
        now_iso = now.isoformat()
        for card in cards[:max_items]:
            a = await card.query_selector('a[href]')
            url = await a.get_attribute('href') if a else ''
            if url and not url.startswith('http'):
                url = f'https://dzen.ru{url}'
            # Нормализация ссылки Дзена: только до знака вопроса
            if url:
                url = url.split('?')[0]
            title_elem = await card.query_selector('p[class*="desktop2--card-top-avatar__text-Pu"]')
            title = await title_elem.inner_text() if title_elem else ''
            # Сниппет Дзена (пока не используется, но задел на будущее)
            dzen_snippet = None
            if not url or not title or url in url_set:
                continue
            
            # --- Проверка ранее проанализированных URL ---
            if s3_storage.is_url_analyzed(url):
                already_analyzed_count += 1
                continue
            # --- ---
            
            url_set.add(url)
            # --- Новый блок: проверка по нормализованному заголовку ---
            norm_title = normalize_text(title)
            if norm_title in dzen_title_map:
                # Уже есть новость с таким заголовком, но возможно другой url
                old_item = dzen_title_map[norm_title]
                if old_item['url'] != url:
                    # Обновляем url в истории (и дату)
                    old_item['url'] = url
                    old_item['added_at'] = now_iso
                    # Сохраняем обновлённую историю
                    dzen_history_raw = [i if normalize_text(i['title']) != norm_title else old_item for i in dzen_history_raw]
                    s3_storage.save_dzen_history(dzen_history_raw)
                # Не считаем новость новой для отправки
                continue
            # --- ---
            # 1. Пропускаем, если уже есть в истории Дзена
            if url in dzen_history_urls:
                continue
            # 2. Проверяем схожесть с mosru_history за последние дни с использованием SBERT
            best_mosru, best_score = find_best_match(title, recent_mosru)
            if best_mosru and best_score >= SBERT_SIMILARITY_THRESHOLD:
                # Проверяем, был ли этот URL mos.ru уже использован как источник ранее
                mosru_url_already_used = False
                for item in dzen_history_raw:
                    # Проверяем только записи с mos.ru и SBERT
                    if item.get('match_type') == 'sbert' and item.get('mosru_source_url') == best_mosru.url:
                        # Получаем текущий score из истории
                        previous_score = item.get('similarity_score', 0)
                        # Если новый score ниже чем предыдущий - пропускаем новость
                        if best_score < previous_score:
                            logger.info(f"URL mos.ru '{best_mosru.url}' уже был использован как источник с более высоким score ({previous_score:.3f}). Текущий score: {best_score:.3f}. Пропускаем.")
                            mosru_url_already_used = True
                            # Сохраняем URL, который не прошел фильтры
                            filtered_out_urls.append(url)
                            break
                
                # Если URL уже использован с более высоким score, пропускаем текущую новость
                if mosru_url_already_used:
                    continue
                
                logger.info(f"Найден первоисточник mos.ru для новости Дзена '{title}' — схожесть: {best_score:.3f}")
                filtered_dzen_news.append(
                    NewsItem(
                        title=title.strip(),
                        url=url,
                        source="Дзен",
                        published_date=now,
                        snippet=None
                    )
                )
                filtered_dzen_history.append(
                    DzenHistoryItem(
                        url=url,
                        title=title.strip(),
                        added_at=now_iso,
                        mosru_source_url=best_mosru.url,
                        mosru_title=best_mosru.title,
                        mosru_snippet=best_mosru.snippet,
                        similarity_score=best_score,
                        match_type="sbert",
                        common_words=count_common_words(title, best_mosru.title)
                    )
                )
                continue
            # 3. Проверяем по ключевым словам
            title_norm = normalize_text(title)
            matched_keywords = []
            for kw in norm_keywords:
                if kw in title_norm:
                    matched_keywords.append(kw)
            
            if matched_keywords:
                logger.info(f"Найдены ключевые слова в новости Дзена '{title}': {', '.join(matched_keywords[:3])}")
                filtered_dzen_news.append(
                    NewsItem(
                        title=title.strip(),
                        url=url,
                        source="Дзен",
                        published_date=now,
                        snippet=None
                    )
                )
                filtered_dzen_history.append(
                    DzenHistoryItem(
                        url=url,
                        title=title.strip(),
                        added_at=now_iso,
                        match_type="keywords",
                        matched_keywords=matched_keywords[:5]
                    )
                )
            else:
                # Сохраняем URL, который не прошел фильтры
                filtered_out_urls.append(url)
                
        await browser.close()
    
    # --- Сохраняем отфильтрованные URL ---
    if filtered_out_urls:
        s3_storage.add_analyzed_urls(filtered_out_urls)
        logger.info(f"Сохранено {len(filtered_out_urls)} URL, не прошедших фильтры")
    # ---
    
    # --- Сохраняем URL новостей, прошедших фильтры ---
    if filtered_dzen_news:
        passed_urls = [news.url for news in filtered_dzen_news]
        s3_storage.add_analyzed_urls(passed_urls)
        logger.info(f"Сохранено {len(passed_urls)} URL, прошедших фильтры")
    # ---
    
    # --- Логирование результатов фильтрации ---
    total_processed = len(filtered_dzen_news) + len(filtered_out_urls)
    logger.info(f"Результаты обработки Дзен: всего найдено {found_news_count}, уже проанализировано ранее {already_analyzed_count}, "
                f"прошли фильтры {len(filtered_dzen_news)}, отфильтровано {len(filtered_out_urls)}, всего обработано {total_processed}")
    # ---
    
    return filtered_dzen_news, filtered_dzen_history 