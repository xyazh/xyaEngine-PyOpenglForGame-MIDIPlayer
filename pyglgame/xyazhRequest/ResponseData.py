import json


class ResponseData:
    def __init__(self, raw_response: bytes) -> None:
        self.raw_response = raw_response
        self.headers:dict[str,str] = {}
        self.body = b""
        self.un_chunks_body = b""
        self.text_body = ""
        self.text_un_chunks_body = ""
        self.status_code = 0
        self.reason_phrase = ""
        self.chunks: list[bytes] = []
        self._parseResponse()

    def _parseResponse(self) -> None:
        parts = self.raw_response.split(b"\r\n\r\n", 1)
        headers_part = parts[0]
        body_part = parts[1] if len(parts) > 1 else b""

        # 解析状态行和头部
        headers_lines = headers_part.split(b"\r\n")
        status_line = headers_lines[0].decode("utf-8")
        self._parseStatusLine(status_line)

        for header in headers_lines[1:]:
            header = header.decode("utf-8")
            key, value = header.split(":", 1)
            self.headers[key.strip()] = value.strip()
        self.body = body_part
        # 检查是否为分块传输编码
        if self.headers.get("Transfer-Encoding", "").lower() == "chunked":
            self.un_chunks_body = self._parseChunkedBody(body_part)
        else:
            self.un_chunks_body = body_part

        self.text_body = self.body.decode("utf-8", errors="ignore")
        self.text_un_chunks_body = self.un_chunks_body.decode(
            "utf-8", errors="ignore")

    def _parseChunkedBody(self, body: bytes) -> bytes:
        while body:
            # 获取块大小
            chunk_size_str, body = body.split(b"\r\n", 1)
            chunk_size = int(chunk_size_str, 16)
            if chunk_size == 0:
                break  # 结束块
            # 获取块数据
            chunk_data = body[:chunk_size]
            self.chunks.append(chunk_data)
            # 移除已解析的块数据和后续的 \r\n
            body = body[chunk_size + 2:]
        return b"".join(self.chunks)

    def _parseStatusLine(self, status_line: str) -> None:
        parts = status_line.split(" ", 2)
        self.status_code = int(parts[1])
        self.reason_phrase = parts[2]

    def _json(self, data: str | bytes):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None

    def json(self) -> dict | None | list[dict | None]:
        if len(self.chunks) == 0:
            return self._json(self.text_body)
        if len(self.chunks) == 1:
            return self._json(self.chunks[0].decode("utf-8",errors="ignore"))
        else:
            return [self._json(chunk.decode("utf-8",errors="ignore")) for chunk in self.chunks]

    def __str__(self):
        return f"HTTP/{self.status_code} {self.reason_phrase}\n{self.headers}\n\n{self.text_body}"
