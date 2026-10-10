from ..shader.ShaderManager import ShaderManager
from ..render.TextureStorage2D import TextureStorage2D
from ..render.TextureBase import TextureBase
from ..RenderGlobal import RenderGlobal
from OpenGL.GL import *


class Bloom:
    """基于 compute shader 的 ping-pong Bloom。

    - 纹理格式默认 ``GL_RGBA16F``（HDR 足够，带宽/显存只有 RGBA32F 的一半）
    - compute shader 走 ``RenderGlobal.bloom_shader`` 懒加载（2D 游戏不再强制依赖它）
    - 提供 :meth:`release`，相机重建/窗口缩放时不会泄漏显存
    """

    def __init__(self, w: int, h: int, level1: float = 2, level2: float = 4,
                 internal_format: int = None, down_iterations: int = 5,
                 up_iterations: int = 3) -> None:
        self.compute_shader = RenderGlobal.instance.bloom_shader
        self.w = int(w)
        self.h = int(h)
        self.level1 = level1
        self.level2 = level2
        self.internal_format = GL_RGBA16F if internal_format is None else internal_format
        self.down_iterations = int(down_iterations)
        self.up_iterations = int(up_iterations)
        fmt = self.internal_format
        self.pingpong_tex1 = TextureStorage2D(int(w/self.level1), int(h/self.level1), unit=1, internal_format=fmt)
        self.pingpong_tex2 = TextureStorage2D(int(w/self.level1), int(h/self.level1), unit=2, internal_format=fmt)
        self.down_sample_tex1 = TextureStorage2D(int(w/self.level2), int(h/self.level2), unit=3, internal_format=fmt)
        self.down_sample_tex2 = TextureStorage2D(int(w/self.level2), int(h/self.level2), unit=4, internal_format=fmt)
        self.out_tex = TextureStorage2D(int(w), int(h), unit=5, internal_format=fmt)
        self.ldr_tex = TextureStorage2D(int(w), int(h), unit=6, internal_format=fmt)
        self.textures = (self.pingpong_tex1, self.pingpong_tex2,
                         self.down_sample_tex1, self.down_sample_tex2,
                         self.out_tex, self.ldr_tex)
        for texture in self.textures:
            texture.useTextureParameteri()
        self.input_texture = 0

    def release(self) -> None:
        """释放所有中间纹理。"""
        for texture in self.textures:
            texture.release()
        self.textures = ()
        self.input_texture = 0

    def sefTexture(self, texture: TextureBase | int,
                   internal_format: int = None) -> None:
        """绑定待处理（相机输出）纹理为 image unit 0。

        ``internal_format`` 必须与纹理实际格式一致，否则 glBindImageTexture
        会因为格式不匹配报错（原来是硬编码的 GL_RGBA32F）。
        """
        if isinstance(texture, TextureBase):
            texture = texture.id
        fmt = self.internal_format if internal_format is None else internal_format
        self.input_texture = texture
        glBindTexture(GL_TEXTURE_2D, texture)
        glBindImageTexture(0, texture, 0, GL_FALSE, 0, GL_READ_ONLY, fmt)

    def bloom(self) -> TextureStorage2D:
        """执行一遍 Bloom 后处理链，返回合成后的纹理。"""
        self.dis_shader = RenderGlobal.instance.using_shader

        
        self.compute_shader.use()
        self.pingpong_tex1.bindUnit(1)
        self.pingpong_tex2.bindUnit(2)
        self.down_sample_tex1.bindUnit(3)
        self.down_sample_tex2.bindUnit(4)
        self.out_tex.bindUnit(5)
        self.ldr_tex.bindUnit(6)
        #提取
        self.compute_shader.uniform1i("mode", 7)
        self.compute_shader.dispatch(
            self.ldr_tex.group_x, self.ldr_tex.group_y, 1)
        self.compute_shader.memoryBarrier()

        # 下采样
        self.compute_shader.uniform1i("mode", 0)
        self.compute_shader.dispatch(
            self.down_sample_tex1.group_x, self.down_sample_tex1.group_y, 1)
        self.compute_shader.memoryBarrier()
        for _ in range(self.down_iterations):
            # 高斯
            self.compute_shader.uniform1i("mode", 4)
            self.compute_shader.dispatch(
                self.down_sample_tex2.group_x, self.down_sample_tex2.group_y, 1)
            self.compute_shader.memoryBarrier()
            # 高斯
            self.compute_shader.uniform1i("mode", 5)
            self.compute_shader.dispatch(
                self.down_sample_tex1.group_x, self.down_sample_tex1.group_y, 1)
            self.compute_shader.memoryBarrier()

        # 上采样
        self.compute_shader.uniform1i("mode", 1)
        self.compute_shader.dispatch(
            self.pingpong_tex1.group_x, self.pingpong_tex1.group_y, 1)
        self.compute_shader.memoryBarrier()
        for _ in range(self.up_iterations):
            # 高斯
            self.compute_shader.uniform1i("mode", 2)
            self.compute_shader.dispatch(
                self.pingpong_tex2.group_x, self.pingpong_tex2.group_y, 1)
            self.compute_shader.memoryBarrier()
            # 高斯
            self.compute_shader.uniform1i("mode", 3)
            self.compute_shader.dispatch(
                self.pingpong_tex1.group_x, self.pingpong_tex1.group_y, 1)
            self.compute_shader.memoryBarrier()
        self.compute_shader.uniform1i("mode", 6)
        self.compute_shader.dispatch(
            self.out_tex.group_x, self.out_tex.group_y, 1)
        self.compute_shader.memoryBarrier()
        self.dis_shader.use()
        return self.out_tex