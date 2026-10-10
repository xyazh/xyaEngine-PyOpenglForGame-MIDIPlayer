"""骨骼动画的**数据层**：关键帧 → 轨道 → 剪辑（纯 Python，不碰 GL）。

三层结构
-------

``Keyframe``
    一个时间点上的若干**通道**值（``x`` / ``y`` / ``rotation`` / ``scale_x`` /
    ``scale_y`` / ``alpha``）＋这一段的**缓动**。只写关心的通道，``None`` = "沿用上一个
    关键帧的值"（所以"只抬手不改腿"就是一行 ``setKey("arm", 0.4, rotation=-60)``）。

``BoneTrack``
    **一根骨**的整条时间线（关键帧按时间升序）。:meth:`BoneTrack.sample` 是**纯函数**：
    ``(骨骼名, 时间) → 一组通道值``，不写骨骼、不碰渲染 —— 单测直接对着它断言。

``AnimationClip``
    一堆轨道 + 时长 + 事件，就是"一个动作"（走路 / 挥手）。:meth:`AnimationClip.sample`
    一次采样**所有**受影响骨头，:meth:`AnimationClip.apply` 把结果写进 ``Skeleton``。

插值与缓动
--------

- 两个关键帧之间按**前一个**关键帧的 ``easing`` 插值（业界惯例）；
- 内置 ``linear`` / ``step``（hold）/ ``ease-in`` / ``ease-out`` / ``ease-in-out`` /
  ``smooth``（smoothstep）；也可以直接给一个 ``callable(t)``（入参 ``0~1``）；
- 旋转插值默认**按数值直线**（可预期、好测）；要"从 350° 转到 10° 走 20° 的近路"，
  打开 ``rotation_shortest=True``。

时间语义
-------

- 时间轴推进 / 循环折回由 :class:`~pyglgame.gameobject.animation.IAnimationBase.IAnimationBase`
  负责，本模块只回答"**t 时刻是什么姿态**"；所以 :meth:`BoneTrack.sample` 的
  ``loop`` 参数默认取 ``True``（把超出的时间折回，方便单独使用轨道）。
- 时长：``AnimationClip.duration`` 显式给了就用它，否则 = 所有轨道里最晚的关键帧时间。

数据来源
-------

:meth:`AnimationClip.fromDict` / :meth:`BoneTrack.fromDict` 能直接吃
AIGC / 编辑器导出的 JSON（``ResourceLocation.tryLoadJson()`` 的返回值），
不认识的多余键会被忽略。
"""

from .IAnimationBase import wrapTime
from .Skeleton import Bone

import math

#: 关键帧的通道顺序（``sample()`` 返回的元组按这个顺序）
CHANNELS = ("x", "y", "rotation", "scale_x", "scale_y", "alpha")

#: 通道默认值（第一个关键帧没写的通道从这里开始"沿用"）
CHANNEL_DEFAULTS = (0.0, 0.0, 0.0, 1.0, 1.0, 1.0)

#: 旋转通道在元组里的位置（``rotation_shortest`` 只对它生效）
ROTATION_CHANNEL = CHANNELS.index("rotation")


def _easeLinear(t: float) -> float:
    return t


def _easeStep(t: float) -> float:
    return 0.0


def _easeIn(t: float) -> float:
    return t * t


def _easeOut(t: float) -> float:
    return 1.0 - (1.0 - t) * (1.0 - t)


def _easeInOut(t: float) -> float:
    if t < 0.5:
        return 2.0 * t * t
    return 1.0 - 2.0 * (1.0 - t) * (1.0 - t)


def _easeSmooth(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


#: 内置缓动表（键名大小写 / 空格 / 下划线 / 连字符都不敏感：
#: ``Ease-InOut`` / ``ease_in_out`` / ``easeinout`` 都能用）
EASINGS = {
    "linear": _easeLinear,
    "step": _easeStep,
    "hold": _easeStep,
    "ease-in": _easeIn,
    "ease-in-out": _easeInOut,
    "ease-out": _easeOut,
    "smooth": _easeSmooth,
    "smoothstep": _easeSmooth,
}


def normalizeEasingName(easing) -> str:
    """缓动名归一化：去空格 / 下划线 / 连字符并小写（见 :data:`EASINGS`）。"""
    return str(easing).strip().lower().replace(" ", "") \
        .replace("_", "").replace("-", "")


_EASING_LOOKUP = {normalizeEasingName(name): func for name, func in EASINGS.items()}


def resolveEasing(easing):
    """缓动名 / 可调用对象 → ``callable(t)``（``0~1`` → ``0~1``）。"""
    if easing is None:
        return _easeLinear
    if callable(easing):
        return easing
    func = _EASING_LOOKUP.get(normalizeEasingName(easing))
    if func is None:
        raise ValueError("未知缓动 %r（可用：%s）"
                         % (easing, " / ".join(sorted(set(EASINGS)))))
    return func


class Keyframe:
    """一个时间点上的通道值 + 这一段的缓动（``None`` = 沿用上一个关键帧）。"""

    def __init__(self, time: float, x: float = None, y: float = None,
                 rotation: float = None, scale: float = None,
                 scale_x: float = None, scale_y: float = None,
                 alpha: float = None, easing="linear") -> None:
        self.time = float(time)
        if scale is not None:
            scale_x = scale_y = float(scale)
        self.x = None if x is None else float(x)
        self.y = None if y is None else float(y)
        self.rotation = None if rotation is None else float(rotation)
        self.scale_x = None if scale_x is None else float(scale_x)
        self.scale_y = None if scale_y is None else float(scale_y)
        self.alpha = None if alpha is None else float(alpha)
        #: 本关键帧 → 下一个关键帧之间的缓动（名字或 ``callable``）
        self.easing = easing

    def channel(self, name: str):
        """取某个通道的原始值（可能是 ``None``）。"""
        return getattr(self, name)

    def setChannels(self, **values) -> "Keyframe":
        """改通道值（只认 :data:`CHANNELS` 里的名字）。"""
        for name, value in values.items():
            if name not in CHANNELS:
                raise TypeError("未知通道 %r（可用：%s）"
                                % (name, " / ".join(CHANNELS)))
            setattr(self, name, None if value is None else float(value))
        return self

    def toDict(self) -> dict:
        """导出成 dict（只写有值的通道）。"""
        data = {"time": self.time}
        for name in CHANNELS:
            value = getattr(self, name)
            if value is not None:
                data[name] = value
        if not isinstance(self.easing, str) or self.easing != "linear":
            data["easing"] = self.easing if isinstance(self.easing, str) \
                else getattr(self.easing, "__name__", "custom")
        return data

    @classmethod
    def fromDict(cls, data: dict) -> "Keyframe":
        """从 dict 建关键帧（多余键忽略；``scale`` 是等比缩放简写）。"""
        data = dict(data)
        time = data.pop("time", data.pop("t", 0.0))
        easing = data.pop("easing", "linear")
        kwargs = {name: data[name] for name in CHANNELS if name in data}
        if "scale" in data:
            kwargs["scale"] = data["scale"]
        return cls(time, easing=easing, **kwargs)

    def __repr__(self) -> str:
        channels = ", ".join("%s=%g" % (name, getattr(self, name))
                             for name in CHANNELS
                             if getattr(self, name) is not None)
        return "<Keyframe t=%g %s 缓动=%s>" % (
            self.time, channels or "(沿用)", getattr(self.easing, "__name__",
                                                   self.easing))


class BoneTrack:
    """一根骨头的时间线：关键帧（升序）+ 插值采样（纯函数）。

    .. code-block:: python

        track = BoneTrack("arm_upper")
        track.add(0.0, rotation=-30.0, easing="smooth")
        track.add(0.4, rotation=-90.0)
        track.add(0.8, rotation=-30.0)
        print(track.sample(0.2))       # (0.0, 0.0, -60.0, 1.0, 1.0, 1.0)
    """

    def __init__(self, bone, keyframes=None, rotation_shortest: bool = False,
                 defaults=None) -> None:
        self.bone = bone.name if isinstance(bone, Bone) else str(bone)
        #: 关键帧（按 ``time`` 升序，同一时间点只留一个）
        self.keys: list = []
        #: 旋转走"最短弧"（350° → 10° 只转 20°）
        self.rotation_shortest = bool(rotation_shortest)
        #: 第一个关键帧之前（以及没写的通道）用的默认值
        self.defaults = tuple(CHANNEL_DEFAULTS) if defaults is None \
            else tuple(float(v) for v in defaults)
        self._cache = None
        for key in keyframes or ():
            self.addKeyframe(key)

    # === 关键帧 ===

    def add(self, time: float, easing="linear", **values) -> Keyframe:
        """在 ``time`` 加/换一个关键帧（同时间点直接替换）。"""
        return self.addKeyframe(Keyframe(time, easing=easing, **values))

    def addKeyframe(self, keyframe: Keyframe) -> Keyframe:
        """加一个 :class:`Keyframe` 对象（同时间点直接替换，返回留在轨道里的那个）。"""
        index = self.indexOf(keyframe.time)
        if index is not None:
            self.keys[index] = keyframe
        else:
            self.keys.append(keyframe)
            self.keys.sort(key=lambda key: key.time)
        self._cache = None
        return keyframe

    def setKey(self, time: float, easing="linear", **values) -> Keyframe:
        """``add`` 的别名（写起来更像"打关键帧"）。"""
        return self.add(time, easing=easing, **values)

    def indexOf(self, time: float):
        """时间点对应的关键帧下标（``1e-6`` 容差；没有返回 ``None``）。"""
        for index, key in enumerate(self.keys):
            if abs(key.time - float(time)) <= 1e-6:
                return index
        return None

    def keyAt(self, time: float):
        """时间点上的关键帧（没有返回 ``None``）。"""
        index = self.indexOf(time)
        return None if index is None else self.keys[index]

    def remove(self, time: float) -> bool:
        """删掉某个时间点的关键帧，返回是否真的删了。"""
        index = self.indexOf(time)
        if index is None:
            return False
        del self.keys[index]
        self._cache = None
        return True

    def clear(self) -> "BoneTrack":
        """清空关键帧。"""
        self.keys = []
        self._cache = None
        return self

    @property
    def duration(self) -> float:
        """最后一个关键帧的时间（空轨道 = 0）。"""
        return self.keys[-1].time if self.keys else 0.0

    @property
    def keyframeCount(self) -> int:
        return len(self.keys)

    def channels(self) -> set:
        """这条轨道**碰过的**通道名集合（"只动 rotation"就是 ``{"rotation"}``）。

        :meth:`AnimationClip.sample` 用它决定"哪些通道该用骨骼的静止姿态兜底"。
        """
        used = set()
        for key in self.keys:
            for name in CHANNELS:
                if getattr(key, name) is not None:
                    used.add(name)
        return used

    # === 采样 ===

    def resolvedKeys(self) -> list:
        """把 ``None`` 通道"沿用上一个关键帧"解析成完整值（带缓存）。

        返回 ``[(time, 缓动函数, 六通道元组), …]``；关键帧一变缓存自动作废。
        """
        if self._cache is not None:
            return self._cache
        resolved = []
        current = list(self.defaults)
        for key in self.keys:
            for index, name in enumerate(CHANNELS):
                value = getattr(key, name)
                if value is not None:
                    current[index] = value
            resolved.append((key.time, resolveEasing(key.easing),
                             tuple(current)))
        self._cache = resolved
        return resolved

    def sample(self, time: float, loop: bool = True) -> tuple:
        """``time`` 时刻的 ``(x, y, rotation, scale_x, scale_y, alpha)``。

        ``loop=True`` 时先把时间折回 ``[0, duration)``（单独用一条轨道也顺手）；
        空轨道返回 :attr:`defaults`。
        """
        keys = self.resolvedKeys()
        if not keys:
            return self.defaults
        duration = keys[-1][0]
        t = float(time)
        if loop and duration > 0.0:
            t = wrapTime(t, duration)
        if t <= keys[0][0]:
            return keys[0][2]
        if t >= duration:
            return keys[-1][2]
        for index in range(len(keys) - 1):
            time0, easing, values0 = keys[index]
            time1, _, values1 = keys[index + 1]
            if time0 <= t <= time1:
                span = time1 - time0
                if span <= 1e-12:
                    return values1
                return self._blend(values0, values1,
                                   float(easing((t - time0) / span)))
        return keys[-1][2]

    def _blend(self, values0: tuple, values1: tuple, ratio: float) -> tuple:
        """按 ``ratio`` 在两组通道值之间插值（旋转可走最短弧）。"""
        result = []
        for index, (start, end) in enumerate(zip(values0, values1)):
            if index == ROTATION_CHANNEL and self.rotation_shortest:
                # 把差值折到 (-180, 180]：350° → 10° 就走 +20° 的近路
                delta = math.fmod(end - start + 180.0, 360.0)
                if delta < 0.0:
                    delta += 360.0
                result.append(start + (delta - 180.0) * ratio)
            else:
                result.append(start + (end - start) * ratio)
        return tuple(result)

    def apply(self, skeleton, time: float, loop: bool = True) -> tuple:
        """采样并写进骨架（**不** ``solve``），返回写进去的那组通道值。"""
        values = self.sample(time, loop=loop)
        skeleton.apply({self.bone: values})
        return values

    # === 数据 ===

    def toDict(self) -> dict:
        return {"bone": self.bone,
                "rotation_shortest": self.rotation_shortest,
                "keys": [key.toDict() for key in self.keys]}

    @classmethod
    def fromDict(cls, data) -> "BoneTrack":
        """从 dict（或裸的关键帧列表）建轨道；``bone`` 名字必填。"""
        if isinstance(data, list):
            data = {"keys": data}
        data = dict(data)
        bone = data.pop("bone", data.pop("name", None))
        if bone is None:
            raise ValueError("轨道缺 'bone' 名字：%r" % (data,))
        keys = data.get("keys", data.get("keyframes", [])) or []
        return cls(bone,
                   keyframes=[Keyframe.fromDict(key) for key in keys],
                   rotation_shortest=data.get("rotation_shortest", False),
                   defaults=data.get("defaults"))

    # === 便利 ===

    def __len__(self) -> int:
        return len(self.keys)

    def __iter__(self):
        return iter(self.keys)

    def __repr__(self) -> str:
        return "<BoneTrack %r %d 关键帧 时长=%g%s>" % (
            self.bone, len(self.keys), self.duration,
            " 最短弧" if self.rotation_shortest else "")


class AnimationClip:
    """一个动作（走路 / 挥手）：一堆轨道 + 时长 + 事件（见模块文档）。

    .. code-block:: python

        clip = AnimationClip("walk", duration=1.0, loop=True)
        clip.setKey("torso", 0.0, rotation=4.0, y=0.60)
        clip.setKey("torso", 0.5, rotation=-4.0, y=0.56, easing="smooth")
        clip.setKey("torso", 1.0, rotation=4.0, y=0.60)

        pose = clip.sample(0.25)            # {骨头名: 六通道元组}，纯采样
        clip.apply(skeleton, 0.25, solve=True)     # 写进骨架并解算
    """

    def __init__(self, name: str = "", duration: float = None,
                 loop: bool = True, rotation_shortest: bool = False,
                 tracks=None, events=None) -> None:
        """``duration=None`` 时取"所有轨道里最晚的关键帧时间"。"""
        self.name = str(name)
        self.loop = bool(loop)
        #: 新建的轨道是否走"最短弧"旋转插值（轨道自己也能单独覆盖）
        self.rotation_shortest = bool(rotation_shortest)
        self._duration = None if duration is None else float(duration)
        #: 骨头名 → :class:`BoneTrack`（插入顺序 = 采样顺序，方便复现）
        self.tracks: dict = {}
        #: ``[(time, callback), …]``（按时间升序；脚步音效 / 攻击判定挂这里）
        self.events: list = []
        for track in tracks or ():
            self.addTrack(track)
        for event in events or ():
            self.addEvent(*event)

    # === 轨道 ===

    def addTrack(self, track) -> BoneTrack:
        """加一条轨道（``BoneTrack`` 对象，或骨头名字 → 按剪辑的设置建一条空轨道）。"""
        if not isinstance(track, BoneTrack):
            track = BoneTrack(track, rotation_shortest=self.rotation_shortest)
        else:
            # 轨道自己显式打开过就别关掉
            track.rotation_shortest = track.rotation_shortest \
                or self.rotation_shortest
        self.tracks[track.bone] = track
        return track

    def track(self, bone, create: bool = True) -> BoneTrack:
        """取一条轨道；``create=True`` 时不存在就建。"""
        name = bone.name if isinstance(bone, Bone) else str(bone)
        track = self.tracks.get(name)
        if track is None and create:
            track = self.addTrack(name)
        return track

    def removeTrack(self, bone) -> bool:
        """删掉一条轨道，返回是否真的删了。"""
        name = bone.name if isinstance(bone, Bone) else str(bone)
        if name in self.tracks:
            del self.tracks[name]
            return True
        return False

    def setKey(self, bone, time: float, easing="linear", **values) -> Keyframe:
        """给某根骨在 ``time`` 打一个关键帧（轨道不存在就自动建）。

        ::

            clip.setKey("arm_upper", 0.4, rotation=-60.0, easing="smooth")
            clip.setKey("arm_upper", 0.6, alpha=0.5)      # 只淡出这条骨
        """
        return self.track(bone).add(time, easing=easing, **values)

    def keyAt(self, bone, time: float):
        """某根骨在 ``time`` 上的关键帧（没有返回 ``None``）。"""
        track = self.track(bone, create=False)
        return None if track is None else track.keyAt(time)

    @property
    def duration(self) -> float:
        """剪辑时长（秒）：显式值优先，否则取最晚的关键帧时间。"""
        if self._duration is not None:
            return self._duration
        return max([track.duration for track in self.tracks.values()] or [0.0])

    @duration.setter
    def duration(self, value) -> None:
        self._duration = None if value is None else float(value)

    def setDuration(self, duration: float) -> "AnimationClip":
        """显式设定长度（例如"动作播 2 秒"，即使最后一个关键帧在 1.6 秒）。"""
        self._duration = None if duration is None else float(duration)
        return self

    # === 采样 ===

    def sample(self, time: float, loop: bool = None, skeleton=None) -> dict:
        """``time`` 时刻的 ``{骨头名: (x, y, rotation, scale_x, scale_y, alpha)}``。

        **纯函数**：不写骨骼、不碰渲染 —— 想预览 / 测试姿态直接看返回值。
        ``loop=None`` 时用剪辑自己的 :attr:`loop`。

        ``skeleton`` 给了的话，会把"这条轨道**从没碰过**的通道"换成那根骨的
        **静止姿态**（``Bone.default``）—— 这样"只动 ``rotation``"的剪辑**不会**
        把骨头在 setup pose 里摆好的 ``x`` / ``y`` 清零（和主流 2D 骨骼工具一致）。
        不传 ``skeleton`` 时这些通道留在轨道默认值（``0/0/0/1/1/1``），采样结果与
        骨骼无关、更好断言。
        """
        if loop is None:
            loop = self.loop
        pose = {name: track.sample(time, loop=loop)
                for name, track in self.tracks.items()}
        if skeleton is None:
            return pose
        for name, values in pose.items():
            bone = skeleton.findBone(name)
            if bone is None:
                continue
            rest = bone.default
            used = self.tracks[name].channels()
            merged = list(values)
            for index, channel in enumerate(CHANNELS):
                if channel not in used and index < len(rest):
                    merged[index] = rest[index]
            pose[name] = tuple(merged)
        return pose

    def apply(self, skeleton, time: float, loop: bool = None,
              solve: bool = False) -> dict:
        """采样后写进骨架（``solve=True`` 顺手解算世界变换），返回采样结果。"""
        pose = self.sample(time, loop=loop, skeleton=skeleton)
        skeleton.apply(pose, solve=solve)
        return pose

    def boneNames(self) -> list:
        """受本剪辑影响的骨头名（按轨道顺序）。"""
        return list(self.tracks)

    @property
    def keyframeCount(self) -> int:
        return sum(len(track) for track in self.tracks.values())

    # === 事件（脚步音效 / 攻击判定这类"某一刻发生一次"的东西）===

    def addEvent(self, time: float, callback) -> "AnimationClip":
        """在 ``time`` 挂一个回调（按时间插到合适位置）。"""
        if not callable(callback):
            raise TypeError("事件回调必须可调用，收到 %r" % (callback,))
        self.events.append((float(time), callback))
        self.events.sort(key=lambda event: event[0])
        return self

    def removeEvent(self, callback) -> bool:
        """按回调对象删事件，返回是否删掉了。"""
        before = len(self.events)
        self.events = [event for event in self.events if event[1] is not callback]
        return len(self.events) != before

    def eventsBetween(self, start: float, end: float, looped: bool = False) -> list:
        """``(start, end]`` 区间内要触发的回调（**不含** start、**含** end，不会重复触发）。

        ``looped=True``（或 ``end < start``）表示这一帧跨过了循环边界：
        先给 ``(start, duration]`` 里的，再给 ``[0, end]`` 里的 —— 顺序仍是时间序。
        高倍速一帧跨过多个循环时只按"折回一次"处理（事件不重复刷屏）。
        """
        if not self.events or self.duration <= 0.0:
            return []
        if not looped and end >= start:
            return [callback for time, callback in self.events
                    if start < time <= end]
        return [callback for time, callback in self.events if time > start] \
            + [callback for time, callback in self.events if time <= end]

    # === 数据（AIGC / 编辑器导出的 JSON 直接能读）===

    def toDict(self) -> dict:
        """导出成可写进 JSON 的 dict（事件只导出名字，回调不可序列化）。"""
        return {"name": self.name,
                "duration": self.duration,
                "loop": self.loop,
                "rotation_shortest": self.rotation_shortest,
                "tracks": [track.toDict() for track in self.tracks.values()]}

    @classmethod
    def fromDict(cls, data: dict) -> "AnimationClip":
        """从 dict 建剪辑；``tracks`` 接受列表（``{bone, keys}``）或"名字 → 轨道"字典。"""
        data = dict(data or {})
        data.pop("events", None)          # 回调没法从 JSON 里来
        tracks = data.pop("tracks", data.pop("bones", [])) or []
        if isinstance(tracks, dict):
            entries = []
            for name, values in tracks.items():
                entry = dict(values or {})
                entry.setdefault("bone", name)
                entries.append(entry)
            tracks = entries
        return cls(tracks=[BoneTrack.fromDict(entry) for entry in tracks],
                   **{key: data[key] for key in
                      ("name", "duration", "loop", "rotation_shortest")
                      if key in data})

    # === 便利 ===

    def __len__(self) -> int:
        return len(self.tracks)

    def __contains__(self, bone) -> bool:
        name = bone.name if isinstance(bone, Bone) else str(bone)
        return name in self.tracks

    def __iter__(self):
        return iter(self.tracks.values())

    def __repr__(self) -> str:
        return "<AnimationClip %r %.3fs %d 轨道 %d 关键帧 %s>" % (
            self.name, self.duration, len(self.tracks), self.keyframeCount,
            "循环" if self.loop else "单次")
