import ctypes
import time
import  sys

STD_INPUT_HANDLE = -10
STD_OUTPUT_HANDLE = -11
STD_ERROR_HANDLE = -12

# 字体颜色定义 text colors
FOREGROUND_BLUE = 0x09 # blue.
FOREGROUND_GREEN = 0x0a # green.
FOREGROUND_RED = 0x0c # red.
FOREGROUND_YELLOW = 0x0e # yellow.


class ConsoleMessage:
    DEBUG_LEVEL = 4
    def getTime():
        return time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(time.time()))

    def printWarning(msg) -> None:
        if ConsoleMessage.DEBUG_LEVEL < 2:
            return
        msg = "[%s][WARNING]:%s"%(ConsoleMessage.getTime(),str(msg))
        ConsoleMessage.P.printYellow(bytes(msg,encoding="utf8"))

    def printError(msg) -> None:
        if ConsoleMessage.DEBUG_LEVEL < 1:
            return
        msg = "[%s][ERROR]:%s"%(ConsoleMessage.getTime(),str(msg))
        ConsoleMessage.P.printRed(bytes(str(msg),encoding="utf8"))

    def printMsg(msg) -> None:
        if ConsoleMessage.DEBUG_LEVEL < 3:
            return
        msg = "[%s][INFO]:%s"%(ConsoleMessage.getTime(),str(msg))
        print(msg)

    def printC(msg) -> None:
        msg = "[%s][MSG]:%s"%(ConsoleMessage.getTime(),str(msg))
        print(msg)

    def printDebug(msg) -> None:
        if ConsoleMessage.DEBUG_LEVEL < 4:
            return
        msg = "[%s][DEBUG]:%s"%(ConsoleMessage.getTime(),str(msg))
        ConsoleMessage.P.printBlue(bytes(str(msg),encoding="utf8"))

    class P:
        std_out_handle = ctypes.windll.kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        @staticmethod
        def set_cmd_text_color(color):
            Bool = ctypes.windll.kernel32.SetConsoleTextAttribute(ConsoleMessage.P.std_out_handle, color)
            return Bool

        @staticmethod
        def resetColor() -> None:
            ConsoleMessage.P.set_cmd_text_color(FOREGROUND_RED | FOREGROUND_GREEN | FOREGROUND_BLUE)

        @staticmethod
        def printGreen(mess) -> None:
            mess = str(mess,encoding="utf8")
            ConsoleMessage.P.set_cmd_text_color(FOREGROUND_GREEN)
            sys.stdout.write(mess + '\n')
            ConsoleMessage.P.resetColor()

        @staticmethod
        def printRed(mess) -> None:
            mess = str(mess,encoding="utf8")
            ConsoleMessage.P.set_cmd_text_color(FOREGROUND_RED)
            sys.stdout.write(mess + '\n')
            ConsoleMessage.P.resetColor()

        @staticmethod
        def printYellow(mess) -> None:
            mess = str(mess,encoding="utf8")
            ConsoleMessage.P.set_cmd_text_color(FOREGROUND_YELLOW)
            sys.stdout.write(mess + '\n')
            ConsoleMessage.P.resetColor()
        
        @staticmethod
        def printBlue(mess) -> None:
            mess = str(mess,encoding="utf8")
            ConsoleMessage.P.set_cmd_text_color(FOREGROUND_BLUE)
            sys.stdout.write(mess + '\n')
            ConsoleMessage.P.resetColor()