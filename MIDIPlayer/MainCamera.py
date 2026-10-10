import glm
from OpenGL.GL import *
from pyglgame.KEYS import *
from pyglgame.gameobject.camera.Camera import Camera
from pyglgame.gameobject.camera.m.MPerspective import MPerspective
from pyglgame.gameobject.GameObject import GameObject
from pyglgame.render.MSAAFrameBuffer import MSAAFrameBuffer


class MainCamera(Camera):
    def __new__(cls, msaa_val: int = 0):
        return super().__new__(cls, msaa_val)

    def start(self):
        super().start()
        glDisable(GL_CULL_FACE)
        m = MPerspective()
        m.setPerspective(fov=22.5, aspect=self.size.w/self.size.h)
        self.setProjection(m)
        self.useBloom(level1=4, level2=8)
        self.useObjectId(True)
        self.switch(True)
        self.useWBOIT(True)
        self.setWBOITDepthScale(0.05)

    def createFrameBuffer(self) -> None:
        # 用窗口真实尺寸，而不是写死的 WIN_W/WIN_H：
        # 否则 MSAA 缓冲与 Bloom 纹理尺寸不一致，blit/回读都会错位
        # samples 走 clampWboitSamples：WBOIT 的逐采样 accum/reveal 显存开销
        # 随采样数线性增长，开启时会把 MSAA 压到 WBOIT.MAX_SAMPLES 以内
        self.frame_buffer = MSAAFrameBuffer(
            int(self.size.w), int(self.size.h), use_depth=True,
            param=GL_LINEAR, samples=self.clampWboitSamples(32),
            internal_format=self.internal_format)

    def onWindowSizeChanged(self, width, height) -> None:
        return

    def __init__(self, msaa_val: int = 0) -> None:
        super().__init__(msaa_val)
        self.last_mouse = None
        self.forward = glm.vec3(1, 0, 0.02)
        self.right = None
        self.setDisPos()
        self.can_move = True
        self.name = "MainCamera"

    def setDisPos(self) -> None:
        self.x = 0
        self.y = 0
        self.z = 0
        self.p = 0
        self.t = 90
        self.r = 0

    def setPlayerPos(self) -> None:
        self.x = -8.3
        self.y = 0
        self.z = 2.70
        self.p = 0
        self.t = 93.27
        self.r = 0

    def renderStart(self) -> None:
        self.beginFrame()
        if self.m is not None:
            self.forward = self.m.setCameraPosAndPYR(
                self.x, self.y, self.z, self.p, self.t, self.r)
            self.right = glm.cross(self.forward, glm.vec3(0.0, 1.0, 0.0))
            self.m.setCameraUniforms()
        self.frame_buffer.drawStart()

    def renderEnd(self) -> None:
        super().renderEnd()

    def update(self, dt, tps):
        window = self.render_global.window
        mouse = window.getLeftClickMouse()
        if mouse is not None:
            hovered: GameObject = self.render_global.pickObject(*mouse, self)
            if hovered is not None:
                hovered.onClick(*mouse)
        mouse = window.getMouse()
        if mouse is not None:
            hovered: GameObject = self.render_global.pickObject(*mouse, self)
            if hovered is not None:
                hovered.onHover(*mouse)
        if not self.can_move:
            return super().update(dt, tps)
        if self.forward is None or self.right is None:
            return super().update(dt, tps)
        move_x, move_y, move_z = 0, 0, 0

        speed = 10 * dt
        if window.getKey("w") or window.getKey("W"):
            move_x += self.forward.x * speed
            move_y += self.forward.y * speed
            move_z += self.forward.z * speed
        if window.getKey("s") or window.getKey("S"):
            move_x -= self.forward.x * speed
            move_y -= self.forward.y * speed
            move_z -= self.forward.z * speed
        if window.getKey("d") or window.getKey("D"):
            move_x += self.right.x * speed
            move_z += self.right.z * speed
        if window.getKey("a") or window.getKey("A"):
            move_x -= self.right.x * speed
            move_z -= self.right.z * speed
        if window.getKey(KEY_SPACE):
            move_y += speed
        if window.getKey(KEY_SHIFT):
            move_y -= speed
        self.x += move_x
        self.y += move_y
        self.z += move_z
        mouse = window.getOnMouse()
        if self.last_mouse is not None and window.mouse_left_button_on:
            self.t -= (mouse[0] - self.last_mouse[0]) * dt * 5
            self.p -= (mouse[1] - self.last_mouse[1]) * dt * 5
        self.last_mouse = mouse
        if not window.mouse_left_button_on:
            self.last_mouse = None
            return super().update(dt, tps)
