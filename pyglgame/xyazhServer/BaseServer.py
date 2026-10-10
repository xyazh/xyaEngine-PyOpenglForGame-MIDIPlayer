from http.server import BaseHTTPRequestHandler
from http import HTTPStatus
from .ConsoleMessage import ConsoleMessage
import http.client
import html
import threading
import time
import socket
import logging
DEFAULT_ERROR_MESSAGE = """\
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01//EN"
        "http://www.w3.org/TR/html4/strict.dtd">
<html>
    <head>
        <meta http-equiv="Content-Type" content="text/html;charset=utf-8">
        <title>Error response</title>
    </head>
    <body>
        <h1>Error response</h1>
        <p>Error code: %(code)d</p>
        <p>Message: %(message)s.</p>
        <p>Error code explanation: %(code)s - %(explain)s.</p>
    </body>
</html>
"""
DEFAULT_ERROR_CONTENT_TYPE = "text/html;charset=utf-8"

__version__ = "3.2"

class BaseServer(BaseHTTPRequestHandler):
    IP_CHECK_MAIL_TIMER = {}

    @staticmethod
    def timer(state: list) -> None:
        # state = [stop_flag, cache_dict]；用传入的可变对象避免在类体执行期间
        # 引用尚未定义的 BaseServer 名字
        while not state[0]:
            time.sleep(600)
            state[1].clear()

    # 清理线程必须是 **守护线程**：非守护线程会让 Python 解释器退出时一直等它
    # （threading._shutdown），表现为"进程永远不退出"。由于
    # pyglgame.xyazhServer 的 __init__ 会被 ResourceLocation 间接导入，
    # 这个线程以前会拖住每一个用到引擎的进程。
    _timer_state = [False, IP_CHECK_MAIL_TIMER]
    threading.Thread(target=timer, args=(_timer_state,), daemon=True,
                     name="xyazhServerTimer").start()
    ip_r_num = 400
    sys_version = ""
    server_version = "xyazhServer/" + __version__
    error_message_format = DEFAULT_ERROR_MESSAGE
    error_content_type = DEFAULT_ERROR_CONTENT_TYPE
    default_request_version = "HTTP/1.1"

    def parse_request(self):
        self.command = None
        version = self.default_request_version
        self.request_version = self.default_request_version
        self.close_connection = True
        try:
            requestline = str(self.raw_requestline, 'utf8')
        except UnicodeDecodeError:
            requestline = ""
        requestline = requestline.rstrip('\r\n')
        self.requestline = requestline
        words = requestline.split()
        if len(words) == 0:
            return False
        if len(words) >= 3:  # Enough to determine protocol version
            version = words[-1]
            try:
                if not version.startswith('HTTP/'):
                    raise ValueError
                base_version_number = version.split('/', 1)[1]
                version_number = base_version_number.split(".")
                # RFC 2145 section 3.1 says there can be only one "." and
                #   - major and minor numbers MUST be treated as
                #      separate integers;
                #   - HTTP/2.4 is a lower version than HTTP/2.13, which in
                #      turn is lower than HTTP/12.3;
                #   - Leading zeros MUST be ignored by recipients.
                if len(version_number) != 2:
                    raise ValueError
                version_number = int(version_number[0]), int(version_number[1])
            except (ValueError, IndexError):
                self.send_error(
                    HTTPStatus.BAD_REQUEST,
                    "Bad request version (%r)" % version)
                return False
            if version_number >= (1, 1) and self.protocol_version >= "HTTP/1.1":
                self.close_connection = False
            if version_number >= (2, 0):
                self.send_error(
                    HTTPStatus.HTTP_VERSION_NOT_SUPPORTED,
                    "Invalid HTTP version (%s)" % base_version_number)
                return False
            self.request_version = version

        if not 2 <= len(words) <= 3:
            self.send_error(
                HTTPStatus.BAD_REQUEST,
                "Bad request syntax (%r)" % requestline)
            return False
        command, path = words[:2]
        if len(words) == 2:
            self.close_connection = True
            if command != 'GET':
                self.send_error(
                    HTTPStatus.BAD_REQUEST,
                    "Bad HTTP/0.9 request type (%r)" % command)
                return False
        self.command, self.path = command, path

        # Examine the headers and look for a Connection directive.
        try:
            self.headers = http.client.parse_headers(self.rfile,
                                                     _class=self.MessageClass)
        except http.client.LineTooLong as err:
            self.send_error(
                HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE,
                "Line too long",
                str(err))
            return False
        except http.client.HTTPException as err:
            self.send_error(
                HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE,
                "Too many headers",
                str(err)
            )
            return False

        conntype = self.headers.get('Connection', "")
        if conntype.lower() == 'close':
            self.close_connection = True
        elif (conntype.lower() == 'keep-alive' and
              self.protocol_version >= "HTTP/1.1"):
            self.close_connection = False
        # Examine the headers and look for an Expect directive
        expect = self.headers.get('Expect', "")
        if (expect.lower() == "100-continue" and
                self.protocol_version >= "HTTP/1.1" and
                self.request_version >= "HTTP/1.1"):
            if not self.handle_expect_100():
                return False
        return True

    def handle_expect_100(self):
        """Decide what to do with an "Expect: 100-continue" header.

        If the client is expecting a 100 Continue response, we must
        respond with either a 100 Continue or a final response before
        waiting for the request body. The default is to always respond
        with a 100 Continue. You can behave differently (for example,
        reject unauthorized requests) by overriding this method.

        This method should either return True (possibly after sending
        a 100 Continue response) or send an error response and return
        False.

        """
        self.send_response_only(HTTPStatus.CONTINUE)
        self.end_headers()
        return True

    def handle_one_request(self) -> None:
        self.close_connection = True
        try:
            t_ip = self.address_string()
            if t_ip in BaseServer.IP_CHECK_MAIL_TIMER:
                BaseServer.IP_CHECK_MAIL_TIMER[t_ip] += 1
                n = BaseServer.IP_CHECK_MAIL_TIMER[t_ip]
                if n > BaseServer.ip_r_num:
                    self.request:socket.socket
                    log = "%s >> %s | %d  %s"%(t_ip,"Fail",418, "I\'m a Teapot")
                    ConsoleMessage.printWarning(log)
                    self.request.send(b"HTTP/1.1 418 I\'m a Teapot\r\n")
                    self.request.send(b"server: xyazhServer/%s\r\n"%(__version__.encode("utf8")))
                    self.request.send(b"Content-Length: 0\r\n")
                    self.request.send(b"\r\n")
                    self.request.close()
                    return
            else:
                BaseServer.IP_CHECK_MAIL_TIMER[t_ip] = 0
            self.raw_requestline = self.rfile.readline(65537)
            if len(self.raw_requestline) > 65536:
                self.requestline = ''
                self.request_version = ''
                self.command = ''
                self.send_error(HTTPStatus.REQUEST_URI_TOO_LONG)
                return
            if not self.raw_requestline:
                self.close_connection = True
                return
            if not self.parse_request():
                # An error code has been sent, just exit
                return
            mname = 'do_' + self.command
            if not hasattr(self, mname):
                self.send_error(
                    HTTPStatus.NOT_IMPLEMENTED,
                    "Unsupported method (%r)" % self.command)
                return
            method = getattr(self, mname)
            method()
            self.wfile.flush() #actually send the response if not already done.
            self.rfile.flush()
        except TimeoutError as e:
            #a read or a write timed out.  Discard this connection
            ConsoleMessage.printError("Request timed out: %r"%e)
            self.close_connection = True
            return
        except ConnectionResetError as e:
            pass
        except ConnectionAbortedError as e:
            pass

    def handle(self) -> None:
        """Handle multiple requests if necessary."""
        self.close_connection = True

        self.handle_one_request()
        while not self.close_connection:
            self.handle_one_request()

    def send_error(self, code, message=None, explain=None) -> None:
        try:
            shortmsg, longmsg = self.responses[code]
        except KeyError:
            shortmsg, longmsg = '???', '???'
        if message is None:
            message = shortmsg
        if explain is None:
            explain = longmsg
        self.send_response(code, message)
        self.send_header('Connection', 'close')

        # Message body is omitted for cases described in:
        #  - RFC7230: 3.3. 1xx, 204(No Content), 304(Not Modified)
        #  - RFC7231: 6.3.6. 205(Reset Content)
        body = None
        if (code >= 200 and
            code not in (HTTPStatus.NO_CONTENT,
                         HTTPStatus.RESET_CONTENT,
                         HTTPStatus.NOT_MODIFIED)):
            # HTML encode to prevent Cross Site Scripting attacks
            # (see bug #1100201)
            content = (self.error_message_format % {
                'code': code,
                'message': html.escape(message, quote=False),
                'explain': html.escape(explain, quote=False)
            })
            body = content.encode('UTF-8', 'replace')
            self.send_header("Content-Type", self.error_content_type)
            self.send_header('Content-Length', str(len(body)))
        self.end_headers()

        if self.command != 'HEAD' and body:
            self.wfile.write(body)

    def send_response(self, code, message=None) -> None:
        if isinstance(code, HTTPStatus):
            code = code.value
        log = "%s >> %s | %d  %s"%(self.address_string(),self.requestline,code, message)
        if code >= 500:
            ConsoleMessage.printError(log)
        elif code >= 400:
            ConsoleMessage.printWarning(log)
        else:
            ConsoleMessage.printMsg(log)
        self.send_response_only(code, message)
        self.send_header('Server', self.version_string())
        self.send_header('Date', self.date_time_string())

    def send_response_only(self, code, message=None) -> None:
        """Send the response header only."""
        if self.request_version != 'HTTP/0.9':
            if message is None:
                if code in self.responses:
                    message = self.responses[code][0]
                else:
                    message = ''
            if not hasattr(self, '_headers_buffer'):
                self._headers_buffer = []
            self._headers_buffer.append(("%s %d %s\r\n" %
                    (self.protocol_version, code, message)).encode(
                        'latin-1', 'strict'))

    def send_header(self, keyword, value) -> None:
        """Send a MIME header to the headers buffer."""
        if self.request_version != 'HTTP/0.9':
            if not hasattr(self, '_headers_buffer'):
                self._headers_buffer = []
            self._headers_buffer.append(
                ("%s: %s\r\n" % (keyword, value)).encode('latin-1', 'strict'))

        if keyword.lower() == 'connection':
            if value.lower() == 'close':
                self.close_connection = True
            elif value.lower() == 'keep-alive':
                self.close_connection = False

    def end_headers(self) -> None:
        """Send the blank line ending the MIME headers."""
        if self.request_version != 'HTTP/0.9':
            self._headers_buffer.append(b"\r\n")
            self.flush_headers()

    def flush_headers(self) -> None:
        if hasattr(self, '_headers_buffer'):
            self.wfile.write(b"".join(self._headers_buffer))
            self._headers_buffer = []

    def version_string(self):
        """Return the server software version string."""
        return self.server_version + ' ' + self.sys_version

    def address_string(self):
        """Return the client address."""
        return self.client_address[0]

    # Essentially static class variables

    # The version of the HTTP protocol we support.
    # Set this to HTTP/1.1 to enable automatic keepalive
    protocol_version = "HTTP/1.1"

    # MessageClass used to parse headers
    MessageClass = http.client.HTTPMessage

    # hack to maintain backwards compatibility
    responses = {
        v: (v.phrase, v.description)
        for v in HTTPStatus.__members__.values()
    }