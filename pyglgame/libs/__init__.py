"""引擎自带的第三方库：物理后端（Box2D / pybullet）＋ 运行期依赖固化。

物理后端（Box2D / pybullet）
----------------------------

``Box2D`` 是 pybox2d 的编译扩展（``_Box2D.cp310-win_amd64.pyd``），**只有游戏
实际使用的 Python 3.10 能加载它**。放进包里，谁 import ``pyglgame`` 都能按相对
路径（``from ..libs import Box2D``）找到它，不用动 ``sys.path``，也不会因为
开发机上换了别的解释器就崩掉 —— 拿不到时由
:class:`pyglgame.physics.Backend.PhysicsUnavailable` 给出"该敲哪条命令"的报错。

``pybullet``（3D 后端）比较特殊：它是**顶层扩展模块**
（``pybullet.cp310-win_amd64.pyd`` + ``pybullet_utils`` 等顶层包），不是 ``pybullet/``
目录包，所以没法用相对导入 —— :func:`pyglgame.physics.Backend._vendored3D` 的做法是
**把本目录临时插进 ``sys.path``** 再 ``import pybullet``（只在真的用 3D 后端时发生）。
另外它**在 PyPI 上没有 cp310-win 的 wheel**，这份二进制是**本机源码编译**出来的：
编译/安装/瘦身的完整步骤见 ``docs/物理引擎-3D.md`` 第 1 节。

目录（物理后端）
----------------

| 路径 | 内容 |
|---|---|
| ``Box2D/`` | pybox2d 2.3.10（Box2D 2.3 C++）预编译包；已裁掉 ``examples/``、``tests/`` |
| ``Box2D/LICENSE.txt`` | zlib 许可（**不能删**，改源码/再分发时必须保留） |
| ``Box2D/METADATA.txt`` | 包元数据：来源、版本、依赖，便于日后升级 |
| ``pybullet.cp310-win_amd64.pyd`` | pybullet 3.2.7（Bullet 3）后端本体（5 MB，本机编译） |
| ``pybullet_utils/`` | pybullet 官方小工具包（可选保留） |
| ``pybullet-LICENSE.txt`` | zlib 许可（**不能删**） |
| ``pybullet-METADATA.txt`` | 包元数据 |

目录（运行期依赖固化）
----------------------

| 路径 | 内容 |
|---|---|
| ``numpy/`` ``numpy.libs/`` | numpy 2.2.6（含 BLAS 运行库） |
| ``cv2/`` | opencv-python 4.12.0.88（出片 / 图像处理 demo 用，105 MB） |
| ``PIL/`` | pillow 11.2.1（贴图解码 / 字体绘制） |
| ``OpenGL/`` ``OpenGL_accelerate/`` | PyOpenGL 3.1.9 与其 Cython 加速模块 |
| ``pyglm/`` | pyglm 2.8.2（``import glm``） |
| ``mido/`` | mido 1.3.3（读 MIDI，``Game4``） |
| ``line_profiler/`` | line_profiler 5.0.2（``Game4`` 的逐行火焰图） |
| ``keyboard/`` | keyboard 0.13.5（``BaseWindow`` 的可选按键钩子） |
| ``VENDOR.json`` | **固化清单**：ABI、Python 版本、每个包的版本/文件数/大小 |
| ``<包>/LICENSE*`` ``<包>.dist-info/…`` | 各包**自带的许可与元数据** |

这些包的**许可正文**另外汇总了一份随包分发：``LICENSES/``（索引 ``LICENSES/INDEX.md``，
由 ``tools/collect_licenses.py`` 生成），义务清单见仓库根的 ``THIRD_PARTY_NOTICES.md``。
**别删**各包自带的 ``LICENSE`` / ``dist-info`` —— 那是它们的署名，也是上面那份汇总的来源。

这些是 ``tools/vendor_libs.py`` **从解释器的 site-packages 整份复制**进来的，
目的与物理后端完全一样：让"游戏跑的是哪一份依赖"由仓库决定。刷新 / 校验：

```powershell
py -3.10 tools\vendor_libs.py            # 复制/刷新
py -3.10 tools\vendor_libs.py --verify   # 校验（缺文件返回码 1）
```

**运行期怎么生效**：``pyglgame/__init__.py`` → :mod:`pyglgame.tools.vendor`，
只在 ``VENDOR.json`` 的 ``abi`` 与当前解释器的 ``EXT_SUFFIX`` **逐字相同**时，
把本目录插到 ``sys.path[0]``；不一致就什么都不做（3.14 跑测试仍用 site-packages）。
打包时 ``build.bat`` 用 ``PYTHONPATH=pyglgame\libs`` 让 Nuitka 也从这一份解析。

完整说明（体积账、升级步骤、ABI 门控、环境变量开关）：``docs/依赖固化.md``。

重装 / 升级（装完可以把重新生成的 ``examples/``、``tests/``、``*.dist-info``、
``pybullet_data/``、``pybullet_envs/`` 删掉）::

    C:\\sapp\\Python310\\python.exe -m pip install --no-deps \\
        --target "<项目根>\\pyglgame\\libs" box2d==2.3.10
    C:\\sapp\\Python310\\python.exe -m pip install --no-deps \\
        --target "<项目根>\\pyglgame\\libs" "<编译好的>\\pybullet-3.2.7-cp310-cp310-win_amd64.whl"

用法
----

游戏代码请走 :mod:`pyglgame.physics`（``from pyglgame.physics import Physics2D, Physics3D``），
**不要**直接 ``import Box2D`` / ``import pybullet`` —— 懒加载、跨解释器报错、版本校验、
``sys.path`` 处理都在 :mod:`pyglgame.physics.Backend` 里。
"""
