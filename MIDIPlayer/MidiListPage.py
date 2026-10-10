import os
import re
import subprocess
import sys

from OpenGL.GL import *

from pyglgame.KEYS import KEY_LEFT, KEY_RIGHT
from pyglgame.ResourceLocation import ResourceLocation
from pyglgame.gameobject.GameObject import GameObject
from pyglgame.gameobject.Text import Text
from pyglgame.render.BufferBuilder import *
from pyglgame.render.RenderBuffer import RenderBuffer

from MIDIPlayer import pages
from MIDIPlayer.config import Settings
from MIDIPlayer.widgets import PickThroughText, uiFont

MIDI_DIR_NAME = "midi"
MIDI_EXTENSIONS = (".mid", ".midi")
PAGE_LIMIT = 8
FONT_PATH = "res/fonts/ygblack.ttf"
PANEL_X = 4.5
ITEM_WIDTH = 2.4
ITEM_HEIGHT = 0.13
ITEM_TOP_Y = 0.55
ITEM_SPACING = 0.16
ITEM_TEXT_WIDTH = ITEM_WIDTH - 0.12
TEXT_LIFT = 0.012
ELLIPSIS = "..."
ITEM_TEXT_HEIGHT = 0.06
BUTTON_TEXT_HEIGHT = 0.07
PATH_COLOR = (0.55, 0.66, 0.78, 1.0)
PATH_HOVER_COLOR = (1.0, 0.82, 0.38, 1.0)
HINT_TEXT = ("ARROWS / PGUP PGDN : TURN PAGE\n"
             "R : RESCAN MIDI      ESC : MAIN PAGE\n"
             "CLICK THE DIR PATH : OPEN FOLDER")

pageFont = uiFont


def midiDirectory(directory: str = None) -> str:
    if directory:
        return os.path.abspath(str(directory))
    return os.path.join(os.getcwd(), MIDI_DIR_NAME)


def isMidiFile(name: str) -> bool:
    return os.path.splitext(str(name))[1].lower() in MIDI_EXTENSIONS


def naturalKey(name: str) -> list:
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", str(name))]


def scanMidiFiles(directory: str = None) -> list:
    folder = midiDirectory(directory)
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    files = [name for name in names
             if isMidiFile(name) and os.path.isfile(os.path.join(folder, name))]
    files.sort(key=naturalKey)
    return files


def openFolder(folder: str) -> bool:
    folder = os.path.abspath(str(folder))
    if not os.path.isdir(folder):
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            return False
    try:
        if hasattr(os, "startfile"):
            os.startfile(folder)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except OSError:
        return False
    return True


def pageCount(total: int, page_size: int = PAGE_LIMIT) -> int:
    """总页数（至少 1 页：空列表也显示一页"没找到"的提示）。"""
    page_size = max(int(page_size), 1)
    total = max(int(total), 0)
    return max((total + page_size - 1) // page_size, 1)


def fitLabel(label: Text, text: str, max_width: float) -> str:
    text = "" if text is None else str(text)
    label.setText(text)
    if max_width <= 0.0 or label.width <= max_width:
        return text
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        label.setText(text[:middle] + ELLIPSIS)
        if label.width <= max_width:
            low = middle
        else:
            high = middle - 1
    shown = text[:low] + ELLIPSIS
    label.setText(shown)
    return shown


def advanceLabelScale(owner, dt: float) -> None:
    if owner.scale != owner.target_scale:
        owner.scale += (owner.target_scale - owner.scale) * dt * 10
        owner.label.setHeight(owner.label_height * owner.scale)
    owner.target_scale = 1.0


class MidiItem(GameObject):
    def __new__(cls, *args, **kwargs):
        return super().__new__(cls)

    def __init__(self, owner: "MidiListPage", slot: int = 0) -> None:
        super().__init__()
        self.name = "MidiItem%d" % (int(slot) + 1)
        self.owner = owner
        self.slot = int(slot)
        self.w = ITEM_WIDTH
        self.h = ITEM_HEIGHT
        self.x = PANEL_X
        self.y = ITEM_TOP_Y - self.slot * ITEM_SPACING
        self.z = 0.0
        self.scale = 1.0
        self.target_scale = 1.0
        self.hovered = False
        self.selected = False
        self.file_name: str = None
        self.label_height = ITEM_TEXT_HEIGHT
        self.label = PickThroughText(
            self, "", font=pageFont(), font_size=64, height=self.label_height,
            x=PANEL_X - TEXT_LIFT, y=self.y, z=self.z - ITEM_WIDTH / 2.0 + 0.06,
            t=270.0, align="left", anchor="left",
            font_color=(228, 236, 250, 255))
        self.label.setActive(False)

    def start(self) -> None:
        super().start()
        self.render_buffer = RenderBuffer()

    def onRemoved(self) -> None:
        render_buffer = getattr(self, "render_buffer", None)
        if render_buffer is not None:
            render_buffer.release()

    def setFile(self, file_name: str = None) -> None:
        self.file_name = file_name
        if file_name is None:
            self.setActive(False)
            return
        self.setActive(bool(getattr(self.owner, "active", True)))
        index = self.owner.page_index * self.owner.page_size + self.slot + 1
        fitLabel(self.label, "%02d  %s" % (index, file_name), ITEM_TEXT_WIDTH)

    def setSelected(self, selected: bool) -> None:
        selected = bool(selected)
        if selected == self.selected:
            return
        self.selected = selected
        self.label.setColor((1.0, 0.95, 0.72, 1.0) if selected
                            else (1.0, 1.0, 1.0, 1.0))

    def boardColor(self) -> tuple:
        if self.selected:
            return (0.10, 0.44, 0.55, 0.85)
        if self.hovered:
            return (0.20, 0.33, 0.48, 0.80)
        return (0.07, 0.11, 0.18, 0.55)


    def onClick(self, x: float, y: float) -> None:
        super().onClick(x, y)
        if self.file_name is not None:
            self.owner.selectItem(self.slot)

    def onHover(self, x: float, y: float) -> None:
        super().onHover(x, y)
        self.hovered = True
        self.target_scale = 1.02

    def setActive(self, active: bool) -> None:
        super().setActive(active)
        self.label.setActive(active)

    def render(self, dt, fps) -> None:
        if not self.active:
            return
        render_buffer = getattr(self, "render_buffer", None)
        if render_buffer is None:
            return                       
        buffer_builder = render_buffer.createBuffer(GL_TRIANGLES, POS | COL)
        x = self.x
        y = self.y
        z = self.z
        w = self.w * self.scale
        h = self.h * self.scale
        r, g, b, a = self.boardColor()
        buffer_builder.pos(x, y + h / 2, z - w / 2).col(r, g, b, a).end()
        buffer_builder.pos(x, y + h / 2, z + w / 2).col(r, g, b, a).end()
        buffer_builder.pos(x, y - h / 2, z + w / 2).col(r, g, b, a).end()
        buffer_builder.pos(x, y + h / 2, z - w / 2).col(r, g, b, a).end()
        buffer_builder.pos(x, y - h / 2, z + w / 2).col(r, g, b, a).end()
        buffer_builder.pos(x, y - h / 2, z - w / 2).col(r, g, b, a).end()
        glDisable(GL_CULL_FACE)
        render_buffer.draw()
        self.hovered = False
        advanceLabelScale(self, dt)
        return super().render(dt, fps)


class PageButton(GameObject):
    def __new__(cls, *args, **kwargs):
        return super().__new__(cls)

    def __init__(self, owner: "MidiListPage", text: str, x: float, y: float,
                 z: float = 0.0, action: str = "next",
                 w: float = 0.9, h: float = 0.15) -> None:
        super().__init__()
        self.name = "PageButton_%s" % str(action).upper()
        self.owner = owner
        self.action = action
        self.w = float(w)
        self.h = float(h)
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)
        self.scale = 1.0
        self.target_scale = 1.0
        self.hovered = False
        self.enabled = True
        self.label_height = BUTTON_TEXT_HEIGHT
        self.label = PickThroughText(
            self, text, font=pageFont(), font_size=64, height=self.label_height,
            x=self.x - TEXT_LIFT, y=self.y, z=self.z, t=270.0,
            align="center", anchor="center",
            font_color=(226, 236, 252, 255))

    def start(self) -> None:
        super().start()
        self.render_buffer = RenderBuffer()

    def onRemoved(self) -> None:
        render_buffer = getattr(self, "render_buffer", None)
        if render_buffer is not None:
            render_buffer.release()

    def setEnabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self.enabled:
            return
        self.enabled = enabled
        self.label.setColor((1.0, 1.0, 1.0, 1.0) if enabled
                            else (0.55, 0.60, 0.70, 1.0))

    def boardColor(self) -> tuple:
        if not self.enabled:
            return (0.06, 0.08, 0.12, 0.45)
        if self.hovered:
            return (0.16, 0.42, 0.62, 0.85)
        return (0.09, 0.16, 0.26, 0.70)

    def onClick(self, x: float, y: float) -> None:
        super().onClick(x, y)
        if self.enabled:
            self.owner.pageStep(self.action)

    def onHover(self, x: float, y: float) -> None:
        super().onHover(x, y)
        if not self.enabled:
            return
        self.hovered = True
        self.target_scale = 1.05

    def setActive(self, active: bool) -> None:
        super().setActive(active)
        self.label.setActive(active)

    def render(self, dt, fps) -> None:
        if not self.active:
            return
        render_buffer = getattr(self, "render_buffer", None)
        if render_buffer is None:
            return
        buffer_builder = render_buffer.createBuffer(GL_TRIANGLES, POS | COL)
        x = self.x
        y = self.y
        z = self.z
        w = self.w * self.scale
        h = self.h * self.scale
        r, g, b, a = self.boardColor()
        buffer_builder.pos(x, y + h / 2, z - w / 2).col(r, g, b, a).end()
        buffer_builder.pos(x, y + h / 2, z + w / 2).col(r, g, b, a).end()
        buffer_builder.pos(x, y - h / 2, z + w / 2).col(r, g, b, a).end()
        buffer_builder.pos(x, y + h / 2, z - w / 2).col(r, g, b, a).end()
        buffer_builder.pos(x, y - h / 2, z + w / 2).col(r, g, b, a).end()
        buffer_builder.pos(x, y - h / 2, z - w / 2).col(r, g, b, a).end()
        glDisable(GL_CULL_FACE)
        render_buffer.draw()
        self.hovered = False
        advanceLabelScale(self, dt)
        return super().render(dt, fps)


class MidiListPage(GameObject):
    def __new__(cls, *args, **kwargs):
        return super().__new__(cls)

    def __init__(self, directory: str = None, page_size: int = PAGE_LIMIT,
                 settings=None) -> None:
        super().__init__()
        self.name = "MidiListPage"
        self.settings = settings if settings is not None else Settings.load()
        self.directory = midiDirectory(directory)
        self.page_size = max(int(page_size), 1)
        self.files: list = []
        self.page_index = 0
        self.selected_file: str = None
        self.on_select = None
        self.title = Text("MIDI LIST", font_size=64, x=PANEL_X, y=0.80, z=0.0,
                          t=270.0, height=0.11, align="center", anchor="center",
                          font=pageFont(), font_color=(240, 246, 255, 255))
        self.path_text = PickThroughText(
            self, "", font_size=48, x=PANEL_X, y=0.68, z=0.0,
            t=270.0, height=0.045, align="center", anchor="center",
            font=pageFont(), font_color=(255, 255, 255, 255))
        self.path_text.setColor(PATH_COLOR)
        self.path_hovered = False
        fitLabel(self.path_text, self.directory, ITEM_TEXT_WIDTH + 0.2)
        self.items = [MidiItem(self, slot) for slot in range(self.page_size)]
        self.empty_text = Text("NO MIDI FILE IN midi/", font_size=64, x=PANEL_X,
                               y=-0.10, z=0.0, t=270.0, height=0.07,
                               align="center", anchor="center", font=pageFont(),
                               font_color=(228, 132, 132, 255))
        self.prev_button = PageButton(self, "PREV", PANEL_X, -0.75, z=-0.75,
                                      action="prev")
        self.next_button = PageButton(self, "NEXT", PANEL_X, -0.75, z=0.75,
                                      action="next")
        self.page_text = Text("", font_size=48, x=PANEL_X, y=-0.75, z=0.0,
                              t=270.0, height=0.055, align="center", anchor="center",
                              font=pageFont(), font_color=(190, 210, 235, 255))
        self.hint_text = Text(HINT_TEXT, font_size=48, x=PANEL_X, y=0.87, z=-1.45,
                              t=270.0, height=0.08, align="left", anchor="topleft",
                              font=pageFont(), font_color=(146, 168, 194, 255))
        self.refresh()
        self.setActive(False)

    def totalPages(self) -> int:
        """总页数（空列表也算 1 页：那一页显示"没找到"）。"""
        return pageCount(len(self.files), self.page_size)

    def refresh(self) -> list:
        """重新扫描目录并刷新当前页（返回文件列表）。"""
        self.files = scanMidiFiles(self.directory)
        self.page_index = max(0, min(self.page_index, self.totalPages() - 1))
        self.applyPage()
        return self.files

    def fileAt(self, slot: int) -> str:
        """当前页第 ``slot`` 行对应的文件名（越界 = ``None``）。"""
        index = self.page_index * self.page_size + int(slot)
        if 0 <= index < len(self.files):
            return self.files[index]
        return None

    def gotoPage(self, index: int) -> None:
        """跳到第 ``index`` 页（自动夹在合法范围内）。"""
        self.page_index = max(0, min(int(index), self.totalPages() - 1))
        self.applyPage()

    def nextPage(self) -> None:
        self.gotoPage(self.page_index + 1)

    def prevPage(self) -> None:
        self.gotoPage(self.page_index - 1)

    def pageStep(self, action: str) -> None:
        """按钮回调：``prev`` = 上一页，其余当下一页。"""
        if action == "prev":
            self.prevPage()
        else:
            self.nextPage()

    def applyPage(self) -> None:
        for slot, item in enumerate(self.items):
            item.setFile(self.fileAt(slot))
            item.setSelected(item.file_name is not None
                             and item.file_name == self.selected_file)
        self.page_text.setText("%d / %d" % (self.page_index + 1, self.totalPages()))
        self.prev_button.setEnabled(self.page_index > 0)
        self.next_button.setEnabled(self.page_index < self.totalPages() - 1)
        self.empty_text.setActive(bool(self.active) and not self.files)

    def selectItem(self, slot: int) -> None:
        """点选当前页第 ``slot`` 行：记录选中、刷新高亮、触发 :attr:`on_select`。"""
        file_name = self.fileAt(slot)
        if file_name is None:
            return
        self.selected_file = file_name
        self.applyPage()
        path = os.path.join(self.directory, file_name)
        print("MIDI selected: %s" % path)
        if self.on_select is not None:
            self.on_select(path)
        self.playFile(path, file_name)

    def playFile(self, path: str, file_name: str = None) -> bool:
        target = pages.page("WaterfallPage")
        if target is None:
            print("WaterfallPage 未注册，无法播放")
            return False
        target.applySettings(self.settings)
        if not target.loadFile(path, file_name):
            return False
        pages.switch("WaterfallPage")
        return True

    def openFolder(self) -> bool:
        opened = openFolder(self.directory)
        if opened:
            print("MIDI folder: %s" % self.directory)
        else:
            print("Cannot open folder: %s" % self.directory)
        return opened

    def onClick(self, x: float, y: float) -> None:
        self.openFolder()

    def onHover(self, x: float, y: float) -> None:
        self.path_hovered = True

    def update(self, dt: float, tps: float) -> None:
        if not self.active:
            return
        super().update(dt, tps)
        pages.applyPendingCamera()
        color = PATH_HOVER_COLOR if self.path_hovered else PATH_COLOR
        if color != self.path_text.color:
            self.path_text.setColor(color)
        self.path_hovered = False
        window = self.render_global.window
        if window.getKeyDown(KEY_RIGHT) or window.getKeyDown("page down"):
            self.nextPage()
        if window.getKeyDown(KEY_LEFT) or window.getKeyDown("page up"):
            self.prevPage()
        if window.getKeyDown("r") or window.getKeyDown("R"):
            self.refresh()
        if window.getKeyDown("esc"):
            pages.switch("MainPage")

    def setActive(self, active: bool) -> None:
        super().setActive(active)
        self.title.setActive(active)
        self.path_text.setActive(active)
        self.page_text.setActive(active)
        self.hint_text.setActive(active)
        self.prev_button.setActive(active)
        self.next_button.setActive(active)
        for item in self.items:
            item.setActive(active and item.file_name is not None)
        self.empty_text.setActive(active and not self.files)

