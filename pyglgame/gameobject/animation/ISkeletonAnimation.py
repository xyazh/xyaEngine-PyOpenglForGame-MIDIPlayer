"""骨骼动画（接口）：``Skeleton`` + ``AnimationClip`` + ``BoneAttachment`` → 会动的人。

它把三块拼起来，每帧做三件事：

1. **采样**：按当前时间从剪辑里取姿态（``AnimationClip.sample``）；
2. **解算**：把姿态写进骨架并算出世界变换（``Skeleton.solve``，带上宿主的
   ``x`` / ``y`` / ``r`` 作为"伪父变换"，所以整具骨架会跟着宿主走）；
3. **绘制**：每个挂点用骨的世界变换摆好四边形，**同贴图的挂点合成一次 draw call**
   （顶点是世界坐标 → 画之前把模型矩阵复位成单位阵）。

用法：当世界里的对象（接口写在左边）
----------------------------------

.. code-block:: python

    from pyglgame.gameobject.Sprite2D import Sprite2D
    from pyglgame.gameobject.animation import (AnimationClip, BoneAttachment,
                                               ISkeletonAnimation, Skeleton)

    skeleton = Skeleton.fromDict(rig_json["skeleton"])
    walk = AnimationClip.fromDict(rig_json["animations"]["walk"])

    class Hero(ISkeletonAnimation, Sprite2D):
        def __init__(self, x=0.0, y=0.0):
            # ① 接口参数 ② 宿主（Sprite2D）—— 顺序由 super() 链保证
            super().__init__(skeleton=skeleton, clips={"walk": walk},
                             animation="walk", x=x, y=y)
            self.rebuild()          # 精灵侧几何（这里没贴图，套一层也不影响）

    hero = Hero(x=0.0, y=-1.0)
    hero.playClip("walk")           # 切动作（循环由剪辑决定）

用法：当对象的零件
-----------------

::

    anim = ISkeletonAnimation(skeleton=skeleton, clips={"walk": walk})
    anim.addAttachment(BoneAttachment("torso", texture=body_tex, width=1.2,
                                      height=1.6))
    anim.playClip("walk")
    ...
    anim.update(dt, tps)
    anim.render(dt, fps)

谁排前面
-------

挂点按 ``self.attachments`` 的**顺序**绘制（前面的在下面），同贴图的相邻挂点会被合批；
所以"后画的部件"往后加就行（和 2D 骨骼工具里的"层顺序"一个意思）。
不做深度排序 —— 2D 骨骼的遮挡关系是**作者指定**的，交给 z / 顺序更可控。
"""

import glm
from OpenGL.GL import GL_TEXTURE0, GL_TRIANGLES

from ...render.BufferBuilder import COL, POS, TEX
from ...render.RenderBuffer import RenderBuffer
from ...render.Texture import Texture
from ...RenderGlobal import RenderGlobal
from .AnimationClip import AnimationClip
from .BoneAttachment import BoneAttachment
from .IAnimationBase import IAnimationBase
from .Skeleton import Bone, Skeleton

#: 挂点顶点格式（与 :data:`~pyglgame.gameobject.animation.BoneAttachment.VERTEX_FLAGS` 一致）
VERTEX_FLAGS = POS | COL | TEX


def _asSkeleton(skeleton) -> Skeleton:
    """``None`` / ``Skeleton`` / ``dict`` / ``Bone 列表`` → :class:`Skeleton`。"""
    if skeleton is None:
        return Skeleton()
    if isinstance(skeleton, Skeleton):
        return skeleton
    if isinstance(skeleton, dict):
        return Skeleton.fromDict(skeleton)
    return Skeleton(bones=skeleton)


class ISkeletonAnimation(IAnimationBase):
    """骨骼动画（见模块文档）。"""

    def __init__(self, skeleton=None, clips=None, animation: str = None,
                 attachments=None, speed: float = 1.0, loop: bool = None,
                 autoplay: bool = True, rig_scale=(1.0, 1.0),
                 blend_mode: str = None, name: str = None, **kwargs) -> None:
        """
        :param skeleton: :class:`Skeleton` / 骨头的 dict / :class:`Bone` 列表
        :param clips: 剪辑表：``{名字: AnimationClip}``，或 ``AnimationClip`` 列表
                      （列表时用剪辑自己的 ``name``）
        :param animation: 建好就播哪个剪辑（``None`` = 不播，保持静止姿态）
        :param attachments: 初始挂点列表（也可以之后 :meth:`addAttachment`）
        :param speed: 播放速度倍数
        :param loop: 是否循环（``None`` = 跟随剪辑自己的 ``loop``）
        :param autoplay: ``animation`` 给了之后是否立刻开始播（``False`` = 先摆好姿势）
        :param rig_scale: 整具骨架的缩放（``x`` / ``y`` 来自宿主）
        :param blend_mode: 批次混合模式（``None`` = 跟随当前 pass；发光部件可以给 ``"add"``）
        :param name: 调试用名字
        :param kwargs: **其余关键字参数原样透传给宿主**，例如
                       ``super().__init__(skeleton=rig, x=-1.0, width=3.0)`` ——
                       ``x`` / ``width`` / ``transparent`` 都会落到宿主（``Sprite2D``…）
        """
        super().__init__(**kwargs)
        self.name = name
        self.skeleton = _asSkeleton(skeleton)
        self.clips: dict = {}
        #: 挂点列表（**顺序 = 绘制顺序**）
        self.attachments: list = list(attachments or ())
        #: 批次混合模式（见 ``pyglgame.render.RenderState``）
        self.blend_mode = blend_mode
        #: 当前剪辑名（``None`` = 不采样剪辑，只显示手动姿态）
        self.animation = None
        #: 整具骨架的缩放（根变换的一部分，配合宿主的 x / y / r）
        self.rig_scale = (float(rig_scale[0]), float(rig_scale[1]))
        #: 骨头名 → 本帧的 alpha（来自剪辑的 ``alpha`` 通道；挂点会乘上它）
        self.bone_alpha: dict = {}
        #: 每个贴图一个 :class:`RenderBuffer`（同贴图挂点合批）
        self.texture_buffers: dict = {}
        #: 上一帧画了多少顶点 / 多少次 draw call（调试 / 测试用）
        self.draw_vertices = 0
        self.draw_batches = 0
        self._event_time = None
        self._event_loops = 0
        if clips:
            if isinstance(clips, dict):
                for clip_name, clip in clips.items():
                    self.addClip(clip, clip_name)
            else:
                for clip in clips:
                    self.addClip(clip)
        if loop is not None:
            self.animation_loop = bool(loop)
        self.animation_speed = float(speed)
        if animation is not None:
            self.playClip(animation, restart=True)
            self.playing = bool(autoplay)
        else:
            # 没有剪辑：先把静止姿态解算一次，免得画出来是"没解算过的骨头"
            self.playing = False
            self.solvePose()

    # === 剪辑 ===

    def addClip(self, clip, name: str = None) -> AnimationClip:
        """加一个剪辑（``AnimationClip`` 或它的 dict）；名字默认取剪辑自己的。"""
        if isinstance(clip, dict):
            clip = AnimationClip.fromDict(clip)
        if not isinstance(clip, AnimationClip):
            raise TypeError("clips 里只能是 AnimationClip 或 dict，收到 %r"
                            % (clip,))
        clip_name = name if name is not None else clip.name
        if not clip_name:
            raise ValueError("剪辑必须有名字（Clip.name 或 addClip(clip, 名字)）")
        self.clips[clip_name] = clip
        return clip

    def getClip(self, name: str = None) -> AnimationClip:
        """按名字取剪辑（``None`` = 当前剪辑；取不到返回 ``None``）。"""
        return self.clips.get(self.animation if name is None else name)

    def removeClip(self, name: str) -> bool:
        """删一个剪辑（正在播的那个会被停掉）。"""
        if self.animation == name:
            self.animation = None
        return self.clips.pop(name, None) is not None

    @property
    def current_clip(self) -> AnimationClip:
        """当前正在播 / 采样的剪辑对象（没有就是 ``None``）。"""
        return self.clips.get(self.animation)

    def playClip(self, name: str, restart: bool = True, loop: bool = None,
                 speed: float = None) -> "ISkeletonAnimation":
        """切到某个剪辑（``restart=True`` 从 0 开始）。

        **会同时把时间轴长度换成剪辑的长度**；``loop=None`` 时用剪辑自己的
        :attr:`AnimationClip.loop`。相当于"这个动作从头（或从当前进度）开始演"。
        """
        if name not in self.clips:
            raise KeyError("没有剪辑 %r（可用：%s）"
                           % (name, ", ".join(self.clips) or "空"))
        clip = self.clips[name]
        changed = self.animation != name
        self.animation = name
        self.duration = clip.duration
        self.animation_loop = clip.loop if loop is None else bool(loop)
        if speed is not None:
            self.animation_speed = float(speed)
        if restart or changed:
            self.animation_time = 0.0
            self.animation_loops = 0
            self.finished = False
            self._event_time = None
        self.playing = True
        self.refreshAnimation()
        return self

    # === 骨架 / 挂点 ===

    def setSkeleton(self, skeleton) -> "ISkeletonAnimation":
        """换整具骨架（之后重新解算一次）。"""
        self.skeleton = _asSkeleton(skeleton)
        self.bone_alpha = {}
        self.solvePose()
        return self

    def addAttachment(self, attachment, **kwargs) -> BoneAttachment:
        """加挂点（只给骨头名字 + 参数也行，会自动建 :class:`BoneAttachment`）。"""
        if not isinstance(attachment, BoneAttachment):
            attachment = BoneAttachment(attachment, **kwargs)
        self.attachments.append(attachment)
        return attachment

    def getAttachment(self, name: str):
        """按名字取挂点（取不到返回 ``None``）。"""
        for attachment in self.attachments:
            if attachment.name == name:
                return attachment
        return None

    def removeAttachment(self, target) -> bool:
        """按名字或对象删挂点，返回是否删掉了。"""
        if isinstance(target, str):
            target = self.getAttachment(target)
        if target is None or target not in self.attachments:
            return False
        self.attachments.remove(target)
        return True

    def setAttachmentVisible(self, name: str, visible: bool = True) -> bool:
        """按名字开关一个挂点（"换武器 / 摘帽子"用）。"""
        attachment = self.getAttachment(name)
        if attachment is None:
            return False
        attachment.setVisible(visible)
        return True

    def setRigScale(self, scale_x: float,
                    scale_y: float = None) -> "ISkeletonAnimation":
        """整具骨架的缩放（``scale_y`` 不传 = 等比）。"""
        self.rig_scale = (float(scale_x),
                          float(scale_x) if scale_y is None else float(scale_y))
        return self.solvePose()

    def rootTransform(self) -> tuple:
        """整具骨架的"伪父变换"：宿主的 ``x`` / ``y`` / ``r`` + :attr:`rig_scale`。"""
        return (self.x, self.y, self.r, self.rig_scale[0], self.rig_scale[1])

    def setBone(self, name, **values) -> Bone:
        """手动摆一根骨（``x`` / ``y`` / ``rotation`` / ``scale`` …），并立刻解算。

        ⚠ 正在播剪辑时，下一帧会被剪辑采样覆盖 —— 想手动摆就别播（``stop()``），
        或者把这段手动动作也做成剪辑。
        """
        bone = self.skeleton.setBone(name, **values)
        self.solvePose()
        return bone

    def solvePose(self) -> "ISkeletonAnimation":
        """按当前局部姿态解算世界变换（带上宿主的 ``x`` / ``y`` / ``r``）。"""
        self.skeleton.solve(self.rootTransform())
        return self

    # === 每帧姿态 ===

    def updateAnimation(self, dt: float) -> None:
        """采样当前剪辑 → 写进骨骼 → 解算 → 触发这一帧跨过的事件。

        没有剪辑（``animation is None``）时只解算一次世界变换，所以手动
        :meth:`setBone` 摆的姿态会一直保持住。
        """
        clip = self.current_clip
        alpha = {}
        if clip is not None:
            # 传 skeleton：轨道没碰过的通道用"静止姿态"兜底（见 AnimationClip.sample）
            pose = clip.sample(self.animation_time, loop=self.animation_loop,
                               skeleton=self.skeleton)
            for name, values in pose.items():
                alpha[name] = values[5]
            self.skeleton.apply(pose)
        self.bone_alpha = alpha
        self.solvePose()
        if clip is not None:
            # ⚠ 这里**不能**加 "and clip.events" 判断：事件可以在运行中才挂上去，
            # 而 _event_time 的推进必须每帧都做（否则挂上事件的当帧区间是空的）。
            self._fireEvents(clip)

    def _fireEvents(self, clip: AnimationClip) -> None:
        """把 ``(上一帧时间, 这一帧时间]`` 里的事件回调一次。

        - **跨循环**（``animation_loops`` 真的涨了）→ 拆成两段：``(上一帧, 时长]``
          再 ``[0, 当前]``，顺序仍是时间序；
        - **时间被手动拨回去**（``seek`` / ``restart``）→ 不补触发中间的事件
          （不然"重播一次"会把一路上的脚步音全刷一遍）。
        """
        current = float(self.animation_time)
        previous = self._event_time
        loops = int(self.animation_loops)
        wrapped = loops > self._event_loops
        self._event_loops = loops
        if previous is None:
            previous = current                    # 刚切剪辑：不补触发历史事件
        elif current < previous and not wrapped:
            previous = current                    # 手动回拨：只从当前时间往后算
        for callback in clip.eventsBetween(previous, current, looped=wrapped):
            callback(self)
        self._event_time = current

    # === 几何（纯 CPU，可单测）===

    def buildVertices(self, builder, skeleton=None, z_offset: float = 0.0) -> int:
        """把**所有**挂点写进同一个 ``builder``（世界坐标），返回写了几个顶点。

        不做贴图分批（那是 :meth:`renderAnimation` 的事）—— 想看"这一帧画在哪"
        直接拿一个 ``BufferBuilder`` 调它即可，不需要 GL 上下文。
        """
        skeleton = self.skeleton if skeleton is None else skeleton
        base_z = float(self.z) + float(z_offset)
        written = 0
        for attachment in self.attachments:
            written += attachment.buildVertices(
                builder, skeleton,
                alpha=self.bone_alpha.get(attachment.boneName, 1.0),
                z_offset=base_z)
        return written

    def textureGroups(self) -> list:
        """按贴图把可见挂点分组：``[(texture, [挂点, …]), …]``。

        保持挂点列表顺序（绘制顺序 = 作者顺序），只有**同贴图**的相邻/分散挂点会被
        合成一批 → 一次 draw call。
        """
        groups = []
        index = {}
        for attachment in self.attachments:
            if not attachment.visible:
                continue
            key = id(attachment.texture)
            position = index.get(key)
            if position is None:
                index[key] = len(groups)
                groups.append((attachment.texture, [attachment]))
            else:
                groups[position][1].append(attachment)
        return groups

    def bufferFor(self, texture, create: bool = True):
        """取（必要时建）某个贴图对应的 :class:`RenderBuffer`。"""
        key = id(texture)
        buffer = self.texture_buffers.get(key)
        if buffer is None and create:
            buffer = RenderBuffer()
            buffer.createBuffer(GL_TRIANGLES, VERTEX_FLAGS)
            self.texture_buffers[key] = buffer
        return buffer

    # === 绘制 ===

    def renderAnimation(self, dt: float, fps: float) -> None:
        """按贴图分批画挂点：**每个贴图一次 draw call**。

        顶点是世界坐标（骨的世界变换已经算进去了），摆位**不碰** ``RenderGlobal`` 的
        那几个模型矩阵（它们是整体世界坐标系的调节）—— 只把光照 / 环境等的 uniform
        同步过去。宿主用 ``x`` / ``y`` / ``r`` 把整具骨架摆到世界里（:meth:`rootTransform`）。
        """
        if not self.attachments or not getattr(self, "active", True):
            self.draw_vertices = 0
            self.draw_batches = 0
            return
        render_global = RenderGlobal.instance
        render_global.updateGlobalUniforms()
        written = 0
        batches = 0
        for texture, items in self.textureGroups():
            buffer = self.bufferFor(texture)
            if buffer is None:
                continue
            builder = buffer.buffer_builder
            builder.clear()
            z = float(self.z)
            count = 0
            for attachment in items:
                count += attachment.buildVertices(
                    builder, self.skeleton,
                    alpha=self.bone_alpha.get(attachment.boneName, 1.0),
                    z_offset=z)
            if count == 0:
                continue
            (texture if texture is not None else Texture.white()).bind(GL_TEXTURE0)
            buffer.setState(blend_mode=self.blend_mode)
            buffer.build()
            buffer.draw(False)
            written += count
            batches += 1
        self.draw_vertices = written
        self.draw_batches = batches

    def releaseAnimation(self) -> None:
        """释放所有分批用的 VBO/VAO（贴图由资源缓存 / 使用者管）。"""
        for buffer in self.texture_buffers.values():
            buffer.release()
        self.texture_buffers = {}

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        return ("<ISkeletonAnimation%s %s 骨%d 挂点%d 画%d顶点/%d批 %s>"
                % (label, self.animation or "(无剪辑)", len(self.skeleton),
                   len(self.attachments), self.draw_vertices, self.draw_batches,
                   self.skeleton))
