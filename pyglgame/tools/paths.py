"""项目路径解析：``app_root`` / ``project_root`` / ``libs_path``（+ 历史遗留的 ``res_path``）。

**资源路径**请写逻辑路径（``res/shader/dis.vert``）交给
:mod:`pyglgame.resources`/:class:`~pyglgame.ResourceLocation.ResourceLocation` ——
目录形态与 ``res.xypack`` 资源包形态都能读，见 ``docs/资源包.md``。

本模块只负责"项目/可执行文件在哪"，不再替调用方决定资源怎么找；
``res_path()`` 是历史遗留（只为"资源在项目外/必须给出真实路径"这类特殊情况保留）。
"""

import os
import sys

# Nuitka 编译后会注入 __compiled__，PyInstaller 会注入 sys.frozen
def app_root() -> str:
    """返回应作为 ``res`` 上级的目录。

    - 源码运行：项目根目录（``pyglgame`` 的上一级）
    - 打包运行：可执行文件所在目录
    """
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return os.path.dirname(os.path.abspath(sys.argv[0] if sys.argv else sys.executable))
    return project_root()


def project_root() -> str:
    """返回项目根目录（包含 ``pyglgame`` 包与 ``res`` 目录的那一级）。"""
    here = os.path.dirname(os.path.abspath(__file__))   # .../pyglgame/tools
    package = os.path.dirname(here)                     # .../pyglgame
    return os.path.dirname(package)


def res_path(*parts: str) -> str:
    """拼接 ``res`` 目录下的绝对路径（**历史遗留**，新代码别再用）。

    ⚠ 引擎内部已经一处都不用了：资源请写**逻辑路径**
    （``ResourceLocation("res/shader/dis.vert")``），由 :mod:`pyglgame.resources`
    决定落到 ``res/`` 目录还是 ``res.xypack`` 资源包 —— 写死绝对路径就封不了包、
    换个工作目录也会错。这个函数只为特殊情况保留：需要把**真实文件路径**交给
    外部程序、或者明确知道资源没被打进包。
    """
    return os.path.join(app_root(), "res", *parts)


def libs_path(*parts: str) -> str:
    """拼接引擎自带第三方库目录（``pyglgame/libs``）下的绝对路径。

    物理后端（Box2D）就放在这里：``pyglgame/libs/Box2D/_Box2D.cp310-win_amd64.pyd``。
    它是预编译扩展，**只有游戏用的 Python 3.10 能加载**，所以随包分发而不是装进
    全局 site-packages（换解释器就跑不起来）。见 ``pyglgame/physics/Backend.py``。
    """
    return os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "libs", *parts))
