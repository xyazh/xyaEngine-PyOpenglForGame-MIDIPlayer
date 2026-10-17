"""资源路径工具：**逻辑路径**与**真实路径**的判断 / 拼接（纯字符串，不碰磁盘）。

两种路径
--------

* **逻辑路径**（``res/shader/dis.vert``、``res/atlas``）—— 交给
  :class:`~pyglgame.resources.Resources.Resources` 去解析：它落到 ``res/`` 目录
  还是 ``res.xypack`` 封包由挂载表决定，调用方不用知道，所以封包之后照样能跑；
* **真实路径**（``C:/Windows/Fonts/msyh.ttc``、``output.mp4``、``.\\res\\x.png``）
  —— 绝对路径、游戏自己的产物、项目外塞进来的文件，一律由文件系统直接负责，
  **不走挂载表**。

分隔符
------

逻辑路径**内部统一成正斜杠**（:meth:`ResourcePath.normalize`），拼接结果用当前平台
的习惯分隔符（:meth:`ResourcePath.join` ≈ ``os.path.join``，只多一条约定：
**后一段是绝对路径时以它为准**）。

本模块是纯函数式的工具（全是静态方法），被 :mod:`pyglgame.resources` 其余部分共用。
"""

import os


class ResourcePath:
    """逻辑路径 / 真实路径的工具箱（不需要实例）。"""

    #: 逻辑路径统一使用的分隔符
    SEP = "/"

    #: ``blob:`` 前缀：内存里的"没有对应文件的资源"（运行时生成的贴图之类）
    BLOB_PREFIX = "blob:"

    @staticmethod
    def isBlob(path) -> bool:
        """是不是 ``blob:`` 伪路径（这类路径没有字节，但"存在"）。"""
        return str(path)[:len(ResourcePath.BLOB_PREFIX)] == ResourcePath.BLOB_PREFIX

    @staticmethod
    def isReal(path) -> bool:
        """是不是"真实文件系统路径"（绝对路径一律算；含盘符与 UNC）。"""
        return os.path.isabs(str(path))

    @staticmethod
    def normalize(name) -> str:
        """把名字统一成 ``a/b/c``：正斜杠、去 ``.``、``..`` 就地化简、去首尾斜杠。

        ``res\\shader\\dis.frag`` 与 ``res/shader/dis.frag``、``res/./shader/x.frag``
        归一化之后是**同一个键** —— 虚拟文件映射、缓存键都靠它对齐。
        """
        parts = []
        for part in str(name or "").replace("\\", "/").split("/"):
            if part in ("", "."):
                continue
            if part == ".." and parts:
                parts.pop()
                continue
            parts.append(part)
        return "/".join(parts)

    @staticmethod
    def join(*parts) -> str:
        """拼接路径片段，返回**真实习惯的分隔符**（Windows 上就是反斜杠）。

        与 :func:`os.path.join` 的唯一区别：**后一段是绝对路径时以它为准**
        （场景文档里偶尔会引用绝对路径的外部贴图）。

        ⚠ 这里刻意**不**产出"正斜杠的逻辑路径"：逻辑性（首段是不是挂载前缀）由
        :class:`~pyglgame.resources.Resources.Resources` 在查找时用
        :meth:`normalize` 归一化，上层不用、也不该关心分隔符长什么样 ——
        否则路径字符串形态会扩散到全引擎，"同一份资源两种写法"就会到处冒出来。
        """
        result = ""
        for part in parts:
            part = str(part or "")
            if not part:
                continue
            if ResourcePath.isReal(part):
                result = part                  # 绝对路径出现 → 以它为准（前面积累的作废）
                continue
            if result in ("", "."):
                result = part
            else:
                result = result.rstrip("/\\") + "/" + part.lstrip("/\\")
        if not result:
            return ""
        return os.path.normpath(result)

    @staticmethod
    def basename(path) -> str:
        """逻辑路径 / 真实路径的"文件名"（最后一段）。"""
        text = str(path).rstrip("/\\")
        if ResourcePath.isReal(text):
            return os.path.basename(text)
        if "/" not in text and "\\" not in text:
            return text
        return text.replace("\\", "/").rsplit("/", 1)[-1]

    @staticmethod
    def dirname(path) -> str:
        """逻辑路径的"所在目录"（真实路径走 :func:`os.path.dirname`）。"""
        path = str(path)
        if ResourcePath.isReal(path):
            return os.path.dirname(path)
        normalized = ResourcePath.normalize(path)
        if "/" not in normalized:
            return ""
        return normalized.rsplit("/", 1)[0]

    @staticmethod
    def isLogical(path, prefix: str = None) -> bool:
        """是不是"该走挂载表"的逻辑路径（非绝对路径、非 ``blob:``）。

        给了 ``prefix`` 才问"是不是落在这个前缀下"（``res/shader/x`` 落在 ``res`` 下）。
        """
        text = str(path or "")
        if not text or ResourcePath.isBlob(text) or ResourcePath.isReal(text):
            return False
        if prefix is None:
            return True
        normalized = ResourcePath.normalize(text)
        prefix = ResourcePath.normalize(prefix)
        if not prefix:
            return True
        return normalized == prefix or normalized.startswith(prefix + "/")

    @staticmethod
    def normcase(path) -> str:
        """缓存键里统一大小写（Windows 不区分大小写，避免同一文件两个缓存条目）。"""
        return os.path.normcase(str(path)).replace("\\", "/")
