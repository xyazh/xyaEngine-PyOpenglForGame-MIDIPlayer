"""滤镜基类：把"输入纹理 → 输出纹理"这一步统一起来。

## 为什么是 FBO + 全屏四边形（而不是 compute shader）

Bloom 走 compute（image unit）是因为它的 ping-pong 链要在同一张缓冲上反复读写；
对"给定一张输入、写一张输出"的通用滤镜，**fragment + FBO 更合适**：

- 输入用 ``sampler2D`` 采样 → 任何格式都行（RGBA8 的普通贴图、HDR 缓冲、
  甚至深度纹理）；
- 输出可以是**调用方给的任意纹理**（只要求能挂到 color attachment 上），
  不受 image unit 的格式限定符约束（``layout(rgba8)`` / ``rgba32f`` 必须与
  纹理格式完全一致，否则 ``glBindImageTexture`` 就报错）；
- 需要 ping-pong 的（FFT）仍然可以走 compute —— :class:`FillerBase` 不挡这条路，
  ``FFTFilters`` 就是"compute 算完再用拷贝滤镜落到任意格式的目标纹理"。

## 统一约定

.. code-block:: python

    filler = GrayscaleFilter(rec709=True)

    filler.filler(src_texture, dst_texture)      # 两个参数都是 TextureBase

- ``filler`` 返回 ``dst_texture``，方便链式书写：``f = a.filler(a, b); b.filler(b, c)``
- 单输入滤镜用 ``tex0`` 采样；需要第二张输入的（锐化、Camera Raw 的低通图）用 ``tex1``
- 滤镜**不留下任何状态**：结束时还原混合/深度/视口/当前 program
  （否则调用方后面的 draw 会在"无深度测试"下画，见 RenderState 的说明）
"""

import numbers
from OpenGL.GL import *

from ..RenderGlobal import RenderGlobal
from ..render import RenderState
from ..render.RenderBuffer import RenderBuffer
from ..shader.Shader import Shader
from ..shader.ShaderManager import ShaderManager
from ..tools.glDebug import currentContext, isSameContext

#: 所有滤镜共用的全屏顶点着色器（res/shader/filler.vert）
VERTEX_SHADER = "filler"

#: 程序缓存：同一份 .frag 只编译/链接一次。滤镜很容易被大量实例化
#: （每个精灵一套参数都可能建一个），逐个 loadShader 会白建几十个 program ——
#: 引擎里 Bloom / 分形噪声 / 体积噪声都是"懒加载 + 全局一份"，这里同理。
_PROGRAM_CACHE: dict = {}

#: 引擎通用绘制路径会设置的 uniform，滤镜程序里没有这些名字 —— 属于预期行为
_SILENCED = ("formatType", "fuc", "win_wh", "wh", "translate", "rotate", "scale",
             "view", "u_cameraPos", "u_lightCount")


def loadProgram(fragment_name: str) -> Shader:
    """按名字取滤镜程序（``res/shader/<名字>.frag`` + 共享的 ``filler.vert``）。

    两个名字都是**裸名字**，由 :func:`pyglgame.shader.ShaderManager.shaderPath`
    补成 ``res/shader/…``（资源封包后也一样）。
    """
    program = _PROGRAM_CACHE.get(fragment_name)
    if program is None:
        program = ShaderManager.loadShaderFromFiles(
            VERTEX_SHADER + ".vert", fragment_name + ".frag")
        program.silenceUniform(*_SILENCED)
        _PROGRAM_CACHE[fragment_name] = program
    return program


def textureId(texture) -> int:
    """把"纹理 id / 纹理对象"统一成 Python ``int``。

    ⚠ **不能只判 ``isinstance(x, int)``**：PyOpenGL 的 ``glGenTextures`` 返回的是
    numpy 标量（``np.uint32``），它不是 ``int`` 的实例 —— 那样会被当成"纹理对象"，
    ``getattr(x, "id", 0)`` 取到 0，于是拿 0 号纹理去查尺寸、报一句莫名其妙的
    "取不到纹理尺寸"。相机的 ``colorTexture()`` 正是直接把这种 id 传进来的。
    """
    if isinstance(texture, numbers.Integral):     # int / np.uint32 / np.int64 ...
        return int(texture)
    texture_id = getattr(texture, "id", None)
    if texture_id is None:
        raise ValueError(
            f"{type(texture).__name__} 既不是纹理 id 也不是 TextureBase：不能当滤镜输入/目标")
    return int(texture_id)


def textureSize(texture) -> tuple:
    """取纹理的 (宽, 高)。

    接受 :class:`~pyglgame.render.TextureBase.TextureBase` 子类实例（用它的
    ``w/h``）、纹理 id（相机 ``colorTexture()`` 返回的就是 id，且常常是
    ``np.uint32``），以及"有 w/h 属性的任何对象"。取不到就问 GL，问不到才报错。
    """
    width = getattr(texture, "w", None)
    height = getattr(texture, "h", None)
    if width and height:
        return int(width), int(height)
    texture_id = textureId(texture)
    glBindTexture(GL_TEXTURE_2D, texture_id)
    width = int(glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_WIDTH))
    height = int(glGetTexLevelParameteriv(GL_TEXTURE_2D, 0, GL_TEXTURE_HEIGHT))
    glBindTexture(GL_TEXTURE_2D, 0)
    if width <= 0 or height <= 0:
        raise ValueError(
            f"取不到纹理尺寸（id={texture_id}）：不能当滤镜输入/目标"
            "（0 号纹理 = 没绑定；相机直接出屏、没有中间纹理的路径就是这种情况）")
    return width, height


class FillerBase:
    """滤镜基类。

    子类通常只需要做三件事（默认实现已经够多数滤镜用）：

    1. ``SHADER = "filler_xxx"`` 指定 fragment shader；
    2. 重写 :meth:`uploadUniforms` 上传自己的参数；
    3. 需要第二张输入纹理时重写 :meth:`inputs`（必要时用 :meth:`prepare`
       先生成它，例如锐化的低通图）。
    """

    #: ``res/shader/<SHADER>.frag``；None 表示这个滤镜不用 fragment 程序
    #: （例如 FFT：它由 compute shader 算，只用拷贝程序落地）
    SHADER: str = None

    #: 是否需要 ``u_texel``（= 1 / 输入尺寸）：只有"按像素算"的滤镜（卷积、模糊、
    #: 像素化、颗粒）才要。不用的滤镜**不要**上传它 —— 声明了却不用的 uniform
    #: 会被编译器优化掉，``getLoc`` 找不到就会打一条警告（噪音）
    USES_TEXEL: bool = False

    def __init__(self, shader: str = None) -> None:
        name = shader if shader is not None else self.SHADER
        self.shader_name = name
        #: 程序**懒加载**（第一次真正滤镜时才编译）：这样滤镜对象在
        #: 没有 GL 上下文的地方也能构造 —— 单测（tests/test_filler.py）
        #: 就是靠这一点直接检查参数与 uniform 上传的。
        self.program = None
        self.framebuffer = 0
        self._attached_texture = 0
        self._viewport = None
        self._quad = None
        self._context = currentContext()
        self._released = False
        self._input_size = (0, 0)

    def getProgram(self) -> Shader:
        """取本滤镜的 fragment 程序（第一次调用时才编译/链接）。"""
        if self.program is None:
            if not self.shader_name:
                raise RuntimeError(
                    f"{type(self).__name__} 没有 fragment 程序（它由 compute 驱动）")
            self.program = loadProgram(self.shader_name)
        return self.program

    # === 子类接口 ===

    def filler(self, input_texture, output_texture):
        """模板方法：准备 → 绑输入 → 传参数 → 画全屏四边形 → 收尾。

        ``input_texture`` 与 ``output_texture`` 都是
        :class:`~pyglgame.render.TextureBase.TextureBase` 的子类实例；
        返回 ``output_texture``（方便链式书写）。
        """
        self._input_size = textureSize(input_texture)
        self.prepare(input_texture, output_texture)
        shader = self._beginPass(output_texture)
        for index, texture in enumerate(self.inputs(input_texture, output_texture)):
            if texture is not None:
                self._bindInput(texture, index)
        self.uploadUniforms(shader, input_texture, output_texture)
        self._draw()
        self._endPass()
        return output_texture

    def prepare(self, input_texture, output_texture) -> None:
        """主 pass **之前**的准备（例如生成低通图）。默认什么都不做。

        这里可以随便切换帧缓冲 —— :meth:`_beginPass` 会重新绑定目标纹理。
        """

    def inputs(self, input_texture, output_texture) -> tuple:
        """本 pass 要绑定的输入纹理，下标就是纹理单元（tex0、tex1...）。"""
        return (input_texture,)

    def uploadUniforms(self, shader, input_texture, output_texture) -> None:
        """上传本滤镜的参数（每次 ``filler`` 都会调用一次）。"""

    # === pass 生命周期（子类一般不用碰）===

    @property
    def quad(self) -> RenderBuffer:
        """全屏四边形（引擎全局共用的那一份）。"""
        if self._quad is None:
            self._quad = RenderBuffer.getWindowRenderBuffer()
        return self._quad

    def _beginPass(self, output_texture) -> Shader:
        width, height = textureSize(output_texture)
        target_id = textureId(output_texture)
        if self.framebuffer == 0:
            self.framebuffer = glGenFramebuffers(1)
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
        if self._attached_texture != target_id:
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                   GL_TEXTURE_2D, target_id, 0)
            status = glCheckFramebufferStatus(GL_FRAMEBUFFER)
            if status != GL_FRAMEBUFFER_COMPLETE:
                raise RuntimeError(
                    f"滤镜目标纹理挂不上帧缓冲 (status=0x{status:x})："
                    "目标必须是可作为颜色附件的纹理（TextureStorage2D/Texture）")
            self._attached_texture = target_id
        # 备份视口：目标纹理常常比窗口小，不还原的话调用方后面的绘制会被裁掉
        self._viewport = [int(value) for value in glGetIntegerv(GL_VIEWPORT)[:4]]
        glViewport(0, 0, width, height)
        # 全屏后处理：不吃深度、不写深度、关掉混合（要盖掉目标纹理里的旧内容）
        RenderState.invalidate()
        RenderState.setDefaults(RenderState.BLEND_NONE,
                                depth_write=False, depth_test=False)
        program = self.getProgram()
        program.use()
        return program

    def _bindInput(self, texture, unit: int = 0) -> None:
        """把纹理绑到 ``GL_TEXTURE<unit>`` 并同步 ``tex<unit>`` 采样器 uniform。

        ``texture`` 既可以是 :class:`TextureBase`，也可以是纹理 id（可能是
        ``np.uint32`` —— 相机的 ``colorTexture()`` 就是这种）。
        """
        glActiveTexture(GL_TEXTURE0 + unit)
        glBindTexture(GL_TEXTURE_2D, textureId(texture))
        self.getProgram().uniform1i(f"tex{unit}", unit)

    def _draw(self) -> None:
        self.quad.draw(False)

    def _uploadCommon(self, shader) -> None:
        """上传公共 uniform：``u_mix``（有的话）与 ``u_texel``（用得到才传）。"""
        mix = getattr(self, "mix", None)
        if mix is not None:
            shader.uniform1f("u_mix", float(mix))
        if self.USES_TEXEL:
            width, height = self._input_size
            if width and height:
                shader.uniform2f("u_texel", 1.0 / width, 1.0 / height)

    def _endPass(self) -> None:
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        glActiveTexture(GL_TEXTURE0)
        if self._viewport is not None:
            glViewport(*self._viewport)
            self._viewport = None
        # 直接改过 GL 状态：作废缓存 + **真正把默认状态下发一遍**。
        # 只 invalidate 不够 —— 本 pass 关了深度测试/混合/深度写入，
        # 而 RenderLayer 只在各 pass 之前设过一次默认状态。
        RenderState.invalidate()
        RenderState.setDefaults()
        # 还原主着色器：否则调用方后续传的 uniform 会落到滤镜程序上
        render_global = RenderGlobal.instance
        if render_global is None:
            return
        main_shader = render_global.dis_shader
        if main_shader is not None:
            if main_shader is not self.program:
                main_shader.use()
        elif render_global.using_shader is self.program:
            # 没有主着色器（例如离屏跑滤镜）：别把 using_shader 停在滤镜程序上，
            # 否则下一个 RenderBuffer.draw 会把 formatType 传到本程序
            render_global.using_shader = None

    # === 生命周期 ===

    def release(self) -> None:
        """释放本滤镜自己的 GPU 资源（FBO；程序与纹理由别处持有）。"""
        if self._released:
            return
        self._released = True
        if not isSameContext(self._context):
            self.framebuffer = 0
            self._attached_texture = 0
            return
        if self.framebuffer:
            glDeleteFramebuffers(1, [self.framebuffer])
            self.framebuffer = 0
        self._attached_texture = 0

    def __del__(self) -> None:
        try:
            self.release()
        except Exception:
            pass
