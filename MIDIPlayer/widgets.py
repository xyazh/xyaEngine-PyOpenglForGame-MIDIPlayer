from OpenGL.GL import *

from pyglgame.ResourceLocation import ResourceLocation
from pyglgame.gameobject.GameObject import GameObject
from pyglgame.gameobject.Text import Text
from pyglgame.render.BufferBuilder import *
from pyglgame.render.RenderBuffer import RenderBuffer

FONT_PATH = "res/fonts/ygblack.ttf"

_FONT: ResourceLocation = None


def uiFont() -> ResourceLocation:
    global _FONT
    if _FONT is None:
        _FONT = ResourceLocation(FONT_PATH)
    return _FONT


def quad(builder, cx: float, cy: float, cz: float, w: float, h: float, color) -> None:
    x = cx
    y = cy
    z = cz
    r, g, b, a = color
    builder.pos(x, y + h / 2, z - w / 2).col(r, g, b, a).end()
    builder.pos(x, y + h / 2, z + w / 2).col(r, g, b, a).end()
    builder.pos(x, y - h / 2, z + w / 2).col(r, g, b, a).end()
    builder.pos(x, y + h / 2, z - w / 2).col(r, g, b, a).end()
    builder.pos(x, y - h / 2, z + w / 2).col(r, g, b, a).end()
    builder.pos(x, y - h / 2, z - w / 2).col(r, g, b, a).end()


class PickThroughText(Text):
    def __init__(self, owner: GameObject, text: str = "", **kwargs) -> None:
        self.owner = owner
        super().__init__(text, **kwargs)

    def onClick(self, x: float, y: float) -> None:
        super().onClick(x, y)
        owner = self.owner
        if owner is not None and owner.active:
            owner.onClick(x, y)

    def onHover(self, x: float, y: float) -> None:
        super().onHover(x, y)
        owner = self.owner
        if owner is not None and owner.active:
            owner.onHover(x, y)


class TextButton(GameObject):
    def __new__(cls, *args, **kwargs):
        return super().__new__(cls)

    def __init__(self, owner, text: str, x: float, y: float, z: float = 0.0,
                 action: str = "", w: float = 1.0, h: float = 0.16,
                 text_height: float = 0.06, lift: float = 0.012,
                 color=(0.09, 0.16, 0.26, 0.70),
                 hover_color=(0.16, 0.42, 0.62, 0.85),
                 disabled_color=(0.06, 0.08, 0.12, 0.45),
                 anchor: str = "center", t: float = 270.0,
                 billboard: bool = False) -> None:
        super().__init__()
        self.name = "TextButton%s" % (action or text)
        self.owner = owner
        self.action = action
        self.w = float(w)
        self.h = float(h)
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)
        self.lift = float(lift)
        self.color = tuple(color)
        self.hover_color = tuple(hover_color)
        self.disabled_color = tuple(disabled_color)
        self.hovered = False
        self.enabled = True
        self.built_color = None
        self.label = PickThroughText(
            self, text, font=uiFont(), font_size=64, height=text_height,
            x=self.x - self.lift, y=self.y, z=self.z, t=t, billboard=billboard,
            align="center", anchor=anchor, font_color=(232, 240, 252, 255))

    def start(self) -> None:
        super().start()
        self.render_buffer = RenderBuffer()

    def onRemoved(self) -> None:
        if getattr(self, "render_buffer", None) is not None:
            self.render_buffer.release()

    def setText(self, text: str) -> None:
        self.label.setText(text)

    def setEnabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.enabled:
            return
        self.enabled = enabled
        self.label.setColor((1.0, 1.0, 1.0, 1.0) if enabled
                            else (0.55, 0.60, 0.70, 1.0))

    def setActive(self, active: bool) -> None:
        super().setActive(active)
        self.label.setActive(active)

    def boardColor(self) -> tuple:
        if not self.enabled:
            return self.disabled_color
        if self.hovered:
            return self.hover_color
        return self.color

    def onClick(self, x: float, y: float) -> None:
        super().onClick(x, y)
        if not self.enabled:
            return
        handler = getattr(self.owner, "onAction", None)
        if handler is not None:
            handler(self.action, self)

    def onHover(self, x: float, y: float) -> None:
        super().onHover(x, y)
        if self.enabled:
            self.hovered = True

    def render(self, dt, fps) -> None:
        if not self.active:
            return
        render_buffer = getattr(self, "render_buffer", None)
        if render_buffer is None:
            return
        color = self.boardColor()
        if color != self.built_color:
            builder = render_buffer.createBuffer(GL_TRIANGLES, POS | COL)
            quad(builder, self.x, self.y, self.z, self.w, self.h, color)
            self.built_color = color
            glDisable(GL_CULL_FACE)
            render_buffer.draw()
        else:
            render_buffer.draw(False)
        self.hovered = False
        return super().render(dt, fps)


class ProgressBar(GameObject):
    def __new__(cls, *args, **kwargs):
        return super().__new__(cls)

    def __init__(self, x: float, y: float, z: float = 0.0, w: float = 2.1,
                 h: float = 0.07) -> None:
        super().__init__()
        self.name = "ProgressBar"
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)
        self.w = float(w)
        self.h = float(h)
        self.progress = 0.0
        self.message = ""
        self.built_progress = -1.0
        self.title = Text("", font=uiFont(), font_size=48, scale=0.0462,
                          x=self.x, y=self.y + 0.13, z=self.z, t=270.0, billboard=True,
                          align="center", anchor="center",
                          font_color=(228, 238, 252, 255))
        self.label = Text("", font=uiFont(), font_size=48, scale=0.0356,
                          x=self.x, y=self.y - 0.11, z=self.z, t=270.0, billboard=True,
                          align="center", anchor="center",
                          font_color=(150, 176, 205, 255))

    def start(self) -> None:
        super().start()
        self.render_buffer = RenderBuffer()

    def onRemoved(self) -> None:
        if getattr(self, "render_buffer", None) is not None:
            self.render_buffer.release()

    def setActive(self, active: bool) -> None:
        super().setActive(active)
        self.title.setActive(active)
        self.label.setActive(active)

    def show(self, message: str, progress: float = 0.0) -> None:
        self.message = str(message)
        self.setProgress(progress)

    def setProgress(self, progress: float) -> None:
        self.progress = max(0.0, min(1.0, float(progress)))
        self.title.setText(self.message)
        self.label.setText("%3d%%" % int(round(self.progress * 100.0)))

    def render(self, dt, fps) -> None:
        if not self.active:
            return
        render_buffer = getattr(self, "render_buffer", None)
        if render_buffer is None:
            return
        if self.progress != self.built_progress:
            builder = render_buffer.createBuffer(GL_TRIANGLES, POS | COL)
            quad(builder, self.x, self.y, self.z, self.w, self.h,
                 (0.10, 0.12, 0.17, 0.85))
            fill = self.w * self.progress
            if fill > 0.0:
                quad(builder, self.x-0.1, self.y,
                     self.z - 0.002, fill, self.h * 0.62, (0.30, 0.76, 0.95, 0.95))
            self.built_progress = self.progress
            glDisable(GL_CULL_FACE)
            render_buffer.draw()
        else:
            render_buffer.draw(False)
        return super().render(dt, fps)

