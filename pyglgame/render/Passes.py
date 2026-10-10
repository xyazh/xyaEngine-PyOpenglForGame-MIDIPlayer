"""uber shader 的 ``fuc``（渲染函数）编号 —— 与 ``res/shader/dis.vert|frag`` 一一对应。

新增一种渲染模式时**三处都要改**：本文件的常量、``dis.vert`` 的 ``main()``、
``dis.frag`` 的 ``main()``。``tests/test_engine.py`` 里的
:class:`FucConstantsTest` / :class:`LightingShaderContractTest` 会读着色器源码
守着这个约定，漏改会直接测试失败（而不是静默渲染出错误画面）。

已有编号（历史遗留，保持数值不变）：

- ``DIS`` / ``WID``：世界空间 / 屏幕空间普通绘制（``passFuc()`` 返回这两个）
- ``HDR`` / ``PIN`` / ``BLM`` / ``MIX``：Bloom 链用到的后处理模式
- ``OIT`` / ``OIT_WID``：WBOIT 半透明 pass（``passFuc()`` 在半透明 pass 返回）
- ``SHADOW``：阴影贴图 pass（Phase 2），只做光空间变换、只写深度
- ``SHADOW2D``：2D 阴影遮罩 pass（Phase 7），走普通世界空间变换、片元只写 0
  （把遮挡体的阴影三角形画进"每盏投影灯一层"的屏幕空间遮罩，见
  ``pyglgame/render/ShadowMask2D.py``）
- ``OBJECT_ID``：Object ID / Mask pass（GPU Picking 用），走普通世界空间变换、
  片元只写"当前物体的唯一 ID"（不做光照/材质，见
  ``pyglgame/render/ObjectIdBuffer.py`` 与 ``pyglgame/render/ObjectIdRegistry.py``）
- ``OBJECT_ID_DEBUG``：把 Object ID 纹理画到屏幕上的调试模式（全屏 NDC 直通，
  按 ID 哈希成伪彩色，见 ``RenderGlobal.drawObjectIdDebug``）
- ``OBJECT_ID_SKIP``：Object ID pass 里的**屏幕空间**绘制（全屏背景 / UI 覆盖层）。
  它们不是"可拾取的几何"：整屏覆盖会把 ID 缓冲全部写坏、还会写深度挡住后面的物体，
  所以这个分支直接 ``discard``，既不写 ID 也不写深度（见 ``RenderGlobal.passFuc``）
"""

DIS = 0
WID = 1
HDR = 2
PIN = 3
BLM = 4
MIX = 5
OIT = 6
OIT_WID = 7
SHADOW = 8
SHADOW2D = 9
OBJECT_ID = 10
OBJECT_ID_DEBUG = 11
OBJECT_ID_SKIP = 12
