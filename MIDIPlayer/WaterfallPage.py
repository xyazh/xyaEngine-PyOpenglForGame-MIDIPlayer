import mido
import math
import os
import random
import threading
import time
from OpenGL.GL import *
from pyglgame.RenderGlobal import RenderGlobal
from pyglgame.gameobject.GameObject import GameObject
from pyglgame.gameobject.Text import Text
from pyglgame.render.BufferBuilder import *
from pyglgame.render.RenderBuffer import RenderBuffer
from pyglgame.openal.Player import Player
from pyglgame.openal.synth import PianoPreset, Synthesizer
from pyglgame.xyazhServer.ConsoleMessage import ConsoleMessage

from MIDIPlayer import pages
from MIDIPlayer.config import Settings
from MIDIPlayer.widgets import ProgressBar, TextButton, uiFont

# True = 全曲一直踩住持音踏板（不抬键制音，音符自然衰完，混响更足也更糊）
PIANO_PEDAL = True
LONG = 200
HLS = 1


class Particle:
    alive_particles: list["Particle"] = []
    dead_particles: list["Particle"] = []

    def __new__(cls, x, y, z, color):
        if len(cls.dead_particles) > 0:
            obj = cls.dead_particles.pop()
        else:
            obj = super().__new__(cls)
        cls.alive_particles.append(obj)
        return obj

    def __init__(self, x, y, z, color) -> None:
        self.x = x
        self.y = y
        self.z = z
        self.color = color
        self.age = 0
        self.max_age = abs(random.gauss(0, 3))
        self.alive = True
        self.velocity_x = -random.uniform(0, 0.1)
        self.velocity_y = 0
        self.velocity_z = 0
        self.size = 10

    def update(self, dt, tps) -> None:
        self.age += dt
        if self.age > self.max_age:
            self.alive = False

        self.x += self.velocity_x * dt
        self.y += self.velocity_y * dt
        self.z += self.velocity_z * dt
        self.velocity_x *= 0.999
        self.velocity_y *= 1.0
        self.velocity_z *= 1.0

    def render(self, dt, fps, buffer: BufferBuilder) -> None:
        if not self.alive:
            return
        buffer.pos(self.x, self.y, self.z).col(
            *self.color).siz(self.size*(self.max_age-self.age)/self.max_age).end()

    @classmethod
    def updates(cls, dt, tps) -> None:
        alive = []
        for p in cls.alive_particles:
            p.update(dt, tps)
            if p.alive:
                alive.append(p)
            else:
                cls.dead_particles.append(p)
        cls.alive_particles = alive

    @classmethod
    def clear(cls) -> None:
        cls.dead_particles.extend(cls.alive_particles)
        cls.alive_particles = []


class Note:
    def __init__(self, pitch, velocity, start, duration, seed, speed: float = 16, settings: Settings = None) -> None:
        self.speed = float(speed)
        # 这里的 start 和 duration 已经是秒
        self.pitch = pitch
        self.velocity = velocity/127
        self.start = start * self.speed + 24  # 延迟3秒进入画面
        self.orig_start = self.start
        self.duration = duration * self.speed
        # 这个音**理想**的出声时刻（"曲子第几秒"，曲子零点 = 第一条渲染帧）：
        # 音符左端碰到判定线（self.start == -2）的那一刻。帧是离散的，逐音触发
        # 只能取"最接近的那一帧"；整曲混音（PIANO_SONG_MIX）用它把起音摆到精确的
        # 采样点上。⚠ 必须在第一次 update 之前算好 —— update 每帧都在改 self.start。
        self.song_time = (self.start + 2.0) / self.speed
        self.flag = False

        l = 2 * self.velocity * settings.note_brightness
        self.colors = (
            (math.sin((start+seed)/5+51)/2+1) * l,
            (math.sin((start+seed)/7+71)/2+1) * l,
            (math.sin((start+seed)/11+91)/2+1) * l,
            1.0
        )
        self.z = (random.random()-0.5)/100
        self.color = self.colors
        self.played = False  # 防止重复播放
        self.freq = 440 * math.pow(2, (self.pitch - 69) / 12.0)
        # 合成器是**惰性渲染**（一个音 10~50ms）：这里只记参数，真正要弹的时候
        # 才算波形；否则几百个音会在启动时白等好几秒（老写法就在这里渲染）。
        self.audio = None
        self.track = 0

    def audioParams(self):
        """送给合成器的 ``(音高, 力度 0~127, 时长秒)``。

        **预热缓存和真正播放都必须走这里**：两边算出来的缓存键要一模一样，
        否则预热出来的音一个都命中不了（等于白算）。
        """
        velocity = int(round(min(max(self.velocity, 0.0), 1.0) * 127))
        return self.pitch, velocity, self.duration / self.speed

    def play(self) -> None:
        """出声：优先走钢琴合成器（力度同时决定响度与亮度），拿不到就退回正弦。"""
        synth = Synthesizer.app_instance
        if synth is not None:
            pitch, velocity, seconds = self.audioParams()
            self.track = synth.play(pitch, velocity,
                                    duration=seconds, pedal=PIANO_PEDAL)
            if self.track:
                return
        Player.app_instance.play(self.audio)

    def update(self, dt, tps, play: bool = True) -> None:
        self.start -= dt * self.speed
        # 判定：音符左端碰到判定线（start == -2）的那一帧出声。帧是离散的，理想时刻
        # 只会落在两帧之间 —— 这里提前 PIANO_TRIGGER_LEAD_FRAMES 帧判定（默认半帧），
        # 把误差从"永远偏晚 0~半帧"变成"±半帧"（期望 0；dt 是真实帧时长 → 提前量跟着
        # 实际帧率走，本 demo 90fps 即 ±5.6ms）。
        # play=False：整曲混音模式已经在播整条 PCM 了，这里只走画面判定、不出声。
        if not self.played and self.start <= -2.0:
            self.played = True
            if play:
                self.play()

    def reset(self) -> None:
        self.stop()
        self.start = self.orig_start
        self.played = False

    def stop(self) -> None:
        if not self.track:
            return
        synth = Synthesizer.app_instance
        if synth is not None:
            synth.stop(self.track)
        self.track = 0

    def render(self, dt, fps, buffer: "BufferBuilder", offset, lines) -> None:
        pitch = self.pitch - offset
        start = self.start
        duration = self.duration
        x1 = start - 1
        x2 = (duration + start) - 1
        y1 = pitch * 2 / lines - 1
        y2 = y1 + 2 / lines
        x1 /= 3
        x2 /= 3
        if x2 < -1 or x1 > 1 + LONG:
            return
        a1 = 1
        a2 = 0
        r, g, b, _ = self.colors
        if x1 < -1:
            a1 = (x2+1)/(x2-x1)
            x1 = -1
            r *= 3
            g *= 3
            b *= 3

        self.color = (r, g, b)
        y1 *= HLS
        y2 *= HLS
        z = self.z
        buffer.pos(x1, y1, z).col(r, g, b, a1).end()
        buffer.pos(x1, y2, z).col(r, g, b, a1).end()
        buffer.pos(x2, y2, z).col(r, g, b, a2).end()
        buffer.pos(x1, y1, z).col(r, g, b, a1).end()
        buffer.pos(x2, y2, z).col(r, g, b, a2).end()
        buffer.pos(x2, y1, z).col(r, g, b, a2).end()
        if x1 > -1:
            return
        Particle(-1, random.uniform(y1, y2), self.z,
                 (r * 6.0, g * 6.0, b * 6.0))


class MidiNoteReader:
    def __init__(self, file_path: str, speed: float = 16, settings: Settings = None) -> None:
        self.speed = float(speed)
        self.mid = mido.MidiFile(file_path)
        self.settings = settings
        merged = mido.merge_tracks(self.mid.tracks)
        self.ticks_per_beat = self.mid.ticks_per_beat
        self.tempo = 50
        self.notes: list[Note] = []
        self.max_pitch = 0
        self.min_pitch = 127
        self.all_second = 0
        self._parseNotes()

    def _parseNotes(self) -> None:
        notes = []
        note_on_dict = {}
        current_second = 0.0
        seed = random.randint(0, 1000000)
        for msg in self.mid:
            d_second = msg.time
            current_second += d_second
            if msg.type == 'set_tempo':
                continue
            if msg.type == 'note_on' and msg.velocity > 0:
                note_on_dict[msg.note] = (current_second, msg.velocity)
            elif msg.type in ('note_off') or (msg.type == 'note_on' and msg.velocity == 0):
                if msg.note in note_on_dict:
                    start_second, velocity = note_on_dict.pop(msg.note)
                    duration_second = current_second - start_second
                    duration_second = max(duration_second, 0.05)
                    notes.append(
                        (msg.note, velocity, start_second, duration_second, seed)
                    )
                    self.max_pitch = max(self.max_pitch, msg.note)
                    self.min_pitch = min(self.min_pitch, msg.note)
                    self.all_second = max(self.all_second, current_second)
        self.notes = []
        for pitch, velocity, start, duration, seed in notes:
            self.notes.append(Note(pitch, velocity, start, duration, seed,
                                   self.speed, settings=self.settings))

    def getNotes(self):
        return self.notes

    def audioKeys(self):
        """曲子里用到的 ``(音高, 力度, 时长秒)``，**按播放顺序**给出（预热先热最早要弹的）。
        这里不做去重：合成器按量化后的缓存键去重（相邻档本来就会命中同一条），
        少算一遍才是它的事。返回的参数与 :meth:`Note.audioParams` 完全一致。
        """
        return [note.audioParams() for note in self.notes]


STATE_IDLE = "idle"
STATE_READING = "reading"
STATE_PRERENDER = "prerender"
STATE_AUDIO = "audio"
STATE_READY = "ready"
STATE_FAILED = "failed"

LOAD_BUDGET = 0.012
PAUSE_BUTTON_Z = 2.1
PRESET_TABLE = {
    "grand": PianoPreset.grand,
    "upright": PianoPreset.upright,
    "honkyTonk": PianoPreset.honkyTonk,
}


class WaterfallPage(GameObject):
    def __new__(cls, *args, settings=None, ** kwargs):
        return super().__new__(cls)

    def __init__(self, *args, settings=None, ** kwargs) -> None:
        super().__init__()
        self.name = "WaterfallPage"
        self.settings = settings if settings is not None else Settings.load()
        self.midi: "MidiNoteReader | None" = None
        self.midi_path = ""
        self.file_name = ""
        self.state = STATE_IDLE
        self.paused = False
        self.finished = False
        self.speed = 16.0
        self.brightness = 1.0
        self.song_time = 0.0
        self.audio_path = ""
        self.video_path = ""
        self._keys: list = []
        self._key_index = 0
        self._step_done = False
        self._audio_thread = None
        self._audio_progress = 0.0
        self._audio_error = ""
        self.video = None
        self.progress = ProgressBar(5.0, -0.15, 0.0, w=2.2, h=0.07)
        self.pause_title = Text("PAUSED", font=uiFont(), font_size=64, height=0.11,
                                x=0.0, y=0.72, z=PAUSE_BUTTON_Z, billboard=True,
                                align="center", anchor="center",
                                font_color=(236, 244, 255, 255))
        self.resume_button = TextButton(self, "RESUME", 0.0, 0.30, z=PAUSE_BUTTON_Z,
                                        action="resume", w=0.66, h=0.22,
                                        text_height=0.075, billboard=True)
        self.exit_button = TextButton(self, "EXIT", 0.0, -0.30, z=PAUSE_BUTTON_Z,
                                      action="exit", w=0.66, h=0.22,
                                      text_height=0.075, billboard=True)
        self.setActive(False)

    def start(self) -> None:
        glDisable(GL_CULL_FACE)
        glLineWidth(1)
        self.transparent = True
        self.render_buffer_notes = RenderBuffer()
        self.render_particles = RenderBuffer()
        self.render_buffer_line = RenderBuffer(GL_STATIC_DRAW)
        if self.midi_path and self.state == STATE_IDLE:
            self.loadFile(self.midi_path)
        self.setActive(self.active)

    def buildGrid(self) -> None:
        lines = self.midi.max_pitch - self.midi.min_pitch + 1
        if getattr(self, "render_buffer_line", None) is not None:
            self.render_buffer_line.release()
        self.render_buffer_line = RenderBuffer(GL_STATIC_DRAW)
        buffer_line = self.render_buffer_line.createBuffer(GL_LINES, POS | COL)
        strip = [2, 2, 2]
        buffer_line.pos(-1, +HLS, 0).col(*strip).end()
        buffer_line.pos(-1, -HLS, 0).col(*strip).end()
        for i in range(lines + 1):
            if i % 12 == 0:
                a = 0.5
            elif i % 4 == 0:
                a = 0.5
            else:
                continue
            buffer_line.pos(-5, (-1 + (2 / lines) * i) * HLS, 0).col(
                0.9, 0.9, 0.9, a).end()
            buffer_line.pos(+1 + LONG, (-1 + (2 / lines) * i) * HLS, 0).col(
                0.9, 0.9, 0.9, -0.1).end()
        self.render_buffer_line.build()
        self.render_buffer_line.buffer_builder.freeData()

    def applySettings(self, settings=None) -> None:
        if settings is not None:
            self.settings = settings
        if self.settings is not None:
            self.speed = max(1.0, float(self.settings.note_speed))
            self.brightness = max(0.0, float(self.settings.note_brightness))
        self.setActive(self.active)

    def loadFile(self, path: str, file_name: str = None) -> bool:
        path = os.path.abspath(str(path))
        if not os.path.isfile(path):
            print("MIDI not found: %s" % path)
            return False
        self.closeVideo()
        self.stopAllNotes()
        Particle.clear()
        self.midi_path = path
        self.file_name = file_name or os.path.basename(path)
        self.midi = None
        self._keys = []
        self._key_index = 0
        self._step_done = False
        self._audio_thread = None
        self._audio_progress = 0.0
        self._audio_error = ""
        self.song_time = 0.0
        self.paused = False
        self.finished = False
        self.state = STATE_READING
        self.progress.show("READING MIDI ...", 0.02)
        self.setActive(self.active)
        return True

    def updateLoad(self, dt: float) -> None:
        if self.state == STATE_READING:
            if self._step_done:
                self._step_done = False
                self.readMidi()
            else:
                self._step_done = True
                self.progress.show("READING MIDI ...", 0.02)
            return
        if self.state == STATE_PRERENDER:
            self.stepPrerender()
            return
        if self.state == STATE_AUDIO:
            self.stepAudio()

    def readMidi(self) -> None:
        began = time.perf_counter()
        try:
            self.midi = MidiNoteReader(
                self.midi_path, self.speed, settings=self.settings)
        except Exception as e:
            ConsoleMessage.printError("读取 MIDI 失败: %s" % e)
            self.fail("READ MIDI FAILED")
            return
        notes = self.midi.getNotes()
        if not notes:
            self.fail("NO NOTES IN THIS MIDI")
            return
        print("MIDI: %s  notes=%d  length=%.1fs"
              % (self.file_name, len(notes), self.midi.all_second))
        self._keys = []
        synth = Synthesizer.app_instance
        if synth is not None:
            factory = PRESET_TABLE.get(self.presetName(), PianoPreset.grand)
            synth.setPreset(factory())
            seen = set()
            for pitch, velocity, duration in self.midi.audioKeys():
                key = synth.cacheKey(pitch, velocity, duration, PIANO_PEDAL)
                if key in seen:
                    continue
                seen.add(key)
                self._keys.append((key[0], key[1], key[2]))
            synth.max_cache = max(synth.max_cache, len(self._keys) + 8)
        self.buildGrid()
        print("加载用时 %.2fs，去重音色 %d"
              % (time.perf_counter() - began, len(self._keys)))
        if not self._keys:
            self.enterAudioStage()
            return
        self.state = STATE_PRERENDER
        self._key_index = 0
        self.progress.show("PREPARING AUDIO ...", 0.12)

    def presetName(self) -> str:
        if self.settings is None:
            return "grand"
        return self.settings.piano_preset

    def outputPath(self, suffix: str) -> str:
        base = os.path.splitext(self.file_name)[0] or "midi"
        return os.path.join(os.getcwd(), "output.%s.%s" % (base, suffix))

    def stepPrerender(self) -> None:
        total = max(len(self._keys), 1)
        synth = Synthesizer.app_instance
        if synth is None:
            self._key_index = total
        else:
            deadline = time.perf_counter() + LOAD_BUDGET
            while self._key_index < total and time.perf_counter() < deadline:
                key = self._keys[self._key_index]
                try:
                    synth.prerenderNotes(
                        [key], pedal=PIANO_PEDAL, grow_cache=True)
                except Exception as e:
                    ConsoleMessage.printWarning("音频预热失败: %s" % e)
                    break
                self._key_index += 1
        ratio = float(self._key_index) / float(total)
        self.progress.show("PREPARING AUDIO ... (%3d/%3d)"
                           % (self._key_index, len(self._keys)),
                           0.12 + 0.68 * ratio)
        if self._key_index >= total:
            print("音频预热完成: %d 个音色" % len(self._keys))
            self.enterAudioStage()

    def enterAudioStage(self) -> None:
        if self.settings is not None and self.settings.save_audio:
            self.state = STATE_AUDIO
            self._audio_progress = 0.0
            self.startAudioExport()
            self.progress.show("EXPORTING AUDIO ...", 0.82)
            return
        self.enterReady()

    def startAudioExport(self) -> None:
        synth = Synthesizer.app_instance
        if synth is None or self.midi is None:
            return
        notes = [(note.song_time,) + note.audioParams()
                 for note in self.midi.getNotes()]
        notes.sort(key=lambda item: item[0])
        self.audio_path = self.outputPath("wav")
        self._audio_error = ""

        def progress(done, total):
            self._audio_progress = float(done) / float(max(int(total), 1))

        def worker():
            try:
                synth.exportSongWav(self.audio_path, notes, lead=0.5, tail=1.5,
                                    pedal=PIANO_PEDAL, progress=progress)
            except Exception as e:
                self._audio_error = str(e)
            self._audio_progress = 1.0

        self._audio_thread = threading.Thread(target=worker, daemon=True,
                                              name="MidiAudioExport")
        self._audio_thread.start()

    def stepAudio(self) -> None:
        thread = self._audio_thread
        if thread is not None and thread.is_alive():
            ratio = max(0.0, min(1.0, self._audio_progress))
            self.progress.show("EXPORTING AUDIO ...", 0.82 + 0.16 * ratio)
            return
        if self._audio_error:
            ConsoleMessage.printError("音频导出失败: %s" % self._audio_error)
        else:
            print("音频已保存: %s" % self.audio_path)
        self.enterReady()

    def renderLine(self, dt, fps) -> None:
        self.render_buffer_line.draw(re_build=False)

    def renderNotes(self, dt, fps) -> None:
        buffer_notes = self.render_buffer_notes.createBuffer(
            GL_TRIANGLES, POS | COL)
        lines = self.midi.max_pitch - self.midi.min_pitch + 1
        for note in self.midi.getNotes():
            note.render(dt, fps, buffer_notes,
                        self.midi.min_pitch, lines)
        self.render_buffer_notes.draw()

    def renderParticle(self, dt, fps) -> None:
        buffer_star = self.render_particles.createBuffer(
            GL_POINTS, POS | COL | SIZ)
        for p in Particle.alive_particles:
            p.render(dt, fps, buffer_star)
        self.render_particles.draw()

    def updateCamera(self) -> None:
        if self.state in (STATE_READING, STATE_PRERENDER, STATE_AUDIO):
            pages.useUiCamera()
        else:
            pages.useWaterfallCamera()

    def handleKeys(self) -> None:
        window = self.render_global.window
        if not window.getKeyDown("esc"):
            return
        if self.state == STATE_READY:
            self.togglePause()
        else:
            self.leave()

    def update(self, dt, tps) -> None:
        if not self.active:
            return
        super().update(dt, tps)
        self.updateCamera()
        pages.applyPendingCamera()
        self.handleKeys()
        if self.state in (STATE_READING, STATE_PRERENDER, STATE_AUDIO):
            self.updateLoad(dt)
            return
        if self.state != STATE_READY or self.midi is None:
            return
        if self.paused or self.finished:
            return
        Particle.updates(dt, tps)
        for note in self.midi.getNotes():
            was_played = note.played
            note.update(dt, tps, play=True)
        self.song_time += dt
        if self.video is not None:
            self.video.grab()
        notes = self.midi.getNotes()
        if (notes and self.song_time > self.midi.all_second + 60
                and notes[-1].played):
            self.finished = True
            self.closeVideo()
            print("FINISHED: %s（%.1fs）" % (self.file_name, self.song_time))

    def render(self, dt, fps):
        if not self.active:
            return
        if self.midi is not None and self.state in (STATE_READY):
            self.renderNotes(dt, fps)
            self.renderLine(dt, fps)
            self.renderParticle(dt, fps)
        return super().render(dt, fps)

    def enterReady(self) -> None:
        self.state = STATE_READY
        self.song_time = 0.0
        self.paused = False
        self.finished = False
        Particle.clear()
        if self.settings is not None and self.settings.save_video:
            self.startVideo()
        self.setActive(self.active)
        print("READY: %s" % self.file_name)

    def fail(self, message: str) -> None:
        self.state = STATE_FAILED
        self.progress.show(message, 1.0)
        self.setActive(self.active)
        print("LOAD FAILED: %s" % message)

    def startVideo(self) -> None:
        self.closeVideo()
        try:
            fps = self.settings.video_fps if self.settings is not None else 60
            self.video_path = self.outputPath("mp4")
            self.video = VideoRecorder(self.video_path, fps, pages.camera())
        except Exception as e:
            ConsoleMessage.printError("录制初始化失败: %s" % e)
            self.video = None

    def closeVideo(self) -> None:
        if self.video is not None:
            self.video.close()
            self.video = None

    def stopAllNotes(self) -> None:
        if self.midi is not None:
            for note in self.midi.getNotes():
                note.stop()
        synth = Synthesizer.app_instance
        if synth is not None:
            synth.stopAll()

    def replay(self) -> None:
        if self.midi is None:
            return
        for note in self.midi.getNotes():
            note.reset()
        Particle.clear()
        self.song_time = 0.0
        self.finished = False
        self.paused = False
        if self.settings is not None and self.settings.save_video:
            self.startVideo()
        self.setActive(self.active)
        print("REPLAY: %s" % self.file_name)

    def togglePause(self) -> None:
        if self.state != STATE_READY or self.midi is None:
            return
        self.paused = not self.paused
        if self.paused:
            self.stopAllNotes()
        else:
            for note in self.midi.getNotes():
                if note.played and not note.track:
                    note.play()
        self.setActive(self.active)

    def leave(self) -> None:
        if self.state in (STATE_READING, STATE_PRERENDER, STATE_AUDIO):
            self.state = STATE_IDLE
        self.stopAllNotes()
        self.closeVideo()
        target = pages.page("MidiListPage")
        if target is not None and hasattr(target, "refresh"):
            target.refresh()
        pages.switch("MidiListPage")

    def onAction(self, action: str, button) -> None:
        if action == "resume":
            self.togglePause()
        elif action == "exit":
            self.leave()

    def setActive(self, active: bool) -> None:
        super().setActive(active)
        busy = self.state in (STATE_READING, STATE_PRERENDER, STATE_AUDIO,
                              STATE_FAILED)
        self.progress.setActive(bool(active) and busy)
        paused = bool(active) and self.paused and self.state == STATE_READY
        self.pause_title.setActive(paused)
        self.resume_button.setActive(paused)
        self.exit_button.setActive(paused)
        if active and self.settings.save_video:
            fps = self.settings.video_fps if self.settings is not None else 60
            self.render_global.app.setAbsoluteFixedStep(fps)
        else:
            self.render_global.app.setAbsoluteFixedStep(-1)


class VideoRecorder:
    def __init__(self, path: str, fps: int = 60, camera=None) -> None:
        self.path = str(path)
        self.fps = max(1, int(fps))
        self.camera = camera
        self.grabber = None
        self.writer = None
        self.size = (0, 0)
        self.frames = 0
        self.failed = False

    def grab(self) -> None:
        if self.failed:
            return
        camera = self.camera or pages.camera()
        texture = getattr(camera, "bloomed_texture", None) if camera else None
        if texture is None:
            return
        size = (int(texture.w), int(texture.h))
        if self.grabber is None or size != self.size:
            self._makeGrabber(size)
        if self.grabber is not None:
            self.grabber.grab(texture.id)

    def _makeGrabber(self, size: tuple) -> None:
        from pyglgame.tools.AsyncFrameGrabber import AsyncFrameGrabber
        if self.grabber is not None:
            try:
                self.grabber.stop()
                self.grabber.release()
            except Exception:
                pass
            self.grabber = None
        try:
            self.grabber = AsyncFrameGrabber(
                size[0], size[1], self._onFrame,
                read_type=GL_UNSIGNED_BYTE, name="MidiVideoRecorder")
            self.size = size
            print("录制视频: %s (%dx%d @ %dfps)"
                  % (self.path, size[0], size[1], self.fps))
        except Exception as e:
            self.failed = True
            ConsoleMessage.printError("创建抓帧器失败: %s" % e)

    def _onFrame(self, frame, repeat: int = 1) -> None:
        try:
            import cv2
            import numpy as np
        except Exception as e:
            if not self.failed:
                self.failed = True
                ConsoleMessage.printError("缺少 cv2/numpy，无法录制视频: %s" % e)
            return
        img = np.asarray(frame)
        if img.dtype != np.uint8:
            img = np.clip(img * 255.0, 0, 255).astype(np.uint8)
        img = cv2.flip(img, 0)
        img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
        for _ in range(max(1, int(repeat))):
            self._write(cv2, img)

    def _write(self, cv2, img) -> None:
        height, width = img.shape[0], img.shape[1]
        if self.writer is None:
            for fourcc in ("avc1", "mp4v"):
                writer = cv2.VideoWriter(self.path, cv2.VideoWriter_fourcc(*fourcc),
                                         self.fps, (width, height))
                if writer.isOpened():
                    self.writer = writer
                    print("视频编码器: %s" % fourcc)
                    break
                writer.release()
            if self.writer is None:
                self.failed = True
                ConsoleMessage.printError("无法创建视频写入器: %s" % self.path)
                return
        self.writer.write(img)
        self.frames += 1

    def close(self) -> None:
        if self.grabber is not None:
            try:
                self.grabber.stop()
                self.grabber.release()
            except Exception:
                pass
            self.grabber = None
        if self.writer is not None:
            try:
                self.writer.release()
            except Exception:
                pass
            self.writer = None
        if self.frames:
            print("视频已保存: %s（%d 帧）" % (self.path, self.frames))
