"""纹理缓存：同一个文件只解码/上传一次。

原实现里每个对象各自 ``Image(...).getTexture()``，同张贴图会被重复解码并
重复上传到显存。这里以 ``(文件, use_hdr, filter, mipmap, wrap)`` 为键缓存。

缓存里的纹理不会被自动回收（由缓存持有），需要释放时调用 :func:`unload`
或 :func:`clear`。
"""

from OpenGL.GL import *

from ..ResourceLocation import ResourceLocation
from .Texture import Texture, NEAREST, LINEAR

_TEXTURE_CACHE: dict[tuple, Texture] = {}
_IMAGE_CACHE: dict[tuple, object] = {}


def loadImage(path: str, use_hdr: bool = False):
    """按路径加载 :class:`~pyglgame.render.Image.Image`（带缓存）。"""
    from .Image import Image
    location = ResourceLocation(path)
    key = (location.cacheKey(), bool(use_hdr))
    image = _IMAGE_CACHE.get(key)
    if image is None:
        image = Image(location, use_hdr=use_hdr)
        _IMAGE_CACHE[key] = image
    return image


def loadTextureFromImage(image, filter: int = LINEAR, mipmap: bool = False,
                         wrap_mode: int = GL_REPEAT) -> Texture:
    """用现成的 :class:`Image` 上传纹理并按路径缓存（避免重复解码）。"""
    location = getattr(image, "resource_location", None)
    if location is None:
        return Texture.createFromImage(image, filter=filter, mipmap=mipmap, wrap_mode=wrap_mode)
    key = (location.cacheKey(), bool(image.use_hdr), int(filter), bool(mipmap), int(wrap_mode))
    texture = _TEXTURE_CACHE.get(key)
    if texture is None:
        texture = Texture.createFromImage(
            image, filter=filter, mipmap=mipmap, wrap_mode=wrap_mode)
        _TEXTURE_CACHE[key] = texture
        _IMAGE_CACHE[(location.cacheKey(), bool(image.use_hdr))] = image
    return texture


def loadTexture(path: str, use_hdr: bool = False,
                filter: int = LINEAR, mipmap: bool = False,
                wrap_mode: int = GL_REPEAT) -> Texture:
    """按路径加载纹理（带缓存）。"""
    location = ResourceLocation(path)
    key = (location.cacheKey(), bool(use_hdr), int(filter), bool(mipmap), int(wrap_mode))
    texture = _TEXTURE_CACHE.get(key)
    if texture is None:
        image = loadImage(path, use_hdr)
        texture = Texture.createFromImage(
            image, filter=filter, mipmap=mipmap, wrap_mode=wrap_mode)
        _TEXTURE_CACHE[key] = texture
    return texture


def getCachedTexture(path: str, use_hdr: bool = False,
                     filter: int = LINEAR, mipmap: bool = False,
                     wrap_mode: int = GL_REPEAT):
    """只查询缓存，不存在时返回 ``None``。"""
    location = ResourceLocation(path)
    key = (location.cacheKey(), bool(use_hdr), int(filter), bool(mipmap), int(wrap_mode))
    return _TEXTURE_CACHE.get(key)


def unload(path: str) -> int:
    """释放某个文件相关的所有缓存，返回释放的纹理数量。"""
    location = ResourceLocation(path)
    prefix = location.cacheKey()
    removed = 0
    for key in [k for k in _TEXTURE_CACHE if k[0] == prefix]:
        _TEXTURE_CACHE.pop(key).release()
        removed += 1
    for key in [k for k in _IMAGE_CACHE if k[0] == prefix]:
        _IMAGE_CACHE.pop(key)
    return removed


def clear() -> int:
    """清空全部缓存并释放显存。"""
    removed = 0
    for texture in _TEXTURE_CACHE.values():
        texture.release()
        removed += 1
    _TEXTURE_CACHE.clear()
    _IMAGE_CACHE.clear()
    return removed


def stats() -> dict:
    return {"textures": len(_TEXTURE_CACHE), "images": len(_IMAGE_CACHE)}
