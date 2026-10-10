"""帧序列（数据层）：一串**帧名** + 帧率 + 循环 + 方向。

它和 :class:`~pyglgame.gameobject.animation.AnimationClip.AnimationClip`（骨骼关键帧）
是同一层的东西：**可序列化的纯数据**，不叫 ``I...``（不是给别的类组合用的接口）。

为什么不是"帧号"而是"帧名"
--------------------------

帧号是"均匀网格"的思维（``IAnimationFrame`` 的 ``rows × columns``）。图集里的帧是
**打包器给的名字**（``hero 0.3.ase`` / ``idle_0``），还可能被 trim 过 —— 按名字引用
才不会因为"打包顺序变了"就播错帧。:class:`~pyglgame.render.TextureAtlas.TextureAtlas`
负责把名字翻成 UV。

``direction`` 的处理
--------------------

``forward`` / ``reverse`` / ``pingpong`` 在 :meth:`FrameSequence.resolvedFrames` 里**展开**
成真正要逐帧播放的列表（``pingpong`` = ``a b c b``，端点不重复）—— 引擎的帧动画只有
"正向 + 水平翻转"，所以方向必须在数据里烘掉，而不是留到绘制时。

::

    seq = FrameSequence(name="walk", frames=["w0", "w1", "w2"], fps=8.0)
    seq.duration            # 0.375
    seq.sample(0.13)        # 'w1'
    seq.sample(0.13, loop=False)
"""

from .IAnimationBase import wrapTime


class FrameSequence:
    """帧序列（见模块文档）。构造与取值都是纯数据操作，**不碰 GL**。"""

    #: 认识的播放方向
    DIRECTIONS = ("forward", "reverse", "pingpong")

    def __init__(self, name: str = "", frames=(), fps: float = 10.0,
                 loop: bool = True, direction: str = "forward") -> None:
        self.name = str(name or "")
        #: 帧名列表（**原样**保存，方向不烘进来 —— ``resolvedFrames()`` 才展开）
        self.frames = [str(item) for item in (frames or ())]
        try:
            fps = float(fps)
        except (TypeError, ValueError):
            fps = 10.0
        #: 帧率（每秒多少帧；≤0 时退回 10.0，避免除零）
        self.fps = fps if fps > 0.0 else 10.0
        self.loop = bool(loop)
        direction = str(direction or "forward").lower()
        self.direction = direction if direction in self.DIRECTIONS else "forward"

    # === 读数 ===

    def __len__(self) -> int:
        return len(self.frames)

    def __iter__(self):
        return iter(self.frames)

    def __contains__(self, name) -> bool:
        return str(name) in self.frames

    @property
    def frameCount(self) -> int:
        return len(self.frames)

    @property
    def frameTime(self) -> float:
        """单帧时长（秒）。"""
        return 1.0 / self.fps

    @property
    def duration(self) -> float:
        """整段时长（秒）。"""
        return len(self.frames) / self.fps

    def resolvedFrames(self) -> list:
        """按 ``direction`` 展开后的帧序（真正逐帧播的那个列表）。"""
        names = list(self.frames)
        if self.direction == "reverse":
            names.reverse()
        elif self.direction == "pingpong" and len(names) > 2:
            names = names + names[-2:0:-1]
        return names

    def frameAt(self, index: int) -> str:
        """按展开后的索引取帧名（取模，负数也认）。"""
        names = self.resolvedFrames()
        if not names:
            raise IndexError("帧序列 %r 里没有任何帧" % (self.name,))
        return names[int(index) % len(names)]

    def indexAt(self, time: float, loop: bool = None) -> int:
        """时间（秒）→ 展开后的帧索引。

        :param loop: ``None`` = 用本序列的 ``loop``；``False`` = 夹到最后一帧
            （播完停住的用法，与 ``IAnimationBase`` 的单次模式一致）。
        """
        names = self.resolvedFrames()
        if not names:
            return 0
        loop = self.loop if loop is None else bool(loop)
        if loop:
            wrapped = wrapTime(time, self.duration)
        else:
            wrapped = min(max(float(time), 0.0), max(self.duration - 1e-9, 0.0))
        index = int(wrapped * self.fps)
        return max(0, min(index, len(names) - 1))

    def sample(self, time: float, loop: bool = None) -> str:
        """时间（秒）→ 帧名（空序列时返回 ``None``）。"""
        names = self.resolvedFrames()
        if not names:
            return None
        return names[self.indexAt(time, loop=loop)]

    def withFps(self, fps: float) -> "FrameSequence":
        """换帧率（返回**新对象**，不改自己 —— 数据层对象尽量当不可变用）。"""
        return FrameSequence(self.name, self.frames, fps=fps, loop=self.loop,
                             direction=self.direction)

    def reversed(self) -> "FrameSequence":
        """反序（返回新对象）。"""
        return FrameSequence(self.name, list(reversed(self.frames)), fps=self.fps,
                             loop=self.loop, direction="forward")

    # === 序列化 ===

    def toDict(self) -> dict:
        """导出成可写进 JSON 的 dict。"""
        return {"name": self.name, "frames": list(self.frames), "fps": self.fps,
                "loop": self.loop, "direction": self.direction}

    @classmethod
    def fromDict(cls, data) -> "FrameSequence":
        """从 dict 建序列；也接受**裸的帧名列表**。

        多余键忽略（``fps`` / ``loop`` / ``direction`` 缺就用默认），
        所以引擎之外的工具（AIGC / 手写 JSON）多写几个字段也不会炸。
        """
        if isinstance(data, (list, tuple)):
            return cls(name="", frames=data)
        if not isinstance(data, dict):
            raise ValueError("帧序列必须是 dict 或帧名列表，收到 %r" % (type(data).__name__,))
        data = dict(data)
        return cls(name=data.get("name", ""),
                   frames=data.get("frames") or data.get("sequence") or (),
                   fps=data.get("fps", 10.0),
                   loop=bool(data.get("loop", True)),
                   direction=data.get("direction", "forward"))

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        return ("<FrameSequence%s %d 帧 %.3g fps 时长 %.3fs %s%s>"
                % (label, len(self.frames), self.fps, self.duration,
                   "循环" if self.loop else "单次",
                   "" if self.direction == "forward" else " " + self.direction))
