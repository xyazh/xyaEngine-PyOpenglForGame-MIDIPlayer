from .Vec4 import Vec4
class Color(Vec4):
    @staticmethod
    def fromInt(color:int):
        """like 0xffddssaaa"""
        r = (color >> 24) & 0xff
        g = (color >> 16) & 0xff
        b = (color >> 8) & 0xff
        a = color & 0xff
        return Color(r/255,g/255,b/255,a/255)
    
    def fromString(color: str):
        if color[0] == "#":
            color = color[1:]
        r = int(color[0:2], 16)
        g = int(color[2:4], 16)
        b = int(color[4:6], 16)
        a = int(color[6:8], 16) if len(color) > 6 else 255
        return Color(r / 255, g / 255, b / 255, a / 255)


    def __init__(self, r: float, g: float, b: float, a: float = 1.0) -> None:
        super().__init__(r, g, b, a)

    @property
    def r(self)->float:
        return self.x
    
    @r.setter
    def r(self,value:float)->None:
        self.x = value
    
    @property
    def g(self)->float:
        return self.y
    
    @g.setter
    def g(self,value:float)->None:
        self.y = value

    @property
    def b(self)->float:
        return self.z
    
    @b.setter
    def b(self,value:float)->None:
        self.z = value

    @property
    def a(self)->float:
        return self.w
    
    @a.setter
    def a(self,value:float)->None:
        self.w = value