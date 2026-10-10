"""组合数学小工具：阶乘 / 排列 / 组合 / 分数。"""


class MathHelper:
    def A(m: int, n: int) -> float:
        """排列数：n! / (n - m)!。"""
        return MathHelper.factorial(n)/MathHelper.factorial(n-m)

    def C(a: int, b: int) -> float:
        """组合数 C(b, a)：b! / (a! · (b - a)!)。"""
        fz = 1
        n = b-a
        while n < b:
            fz = fz * (n + 1)
            n = n + 1
        fm = MathHelper.factorial(a)
        return fz / fm

    def decreasesTechnique(m: float, n: float) -> float:
        """更相减损求最大公约数（先约掉公共因子 2，再辗转相减）。"""
        if m==n:
            return n
        while m%2==0 and n%2==0:
            m=m/2
            n=n/2
        li=[max(m,n),min(m,n)]
        d=li[0]-li[1]
        while d!=li[1]:
            li[0]=max(d,li[1])
            li[1]=min(d,li[1])
            d=li[0]-li[1]
        return d

    def factorial(n: int) -> int:
        """n 的阶乘（n <= 0 时为 1）。"""
        if n>0:
            return n*MathHelper.factorial(n-1)
        else:
            return 1

    class Fraction:
        def __add__(self, other: object) -> "MathHelper.Fraction | None":
            """加另一个 :class:`Fraction`；**就地修改自己**后返回（``isinstance``
            不通过时返回 ``None``）。"""
            if isinstance(other,MathHelper.Fraction):
                numerator=self.signBit*self.numerator*other.denominator+other.signBit*other.numerator*self.denominator
                denominator=self.denominator*other.denominator
                signBit=1
                if numerator<0:
                    signBit=signBit*-1
                if denominator<0:
                    signBit=signBit*-1
                self.signBit = signBit
                self.numerator = abs(numerator)
                self.denominator = abs(denominator)
                return self
        
        def __init__(self, a: "str | tuple[int, int]") -> None:
            """``a`` 可以是 ``"3/4"`` 字符串或 ``(分子, 分母)`` 元组。"""
            if type(a)==str:
                a=self._strToFraction(s=a)
            self.numerator=abs(a[0])
            self.denominator=abs(a[1])
            if(a[0]/a[1]==0):
                self.numerator=0
                self.denominator=1
            self.signBit=1
            if a[0]<0:
                self.signBit=self.signBit*-1
            if a[1]<0:
                self.signBit=self.signBit*-1


        def __str__(self) -> str:
            if self.numerator==0:
                return "0"
            s1=str(int(self.signBit*self.numerator))
            s2=str(int(self.denominator))
            return s1+"/"+s2

        def __sub__(self, other: object) -> "MathHelper.Fraction | None":
            """减法（⚠ 会把 ``other`` 的符号位翻转，是就地实现的副作用）。"""
            if isinstance(other,MathHelper.Fraction):
                other.signBit = -other.signBit
            return self.__add__(other)
            
        
        def _strToFraction(self=None, s: str = "") -> tuple[int, int]:
            """``"分子/分母"`` → ``(分子, 分母)``。"""
            li=s.split("/")
            return (int(li[0]),int(li[1]))  

        def getFloat(self) -> float:
            """分数 → 浮点值。"""
            return self.signBit * self.numerator / self.denominator

        def reduction(self) -> str:
            """约分并返回 ``"分子/分母"`` 字符串。"""
            if self.numerator==0:
                self.numerator=0
                self.denominator=1
            else:
                l=MathHelper.decreasesTechnique(self.numerator,self.denominator)
                self.numerator=int(self.numerator/l)
                self.denominator=int(self.denominator/l)
            return self.__str__()