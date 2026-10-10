"""把精灵 / 阴影遮罩体拴在一个物理体上（``BodySprite``）。

为什么需要这一层
----------------

物理与渲染是**两件事**：

- 位姿归 Box2D（``Body2D.x / y / angle``）—— 而且 dynamic 刚体的位姿**由物理写**，
  代码只读；
- 外观（:class:`~pyglgame.gameobject.Sprite2D.Sprite2D` 或图集动画）与阴影遮罩体
  （:class:`~pyglgame.gameobject.ShadowCaster2D.ShadowCaster2D`）只是"跟着走"。

所以横板 ACT 里最常用的一件小事就是"每帧把物理位姿抄给外观"—— 本类把它做掉，
顺带处理三个容易漏的点：

1. **角度单位**：物理给的是**弧度**，精灵要**度**（``math.degrees``）；
2. **脚底对齐**：图集帧的轴心是脚底中心（见 ``docs/2D帧动画与场景导入.md``），
   要让"脚踩在碰撞箱底边"就得减半个身高 —— 用 ``offset_y`` 表达；
3. **遮挡体跟随**：阴影遮罩体通常按**碰撞箱中心**摆（而不是精灵原点），
   所以它有独立的 ``caster_offset_y``。

::

    body = Body2D(world, x=0.0, y=2.0, fixed_rotation=True, name="player")
    body.addBox(0.7, 1.4)
    sprite = Sprite2D(tex, width=0.7, height=1.4, name="player")
    caster = ShadowCaster2D.rect(0.7, 1.4, z=0.0)
    BodySprite(body, sprite, caster=caster)        # 之后什么都不用管

构造**不碰 GL**（和引擎其它对象一样：第一次绘制才上传），所以离线也能建。
"""

import math

from .GameObject import GameObject


class BodySprite(GameObject):
    """一个物理体的"外观"：精灵 + 可选的阴影遮罩体，每帧同步过去（见模块文档）。"""

    def __new__(cls, *args, **kwargs):
        # ⚠ ``GameObject.__new__`` 只认 ``is_camera``：构造参数必须在这里吃掉
        return super().__new__(cls)

    def __init__(self, body, sprite, caster=None, offset_y: float = 0.0,
                 follow_rotation: bool = True, caster_offset_y: float = None,
                 name: str = None) -> None:
        """
        :param body: :class:`~pyglgame.physics.Body2D.Body2D`（位姿的唯一来源）
        :param sprite: 外观（``Sprite2D`` / 图集动画，任何有 ``x`` / ``y`` / ``r`` 的对象）
        :param caster: 可选的 :class:`~pyglgame.gameobject.ShadowCaster2D.ShadowCaster2D`
        :param offset_y: 精灵相对**物理体中心**的 y 偏移
            （图集轴心是脚底时给 ``-half_height``，默认 0 = 精灵原点就在质心）
        :param follow_rotation: 是否把物理角度抄给精灵（横板角色常设 ``fixed_rotation``，
            所以这里通常无所谓；翻滚的箱子要 ``True``）
        :param caster_offset_y: 遮挡体的 y 偏移（``None`` = 跟 ``offset_y`` 一样）
        """
        super().__init__()
        if body is None or not getattr(body, "is_physics_2d_body", False):
            # 只在"传了别的东西"时报错；鸭子类型（测试里的假 body）仍然放行
            if not hasattr(body, "x") or not hasattr(body, "y"):
                raise TypeError("BodySprite 需要一个刚体（Body2D），收到 %r"
                                % (type(body).__name__,))
        self.body = body
        self.sprite = sprite
        self.caster = caster
        self.offset_y = float(offset_y)
        self.follow_rotation = bool(follow_rotation)
        self.caster_offset_y = (self.offset_y if caster_offset_y is None
                                else float(caster_offset_y))
        self.name = name
        self.sync()

    def sync(self) -> None:
        """把物理位姿抄给精灵 / 遮挡体（角度：弧度 → 度）。"""
        self.sprite.x = self.body.x
        self.sprite.y = self.body.y + self.offset_y
        if self.follow_rotation:
            self.sprite.r = math.degrees(self.body.angle)
        if self.caster is not None:
            self.caster.x = self.sprite.x
            self.caster.y = self.body.y + self.caster_offset_y
            self.caster.r = self.sprite.r

    def update(self, dt: float, tps: float) -> None:
        self.sync()
        super().update(dt, tps)

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        return ("<BodySprite%s 体=%s 外观=%s%s 位置=(%.2f, %.2f)>"
                % (label, getattr(self.body, "name", None) or "?",
                   type(self.sprite).__name__,
                   " + 遮挡体" if self.caster is not None else "",
                   self.body.x, self.body.y))
