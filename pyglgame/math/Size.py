class Size:
    def __init__(self, w: float, h: float) -> None:
        self.change_fuc = []
        self.w: float = 0
        self.h: float = 0
        self.updateSize(w, h)

    def __repr__(self):
        return "(%s, %s)" % (self.w, self.h)

    def __str__(self):
        return "(%s, %s)" % (self.w, self.h)

    def updateSize(self, w, h) -> None:
        """更新尺寸；只有真的变化时才触发回调（窗口缩放会每帧调用这里）。"""
        if w == self.w and h == self.h:
            return
        w = max(w, 1)
        h = max(h, 1)
        self.w: float = w
        self.h: float = h
        self.x: float = w
        self.y: float = h
        for fuc in tuple(self.change_fuc):
            try:
                fuc(w, h)
            except Exception as e:
                from ..xyazhServer.ConsoleMessage import ConsoleMessage
                ConsoleMessage.printError(f"Size.onChange 回调执行失败: {e}")

    def onChange(self, fuc) -> None:
        self.change_fuc.append(fuc)

    def removeOnChange(self, fuc) -> None:
        if fuc in self.change_fuc:
            self.change_fuc.remove(fuc)
