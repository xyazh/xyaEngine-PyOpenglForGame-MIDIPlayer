import json

class Tree:
    def __init__(self) -> None:
        self.child_nodes:dict[str,Tree] = {}
        self.leaf = None
        self.val = None
        self.parent_node:Tree|None = None

    def _get(self)->dict:
        r = {}
        if self.child_nodes == {}:
            return r
        for i in self.child_nodes:
            v = self.child_nodes[i]._get()
            r[i] = v
        return r

    def __str__(self) -> str:
        try:
            r = json.dumps(self._get(),ensure_ascii=False,indent=4)
        except:
            r = str(self._get())
        return r

    def __repr__(self) -> str:
        return self.__str__()