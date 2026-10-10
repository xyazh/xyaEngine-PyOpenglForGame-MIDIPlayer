from .Tree import Tree
from .UrlHelper import UrlHelper
import os
import urllib.parse

class PageManager:
    D = "*"
    path_trees = {
        "GET":Tree(),
        "POST":Tree()
        }

    @staticmethod
    def addPath(path:str,fuc:object,t:str) -> None:
        path = path.replace("\\","/")
        h_dir = UrlHelper.pathSplit(path)
        if not t in PageManager.path_trees:
            PageManager.path_trees[t] = Tree()
        in_node = PageManager.path_trees[t]
        for i in h_dir:
            if i == "":
                in_node.leaf = fuc
                break
            elif i in in_node.child_nodes:
                in_node = in_node.child_nodes[i]
            else:
                new_node = Tree()
                new_node.parent_node = in_node
                new_node.val = i
                in_node.child_nodes[i] = new_node
                in_node = new_node

    @staticmethod
    def findPath(path:str,t:str)->object|None:
        h_dir = UrlHelper.pathSplit(path)
        if not t in PageManager.path_trees:
            return None
        in_node = PageManager.path_trees[t]
        r = None
        for i in h_dir:
            if i == "":
                r = in_node.leaf
                break
            elif i in in_node.child_nodes:
                in_node = in_node.child_nodes[i]
            elif PageManager.D in in_node.child_nodes:
                in_node = in_node.child_nodes[PageManager.D]
            else:
                break
        return r

    def register(path:str,t:str="GET"):
        def r(fuc):
            PageManager.addPath(path,fuc,t)
            return fuc
        return r

    @staticmethod
    def addFileTree(root:str,virtual_root:str,fuc:object,t:str="GET") -> None:
        for dirpath, dirnames, filenames in os.walk(root):
            dirpath = dirpath.replace(root,virtual_root,1)
            for filename in filenames:
                path = dirpath+"/"+filename
                PageManager.addPath(dirpath+"/"+filename,fuc,t)