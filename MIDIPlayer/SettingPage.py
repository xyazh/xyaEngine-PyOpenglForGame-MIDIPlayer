import glob
import os
import sys

from OpenGL.GL import *

from pyglgame.gameobject.GameObject import GameObject
from pyglgame.gameobject.Text import Text
from pyglgame.render.BufferBuilder import *
from pyglgame.render.RenderBuffer import RenderBuffer

from MIDIPlayer import pages
from MIDIPlayer.config import (BACKGROUND_MODES, PIANO_PRESETS,
                               VIDEO_FPS_OPTIONS, WINDOW_PRESETS, Settings)
from MIDIPlayer.widgets import TextButton, quad, uiFont

PANEL_X = 7.5
ROW_TOP_Y = 0.92
ROW_SPACING = 0.16
ROW_WIDTH = 5.2
ROW_HEIGHT = 0.15
NAME_Z = -2.3
VALUE_Z = 0.15
VALUE_MAX_WIDTH = 1.05
TEXT_LIFT = 0.03
PREV_Z = 1.35
NEXT_Z = 1.8

ROWS = (
    ("window", "WINDOW SIZE", "window"),
    ("fullscreen", "FULLSCREEN", "toggle"),
    ("background_mode", "BACKGROUND", "cycle", BACKGROUND_MODES),
    ("background_image", "BG IMAGE", "images"),
    ("background_brightness", "BG BRIGHTNESS", "number", 0.1, 0.0, 2.0),
    ("save_video", "SAVE VIDEO", "toggle"),
    ("save_audio", "SAVE AUDIO", "toggle"),
    ("video_fps", "VIDEO FPS", "cycle", VIDEO_FPS_OPTIONS),
    ("note_speed", "NOTE SPEED", "number", 2.0, 4.0, 40.0),
    ("note_brightness", "NOTE BRIGHT", "number", 0.1, 0.2, 2.0),
    ("piano_preset", "PIANO VOICE", "cycle", PIANO_PRESETS),
)

PRESET_LABELS = {"grand": "GRAND", "upright": "UPRIGHT", "honkyTonk": "HONKY TONK"}


def fitText(label, text: str, max_width: float) -> str:
    text = "" if text is None else str(text)
    label.setText(text)
    if max_width <= 0.0 or label.width <= max_width:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        label.setText(text[:middle] + "...")
        if label.width <= max_width:
            low = middle
        else:
            high = middle - 1
    shown = text[:low] + "..."
    label.setText(shown)
    return shown


def imageCandidates() -> list:
    from pyglgame.tools import paths
    folder = os.path.join(paths.app_root(), "background")
    names = sorted(os.path.basename(path)
                   for path in glob.glob(os.path.join(folder, "*.png")))
    return [""] + ["background/%s" % name for name in names]


class SettingRow(GameObject):
    def __new__(cls, *args, **kwargs):
        return super().__new__(cls)

    def __init__(self, owner, index: int, key: str, label: str, kind: str,
                 extra: tuple = ()) -> None:
        super().__init__()
        self.name = "SettingRow%d" % index
        self.owner = owner
        self.index = int(index)
        self.key = key
        self.kind = kind
        self.extra = tuple(extra)
        self.x = PANEL_X
        self.y = ROW_TOP_Y - self.index * ROW_SPACING
        self.z = 0.0
        self.hovered = False
        self.built = False
        self.label = Text(label, font=uiFont(), font_size=48, height=0.045,
                          x=self.x - TEXT_LIFT, y=self.y, z=NAME_Z, t=270.0,
                          align="left", anchor="left",
                          font_color=(202, 216, 236, 255))
        self.value = Text("", font=uiFont(), font_size=48, height=0.05,
                          x=self.x - TEXT_LIFT, y=self.y, z=VALUE_Z, t=270.0,
                          align="center", anchor="center",
                          font_color=(248, 232, 190, 255))
        self.prev_button = TextButton(self, "<", self.x, self.y, z=PREV_Z,
                                      action="prev", w=0.26, h=0.12,
                                      text_height=0.05)
        self.next_button = TextButton(self, ">", self.x, self.y, z=NEXT_Z,
                                      action="next", w=0.26, h=0.12,
                                      text_height=0.05)

    def start(self) -> None:
        super().start()
        self.render_buffer = RenderBuffer()

    def onRemoved(self) -> None:
        if getattr(self, "render_buffer", None) is not None:
            self.render_buffer.release()

    def setActive(self, active: bool) -> None:
        super().setActive(active)
        self.label.setActive(active)
        self.value.setActive(active)
        self.prev_button.setActive(active)
        self.next_button.setActive(active)

    def setValueText(self, text: str) -> None:
        fitText(self.value, text, VALUE_MAX_WIDTH)

    def onAction(self, action: str, button) -> None:
        self.owner.changeRow(self.key, -1 if action == "prev" else 1)

    def render(self, dt, fps) -> None:
        if not self.active:
            return
        render_buffer = getattr(self, "render_buffer", None)
        if render_buffer is None:
            return
        if not self.built:
            builder = render_buffer.createBuffer(GL_TRIANGLES, POS | COL)
            quad(builder, self.x, self.y, self.z, ROW_WIDTH, ROW_HEIGHT,
                 (0.10, 0.14, 0.20, 0.45))
            self.built = True
            glDisable(GL_CULL_FACE)
            render_buffer.draw()
        else:
            render_buffer.draw(False)
        return super().render(dt, fps)


def shorten(text: str, limit: int = 76) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit - 3] + "..."


class SettingPage(GameObject):
    def __new__(cls, *args, **kwargs):
        return super().__new__(cls)

    def __init__(self, settings=None) -> None:
        super().__init__()
        self.name = "SettingPage"
        self.settings = settings if settings is not None else Settings.load()
        self.title = Text("SETTINGS", font=uiFont(), font_size=64, height=0.115,
                          x=PANEL_X, y=1.30, z=0.0, t=270.0,
                          align="center", anchor="center",
                          font_color=(238, 246, 255, 255))
        self.subtitle = Text("", font=uiFont(), font_size=48, height=0.04,
                             x=PANEL_X, y=1.14, z=0.0, t=270.0,
                             align="center", anchor="center",
                             font_color=(140, 166, 196, 255))
        self.rows = [SettingRow(self, index, definition[0], definition[1],
                                definition[2], definition[3:])
                     for index, definition in enumerate(ROWS)]
        self.reset_button = TextButton(self, "RESET DEFAULTS", PANEL_X, -1.22,
                                       z=-1.6, action="reset", w=1.7, h=0.14,
                                       text_height=0.05)
        self.save_button = TextButton(self, "SAVE", PANEL_X, -1.22, z=0.15,
                                      action="save", w=0.9, h=0.14,
                                      text_height=0.05)
        self.back_button = TextButton(self, "BACK", PANEL_X, -1.22, z=1.5,
                                      action="back", w=0.9, h=0.14,
                                      text_height=0.05)
        self.note = Text("WINDOW SIZE / FULLSCREEN TAKE EFFECT AFTER RESTART",
                         font=uiFont(), font_size=48, height=0.038,
                         x=PANEL_X, y=-1.42, z=0.0, t=270.0,
                         align="center", anchor="center",
                         font_color=(126, 150, 180, 255))
        self.refresh()
        self.setActive(False)

    def start(self) -> None:
        super().start()
        self.applySettings()

    def applySettings(self) -> None:
        target = pages.page("WaterfallPage")
        if target is not None:
            target.applySettings(self.settings)

    def rowValueText(self, key: str) -> str:
        settings = self.settings
        if key == "window":
            width, height = settings.window_size
            return "%d x %d" % (width, height)
        if key == "fullscreen":
            return "ON" if settings.fullscreen else "OFF"
        if key == "background_mode":
            return settings.background_mode.upper()
        if key == "background_image":
            path = settings.background_image
            return shorten(os.path.basename(path), 28) if path else "NONE"
        if key == "background_brightness":
            return "%.2f" % settings.background_brightness
        if key == "save_video":
            return "ON" if settings.save_video else "OFF"
        if key == "save_audio":
            return "ON" if settings.save_audio else "OFF"
        if key == "show_hud":
            return "ON" if settings.show_hud else "OFF"
        if key == "video_fps":
            return str(settings.video_fps)
        if key == "note_speed":
            return "%.0f" % settings.note_speed
        if key == "note_brightness":
            return "%.2f" % settings.note_brightness
        if key == "piano_preset":
            name = settings.piano_preset
            return PRESET_LABELS.get(name, name)
        return ""

    def refresh(self) -> None:
        for row in self.rows:
            row.setValueText(self.rowValueText(row.key))
        self.subtitle.setText(shorten("CONFIG: %s" % self.settings.path))

    def setActive(self, active: bool) -> None:
        super().setActive(active)
        self.title.setActive(active)
        self.subtitle.setActive(active)
        self.note.setActive(active)
        self.reset_button.setActive(active)
        self.save_button.setActive(active)
        self.back_button.setActive(active)
        for row in self.rows:
            row.setActive(active)

    def cycleWindow(self, delta: int) -> None:
        options = list(WINDOW_PRESETS)
        current = self.settings.window_size
        index = options.index(current) if current in options else 0
        size = options[(index + delta) % len(options)]
        self.settings.setWindowSize(*size)

    def cycleOption(self, key: str, options: list, delta: int) -> None:
        if not options:
            return
        current = self.settings.get(key)
        index = options.index(current) if current in options else 0
        self.settings.set(key, options[(index + delta) % len(options)])

    def changeRow(self, key: str, delta: int) -> None:
        for definition in ROWS:
            if definition[0] != key:
                continue
            kind = definition[2]
            extra = definition[3:]
            if kind == "window":
                self.cycleWindow(delta)
            elif kind == "toggle":
                self.settings.set(key, not bool(self.settings.get(key)))
            elif kind == "cycle":
                self.cycleOption(key, list(extra[0]), delta)
            elif kind == "images":
                self.cycleOption(key, imageCandidates(), delta)
            elif kind == "number":
                self.settings.step(key, float(extra[0]) * delta,
                                   float(extra[1]), float(extra[2]))
            break
        self.settings.save()
        self.applySettings()
        self.refresh()

    def saveSettings(self) -> None:
        path = self.settings.save()
        if path:
            print("设置已保存: %s" % path)

    def resetAll(self) -> None:
        self.settings.reset()
        self.settings.save()
        self.applySettings()
        self.refresh()
        print("设置已恢复默认: %s" % self.settings.path)

    def back(self) -> None:
        pages.switch("MainPage")

    def onAction(self, action: str, button) -> None:
        if action == "save":
            self.saveSettings()
        elif action == "reset":
            self.resetAll()
        elif action == "back":
            self.back()

    def update(self, dt, tps) -> None:
        if not self.active:
            return
        super().update(dt, tps)
        pages.applyPendingCamera()
        if self.render_global.window.getKeyDown("esc"):
            self.back()