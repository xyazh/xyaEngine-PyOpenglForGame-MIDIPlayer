import numpy as np
from PIL import Image as PILImage
from ..ResourceLocation import ResourceLocation
from .Texture import Texture, NEAREST, LINEAR

class Image:

    @property
    def width(self) -> int:
        return self.image.width
    
    @property
    def height(self) -> int:
        return self.image.height
    
    @width.setter
    def width(self,value:int) -> None:
        pass

    @height.setter
    def height(self,value:int) -> None:
        pass

    def __init__(self,resource_location:ResourceLocation,use_hdr: bool = False) -> None:
        self.resource_location = resource_location
        self.image:PILImage = PILImage.open(resource_location.getIO()).convert("RGBA")
        if use_hdr:
            self.data = np.array(self.image,dtype=np.float32)
            #self.data = np.divide(self.data,255.0)
        else:
            # asarray 避免再复制一份（原实现用 array 会多一次拷贝）
            self.data = np.asarray(self.image, dtype=np.uint8)
        self.use_hdr:bool = use_hdr

    @classmethod
    def fromPIL(cls, image: PILImage.Image, use_hdr: bool = False) -> "Image":
        """由已有的 PIL 图像构造（例如 TextLoader 现场绘制的文字贴图）。"""
        obj = cls.__new__(cls)
        obj.resource_location = None
        obj.image = image.convert("RGBA")
        if use_hdr:
            obj.data = np.array(obj.image, dtype=np.float32)
        else:
            obj.data = np.asarray(obj.image, dtype=np.uint8)
        obj.use_hdr = use_hdr
        return obj

    def getTexture(self, filter: int = LINEAR, mipmap: bool = False) -> Texture:
        """上传为纹理；同一文件重复调用会复用缓存中的纹理（避免重复占显存）。"""
        from .TextureCache import loadTextureFromImage
        return loadTextureFromImage(self, filter=filter, mipmap=mipmap)