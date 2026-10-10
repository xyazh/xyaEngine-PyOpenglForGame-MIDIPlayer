import glm
from typing import TYPE_CHECKING
from OpenGL.GL import *

from ..RenderGlobal import RenderGlobal

UINT_MAP = {
    GL_TEXTURE0: ("tex0", 0),
    GL_TEXTURE1: ("tex1", 1),
    GL_TEXTURE2: ("tex2", 2),
    GL_TEXTURE3: ("tex3", 3),
    GL_TEXTURE4: ("tex4", 4),
    GL_TEXTURE5: ("tex5", 5),
    GL_TEXTURE6: ("tex6", 6),
    GL_TEXTURE7: ("tex7", 7),
    GL_TEXTURE8: ("tex8", 8),
    GL_TEXTURE9: ("tex9", 9),
    GL_TEXTURE10: ("tex10", 10),
    GL_TEXTURE11: ("tex11", 11),
    GL_TEXTURE12: ("tex12", 12),
    GL_TEXTURE13: ("tex13", 13),
    GL_TEXTURE14: ("tex14", 14),
    GL_TEXTURE15: ("tex15", 15),
    GL_TEXTURE16: ("tex16", 16),
    GL_TEXTURE17: ("tex17", 17),
    GL_TEXTURE18: ("tex18", 18),
    GL_TEXTURE19: ("tex19", 19),
    GL_TEXTURE20: ("tex20", 20),
    GL_TEXTURE21: ("tex21", 21),
    GL_TEXTURE22: ("tex22", 22),
    GL_TEXTURE23: ("tex23", 23),
    GL_TEXTURE24: ("tex24", 24),
    GL_TEXTURE25: ("tex25", 25),
    GL_TEXTURE26: ("tex26", 26),
    GL_TEXTURE27: ("tex27", 27),
    GL_TEXTURE28: ("tex28", 28),
    GL_TEXTURE29: ("tex29", 29),
    GL_TEXTURE30: ("tex30", 30),
    GL_TEXTURE31: ("tex31", 31),
}


class Shader:
    def __init__(self, shader: int) -> None:
        self.shader: int = int(shader)
        # uniform location 缓存必须每 program 一份（写成类属性会跨 program 串号）
        self.loc_nams: dict[str, int] = {}
        self._warned: set[str] = set()
        # 整型 uniform 的上一帧值：值存在 program 里，可以安全跳重复上传
        self._uniform1i_cache: dict[str, int] = {}

    @property
    def valid(self) -> bool:
        """program 是否可用（0 表示编译/链接失败）。"""
        return self.shader != 0

    def use(self) -> None:
        if not self.valid:
            raise RuntimeError(
                "尝试使用无效的 Shader（program=0），说明 shader 编译或链接失败，请查看上方日志。")
        render_global = RenderGlobal.instance
        if render_global is None:
            glUseProgram(self.shader)
            return
        if render_global.using_shader == self:
            return
        glUseProgram(self.shader)
        render_global.using_shader = self

    def release(self) -> None:
        render_global = RenderGlobal.instance
        if render_global is None:
            glUseProgram(0)
            return
        if render_global.using_shader != self:
            # 当前并没有使用本程序，无需解绑
            return
        glUseProgram(0)
        render_global.using_shader = None

    def getShaderId(self) -> int:
        return self.shader

    def uniformMatrix4fv(self, uniform_name: str, matrix: glm.mat4x4) -> None:
        loc = self.getLoc(uniform_name)
        if loc == -1:
            return
        glUniformMatrix4fv(loc, 1, GL_FALSE, glm.value_ptr(matrix))

    def boolean1b(self, name: str, b: bool) -> None:
        self.uniform1i(name, 1 if b else 0)

    def uniform1i(self, name: str, i: int) -> None:
        loc = self.getLoc(name)
        if loc == -1:
            return
        glUniform1i(loc, i)

    def uniform1iCached(self, name: str, i: int) -> None:
        """只在值变化时上传整型 uniform（uniform 值存放在 program 里，可安全缓存）。"""
        if self._uniform1i_cache.get(name) == i:
            return
        self._uniform1i_cache[name] = i
        self.uniform1i(name, i)

    def invalidateUniformCache(self) -> None:
        self._uniform1i_cache.clear()

    def silenceUniform(self, *names: str) -> None:
        """标记这些 uniform 不再告警。专用程序（WBOIT 合成 shader 等）会遇到
        引擎通用路径设置的、自己不存在的 uniform，属预期行为。"""
        self._warned.update(names)

    def uniform2i(self, name: str, i0: int, i1: int) -> None:
        loc = self.getLoc(name)
        glUniform2i(loc, i0, i1)

    def uniform3i(self, name: str, i0: int, i1: int, i2: int) -> None:
        loc = self.getLoc(name)
        glUniform3i(loc, i0, i1, i2)

    def uniform4i(self, name: str, i0: int, i1: int, i2: int, i3: int) -> None:
        loc = self.getLoc(name)
        glUniform4i(loc, i0, i1, i2, i3)

    def uniformTex(self, uint: int) -> None:
        loc = self.getLoc(UINT_MAP[uint][0])
        glUniform1i(loc, UINT_MAP[uint][1])

    def uniform1f(self, name: str, f: float) -> None:
        loc = self.getLoc(name)
        glUniform1f(loc, f)

    def uniform2f(self, name: str, f0: float, f1: float) -> None:
        loc = self.getLoc(name)
        glUniform2f(loc, f0, f1)

    def uniform3f(self, name: str, f: float, g: float, h: float) -> None:
        loc = self.getLoc(name)
        glUniform3f(loc, f, g, h)

    def uniform4f(self, name: str, f: float, g: float, h: float, i: float) -> None:
        loc = self.getLoc(name)
        glUniform4f(loc, f, g, h, i)

    def getLoc(self, name: str) -> int:
        """取（并缓存）uniform location；不存在时返回 -1 并告警一次。"""
        loc = self.loc_nams.get(name)
        if loc is None:
            loc = glGetUniformLocation(self.getShaderId(), name)
            self.loc_nams[name] = loc
            if loc == -1 and name not in self._warned:
                self._warned.add(name)
                print(
                    f"[Shader] 警告: program={self.shader} 中不存在 uniform '{name}'（可能被编译器优化掉或拼写错误）")
        return loc

    def dispatch(self, x: int, y: int = 1, z: int = 1) -> None:
        """Dispatch compute shader."""
        glDispatchCompute(x, y, z)

    def memoryBarrier(self, barrier: int = GL_ALL_BARRIER_BITS) -> None:
        """Call glMemoryBarrier after dispatch."""
        glMemoryBarrier(barrier)
