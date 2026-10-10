import json

class RequestData(bytearray):
    def __init__(self, h_type, h_path, h_version="HTTP/1.1") -> None:
        super().__init__()

        self.__type: str = h_type
        self.__path: str = h_path
        self.__version: str = h_version
        self.__body = {
            "Sec-CH-UA_Other": "xyazh Request",
        }
        self.__data = b""
        self.updateByteArray()

    def setData(self, data) -> None:
        self.__data = data
        self.__body["Content-Length"] = str(len(data))
        self.updateByteArray()

    def setJsonData(self, data: dict|list) -> None:
        json_data = json.dumps(data)
        self.setData(json_data.encode("utf-8"))

    def addBody(self, key, value) -> None:
        self.__body[key] = value
        self.updateByteArray()

    def addBodys(self, data: dict) -> None:
        for key in data:
            self.__body[key] = data[key]
        self.updateByteArray()


    def bodyToStr(self) -> str:
        body = ""
        for key in self.__body:
            body += key + ": " + self.__body[key] + "\r\n"
        return body

    def bodyToBytes(self) -> bytes:
        return self.bodyToStr().encode("utf-8")

    def updateByteArray(self) -> None:
        data = (self.__type.encode("utf-8") + b" " +
                self.__path.encode("utf-8") + b" " +
                self.__version.encode("utf-8") + b"\r\n" +
                self.bodyToBytes() + b"\r\n" +
                self.__data)
        self.clear()
        self.extend(data)

    def toStr(self) -> str:
        return (self.__type + " " +
                self.__path + " " +
                self.__version + "\r\n" +
                self.bodyToStr() + "\r\n" +
                self.__data.decode("utf-8", "ignore"))

    def toBytes(self) -> bytes:
        return bytes(self)
    


if __name__ == "__main__":
    req = RequestData("GET", "/", "HTTP/1.1")
    req.setData(b"Hello World!")
    req.addBody("Content-Type", "text/plain")
    print(req.toStr())
    print(bytes(req))
    with memoryview(req) as view, view.cast("B") as byte_view:
        amount = len(byte_view)
        print(byte_view)
