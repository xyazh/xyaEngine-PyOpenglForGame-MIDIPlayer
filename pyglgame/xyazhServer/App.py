import ssl
import threading

from .ihttp import server
from .Server import Server
from typing import Callable


class App:
    def __init__(self,host:str,port:int) -> None:
        self.host = host
        self.port = port
        self.http_server = server.ThreadingHTTPServer((self.host, port),Server)
        self.http_server.socket.settimeout(5)
        self.http_server.timeout
        self.context = None
        self.ready = False

    def pTitleVison(self,t:str) -> None:
        print(" * ---------------------------------")
        print(" * xyazhServer %s"%(Server.server_version))
        print(" * Server is runing on %s:%s"%(self.host,self.port))
        print(" * HTTP url is %s://%s:%s/ (Press CTRL+C to quit)"%(t,self.host,self.port))
        print(" * ---------------------------------\r\n\r\n")

    def runThead(self,func:Callable=None,args:tuple = ()) -> None:
        thread = threading.Thread(target=func,args=args)
        thread.start()

    def runHTTP(self,func:Callable=None) -> None:
        self.pTitleVison("http")
        if func is not None:
            func()
        self.ready = True
        self.http_server.serve_forever()

    def runHTTPS(self,cert_chain:tuple[str],func:Callable=None) -> None:
        self.context= ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)    
        self.context.load_cert_chain(*cert_chain)
        self.http_server.socket= self.context.wrap_socket(self.http_server.socket, server_side = True)
        self.pTitleVison("https")
        if func is not None:
            func()
        self.ready = True
        self.http_server.serve_forever()