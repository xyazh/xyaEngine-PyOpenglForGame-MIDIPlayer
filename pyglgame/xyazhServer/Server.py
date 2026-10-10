import logging
import re
import ssl
import socket
import threading
import time
import os
import urllib.parse
import typing
from .BaseServer import BaseServer
from .lzstring import *
from .PageManager import PageManager
from .ServerHelper import ServerHelper



#自义定一个处理模块
class Server(BaseServer,ServerHelper):
    lzs = LZString()

    def sendTextPage(self,text:str|bytes) -> None:
        if type(text) == str:
            text = bytes(text,encoding="utf8")
        self.send_response(200,"OK")
        self.send_header("Content-type","text/html;charset=utf-8")
        self.send_header("Content-Length",str(len(text)))
        if self.set_cookie:
            for i in self.set_cookie:
                self.send_header("Set-Cookie",i)
            self.set_cookie = []
        self.end_headers()
        self.wfile.write(text)

    def sendFile(self,file_path:str,max_size:int=-1,file_class:str="auto",chunk_size=4096) -> None:
        if not os.path.isfile(file_path):
            self.send_error(404)
            return
        if file_class == "auto":
            file_class = self.getFileContentType(file_path)
        size = os.path.getsize(file_path)
        if max_size > 0:
            size = min(size,max_size)
        self.send_response(200,"OK")
        self.send_header("Content-type",file_class)
        self.send_header("Content-Length",str(size))
        if self.set_cookie:
            for i in self.set_cookie:
                self.send_header("Set-Cookie",i)
            self.set_cookie = []
        self.end_headers()
        with open(file_path,"rb") as f:
            while size > f.tell():
                try:
                    self.wfile.write(f.read(chunk_size))
                except BaseException as e:
                    #logging.exception(e)
                    break
    def sendFileBreakpoint(self,file_path:str,max_size:int=-1,file_class:str="auto",chunk_size=4096) -> None:
        if not os.path.isfile(file_path):
            self.send_error(404)
            return
        if file_class == "auto":
            file_class = self.getFileContentType(file_path)
        size = os.path.getsize(file_path)
        if max_size > 0:
            size = min(size,max_size)
        ran:str|None = self.headers.get("Range")
        ran_min = 0
        if ran != None:
            ra = re.findall(r"\d+",ran) 
            ran_min = int(ra[0])
            ran_max = min(ran_min+chunk_size,size)
            self.send_response(206,"Partial Content")
            self.send_header("Content-type",file_class)
            self.send_header("Accept-Ranges","bytes")
            self.send_header("Content-Length",str(size-ran_min))
            self.send_header("Content-Range", "bytes %d-%d/%d"%(ran_min,ran_max,size))
            if self.set_cookie:
                for i in self.set_cookie:
                    self.send_header("Set-Cookie",i)
                self.set_cookie = []
            self.end_headers()       
        else:
            self.send_response(200,"OK")
            self.send_header("Content-type",file_class)
            self.send_header("Accept-Ranges","bytes")
            self.send_header("Content-Length",str(size))
            if self.set_cookie:
                for i in self.set_cookie:
                    self.send_header("Set-Cookie",i)
                self.set_cookie = []
            self.end_headers()
        with open(file_path,"rb") as f:
            f.seek(ran_min)
            while size > f.tell():
                try:
                    self.wfile.write(f.read(chunk_size))
                except BaseException as e:
                    #logging.exception(e)
                    break

    def readPostData(self,max_size=4096)->bytes:
        post_size:str|None = self.headers.get("Content-Length")
        if post_size == None or not post_size.isdigit():
            return b""
        post_size = int(post_size)
        if max_size > 0:
            post_size = min(post_size, max_size)
        return self.rfile.read(post_size)
    
    def getCookieToDict(self)->dict:
        cookie_str:str = self.headers.get("Cookie")
        if cookie_str == None:
            return {}
        cookie_str = cookie_str.replace(" ","")
        return self.cookiesStrToDict(cookie_str)

    def readPostDataTimeout(self,max_size=4096,time_out=5)->bytes:
        post_size:str|None = self.headers.get("Content-Length")
        if post_size == None or not post_size.isdigit():
            return b""
        post_size = int(post_size)
        if max_size > 0:
            post_size = min(post_size, max_size)
        r = [b"",True,time_out + 1]
        def f(r) -> None:
            for i in range(post_size):
                r[0] += self.rfile.read(1)
                r[2] = time_out
            r[1] = False
        th = threading.Thread(target=f,args=(r,))
        th.daemon = True
        th.start()
        while r[1]:
            r[2] -= 1
            if r[2] <= 0:
                raise TimeoutError()
            time.sleep(1)
        return r[0]

    def setCookie(self,key:str,val:str,max_age:str=None,expires:str=None,http_only:bool=False,domain:str=None,path:str=None) -> None:
        s = "%s=%s"%(key,val)
        if max_age:
            s += ";Max-Age=%s" % max_age
        if expires:
            s += ";Expires=%s"%expires
        if http_only:
            s += ";HttpOnly"
        if domain:
            s += ";Domain=%s"%domain
        if path:
            s += ";path=%s"%path
        self.set_cookie.append(s)

    def doStart(self) -> None:
        self.request:socket.socket|ssl.SSLSocket
        url_datas = urllib.parse.urlparse(self.path)
        self.virtual_path:str = url_datas.path
        self.set_cookie = []

    def do_GET(self) -> None:
        self.doStart()
        try:
            fuc = PageManager.findPath(self.virtual_path,"GET")
            if fuc == None:
                self.send_error(404)
            else:
                fuc(self)
        except ConnectionError:
            pass
        except BaseException as e:
            logging.exception(e)
            self.send_error(500)

    def do_POST(self) -> None:
        self.doStart()
        try:
            fuc = PageManager.findPath(self.virtual_path,"POST")
            if fuc == None:
                self.send_error(404)
            else:
                fuc(self)
        except ConnectionError:
            pass
        except BaseException as e:
            logging.exception(e)
            self.send_error(500)