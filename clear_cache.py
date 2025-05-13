#!/usr/bin/env python3
"""
Скрипт для ручной очистки кэша эмбеддингов.
Используйте его, если нужно освободить память на сервере.
"""
import sys
import logging
from datetime import datetime, timedelta
from storage.s3 import s3_storage
from utils.similarity import cleanup_cache, DZEN_EMB_CACHE, MOSRU_EMB_CACHE

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

def main():
    try:
        # Вывод информации о текущем состоянии кэша
        logger.info(f"Текущий размер кэша эмбеддингов:")
        logger.info(f"- Дзен: {len(DZEN_EMB_CACHE)} записей")
        logger.info(f"- Mos.ru: {len(MOSRU_EMB_CACHE)} записей")
        
        # Информация о возрасте эмбеддингов в кэше
        now = datetime.now().timestamp()
        dzen_old = sum(1 for data in DZEN_EMB_CACHE.values() 
                     if (now - data.get('timestamp', 0)) > 3 * 24 * 3600)
        mosru_old = sum(1 for data in MOSRU_EMB_CACHE.values() 
                      if (now - data.get('timestamp', 0)) > 3 * 24 * 3600)
        
        logger.info(f"Записи старше 3 дней:")
        logger.info(f"- Дзен: {dzen_old} записей")
        logger.info(f"- Mos.ru: {mosru_old} записей")
        
        # Подтверждение от пользователя
        if len(sys.argv) > 1 and sys.argv[1] == "--force":
            user_input = "y"
        else:
            user_input = input("Очистить кэш эмбеддингов? (y/n): ").lower()
        
        if user_input != "y":
            logger.info("Операция отменена.")
            return
        
        # Загружаем URL из истории, которые нужно сохранить в кэше
        logger.info("Загрузка истории новостей...")
        mosru_history = s3_storage.load_mosru_history()
        dzen_history = s3_storage.load_dzen_history()
        
        # Собираем все URL, которые нужно сохранить в кэше
        keep_urls = set()
        for item in mosru_history:
            if isinstance(item, dict):
                keep_urls.add(item.get('url', ''))
            else:
                keep_urls.add(getattr(item, 'url', ''))
        
        for item in dzen_history:
            if isinstance(item, dict):
                keep_urls.add(item.get('url', ''))
                keep_urls.add(item.get('mosru_source_url', ''))
            else:
                keep_urls.add(getattr(item, 'url', ''))
                keep_urls.add(getattr(item, 'mosru_source_url', ''))
        
        # Очищаем кэш, сохраняя только нужные URL
        keep_urls = {url for url in keep_urls if url}  # Удаляем пустые URL
        logger.info(f"Сохраняем в кэше {len(keep_urls)} URL из истории")
        
        # Параметры очистки кэша
        max_age_days = 3
        if len(sys.argv) > 2 and sys.argv[1] == "--age":
            try:
                max_age_days = int(sys.argv[2])
                logger.info(f"Установлен максимальный возраст: {max_age_days} дней")
            except:
                logger.warning(f"Неверный формат возраста, используется значение по умолчанию: {max_age_days} дней")
        
        stats = cleanup_cache(keep_urls, max_age_days=max_age_days)
        
        # Вывод информации о результатах очистки
        logger.info(f"Результаты очистки кэша:")
        logger.info(f"- Дзен: было {stats['dzen_before']}, удалено {stats['dzen_cleared']}, осталось {stats['dzen_after']}")
        logger.info(f"- Mos.ru: было {stats['mosru_before']}, удалено {stats['mosru_cleared']}, осталось {stats['mosru_after']}")
        
        # Информация о состоянии памяти
        import psutil
        process = psutil.Process()
        memory_info = process.memory_info()
        memory_mb = memory_info.rss / 1024 / 1024
        logger.info(f"Текущее использование памяти: {memory_mb:.1f} МБ")
        
        logger.info("Очистка кэша завершена успешно!")
    
    except Exception as e:
        logger.error(f"Ошибка при очистке кэша: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    sys.exit(main()) 