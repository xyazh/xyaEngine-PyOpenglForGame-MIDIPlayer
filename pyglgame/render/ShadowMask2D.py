"""2D 阴影遮罩（Phase 7）：每盏投影灯一层**屏幕空间**"可见度"贴图。

## 数据流

1. :meth:`ShadowMask2D.begin` —— 绑定自己的 FBO（颜色附件是 ``GL_TEXTURE_2D_ARRAY``
   的一层）、保存 framebuffer/viewport、关掉混合 / 深度测试 / 深度写入
2. 逐投影灯：:meth:`ShadowMask2D.beginLayer`（换上第 ``i`` 层并清成 ``1.0`` = 全可见）
   → 用主着色器 ``fuc = SHADOW2D(9)`` 把**遮挡体的阴影三角形**画成 ``0``（= 被挡住）
3. :meth:`ShadowMask2D.end` —— 恢复 framebuffer / viewport 与不透明 pass 的基准状态
4. :meth:`ShadowMask2D.upload` —— 把 ``u_shadowMask2D``（``sampler2DArray``，纹理单元 8）
   与 ``u_shadow2DCast[i]`` 交给 ``dis.frag``

采样端在 ``dis.frag`` 的光照循环里：

.. code-block:: glsl

    if (u_shadow2DCast[i] == 1) {
        visible = texture(u_shadowMask2D, vec3(gl_FragCoord.xy / win_wh, float(i))).r;
    }

也就是**屏幕空间查表**：遮罩用与主场景**同一个相机**渲染，所以逐像素对齐，
不用管遮挡体 / 接收者的 z（层高只决定绘制顺序，不参与阴影计算）。
纹理是 ``GL_LINEAR``：一个纹素的过渡带 = 阴影边缘自带一点点柔化。

## 为什么是"每盏灯一层"，不是一张

光照是**前向、一次 pass 算完所有光源**的（见 ``docs/光照与体积渲染.md``）：
``dis.frag`` 的循环同时处理 ``0..u_lightCount-1`` 盏灯，所以每盏灯的遮挡结果必须
**同时**可取 —— 一层一张，按下标对应。层数 = ``Light.MAX_LIGHTS``（8），
与光源数组同宽，映射就是"第 i 盏灯用第 i 层"，不需要额外的重映射表。
"""

from OpenGL.GL import *

from . import RenderState

#: 遮罩层数：必须与 ``pyglgame.render.Light.MAX_LIGHTS`` / ``dis.frag`` 的
#: ``MAX_LIGHTS`` 一致（光源数组按"下标 = 层号"映射）。tests 里有断言守着。
MAX_LAYERS = 8


class ShadowMask2D:
    """每盏投影灯一层屏幕空间遮挡遮罩。见模块文档。"""

    # 纹理单元 8：0..5 是 dis.frag 的 tex0..tex5，6 = 方向光阴影贴图，7 = SSAO
    TEXTURE_UNIT = 8

    def __init__(self, width: int, height: int, layers: int = MAX_LAYERS) -> None:
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self.layers = max(1, int(layers))
        self._saved_framebuffer = 0
        self._saved_viewport = None
        self._released = False

        self.texture = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D_ARRAY, self.texture)
        glTexStorage3D(GL_TEXTURE_2D_ARRAY, 1, GL_R8,
                       self.width, self.height, self.layers)
        # 线性过滤：一个纹素的过渡带 = 阴影边缘自带一点柔化（硬边仍然是硬的）
        glTexParameteri(GL_TEXTURE_2D_ARRAY, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D_ARRAY, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D_ARRAY, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D_ARRAY, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

        self.framebuffer = glGenFramebuffers(1)
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
        self._attachLayer(0)
        status = glCheckFramebufferStatus(GL_FRAMEBUFFER)
        if status != GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError(f"2D 阴影遮罩帧缓冲不完整 (status=0x{status:x})")
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        glBindTexture(GL_TEXTURE_2D_ARRAY, 0)

    # === pass ===

    def _attachLayer(self, index: int) -> None:
        """把第 ``index`` 层挂到颜色附件 0（``glClear`` / 绘制都作用在这一层）。"""
        glFramebufferTextureLayer(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                                  self.texture, 0, int(index))

    def begin(self) -> None:
        """进入遮罩 pass：保存 framebuffer/viewport，只保留"写颜色"这一件事。"""
        self._saved_framebuffer = glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING)
        viewport = glGetIntegerv(GL_VIEWPORT)
        self._saved_viewport = tuple(int(value) for value in viewport)
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
        glViewport(0, 0, self.width, self.height)
        # 颜色通道全开（别的 pass 可能关过），再关掉混合与深度
        glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE)
        RenderState.setDefaults(blend_mode=RenderState.BLEND_NONE,
                                depth_write=False, depth_test=False)

    def beginLayer(self, index: int, visible: float = 1.0) -> None:
        """换上第 ``index`` 层并清成 ``visible``（``1.0`` = 全可见，被挡的像素再画成 0）。"""
        self._attachLayer(index)
        glClearColor(float(visible), float(visible), float(visible), 1.0)
        glClear(GL_COLOR_BUFFER_BIT)

    def end(self) -> None:
        """退出遮罩 pass：恢复 framebuffer / viewport 与不透明 pass 的基准状态。"""
        glBindFramebuffer(GL_FRAMEBUFFER, self._saved_framebuffer or 0)
        if self._saved_viewport:
            glViewport(*self._saved_viewport)
        # 本 pass 直接改过混合/深度状态，缓存必须作废后重新下发
        # （同 ShadowMap.end / WBOIT 的坑）
        RenderState.invalidate()
        RenderState.setDefaults()

    def upload(self, shader, casting) -> None:
        """把遮罩交给着色器；``casting`` 是"逐光源是否投影"的序列（按光源下标）。"""
        glActiveTexture(GL_TEXTURE0 + self.TEXTURE_UNIT)
        glBindTexture(GL_TEXTURE_2D_ARRAY, self.texture)
        shader.uniform1i("u_shadowMask2D", self.TEXTURE_UNIT)
        glActiveTexture(GL_TEXTURE0)
        for index in range(self.layers):
            flag = 1 if (index < len(casting) and casting[index]) else 0
            shader.uniform1i(f"u_shadow2DCast[{index}]", flag)

    def release(self) -> None:
        """释放 GPU 资源（需要在 GL 上下文里调用）。"""
        if self._released:
            return
        self._released = True
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        if self.framebuffer:
            glDeleteFramebuffers(1, [self.framebuffer])
            self.framebuffer = 0
        if self.texture:
            glDeleteTextures(1, [self.texture])
            self.texture = 0

    def __repr__(self) -> str:
        return (f"<ShadowMask2D {self.width}x{self.height} 层={self.layers} "
                f"单元={self.TEXTURE_UNIT}>")
