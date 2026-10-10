from .FileClassTable import FileClassTable
import hashlib
import json
import random
import os
import hmac


class ServerHelper:
    def getStrMd5(self,s:str)->str:
        s = str(s)
        md5 = hashlib.md5()
        md5.update(s.encode('utf8'))
        return md5.hexdigest()

    def randomIntStr(self,l:int=4)->str:
        s = str(random.randint(0,10 ** l - 1))
        return "0" * (l - len(s)) + s

    def urlVals(self,strs:str)->dict:
        l="[\"" + ("1" + strs).replace("?","\",\"") + "\"]"
        li = json.loads(l)
        if len(li) != 2:
            return {}
        strs = li[1]
        strs = "{\"" + strs.replace("&","\",\"") + "\"}"
        d = strs.replace("=","\":\"")
        di = json.loads(d)
        return di

    def cookiesStrToDict(self,strs:str)->dict:
        strs = strs.replace("\r\n","")
        strs = strs.replace("=","\":\"")
        strs = strs.replace(";","\",\"")
        strs =  "{\"" + strs + "\"}"
        return json.loads(strs)

    def getFileContentType(self,file_name:str)->str:
        ed = os.path.splitext(file_name)[-1]
        if ed in FileClassTable.TABLE:
            return FileClassTable.TABLE[ed]
        else:
            return "application/octet-stream"

    def safeHash(self,key:str|bytes,data:str|bytes)->str:
        if isinstance(key,str):
            key = bytes(key,encoding="utf8")
        if isinstance(data,str):
            data = bytes(data,encoding="utf8")
        h = hmac.new(key, data, digestmod='SHA256')
        return h.hexdigest()