from pyglgame.App import App

from MIDIPlayer import pages
from MIDIPlayer.MainCamera import MainCamera
from MIDIPlayer.BackGround import BackGround
from MIDIPlayer.config import Settings


def buildPages(settings: Settings = None) -> Settings:
    settings = settings if settings is not None else Settings.load()
    app = App()
    app.settings = settings
    app.window.setWindownSize(settings.window_size)
    if settings.fullscreen:
        app.window.setFullScreen()
    app.setMaxFps(240)
    app.setRealTimeUpdate(True)
    MainCamera(msaa_val=32)
    from MIDIPlayer.MainPage import MainPage
    from MIDIPlayer.MidiListPage import MidiListPage
    from MIDIPlayer.SettingPage import SettingPage
    from MIDIPlayer.WaterfallPage import WaterfallPage
    BackGround(settings=settings)
    pages.register(MainPage())
    pages.register(MidiListPage(settings=settings))
    pages.register(WaterfallPage(settings=settings))
    pages.register(SettingPage(settings=settings))
    pages.switch("MainPage")
    return settings


if __name__ == "__main__":
    buildPages()
    App().start(title="MIDI PLAYER")
