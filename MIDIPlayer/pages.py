from pyglgame.gameobject.GameObject import GameObject

_pages = {}
_current = None


def register(page) -> GameObject:
    name = getattr(page, "name", None)
    if not name:
        raise ValueError("page must have a name")
    _pages[name] = page
    return page


def page(name: str):
    return _pages.get(name)


def names() -> list:
    return list(_pages)


def current() -> str:
    return _current


def switch(name: str, active: bool = True) -> bool:
    global _current
    if name not in _pages:
        print("No such page: %s" % name)
        return False
    for key, item in _pages.items():
        item.setActive(bool(active) and key == name)
    _current = name if active else None
    if active:
        if name == "WaterfallPage":
            useWaterfallCamera()
        else:
            useUiCamera()
    return True


def camera():
    from pyglgame.gameobject.camera.Camera import Camera
    found = GameObject.getObjests(type=Camera)
    return found[0] if found else None


_camera_mode = None
_pending_mode = None


def useWaterfallCamera() -> None:
    global _pending_mode
    _pending_mode = "waterfall"


def useUiCamera() -> None:
    global _pending_mode
    _pending_mode = "ui"


def applyPendingCamera() -> None:
    global _pending_mode, _camera_mode
    mode = _pending_mode
    _pending_mode = None
    if mode is None or mode == _camera_mode:
        return
    _camera_mode = mode
    cam = camera()
    if cam is None:
        return
    if mode == "waterfall":
        if hasattr(cam, "setPlayerPos"):
            cam.setPlayerPos()
        cam.can_move = True
        if hasattr(cam, "useWBOIT"):
            cam.useWBOIT(True)
            cam.setWBOITDepthScale(1.0 / 20.0)
        return
    if hasattr(cam, "setDisPos"):
        cam.setDisPos()
    cam.can_move = False
    if hasattr(cam, "useWBOIT"):
        cam.useWBOIT(False)


def windowSize() -> tuple:
    from pyglgame.RenderGlobal import RenderGlobal
    instance = RenderGlobal.instance
    if instance is None or instance.window is None:
        return (0, 0)
    size = instance.window.size
    return (int(size.w), int(size.h))


def settings():
    from MIDIPlayer.config import Settings
    holder = page("MidiListPage")
    if holder is not None and getattr(holder, "settings", None) is not None:
        return holder.settings
    from pyglgame.App import App
    app = App.instance
    if app is not None and getattr(app, "settings", None) is not None:
        return app.settings
    return Settings.load()
