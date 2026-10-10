"""把项目固化的第三方依赖（``pyglgame/libs``）接进 ``sys.path``。

为什么要有这一步、固化哪些包、怎么刷新：见 ``tools/vendor_libs.py``。

⚠ **只在 ABI 完全一致时启用**：固化的二进制是给游戏解释器编的
（例如 ``.cp310-win_amd64.pyd``，记在 ``pyglgame/libs/VENDOR.json`` 的 ``abi``
字段里）。拿 3.14 跑测试时如果让它抢到 ``sys.path`` 最前面，``numpy``/``PIL``
会去加载 cp310 的扩展 → 直接 ImportError。所以 ABI 不匹配就什么都不做，
让它继续用 site-packages 里那一套。

两个环境变量（排查用，不要写进游戏代码）：

=====================  ====================================================
``XYA_NO_VENDOR=1``    这次不用固化依赖（怀疑"是不是固化那份有问题"时）
``XYA_VENDOR_FORCE=1`` 即使 ABI 不匹配也强行接进 sys.path（仅调试）
=====================  ====================================================
"""

import json
import os
import sys
import sysconfig

#: 固化目录：``<项目根>/pyglgame/libs``（本文件在 ``pyglgame/tools/`` 下）
_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_VENDOR_DIR = os.path.join(os.path.dirname(_TOOLS_DIR), "libs")
_MANIFEST = os.path.join(_VENDOR_DIR, "VENDOR.json")

_ENV_DISABLE = "XYA_NO_VENDOR"
_ENV_FORCE = "XYA_VENDOR_FORCE"

_activated = False


def vendorDir() -> str:
    """固化目录（``pyglgame/libs``）的绝对路径。"""
    return _VENDOR_DIR


def abiTag() -> str:
    """当前解释器的扩展模块后缀，例如 ``.cp310-win_amd64.pyd``。"""
    return sysconfig.get_config_var("EXT_SUFFIX") or ""


def manifest():
    """读固化清单（没固化过返回 ``None``）。"""
    try:
        with open(_MANIFEST, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def isVendored() -> bool:
    """当前这次运行是否真的在用固化依赖。"""
    return _activated


def status() -> dict:
    """给日志/自检用的一句话状态（版本、ABI、是否启用）。"""
    info = manifest() or {}
    return {
        "dir": _VENDOR_DIR,
        "manifest": bool(info),
        "abi": info.get("abi", ""),
        "current_abi": abiTag(),
        "python": info.get("python", ""),
        "packages": {entry["module"]: entry["version"]
                     for entry in info.get("packages", [])},
        "activated": _activated,
    }


def activateVendoredLibs(force: bool = False) -> bool:
    """把固化目录放到 ``sys.path`` 最前面；返回最终是否启用。

    幂等；``force=True`` 时忽略 ABI 检查（调试用）。
    """
    global _activated
    if _activated:
        return True
    if os.environ.get(_ENV_DISABLE):
        return False
    if not os.path.isdir(_VENDOR_DIR):
        return False
    info = manifest()
    if info is None:
        # 没有清单说明还没固化：不要瞎猜，直接不动 sys.path
        return False
    agreed = force or bool(os.environ.get(_ENV_FORCE))
    if not agreed and info.get("abi") != abiTag():
        return False
    if _VENDOR_DIR not in sys.path:
        sys.path.insert(0, _VENDOR_DIR)
    _activated = True
    return True
