"""懒上传贴图：**构造不碰 GL，第一次绑定才上传**。

为什么需要它
------------

场景文档里贴图只是"名字 → 路径"（``{"ground": "res/img/ground.png"}``），而引擎的
约定是"**构造不碰 GL，绘制才上传**"（``Sprite2D`` / ``IAnimationFrame`` / ``TextureAtlas``
都是这样）。"文档 → 对象"这一步完全可能发生在**没有 GL 上下文**的时候：

- 离线校验 / 编辑器里加载场景（只想看结构，不想开窗口）；
- 单测（``tests/`` 里全程没有 GL 上下文）。

所以本类只记住路径，把 ``Image`` 解码与 ``glTexImage2D`` 推迟到 :meth:`bind`
（= 真的画到屏幕上的那一刻）。它只需要提供 ``bind(unit)`` / ``id`` 这两个
``Texture`` 的小接口，就能直接塞给 ``Sprite2D`` / ``RenderBuffer``。

::

    from pyglgame.render.LazyTexture import LazyTexture
    sprite = Sprite2D(LazyTexture("res/img/ground.png"))    # 构造：不碰 GL
    sprite.render(dt, fps)                                  # 这一帧才上传
"""

from OpenGL.GL import GL_CLAMP_TO_EDGE, GL_REPEAT, GL_TEXTURE0

from .Texture import LINEAR, NEAREST


class LazyTexture:
    """按路径懒上传的贴图（见模块文档）。"""

    def __init__(self, path: str, filter: int = NEAREST, mipmap: bool = False,
                 wrap_mode: int = GL_REPEAT, use_hdr: bool = False) -> None:
        self.path = str(path)
        self.filter = filter
        self.mipmap = bool(mipmap)
        self.wrap_mode = wrap_mode
        self.use_hdr = bool(use_hdr)
        self._texture = None

    # === 懒加载 ===

    @property
    def texture(self):
        """真实贴图（第一次访问才上传；同一路径走 ``TextureCache``，不会重复上传）。"""
        if self._texture is None:
            from .TextureCache import loadTexture
            self._texture = loadTexture(self.path, use_hdr=self.use_hdr,
                                        filter=self.filter, mipmap=self.mipmap,
                                        wrap_mode=self.wrap_mode)
        return self._texture

    @property
    def id(self) -> int:
        """GL 纹理名（**会触发上传**）。"""
        return self.texture.id

    @property
    def loaded(self) -> bool:
        """是否已经上传过（想知道"现在能不能不碰 GL"时用）。"""
        return self._texture is not None

    @property
    def width(self) -> int:
        return self.texture.w

    @property
    def height(self) -> int:
        return self.texture.h

    def setTexture(self, texture) -> "LazyTexture":
        """直接塞一张现成贴图（``None`` = 回到懒上传）。"""
        self._texture = texture
        return self

    def bind(self, unit=GL_TEXTURE0) -> "LazyTexture":
        """绑定到纹理单元（与 ``Texture.bind`` 同签名）。"""
        self.texture.bind(unit)
        return self

    def release(self) -> None:
        """丢掉自己持有的引用（**不**释放缓存里的纹理 —— 那是 ``TextureCache`` 的活）。"""
        self._texture = None

    def __repr__(self) -> str:
        state = "已上传" if self.loaded else "未上传"
        return "<LazyTexture %s %s>" % (self.path, state)


def lazyTexture(path, filter: int = NEAREST, mipmap: bool = False,
                wrap_mode: int = GL_REPEAT):
    """``LazyTexture`` 的快捷构造（``None`` / 空路径 → ``None``）。

    场景运行时用它把"贴图名 → 路径"变成能直接塞给 ``Sprite2D`` 的东西。
    """
    if path is None or path == "":
        return None
    if isinstance(path, LazyTexture):
        return path
    return LazyTexture(path, filter=filter, mipmap=mipmap, wrap_mode=wrap_mode)
