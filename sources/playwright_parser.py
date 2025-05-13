import os
import asyncio
from datetime import datetime
from playwright.async_api import async_playwright
from utils.models import NewsItem, MosruHistoryItem

async def fetch_mosru_news(url: str, max_items: int = 20):
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
        # Отключаю вывод сообщений браузера
        # page.on("console", lambda msg: print(f"[BROWSER CONSOLE] {msg.type}: {msg.text}"))
        # page.on("pageerror", lambda exc: print(f"[BROWSER ERROR] {exc}"))
        try:
            await page.goto(url, timeout=60000)
            await page.wait_for_timeout(4000)
        except Exception as e:
            print(f"[PLAYWRIGHT ERROR] Ошибка при переходе на {url}: {e}")
            await browser.close()
            return [], []
        news_items = []
        history_items = []
        url_set = set()
        now_iso = datetime.now().isoformat()
        # Универсальный парсинг для двух основных структур mos.ru
        if "search/newsfeed" in url:
            ul = await page.query_selector('div[class^="sc-AOXSc"] ul')
            if not ul:
                print('Не найден список новостей (ul)')
                await browser.close()
                return [], []
            cards = await ul.query_selector_all('li')
            for card in cards:
                a = await card.query_selector('a[href][target]')
                href = await a.get_attribute('href') if a else ''
                if href and not href.startswith('http'):
                    href = f'https://www.mos.ru{href}'
                href = normalize_mosru_url(href)
                if not href or href in url_set:
                    continue
                url_set.add(href)
                title_elem = await card.query_selector('h5[class*="Heading-Text"]')
                title = await title_elem.inner_text() if title_elem else ''
                snippet_elem = await card.query_selector('p[class*="Paragraph-Text"]')
                snippet = await snippet_elem.inner_text() if snippet_elem else ''
                if title:
                    news_items.append(
                        NewsItem(
                            title=title.strip(),
                            url=href,
                            source="mos.ru",
                            published_date=None,
                            snippet=snippet.strip(),
                            categories=[]
                        )
                    )
                    history_items.append(
                        MosruHistoryItem(
                            url=href,
                            title=title.strip(),
                            snippet=snippet.strip(),
                            added_at=now_iso,
                            in_dzen=False
                        )
                    )
                if len(news_items) >= max_items:
                    break
        else:
            cards = await page.query_selector_all('li.mos-oiv-news-page-list__item')
            for card in cards:
                a = await card.query_selector('a.mos-oiv-news-page__link')
                title = await a.inner_text() if a else ''
                href = await a.get_attribute('href') if a else ''
                if href and not href.startswith('http'):
                    href = f'https://www.mos.ru{href}'
                href = normalize_mosru_url(href)
                if not href or href in url_set:
                    continue
                url_set.add(href)
                snippet_elem = await card.query_selector('p.mos-oiv-news-page__text')
                snippet = await snippet_elem.inner_text() if snippet_elem else ''
                if title:
                    news_items.append(
                        NewsItem(
                            title=title.strip(),
                            url=href,
                            source="mos.ru",
                            published_date=None,
                            snippet=snippet.strip(),
                            categories=[]
                        )
                    )
                    history_items.append(
                        MosruHistoryItem(
                            url=href,
                            title=title.strip(),
                            snippet=snippet.strip(),
                            added_at=now_iso,
                            in_dzen=False
                        )
                    )
                if len(news_items) >= max_items:
                    break
        await browser.close()
        return news_items, history_items 

def normalize_mosru_url(url):
    # Убираем параметры, приводим к единому виду, всегда с завершающим /
    if not url:
        return url
    url = url.split('?')[0]
    if not url.endswith('/'):
        url += '/'
    return url 