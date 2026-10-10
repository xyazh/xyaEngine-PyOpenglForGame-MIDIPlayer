from OpenGL.GL import *
from . import ImageUnits
from .TextureBase import TextureBase
import math


class TextureStorage2D(TextureBase):
    """不可变存储的 2D 纹理（``glTexStorage2D``），可直接作为 image unit 使用。

    ``unit`` 默认 ``None`` = **不占 image unit**（构造时只建纹理）。
    真要用的时候 :meth:`bindUnit` / :meth:`bindImage2D` 会自动向
    :mod:`~pyglgame.render.ImageUnits` 要一个空闲单元，``release()`` 自动还，
    不用再自己数 0..7。
    """

    def __init__(self,w:int,h:int,unit:int=None,internal_format:int=GL_RGBA32F,access:int=GL_READ_WRITE) -> None:
        super().__init__(glGenTextures(1))
        self.w = w
        self.h = h
        self.group_x = math.ceil(w / 16)
        self.group_y = math.ceil(h / 16)
        self.access = access
        self.internal_format = internal_format
        # unit=None（默认）= 不占用 image unit：GL 常常只有 8 个，引擎自己就占满了
        self.unit = None
        self._owned_unit = None
        glBindTexture(GL_TEXTURE_2D, self.id)
        glTexStorage2D(GL_TEXTURE_2D, 1, internal_format, w, h)
        if unit is not None:
            self.bindUnit(unit)
        glBindTexture(GL_TEXTURE_2D, 0)

    def _takeUnit(self, unit) -> int:
        """定下这次绑哪个单元：``None`` = 自动租一个（释放时归还）。"""
        if unit is None:
            if self._owned_unit is None:
                self._owned_unit = ImageUnits.alloc(owner=self)
            self.unit = self._owned_unit
            return self._owned_unit
        if self._owned_unit is not None and int(unit) != self._owned_unit:
            ImageUnits.free(self._owned_unit, owner=self)   # 改用别人给的单元 -> 还掉租的
            self._owned_unit = None
        self.unit = int(unit)
        return self.unit

    def useTextureParameteri(self,filter = GL_LINEAR) -> None:
        glBindTexture(GL_TEXTURE_2D,self.id)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, filter)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, filter)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)

    def bindUnit(self, unit=None) -> int:
        """绑定为 compute shader 的 image unit，返回**真正用的**单元编号。

        ``unit=None``（默认）：自动租一个空闲单元，``release()`` 时归还。
        """
        unit = self._takeUnit(unit)
        glBindImageTexture(unit, self.id, 0, GL_FALSE,
                           0, self.access, self.internal_format)
        return unit

    def bindImage2D(self, unit=None, access=None, internal_format=None) -> int:
        unit = self._takeUnit(unit)
        glBindImageTexture(unit, self.id, 0, GL_FALSE, 0,
                           self.access if access is None else access,
                           self.internal_format if internal_format is None else internal_format)
        return unit

    def bind(self, unit=GL_TEXTURE0) -> None:
        """绑定为普通 sampler2D，并同步设置 shader 的采样器 uniform。"""
        glActiveTexture(unit)
        glBindTexture(GL_TEXTURE_2D, self.id)
        try:
            from ..RenderGlobal import RenderGlobal
            from ..shader.Shader import UINT_MAP
            render_global = RenderGlobal.instance
            if render_global is not None and render_global.using_shader is not None:
                if unit in UINT_MAP:
                    render_global.using_shader.uniformTex(unit)
        except Exception:
            pass

    def release(self) -> None:
        """删纹理，并归还自动租来的 image unit。"""
        if self._owned_unit is not None:
            ImageUnits.free(self._owned_unit, owner=self)
            self._owned_unit = None
            self.unit = None
        super().release()

    