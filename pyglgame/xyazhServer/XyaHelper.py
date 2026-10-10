import time
import functools

class XyaHelper:

    @staticmethod
    def timeit(fuc):
        @functools.wraps(fuc)
        def wrapper(*args, **kwargs):
            start = time.time()
            result = fuc(*args, **kwargs)
            end = time.time()
            print(f"{fuc.__name__} 耗时 {end - start} 秒")
            return result
        return wrapper