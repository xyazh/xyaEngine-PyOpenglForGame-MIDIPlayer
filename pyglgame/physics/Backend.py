"""Box2D 后端的定位与加载（**唯一** import Box2D 的地方）。

为什么要有这一层
----------------

pybox2d 是**编译好的扩展**（``pyglgame/libs/Box2D/_Box2D.cp310-win_amd64.pyd``），
只有游戏实际使用的 **Python 3.10** 能加载它。而引擎的模块在别的解释器
（例如开发机上的 3.14）里也要能被 import —— ``tests/test_imports.py`` 就会把
``pyglgame.*`` 全部 import 一遍。所以：

- 本模块**模块级不 import Box2D**：真正的 import 推迟到第一次用（:func:`load`）；
- 拿不到后端时抛 :class:`PhysicsUnavailable`，错误信息里直接给出该敲的命令；
- 后端随包分发（``from ..libs import Box2D``），
  **不需要**改 ``sys.path``，编辑器也能正常解析这个导入；
- 退路：如果哪天全局 site-packages 里也装了一份 ``Box2D``（例如你给某个虚拟环境
  装了自编译的 pybox2d），也能用。
"""

import os
import sys

from ..tools.paths import libs_path

_module = None
_error: str = None
_module_3d = None
_error_3d: str = None

#: 判断"拿到的确实是我们那个 Box2D"（防止同名 stub 包混进来）
_REQUIRED = ("b2World", "b2Body", "b2Fixture", "b2ContactListener",
             "b2RayCastCallback", "b2QueryCallback")


class PhysicsUnavailable(RuntimeError):
    """物理后端不可用（Box2D 没装 / 装错 Python / 缺 DLL）。"""


def _vendored():
    """引擎包里自带的那份（``pyglgame/libs/Box2D``）。"""
    from ..libs import Box2D
    return Box2D


def _global():
    """全局 site-packages 里的那份（可选退路）。"""
    import Box2D
    return Box2D


def _extensionTags() -> list[str]:
    """列出自带的后端扩展文件名（``_Box2D.cp310-win_amd64.pyd`` 这种）。"""
    directory = libs_path("Box2D")
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    return sorted(name for name in names
                  if name.startswith("_Box2D") and name.endswith(".pyd"))


def _tagHint() -> str:
    """解释器与自带扩展不匹配时给一句直白的话（否则返回空串）。

    比"cannot import name '_Box2D'"友好得多 —— 那是 cp310 的扩展在 3.14 里的
    真实报错，``pip`` 装的时候看不见，跑起来才发现。
    """
    tags = _extensionTags()
    if not tags:
        return ""
    current = f".cp{_pythonTag()}-"
    if any(current in tag for tag in tags):
        return ""
    return (f"（引擎自带的扩展是 {', '.join(tags)}，当前解释器 {pythonHint()} 的名称不匹配"
            f" → 请用 Python 3.10 运行）")


def _pythonTag() -> str:
    return f"{sys.version_info.major}{sys.version_info.minor}"


def _message(reason: str) -> str:
    target = libs_path()
    return (
        "物理后端（Box2D）不可用：" + reason + _tagHint() + "\n"
        "物理用的 pybox2d 是预编译扩展，只有**游戏用的 Python 3.10** 能加载它。\n"
        "  1) 用游戏自带的解释器跑 demo / 测试：\n"
        "     C:\\sapp\\Python310\\python.exe Game7.py\n"
        "  2) 重新安装预编译包到引擎目录（不要装全局）：\n"
        "     <python3.10> -m pip install --no-deps --target \"" + target + "\" box2d==2.3.10\n"
        "  3) 检查 " + target + "\\Box2D\\_Box2D.cp310-win_amd64.pyd 是否还在。"
    )


def load():
    """导入并返回 ``Box2D`` 模块（带缓存）；不可用时抛 :class:`PhysicsUnavailable`。"""
    global _module, _error
    if _module is not None:
        return _module
    problems = []
    for name, loader in (("引擎自带 pyglgame/libs/Box2D", _vendored),
                         ("全局 site-packages", _global)):
        try:
            module = loader()
        except Exception as exc:                  # ModuleNotFoundError / ImportError(DLL)
            problems.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        missing = [field for field in _REQUIRED if not hasattr(module, field)]
        if missing:
            problems.append(f"{name}: 缺少 {', '.join(missing)}（是不是别的同名包？）")
            continue
        _module = module
        _error = None
        return module
    _error = _message("没有可用的 Box2D —— " + "；".join(problems))
    raise PhysicsUnavailable(_error)


def available() -> bool:
    """后端是否可用（**不抛异常**，供测试/界面里做能力探测）。"""
    try:
        load()
    except PhysicsUnavailable:
        return False
    return True


def version() -> str:
    """后端版本号（不可用时返回空串）。"""
    try:
        return str(getattr(load(), "__version__", ""))
    except PhysicsUnavailable:
        return ""


def lastError() -> str:
    """最近一次失败原因（``available()`` 为 True 时是 ``None``）。"""
    return _error


def modulePath() -> str:
    """后端包的实际路径（调试用；不可用时返回空串）。"""
    try:
        return str(getattr(load(), "__file__", ""))
    except PhysicsUnavailable:
        return ""


def pythonHint() -> str:
    """当前解释器是不是"游戏用的那一个"（3.10）。"""
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def reset() -> None:
    """丢弃缓存（测试里用来模拟"后端不可用"）。"""
    global _module, _error, _module_3d, _error_3d
    _module = None
    _error = None
    _module_3d = None
    _error_3d = None


# === 3D 后端：pybullet（Bullet 3）===
#
# 和 2D 同一套路：模块级不 import、懒加载、失败给"能照着做"的报错。
# 区别：pybullet 在 PyPI 上**没有 cp310-win 的 wheel**，所以引擎里这份是
# 本机编译出来的（见 docs/物理引擎-3D.md 的"后端与环境"）。

#: 判断"拿到的确实是我们要的 pybullet"
_REQUIRED_3D = ("connect", "DIRECT", "disconnect", "resetSimulation",
                "createCollisionShape", "createMultiBody", "stepSimulation",
                "getBasePositionAndOrientation", "resetBasePositionAndOrientation",
                "rayTest", "getContactPoints", "getOverlappingObjects",
                "createConstraint")


def _vendored3D():
    """引擎包里自带的那份。

    ⚠ pybullet 不是"可嵌套的包"，而是**顶层扩展模块**：
    ``pyglgame/libs/pybullet.cp310-win_amd64.pyd``（外加 ``pybullet_utils`` 等顶层包）。
    所以这里只能把 ``pyglgame/libs`` 临时插进 ``sys.path`` 再 ``import pybullet`` ——
    只在真的要用 3D 后端时才发生，且只插一次。
    """
    import sys

    directory = libs_path()
    if directory not in sys.path:
        sys.path.insert(0, directory)
    import pybullet
    return pybullet


def _global3D():
    """全局 site-packages 里的那份（可选退路）。"""
    import pybullet
    return pybullet


def _message3D(reason: str) -> str:
    target = libs_path("pybullet")
    return (
        "3D 物理后端（pybullet）不可用：" + reason + "\n"
        "pybullet 在 PyPI 上**没有 Python 3.10 + Windows 的预编译 wheel**，"
        "需要源码编译一次：\n"
        "  1) 确认工具链：Visual Studio 2022（含 C++ 工具集）+ CMake\n"
        "     （VS 自带 CMake：C:\\Program Files\\Microsoft Visual Studio\\2022\\"
        "Community\\Common7\\IDE\\CommonExtensions\\Microsoft\\CMake\\CMake\\bin）\n"
        "  2) 编译并装到引擎目录（不要装全局）：\n"
        "     <python3.10> -m pip wheel pybullet --no-deps -w <临时目录>\n"
        "     <python3.10> -m pip install --no-deps --target \"" + libs_path() + "\" "
        "<临时目录>\\pybullet-*.whl\n"
        "  3) 检查 " + target + " 目录与里面的 pybullet.pyd 是否还在。\n"
        "  4) 用游戏自带的解释器运行：C:\\sapp\\Python310\\python.exe"
    )


def load3D():
    """导入并返回 ``pybullet`` 模块（带缓存）；不可用时抛 :class:`PhysicsUnavailable`。"""
    global _module_3d, _error_3d
    if _module_3d is not None:
        return _module_3d
    problems = []
    for name, loader in (("引擎自带 pyglgame/libs/pybullet", _vendored3D),
                         ("全局 site-packages", _global3D)):
        try:
            module = loader()
        except Exception as exc:                  # ModuleNotFoundError / ImportError(DLL)
            problems.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        missing = [field for field in _REQUIRED_3D if not hasattr(module, field)]
        if missing:
            problems.append(f"{name}: 缺少 {', '.join(missing)}（是不是别的同名包？）")
            continue
        _module_3d = module
        _error_3d = None
        return module
    _error_3d = _message3D("没有可用的 pybullet —— " + "；".join(problems))
    raise PhysicsUnavailable(_error_3d)


def available3D() -> bool:
    """3D 后端是否可用（**不抛异常**）。"""
    try:
        load3D()
    except PhysicsUnavailable:
        return False
    return True


def version3D() -> str:
    """3D 后端版本（``pybullet.getAPIVersion()``；不可用时返回空串）。"""
    try:
        module = load3D()
        return str(module.getAPIVersion())
    except Exception:
        return ""


def lastError3D() -> str:
    """最近一次 3D 后端失败原因。"""
    return _error_3d


def modulePath3D() -> str:
    """3D 后端包的实际路径（调试用；不可用时返回空串）。"""
    try:
        return str(getattr(load3D(), "__file__", ""))
    except PhysicsUnavailable:
        return ""
