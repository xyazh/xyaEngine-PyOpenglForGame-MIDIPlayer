import math
from OpenGL.GL import *
from pyglgame.compute import glCompute
from pyglgame.gameobject.GameObject import GameObject
from pyglgame.render.BufferBuilder import *
from pyglgame.render.Image import Image
from pyglgame.render.RenderBuffer import RenderBuffer
from pyglgame.render.TextureStorage2D import TextureStorage2D
from pyglgame.RenderGlobal import RenderGlobal
from pyglgame.ResourceLocation import ResourceLocation

class LLTexture(TextureStorage2D):
    @glCompute(source="""
        x = global_x()  # type: ignore
        y = global_y()  # type: ignore
        w = width()     # type: ignore
        h = height()    # type: ignore
        if x >= w or y >= h:
            return
        fx = (float(x) + 0.5) / float(w)
        fy = (float(y) + 0.5) / float(h)
        r = 0.018 + 0.030 * (1.0 - fy)
        g = 0.022 + 0.040 * (1.0 - fy)
        b = 0.035 + 0.075 * (1.0 - fy)
        r *= 2
        g *= 2
        b *= 2
        beat_u = fx * 32.0 - time * 0.18
        beat_phase = beat_u - floor(beat_u)  # type: ignore
        beat_dist = abs(beat_phase - 0.5)
        beat = clamp(1.0 - beat_dist * 24.0, 0.0, 1.0) * 0.018  # type: ignore
        staff = 0.0
        for i in range(8):
            py = 0.14 + float(i) * 0.10
            d = abs(fy - py)
            staff += clamp(1.0 - d * 450.0, 0.0, 1.0) * 0.008  # type: ignore
        r += beat + staff
        g += beat + staff
        b += beat * 1.4 + staff
        vx = fx * 2.0 - 1.0
        vy = fy * 2.0 - 1.0
        vig = clamp(1.0 - (vx * vx * 0.35 + vy * vy * 0.65), 0.0, 1.0)  # type: ignore
        r *= 0.55 + vig * 0.45
        g *= 0.55 + vig * 0.45
        b *= 0.55 + vig * 0.45
        img[x, y] = vec4(  # type: ignore
            clamp(r, 0.0, 1.0),  # type: ignore
            clamp(g, 0.0, 1.0),  # type: ignore
            clamp(b, 0.0, 1.0),  # type: ignore
            1.0
        )""")
    @staticmethod
    def compute(img: TextureStorage2D, time: float) -> None:
        pass
        

    def __init__(self):
        super().__init__(1920, 1080,
                         internal_format=GL_RGBA32F, access=GL_READ_WRITE)
        self.useTextureParameteri(GL_LINEAR)


class BackGround(GameObject):
    def __new__(cls, *args, **kwargs):
        return super().__new__(cls)

    def __init__(self, settings=None) -> None:
        super().__init__()
        self.name = "BackGround"
        self.settings = settings
        self.brightness = 1.0
        self.dynamic = True
        self.image_path = ""
        self.image_texture = None
        self.dirty = True

    def start(self) -> None:
        super().start()
        self.bg = RenderBuffer(GL_STATIC_DRAW)
        self.tex = LLTexture()
        self.time = 0
        self.last_time = 0
        self.applySettings(self.settings)

    def applySettings(self, settings=None) -> None:
        if settings is not None:
            self.settings = settings
        settings = self.settings
        if settings is None:
            self.dirty = True
            return
        self.brightness = max(0.0, min(2.0, settings.background_brightness))
        path = settings.background_image
        self.dynamic = (settings.background_mode != "image") or not path
        if path != self.image_path:
            self.image_path = path
            self.loadImage(path)
        self.dirty = True

    def loadImage(self, path: str) -> None:
        self.image_texture = None
        if not path:
            return
        location = ResourceLocation(path)
        if not location.exists():
            print("背景图片不存在: %s" % path)
            return
        try:
            self.image_texture = Image(location).getTexture()
            print("背景图片: %s" % path)
        except Exception as e:
            print("背景图片加载失败 %s: %s" % (path, e))

    def texture(self):
        if not self.dynamic and self.image_texture is not None:
            return self.image_texture
        return self.tex

    def rebuild(self) -> None:
        if getattr(self, "bg", None) is not None:
            self.bg.release()
        level = max(0.0, min(2.0, self.brightness))
        c = (level, level, level)
        self.bg = RenderBuffer(GL_STATIC_DRAW)
        bg_builder = self.bg.createBuffer(GL_TRIANGLES, POS | TEX | COL)
        bg_builder.pos(-1, -1, 0).col(*c).tex(0, 1).end()  # bottom left
        bg_builder.pos(+1, -1, 0).col(*c).tex(1, 1).end()  # bottom right
        bg_builder.pos(-1, +1, 0).col(*c).tex(0, 0).end()  # top left
        bg_builder.pos(+1, -1, 0).col(*c).tex(1, 1).end()  # bottom right
        bg_builder.pos(+1, +1, 0).col(*c).tex(1, 0).end()  # top right
        bg_builder.pos(-1, +1, 0).col(*c).tex(0, 0).end()  # top left
        self.bg.build()
        self.bg.buffer_builder.freeData()

    def render(self, dt, fps) -> None:
        if not self.active:
            return
        if self.dirty:
            self.rebuild()
            self.dirty = False
        self.time += dt
        s_time = int(self.time*3)
        if s_time > self.last_time:
            self.applySettings(self.settings)
            self.last_time = s_time
        if self.dynamic:
            LLTexture.compute(self.tex, self.time)
        texture = self.texture()
        if texture is not None:
            texture.bind()
        glDisable(GL_DEPTH_TEST)
        glDepthMask(GL_FALSE)
        render = self.bg
        # 背景是屏幕空间绘制：WBOIT pass 内要用 OIT_WID(7)，否则会被当成世界坐标
        render_global = RenderGlobal.instance
        render_global.using_shader.uniform1i(
            "fuc", render_global.passFuc(screen_space=True))
        render.draw(re_build=False)
        render_global.using_shader.uniform1i(
            "fuc", render_global.passFuc(screen_space=False))
        glDepthMask(GL_TRUE)
        glEnable(GL_DEPTH_TEST)