import math
from OpenGL.GL import *
from pyglgame.compute import glCompute
from pyglgame.gameobject.GameObject import GameObject
from pyglgame.gameobject.Text import Text
from pyglgame.render.BufferBuilder import *
from pyglgame.render.Image import Image
from pyglgame.render.RenderBuffer import RenderBuffer
from pyglgame.render.TextureStorage2D import TextureStorage2D
from pyglgame.RenderGlobal import RenderGlobal
from pyglgame.ResourceLocation import ResourceLocation

from MIDIPlayer import pages



class StartButton(GameObject):
    def __init__(self) -> None:
        super().__init__()
        self.name = "StartButton"
        self.w = 0.8
        self.h = 0.3
        self.x = 3
        self.y = 0.25
        self.z = 0.5
        self.scale = 1
        self.target_scale = 1
        self.alpha = 0.8

    def start(self) -> None:
        super().start()
        self.render_buffer = RenderBuffer()
        self.tex = Image(ResourceLocation("res/img/startbtn.png")).getTexture()

    def onClick(self, x, y) -> None:
        super().onClick(x, y)
        pages.switch("MidiListPage")

    def onHover(self, x, y) -> None:
        super().onHover(x, y)
        self.alpha = 1.0
        self.target_scale = 1.03

    def render(self, dt, fps):
        buffer_builder = self.render_buffer.createBuffer(
            GL_TRIANGLES, POS | COL | TEX)
        x = self.x
        y = self.y
        z = self.z
        w = self.w * self.scale
        h = self.h * self.scale
        a = self.alpha
        self.tex.bind()
        buffer_builder.pos(x, y+h/2, z-w/2).col(1, 1, 1, a).tex(0, 0).end()
        buffer_builder.pos(x, y+h/2, z+w/2).col(1, 1, 1, a).tex(1, 0).end()
        buffer_builder.pos(x, y-h/2, z+w/2).col(1, 1, 1, a).tex(1, 1).end()
        buffer_builder.pos(x, y+h/2, z-w/2).col(1, 1, 1, a).tex(0, 0).end()
        buffer_builder.pos(x, y-h/2, z+w/2).col(1, 1, 1, a).tex(1, 1).end()
        buffer_builder.pos(x, y-h/2, z-w/2).col(1, 1, 1, a).tex(0, 1).end()
        glDisable(GL_CULL_FACE)
        self.render_buffer.draw()
        self.alpha = 0.8
        if self.scale != self.target_scale:
            self.scale += (self.target_scale - self.scale) * dt * 10
        self.target_scale = 1
        return super().render(dt, fps)


class SettingButton(GameObject):
    def __init__(self) -> None:
        super().__init__()
        self.name = "SettingButton"
        self.w = 0.8
        self.h = 0.3
        self.x = 3
        self.y = -0.2
        self.z = 0.5
        self.scale = 1
        self.target_scale = 1
        self.alpha = 0.8

    def start(self) -> None:
        super().start()
        self.render_buffer = RenderBuffer()
        self.tex = Image(ResourceLocation("res/img/settingbtn.png")).getTexture()

    def onClick(self, x, y) -> None:
        super().onClick(x, y)
        pages.switch("SettingPage")

    def onHover(self, x, y) -> None:
        super().onHover(x, y)
        self.alpha = 1.0
        self.target_scale = 1.03

    def render(self, dt, fps):
        buffer_builder = self.render_buffer.createBuffer(
            GL_TRIANGLES, POS | COL | TEX)
        x = self.x
        y = self.y
        z = self.z
        w = self.w * self.scale
        h = self.h * self.scale
        a = self.alpha
        self.tex.bind()
        buffer_builder.pos(x, y+h/2, z-w/2).col(1, 1, 1, a).tex(0, 0).end()
        buffer_builder.pos(x, y+h/2, z+w/2).col(1, 1, 1, a).tex(1, 0).end()
        buffer_builder.pos(x, y-h/2, z+w/2).col(1, 1, 1, a).tex(1, 1).end()
        buffer_builder.pos(x, y+h/2, z-w/2).col(1, 1, 1, a).tex(0, 0).end()
        buffer_builder.pos(x, y-h/2, z+w/2).col(1, 1, 1, a).tex(1, 1).end()
        buffer_builder.pos(x, y-h/2, z-w/2).col(1, 1, 1, a).tex(0, 1).end()
        glDisable(GL_CULL_FACE)
        self.render_buffer.draw()
        self.alpha = 0.8
        if self.scale != self.target_scale:
            self.scale += (self.target_scale - self.scale) * dt * 10
        self.target_scale = 1
        return super().render(dt, fps)




class MainPage(GameObject):
    def __init__(self) -> None:
        super().__init__()
        self.name = "MainPage"
        self.start_button = StartButton()
        self.setting_button = SettingButton()
        self.title = Text("MIDI PLAYER", font_size=60, x=3, y=0.5, z=-0.7,
                          t=270, align="center", anchor="center", pixels_per_unit=1024,
                          font=ResourceLocation("res/fonts/ygblack.ttf"))

    def update(self, dt, tps) -> None:
        if not self.active:
            return
        pages.applyPendingCamera()

    def setActive(self, active: bool) -> None:
        super().setActive(active)
        self.start_button.setActive(active)
        self.setting_button.setActive(active)
        self.title.setActive(active)