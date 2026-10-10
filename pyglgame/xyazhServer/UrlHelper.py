class UrlHelper:
    @staticmethod
    def pathSplit(path:str)->list[str]:
        dir = path.split("/")[1:]
        if dir[-1] != "":
            dir.append("")
        return dir