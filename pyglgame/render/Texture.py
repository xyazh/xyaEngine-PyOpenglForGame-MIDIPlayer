from typing import Optional
from typing import TYPE_CHECKING
from OpenGL.GL import *
from ..RenderGlobal import RenderGlobal
from .TextureBase import TextureBase

if TYPE_CHECKING:
    from .Image import Image

NEAREST = 0
LINEAR = 1


class Texture(TextureBase):
    _white_texture = None

    @staticmethod
    def white() -> "Texture":
        """1x1 白色纹理（带缓存）。

        用于"要采样但暂时没有图"的场合：如果什么都不绑，就可能采样到上一个
        绘制阶段留下的纹理（例如上一帧的 Bloom 输出）——表现就是画面重影。
        采样白色纹理时 ``color *= texture(...)`` 等价于不变，行为可预期。
        """
        # 局部导入：Image 依赖 Texture，避免模块级循环导入
        import numpy as np
        from PIL import Image as PILImage
        from .Image import Image

        if Texture._white_texture is None or Texture._white_texture.released:
            data = np.ones((1, 1, 4), dtype=np.uint8) * 255
            image = Image.fromPIL(PILImage.fromarray(data, "RGBA"))
            Texture._white_texture = Texture.createFromImage(
                image, filter=NEAREST, wrap_mode=GL_CLAMP_TO_EDGE)
        return Texture._white_texture

    @staticmethod
    def createDataTexture(image: "Image",
                          width: int, height: int,
                          filter: int = LINEAR, mipmap: bool = False, wrap_mode: int = GL_REPEAT) -> "Texture":
        texture_id = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, texture_id)
        internal_format = GL_RGBA32F if image.use_hdr else GL_RGBA
        target_type = GL_FLOAT if image.use_hdr else GL_UNSIGNED_BYTE
        # 行数据按 1 字节对齐，避免非 4 倍数宽度出现错行
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
        glTexImage2D(GL_TEXTURE_2D, 0, internal_format,
                     width, height,
                     0, GL_RGBA, target_type,
                     image.data)
        glPixelStorei(GL_UNPACK_ALIGNMENT, 4)
        if mipmap:
            glGenerateMipmap(GL_TEXTURE_2D)
            if filter == NEAREST:
                glTexParameteri(
                    GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST_MIPMAP_NEAREST)
                glTexParameteri(
                    GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
            else:
                glTexParameteri(
                    GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR)
                glTexParameteri(
                    GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        else:
            if filter == NEAREST:
                glTexParameteri(
                    GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
                glTexParameteri(
                    GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
            else:
                glTexParameteri(
                    GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
                glTexParameteri(
                    GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, wrap_mode)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, wrap_mode)
        glBindTexture(GL_TEXTURE_2D, 0)
        texture = Texture(texture_id)
        # 记录尺寸：TextureBase.getData / 滤镜（pyglgame/filler）都要按它算
        # 纹素大小与采样坐标，而 TextureStorage2D 早就有 w/h —— 这里补齐
        texture.w = width
        texture.h = height
        return texture

    @staticmethod
    def createFromImage(image: "Image",
                        width: int = -1, height: int = -1,
                        filter: int = LINEAR, mipmap: bool = False, wrap_mode: int = GL_REPEAT) -> "Texture":
        if width == -1:
            width = image.width
        if height == -1:
            height = image.height
        return Texture.createDataTexture(image, width, height, filter, mipmap, wrap_mode)

    def bind(self, unit=GL_TEXTURE0) -> None:
        glActiveTexture(unit)
        glBindTexture(GL_TEXTURE_2D, self.id)
        shader = RenderGlobal.instance.using_shader
        if shader is not None:
            shader.uniformTex(unit)

    def bindImage2D(self, uint=None) -> int:
        """绑定为 compute shader 的 image unit（``uint=None`` = 自动租一个）。

        自动租来的单元在 :meth:`release` 时归还 —— 见
        :mod:`pyglgame.render.ImageUnits`（GL 常常只有 8 个单元，引擎自己就用满了）。
        """
        if uint is None:
            if getattr(self, "_owned_unit", None) is None:
                from . import ImageUnits
                self._owned_unit = ImageUnits.alloc(owner=self)
            uint = self._owned_unit
        glBindTexture(GL_TEXTURE_2D, self.id)
        glBindImageTexture(uint, self.id, 0,
                           GL_FALSE, 0, GL_READ_ONLY, GL_RGBA32F)
        return uint

    def release(self) -> None:
        """删纹理，并归还 :meth:`bindImage2D` 自动租来的 image unit。"""
        if getattr(self, "_owned_unit", None) is not None:
            from . import ImageUnits
            ImageUnits.free(self._owned_unit, owner=self)
            self._owned_unit = None
        super().release()


