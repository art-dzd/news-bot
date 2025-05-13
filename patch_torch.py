# Патч для обхода ошибки "module 'torch' has no attribute 'compiler'"
# Этот файл нужно импортировать в main.py до импорта других модулей

import sys
import torch

# Проверяем, есть ли атрибут compiler в torch
if not hasattr(torch, 'compiler'):
    # Если нет, создаем заглушку
    class DummyCompiler:
        @staticmethod
        def compile(*args, **kwargs):
            # Просто возвращает исходную функцию без изменений
            if args and callable(args[0]):
                return args[0]
            return lambda x: x
            
        @staticmethod
        def disable(*args, **kwargs):
            # Декоратор, который ничего не делает, просто возвращает исходную функцию
            def decorator(fn):
                return fn
            return decorator
    
    # Добавляем заглушку в torch
    torch.compiler = DummyCompiler()
    
    print("Патч для torch.compiler применен")
