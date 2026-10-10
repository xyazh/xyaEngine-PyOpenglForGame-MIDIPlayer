from functools import update_wrapper
import types


class MethodWrapper:
    def __init__(self, *args) -> None:
        self._methods = []
        for method in args:
            if not callable(method):
                raise TypeError(f"{method} is not callable")
            self._methods.append(method)
            update_wrapper(self, method)

    def __get__(self, obj, cls):
        if obj is None:
            return self
        return types.MethodType(self, obj)

    def __call__(self, *args, **kwargs):
        result = None
        for method in self._methods:
            result = method(*args, **kwargs)
        return result

    def __add__(self, other):
        if isinstance(other, MethodWrapper):
            self._methods.extend(other._methods)
        elif callable(other):
            self._methods.append(other)
        else:
            raise TypeError(f"Can't add {type(other)}")
        return self

    def __iadd__(self, other):
        return self.__add__(other)

    @property
    def methods(self):
        return self._methods