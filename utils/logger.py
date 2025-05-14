import sys
import os
from loguru import logger
import datetime

from config import LOG_LEVEL

# Пути к файлам логов
LOG_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'storage', 'news_bot.log')

# Максимальное количество строк в лог-файле
MAX_LOG_LINES = 10000

class RotatingFileSink:
    """
    Кастомный sink для loguru, который ограничивает количество строк в файле лога.
    """
    def __init__(self, file_path, max_lines=10000):
        self.file_path = file_path
        self.max_lines = max_lines
        self.file = None
        self.line_count = 0
        self._initialize()
    
    def _initialize(self):
        # Подготавливаем директорию
        os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
        
        # Если файл существует, считаем количество строк
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    self.line_count = len(lines)
                
                # Если превышен лимит, оставляем только последние max_lines строк
                if self.line_count >= self.max_lines:
                    with open(self.file_path, 'w', encoding='utf-8') as f:
                        f.writelines(lines[-self.max_lines//2:])
                    self.line_count = self.max_lines // 2
                    
            except Exception as e:
                # В случае ошибки, создаем новый файл
                with open(self.file_path, 'w', encoding='utf-8') as f:
                    f.write(f"# Лог создан {datetime.datetime.now().isoformat()}\n")
                self.line_count = 1
        else:
            # Создаем новый файл
            with open(self.file_path, 'w', encoding='utf-8') as f:
                f.write(f"# Лог создан {datetime.datetime.now().isoformat()}\n")
            self.line_count = 1
        
        # Открываем файл для записи в режиме append
        self.file = open(self.file_path, 'a', encoding='utf-8')
    
    def write(self, message):
        """Запись сообщения в лог"""
        if self.file is None:
            self._initialize()
        
        self.file.write(message)
        self.file.flush()
        
        # Подсчет новых строк в сообщении
        new_lines = message.count('\n')
        self.line_count += new_lines
        
        # Если превышен лимит строк, перезапускаем лог
        if self.line_count > self.max_lines:
            self.file.close()
            
            # Читаем последние max_lines//2 строк
            with open(self.file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            # Записываем только половину от максимального количества строк
            with open(self.file_path, 'w', encoding='utf-8') as f:
                f.write(f"# Лог обрезан {datetime.datetime.now().isoformat()}, сохранено {self.max_lines//2} последних записей\n")
                f.writelines(lines[-(self.max_lines//2):])
            
            self.line_count = 1 + len(lines[-(self.max_lines//2):])
            self.file = open(self.file_path, 'a', encoding='utf-8')
    
    def close(self):
        """Закрытие файла"""
        if self.file:
            self.file.close()
            self.file = None

# Функция для ограничения размера файла лога
def truncate_log_file(log_path, max_lines=10000):
    """
    Обрезает файл лога, если он превышает максимальное количество строк.
    
    Args:
        log_path (str): Путь к файлу лога
        max_lines (int): Максимальное количество строк
    """
    if not os.path.exists(log_path):
        return
        
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # Если лог слишком большой, оставляем только последние max_lines строк
        if len(lines) > max_lines:
            with open(log_path, 'w', encoding='utf-8') as f:
                f.write(f"# Лог обрезан {datetime.datetime.now().isoformat()}, сохранено {max_lines} последних записей\n")
                f.writelines(lines[-max_lines:])
    except Exception as e:
        # В случае ошибки пишем в логи и продолжаем
        print(f"Ошибка при обрезке лог-файла {log_path}: {e}")

# Настройка логгера
def setup_logger():
    """
    Настройка логгера для проекта.
    Логи будут выводиться в консоль и в файл с ротацией.
    """
    # Удаляем стандартный обработчик
    logger.remove()
    
    # Форматирование логов
    log_format = "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    
    # Добавляем обработчик для вывода в консоль
    logger.add(
        sys.stderr,
        level=LOG_LEVEL,
        format=log_format,
    )
    
    # Создаем и добавляем sink для файла с ротацией
    file_sink = RotatingFileSink(LOG_FILE_PATH, max_lines=MAX_LOG_LINES)
    logger.add(
        file_sink.write,
        level=LOG_LEVEL,
        format=log_format,
    )
    
    return logger

logger = setup_logger() 