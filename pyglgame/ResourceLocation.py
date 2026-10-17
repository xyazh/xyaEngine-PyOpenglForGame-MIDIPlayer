"""兼容入口：``from pyglgame.ResourceLocation import ResourceLocation`` 照旧可用。

实现在 :mod:`pyglgame.resources.ResourceLocation`（重写后的资源层）：
``ResourceLocation(path)`` 的用法与行为**保持不变** —— 惰性读取、``toBytes()`` /
``getIO()`` / ``tryLoadJson()`` / 缓存键（``__hash__`` / ``__eq__``）都在；
新增的 :meth:`ResourceLocation.entry` / :meth:`ResourceLocation.setEntry` 用来拿
"虚拟文件映射"里的可调用对象（自定义加密 / 格式转换），见 ``docs/资源包.md``。
"""

from .resources.ResourceLocation import ResourceLocation

__all__ = ["ResourceLocation"]
