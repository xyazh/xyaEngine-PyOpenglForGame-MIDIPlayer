"""pyglgame 引擎包。

导入这个包时会做一件**有副作用**的事：把项目固化下来的第三方依赖
（``pyglgame/libs``，见 ``tools/vendor_libs.py``）接到 ``sys.path`` 最前面 ——
这样"游戏跑的是哪一份 numpy / PIL / OpenGL / cv2"由**仓库**决定，而不是由
开发机上的 site-packages 决定。

只在 ABI 完全一致时才接（固化的是 cp310-win_amd64 那套）；拿 3.14 跑测试时
它什么都不做，测试照旧用 site-packages 里的版本。细节与开关见
:mod:`pyglgame.tools.vendor`。
"""

from .tools.vendor import activateVendoredLibs, isVendored, status  # noqa: F401

__all__ = ["activateVendoredLibs", "isVendored", "status"]

activateVendoredLibs()

