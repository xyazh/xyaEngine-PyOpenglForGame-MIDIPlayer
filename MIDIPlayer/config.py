import json
import os

CONFIG_NAME = "config.json"

DEFAULTS = {
    "window_width": 1920,
    "window_height": 1080,
    "fullscreen": False,
    "background_mode": "dynamic",
    "background_image": "",
    "background_brightness": 1.0,
    "save_video": False,
    "save_audio": False,
    "show_hud": True,
    "video_fps": 60,
    "note_speed": 16.0,
    "note_brightness": 1.0,
    "piano_preset": "grand",
}

WINDOW_PRESETS = ((960, 540), (1280, 720), (1600, 900), (1920, 1080), (2560, 1440))
VIDEO_FPS_OPTIONS = (30, 60, 90, 120)
BACKGROUND_MODES = ("dynamic", "image")
PIANO_PRESETS = ("grand", "upright", "honkyTonk")


def configPath(directory: str = None) -> str:
    if directory:
        return os.path.join(os.path.abspath(str(directory)), CONFIG_NAME)
    return os.path.join(os.getcwd(), CONFIG_NAME)


def coerce(default, value):
    try:
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if isinstance(default, int):
            return int(value)
        if isinstance(default, float):
            return float(value)
        if isinstance(default, str):
            return str(value)
    except (TypeError, ValueError):
        return default
    return value


class Settings:
    def __init__(self, values: dict = None, path: str = None) -> None:
        self.path = str(path) if path else configPath()
        self.values = dict(DEFAULTS)
        if values:
            self.update(values)

    def update(self, values: dict) -> "Settings":
        for key, default in DEFAULTS.items():
            if key in values:
                self.values[key] = coerce(default, values[key])
        return self

    def copy(self) -> dict:
        return dict(self.values)

    def get(self, key: str, default=None):
        if key in self.values:
            return self.values[key]
        if key in DEFAULTS:
            return DEFAULTS[key]
        return default

    def set(self, key: str, value) -> "Settings":
        self.values[key] = coerce(DEFAULTS[key], value) if key in DEFAULTS else value
        return self

    def reset(self) -> "Settings":
        self.values = dict(DEFAULTS)
        return self

    def isDefault(self, key: str) -> bool:
        return self.values.get(key) == DEFAULTS.get(key)

    def cycle(self, key: str, options) -> object:
        options = list(options)
        try:
            index = options.index(self.get(key))
        except ValueError:
            index = -1
        return self.set(key, options[(index + 1) % len(options)]).get(key)

    def step(self, key: str, delta: float, low: float, high: float) -> float:
        value = float(self.get(key)) + float(delta)
        value = min(max(value, float(low)), float(high))
        return float(self.set(key, round(value, 4)).get(key))

    def reload(self) -> "Settings":
        try:
            with open(self.path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
        except (OSError, ValueError):
            return self
        if isinstance(data, dict):
            self.update(data)
        return self

    def save(self) -> str:
        try:
            with open(self.path, "w", encoding="utf-8") as fp:
                json.dump(self.values, fp, ensure_ascii=False, indent=2)
        except OSError as e:
            print("Cannot write %s: %s" % (self.path, e))
            return ""
        return self.path

    @classmethod
    def load(cls, directory: str = None) -> "Settings":
        return cls(path=configPath(directory)).reload()

    @property
    def window_size(self) -> tuple:
        return (int(self.get("window_width")), int(self.get("window_height")))

    def setWindowSize(self, width: int, height: int) -> "Settings":
        self.set("window_width", width)
        self.set("window_height", height)
        return self

    def cycleWindowSize(self) -> tuple:
        current = self.window_size
        try:
            index = WINDOW_PRESETS.index(current)
        except ValueError:
            index = -1
        size = WINDOW_PRESETS[(index + 1) % len(WINDOW_PRESETS)]
        self.setWindowSize(*size)
        return size

    @property
    def fullscreen(self) -> bool:
        return bool(self.get("fullscreen"))

    @property
    def background_mode(self) -> str:
        mode = str(self.get("background_mode"))
        return mode if mode in BACKGROUND_MODES else BACKGROUND_MODES[0]

    @property
    def background_image(self) -> str:
        return str(self.get("background_image") or "")

    @property
    def background_brightness(self) -> float:
        return float(self.get("background_brightness"))

    @property
    def save_video(self) -> bool:
        return bool(self.get("save_video"))

    @property
    def save_audio(self) -> bool:
        return bool(self.get("save_audio"))

    @property
    def show_hud(self) -> bool:
        return bool(self.get("show_hud"))

    @property
    def video_fps(self) -> int:
        return int(self.get("video_fps"))

    @property
    def note_speed(self) -> float:
        return float(self.get("note_speed"))

    @property
    def note_brightness(self) -> float:
        return float(self.get("note_brightness"))

    @property
    def piano_preset(self) -> str:
        preset = str(self.get("piano_preset"))
        return preset if preset in PIANO_PRESETS else PIANO_PRESETS[0]

    def __repr__(self) -> str:
        return "<Settings %s %r>" % (self.path, self.values)
