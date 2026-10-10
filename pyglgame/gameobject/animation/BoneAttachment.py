"""骨骼挂点：**一根骨 + 一张贴图 = 一个会跟着骨动的四边形**。

它是 2D 骨骼动画里被画出来的东西（"插槽 / 皮肤"的那一层）：

.. code-block:: python

    from pyglgame.gameobject.animation import BoneAttachment

    Body = BoneAttachment("torso", texture=body_tex, width=1.2, height=1.6)
    Arm = BoneAttachment("arm_upper", texture=arm_tex, width=1.0, height=0.3,
                         offset=(0.5, 0.0))          # 挂在骨的 +X 方向

    rig.addAttachment(Body).addAttachment(Arm)       # 顺序 = 绘制顺序

几个约定
-------

- **只在 :meth:`BoneAttachment.buildVertices` 里算**：把四边形四角用骨的世界变换摆到
  **世界坐标**（顶点不带模型矩阵 → 绘制前把模型矩阵复位成单位阵），
  所以整个过程是纯 Python 数学、不碰 GL —— 单测拿一个 ``BufferBuilder`` 就能验。
- **顶点格式** ``POS | COL | TEX``，每挂点 6 个顶点（两个三角形）。
- **UV 用 ``Sprite2D`` 的约定**：``(u0, v0)`` 对应四边形**下**边（也就是图集按
  ``FLIP_TOP_BOTTOM`` 翻过的那种，见 docs/2D光照与阴影.md）。和帧动画
  （沿用旧 ``AnimationFrame`` 的"未翻转图集"）不同，转换见
  :meth:`~pyglgame.gameobject.animation.IAnimationFrame.IAnimationFrame.spriteUV`。
- ``color`` 的 alpha 是**底色 alpha**；剪辑里的 ``alpha`` 通道会乘在它上面
  （做"淡入淡出"不用改贴图）。
- ``visible=False`` 或 alpha 减到 0 的挂点**一个顶点都不写**。
"""

from OpenGL.GL import GL_TRIANGLES

import math

from ...render.BufferBuilder import COL, POS, TEX
from .Skeleton import Bone

#: 顶点的属性位（挂点固定用它）
VERTEX_FLAGS = POS | COL | TEX

#: 图元类型
PRIMITIVE = GL_TRIANGLES


class BoneAttachment:
    """一块挂在骨头上的贴图四边形（见模块文档）。"""

    def __init__(self, bone, texture=None, width: float = 1.0,
                 height: float = 1.0, offset=(0.0, 0.0), rotation: float = 0.0,
                 scale: float = 1.0, scale_x: float = None, scale_y: float = None,
                 color=(1.0, 1.0, 1.0, 1.0), z: float = 0.0,
                 uv=(0.0, 0.0, 1.0, 1.0), flip_x: bool = False,
                 flip_y: bool = False, visible: bool = True, name: str = None) -> None:
        """
        :param bone: 挂在哪根骨上（:class:`Bone` 对象，或骨头**名字**）
        :param texture: 贴图（``Texture``；``None`` = 绘制时用 1×1 白纹理）
        :param width: 四边形宽（**骨空间的尺寸**，会被骨的世界缩放拉伸）
        :param height: 四边形高（同上）
        :param offset: 在骨空间的偏移 ``(x, y)``（骨空间 +X 通常 = 骨的朝向）
        :param rotation: 挂点自身的旋转（度，绕挂点原点）
        :param scale: 挂点自身的等比缩放（``scale_x`` / ``scale_y`` 可覆盖）
        :param color: ``(r, g, b, a)``，alpha 会被剪辑的 ``alpha`` 通道乘
        :param z: 这个挂点写进顶点的 z（越大越靠前；``ISkeletonAnimation`` 会再加宿主的 z）
        :param uv: 图集矩形 ``(u0, v0, u1, v1)``
        :param flip_x: 水平翻转（只翻 UV）
        :param flip_y: 垂直翻转（只翻 UV）
        :param visible: 关掉就完全不写顶点（做"换部件"用）
        :param name: 调试用名字
        """
        self.bone = bone
        self.name = name
        self.setTexture(texture)
        self.width = float(width)
        self.height = float(height)
        self.offset = (float(offset[0]), float(offset[1]))
        self.rotation = float(rotation)
        if scale is not None:
            scale_x = scale_y = float(scale)
        self.scale_x = 1.0 if scale_x is None else float(scale_x)
        self.scale_y = 1.0 if scale_y is None else float(scale_y)
        self.color = tuple(float(v) for v in color)
        self.z = float(z)
        self.uv = tuple(float(v) for v in uv)
        self.flip_x = bool(flip_x)
        self.flip_y = bool(flip_y)
        self.visible = bool(visible)

    # === 便捷 setter（都返回 self，方便链式搭骨架）===

    def setTexture(self, texture) -> "BoneAttachment":
        self.texture = texture
        return self

    def setSize(self, width: float, height: float) -> "BoneAttachment":
        self.width, self.height = float(width), float(height)
        return self

    def setOffset(self, x: float, y: float) -> "BoneAttachment":
        self.offset = (float(x), float(y))
        return self

    def setRotation(self, degrees: float) -> "BoneAttachment":
        self.rotation = float(degrees)
        return self

    def setScale(self, scale: float) -> "BoneAttachment":
        self.scale_x = self.scale_y = float(scale)
        return self

    def setColor(self, color) -> "BoneAttachment":
        self.color = tuple(float(v) for v in color)
        return self

    def setUV(self, uv) -> "BoneAttachment":
        self.uv = tuple(float(v) for v in uv)
        return self

    def setFlipX(self, flip: bool = True) -> "BoneAttachment":
        self.flip_x = bool(flip)
        return self

    def setFlipY(self, flip: bool = True) -> "BoneAttachment":
        self.flip_y = bool(flip)
        return self

    def setVisible(self, visible: bool = True) -> "BoneAttachment":
        self.visible = bool(visible)
        return self

    def setBone(self, bone) -> "BoneAttachment":
        """换骨头（``Bone`` 对象或名字）。"""
        self.bone = bone
        return self

    @property
    def boneName(self) -> str:
        """所在骨头的名字（``bone`` 给的是对象时从它身上取）。"""
        if isinstance(self.bone, Bone):
            return self.bone.name
        return str(self.bone)

    def resolveBone(self, skeleton=None):
        """拿到 :class:`Bone` 对象（给的是名字就查骨架；查不到返回 ``None``）。"""
        if isinstance(self.bone, Bone):
            return self.bone
        if skeleton is None:
            return None
        return skeleton.findBone(self.bone)

    def effectiveColor(self, alpha: float = 1.0) -> tuple:
        """叠加动画 ``alpha`` 之后的顶点色。"""
        r, g, b, a = self.color
        return (r, g, b, a * float(alpha))

    # === 几何（纯 CPU）===

    def uvCorners(self) -> tuple:
        """四角 UV（下左 → 下右 → 上右 → 上左，含翻转）。"""
        u0, v0, u1, v1 = self.uv
        if self.flip_x:
            u0, u1 = u1, u0
        if self.flip_y:
            v0, v1 = v1, v0
        return ((u0, v0), (u1, v0), (u1, v1), (u0, v1))

    def worldCorners(self, skeleton=None) -> tuple:
        """四角的世界坐标（下左 → 下右 → 上右 → 上左）；骨头取不到就返回 ``()``。"""
        bone = self.resolveBone(skeleton)
        if bone is None:
            return ()
        rad = math.radians(self.rotation)
        cosine, sine = math.cos(rad), math.sin(rad)
        half_w = self.width * 0.5
        half_h = self.height * 0.5
        offset_x, offset_y = self.offset
        local = ((-half_w, -half_h), (half_w, -half_h),
                 (half_w, half_h), (-half_w, half_h))
        corners = []
        for corner_x, corner_y in local:
            # 先套挂点自己的缩放 / 旋转，再在骨空间平移
            scaled_x = corner_x * self.scale_x
            scaled_y = corner_y * self.scale_y
            local_x = scaled_x * cosine - scaled_y * sine + offset_x
            local_y = scaled_x * sine + scaled_y * cosine + offset_y
            corners.append(bone.worldPoint(local_x, local_y))
        return tuple(corners)

    def buildVertices(self, builder, skeleton=None, alpha: float = 1.0,
                      z_offset: float = 0.0) -> int:
        """把四边形写进 ``builder``（世界坐标，6 个顶点），返回写了几个顶点。

        跳过的情况：``visible=False``、alpha 乘完是 0、骨头找不到 —— 这三种都不写顶点，
        调用方可以直接把返回值加起来当"这批画了几个顶点"。
        """
        if not self.visible:
            return 0
        color = self.effectiveColor(alpha)
        if color[3] <= 0.0:
            return 0
        corners = self.worldCorners(skeleton)
        if len(corners) != 4:
            return 0
        uvs = self.uvCorners()
        z = self.z + float(z_offset)
        # 三角形 1：0-1-2；三角形 2：0-2-3
        for index in (0, 1, 2, 0, 2, 3):
            x, y = corners[index]
            u, v = uvs[index]
            builder.pos(x, y, z).col(*color).tex(u, v).end()
        return 6

    # === 数据 ===

    def toDict(self, with_texture: bool = False) -> dict:
        """导出成 dict（``texture`` 默认不导出：贴图对象没法序列化）。"""
        data = {"bone": self.boneName, "name": self.name,
                "width": self.width, "height": self.height,
                "offset": list(self.offset), "rotation": self.rotation,
                "scale_x": self.scale_x, "scale_y": self.scale_y,
                "color": list(self.color), "z": self.z, "uv": list(self.uv),
                "flip_x": self.flip_x, "flip_y": self.flip_y,
                "visible": self.visible}
        if with_texture:
            data["texture"] = self.texture
        return data

    @classmethod
    def fromDict(cls, data: dict, textures: dict = None) -> "BoneAttachment":
        """从 dict 建挂点；``textures`` 是 ``{贴图名: Texture}``（可选）。

        ``data["texture"]`` 是字符串时会去 ``textures`` 里查（AIGC / 编辑器导出的
        JSON 一般只写贴图名）。
        """
        data = dict(data or {})
        texture = data.get("texture")
        if isinstance(texture, str):
            texture = None if textures is None else textures.get(texture)
        bone = data.pop("bone", data.pop("bone_name", None))
        if bone is None:
            raise ValueError("挂点缺 'bone'（挂在哪根骨上）：%r" % (data,))
        kwargs = {key: data[key] for key in
                  ("width", "height", "offset", "rotation", "scale", "scale_x",
                   "scale_y", "color", "z", "uv", "flip_x", "flip_y",
                   "visible", "name") if key in data}
        return cls(bone, texture=texture, **kwargs)

    def __repr__(self) -> str:
        label = f" {self.name!r}" if self.name else ""
        return ("<BoneAttachment%s 骨=%s %.2f×%.2f 偏移(%.2f, %.2f) %g°%s>"
                % (label, self.boneName, self.width, self.height,
                   self.offset[0], self.offset[1], self.rotation,
                   "" if self.visible else " 隐藏"))
