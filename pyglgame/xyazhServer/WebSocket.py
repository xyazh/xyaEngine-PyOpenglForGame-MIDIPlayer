import hashlib
import base64
import struct
import errno
from typing import BinaryIO
from socket import error as SocketError
from .ConsoleMessage import ConsoleMessage

FIN = 0x80
OPCODE = 0x0f
MASKED = 0x80
PAYLOAD_LEN = 0x7f
PAYLOAD_LEN_EXT16 = 0x7e
PAYLOAD_LEN_EXT64 = 0x7f

OPCODE_CONTINUATION = 0x0
OPCODE_TEXT = 0x1
OPCODE_BINARY = 0x2
OPCODE_CLOSE_CONN = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA

CLOSE_STATUS_NORMAL = 1000
DEFAULT_CLOSE_REASON = bytes('', encoding='utf-8')


class WebSocket:
    @staticmethod
    def wsAcceptKey(ws_key: str):
        magic_string = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        combined = ws_key + magic_string
        sha1 = hashlib.sha1(combined.encode())
        encoded = base64.b64encode(sha1.digest())
        return encoded.decode()

    def __init__(self, s) -> None:
        self._server = s
        self.wfile: BinaryIO = s.wfile
        self.rfile: BinaryIO = s.rfile
        ConsoleMessage.printMsg("New websockets-client connected")

    def _old_webSocketSend(self, msg: bytes) -> bool:
        token = b"\x81"
        length = len(msg)
        if length < 126:
            token += struct.pack("B", length)
        elif length <= 0xFFFF:
            token += struct.pack("!BH", 126, length)
        else:
            token += struct.pack("!BQ", 127, length)
        msg = token + msg
        self.wfile.write(msg)
        return True

    def _old_webSocketRecv(self, n: int = 1024) -> bytes:
        conn = self._server
        info = conn.request.recv(n)
        if len(info) == 0:
            return b""
        payload_len = info[1] & 127
        if payload_len == 126:
            mask = info[4:8]
            decoded = info[8:]
        elif payload_len == 127:
            mask = info[10:14]
            decoded = info[14:]
        else:
            mask = info[2:6]
            decoded = info[6:]
        bytes_list = bytearray()
        for i in range(len(decoded)):
            chunk = decoded[i] ^ mask[i % 4]
            bytes_list.append(chunk)
        return bytes(bytes_list)

    def readBytes(self, num) -> bytes:
        return self.rfile.read(num)

    def webSocketRecv(self) -> tuple[bytes|None, str]:
        try:
            b1, b2 = self.readBytes(2)
        except SocketError as e:
            if e.errno == errno.ECONNRESET:
                ConsoleMessage.printMsg("Client closed connection.")
                return None,"error"
            b1, b2 = 0, 0
        except ValueError as e:
            b1, b2 = 0, 0

        fin = b1 & FIN
        opcode = b1 & OPCODE
        masked = b2 & MASKED
        payload_length = b2 & PAYLOAD_LEN

        if opcode == OPCODE_CLOSE_CONN:
            ConsoleMessage.printMsg("Client asked to close connection.")
            return None,"close"
        if not masked:
            ConsoleMessage.printWarning("Client must always be masked.")
            return None,"not masked"
        if opcode == OPCODE_CONTINUATION:
            ConsoleMessage.printWarning(
                "Continuation frames are not supported.")
            return None,"continuation"
        elif opcode == OPCODE_BINARY:
            ConsoleMessage.printWarning("Binary frames are not supported.")
            return None,"binary"
        elif opcode == OPCODE_TEXT:
            t = "text"
        elif opcode == OPCODE_PING:
            t = "ping"
        elif opcode == OPCODE_PONG:
            t = "pong"
        else:
            ConsoleMessage.printWarning("Unknown opcode %#x." % opcode)
            return
        if payload_length == 126:
            payload_length = struct.unpack(">H", self.rfile.read(2))[0]
        elif payload_length == 127:
            payload_length = struct.unpack(">Q", self.rfile.read(8))[0]
        masks = self.readBytes(4)
        message_bytes = bytearray()
        for message_byte in self.readBytes(payload_length):
            message_byte ^= masks[len(message_bytes) % 4]
            message_bytes.append(message_byte)
        message_bytes = bytes(message_bytes)
        if t == "ping":
            self.sendPong(message_byte)
        return message_bytes, t

    def webSocketSend(self, message: bytes, opcode=OPCODE_TEXT) -> None:
        header = bytearray()
        payload = message
        payload_length = len(payload)
        # Normal payload
        if payload_length <= 125:
            header.append(FIN | opcode)
            header.append(payload_length)
        # Extended payload
        elif payload_length >= 126 and payload_length <= 65535:
            header.append(FIN | opcode)
            header.append(PAYLOAD_LEN_EXT16)
            header.extend(struct.pack(">H", payload_length))
        # Huge extended payload
        elif payload_length < 18446744073709551616:
            header.append(FIN | opcode)
            header.append(PAYLOAD_LEN_EXT64)
            header.extend(struct.pack(">Q", payload_length))
        else:
            ConsoleMessage.printError(
                "Message is too big. Consider breaking it into chunks.")
            return
        self._server.request.send(header + payload)

    def sendPong(self, message: bytes) -> None:
        self.webSocketSend(message, OPCODE_PONG)

    def sendClose(self, status=CLOSE_STATUS_NORMAL, reason=DEFAULT_CLOSE_REASON) -> None:
        if status < CLOSE_STATUS_NORMAL or status > 1015:
            raise Exception(f"CLOSE status must be between 1000 and 1015, got {status}")
        header = bytearray()
        payload = struct.pack('!H', status) + reason
        payload_length = len(payload)
        assert payload_length <= 125, "We only support short closing reasons at the moment"
        header.append(FIN | OPCODE_CLOSE_CONN)
        header.append(payload_length)
        self._server.request.send(header + payload)
