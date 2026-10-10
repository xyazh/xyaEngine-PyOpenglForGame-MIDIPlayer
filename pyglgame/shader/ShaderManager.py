import re
from OpenGL.GL import *
from OpenGL.GL.shaders import compileShader, compileProgram
from .Shader import Shader
from .. import resources
from ..ResourceLocation import ResourceLocation
from ..xyazhServer.ConsoleMessage import ConsoleMessage

# ``#include "noise.glsl"`` 形式的源码内联（一行一条，必须单独占行）。
# GLSL 没有标准的 include，但公共函数（噪声库之类）复制多份必然慢慢跑偏，
# 所以在**编译前**由 Python 把文件内容替换进来。
# ``\r?``：Windows 上是 CRLF，不能让它挡住行尾匹配（踩过一次：include 静默失效，
# 编译器直接报 "OpenGL does not allow #include directives"）。
INCLUDE_PATTERN = re.compile(r'^[ \t]*#include[ \t]+"([^"]+)"[ \t]*\r?$',
                             re.MULTILINE)

#: 引擎 GLSL 所在目录的**逻辑路径**（目录形态 / 资源包形态都吃，见 pyglgame.resources）
SHADER_DIR = "res/shader"


def shaderPath(name: str) -> str:
    """把"裸名字"补成 ``res/shader/<名字>``；已经带目录的路径原样返回。

    于是调用方只写 ``loadShader("dis")`` / ``loadComputeShader("computeBloom")``，
    "shader 放在哪"只剩这一处知道 —— 以前是每个调用点各自
    ``res_path("shader", …)``，换打包/换目录就要全改一遍。
    """
    name = str(name)
    if "/" in name or "\\" in name:
        return name
    return "%s/%s" % (SHADER_DIR, name)


class ShaderManager:
    """着色器加载器。

    编译/链接失败会直接抛 :class:`RuntimeError`（fail-fast）。
    原先失败时返回 ``Shader(0)``，之后每一帧都在错误状态下静默渲染黑屏，
    排查成本极高。
    """

    @staticmethod
    def loadShader(file: str) -> Shader:
        """加载 ``<file>.vert`` + ``<file>.frag``（同名一对）。

        ``file`` 可以是裸名字（``"dis"`` → ``res/shader/dis.vert|frag``），
        也可以是完整路径（``"res/shader/dis"`` / 绝对路径）。
        """
        return ShaderManager.loadShaderFromFiles(f"{file}.vert", f"{file}.frag")

    @staticmethod
    def loadShaderFromFiles(vertex_file: str, fragment_file: str) -> Shader:
        """由**任意**顶点 / 片元文件组合出程序。

        给"一族 shader 共用同一个顶点着色器"用：``loadShader`` 要求两个文件名
        同基名，照那个约定就得把同一份 12 行全屏顶点代码复制 N 份
        （见 ``res/shader/filler.vert`` 与 pyglgame/filler 下的各滤镜）。
        """
        program = 0
        vertex_shader = 0
        fragment_shader = 0
        # 裸名字 → res/shader/<名字>；已经是路径的原样（见 shaderPath）
        vertex_file = shaderPath(vertex_file)
        fragment_file = shaderPath(fragment_file)
        try:
            vertex_shader_source = ShaderManager.readFile(vertex_file)
            fragment_shader_source = ShaderManager.readFile(fragment_file)

            program = glCreateProgram()
            vertex_shader = glCreateShader(GL_VERTEX_SHADER)
            glShaderSource(vertex_shader, vertex_shader_source)
            glCompileShader(vertex_shader)
            ShaderManager._checkShader(vertex_shader, vertex_file)

            fragment_shader = glCreateShader(GL_FRAGMENT_SHADER)
            glShaderSource(fragment_shader, fragment_shader_source)
            glCompileShader(fragment_shader)
            ShaderManager._checkShader(fragment_shader, fragment_file)

            glAttachShader(program, vertex_shader)
            glAttachShader(program, fragment_shader)
            glLinkProgram(program)
            ShaderManager._checkProgram(program, f"{vertex_file}+{fragment_file}")
            return Shader(program)
        except BaseException:
            if program:
                glDeleteProgram(program)
            raise
        finally:
            if vertex_shader:
                glDeleteShader(vertex_shader)
            if fragment_shader:
                glDeleteShader(fragment_shader)

    @staticmethod
    def loadComputeShader(file: str) -> Shader:
        program = 0
        compute_shader = 0
        try:
            compute_shader_source = ShaderManager.readFile(
                shaderPath(f"{file}.comp"))
            program = glCreateProgram()
            compute_shader = glCreateShader(GL_COMPUTE_SHADER)
            glShaderSource(compute_shader, compute_shader_source)
            glCompileShader(compute_shader)
            ShaderManager._checkShader(compute_shader, f"{file}.comp")

            glAttachShader(program, compute_shader)
            glLinkProgram(program)
            ShaderManager._checkProgram(program, file)
            return Shader(program)
        except BaseException:
            if program:
                glDeleteProgram(program)
            raise
        finally:
            if compute_shader:
                glDeleteShader(compute_shader)

    @staticmethod
    def readFile(file):
        resource = ResourceLocation(file)
        data = resource.toBytes()
        if not data:
            raise FileNotFoundError(f"着色器文件读取失败（文件不存在或为空）: {file}")
        if b"#include" not in data:
            return data
        # 只有真的用到 include 才解码/再编码：没用的着色器保持逐字节原样
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise RuntimeError(f"着色器 {file} 含 #include，但不是 UTF-8 文本: {error}")
        key = resources.normalizeEntry(file)
        return ShaderManager._resolveIncludes(text, resources.dirnamePath(file),
                                             (key,)).encode("utf-8")

    @staticmethod
    def _resolveIncludes(text: str, base_dir: str, stack: tuple) -> str:
        """把 ``#include "xxx.glsl"`` 替换成文件内容（相对当前文件所在目录解析）。

        - 支持嵌套 include，路径相对**包含它的那个文件**（与 C 一致）
        - 循环 include 直接报错，而不是无限递归到 RecursionError
        """
        def replace(match: "re.Match") -> str:
            name = match.group(1)
            path = resources.joinPath(base_dir, name)
            key = resources.normalizeEntry(path)
            if key in stack:
                raise RuntimeError(f"着色器 #include 出现循环: {name}")
            data = ResourceLocation(path).toBytes()
            if not data:
                raise FileNotFoundError(
                    f"着色器 #include 找不到文件: {name}（解析为 {path}）")
            inner = data.decode("utf-8")
            return ShaderManager._resolveIncludes(inner, resources.dirnamePath(path),
                                                 stack + (key,))

        return INCLUDE_PATTERN.sub(replace, text)

    @staticmethod
    def _checkShader(shader: int, name: str) -> None:
        if glGetShaderiv(shader, GL_COMPILE_STATUS) != GL_FALSE:
            return
        log = glGetShaderInfoLog(shader)
        text = log.decode("utf-8", "replace") if isinstance(log, bytes) else str(log)
        ConsoleMessage.printError(text)
        raise RuntimeError(f"着色器编译失败: {name}\n{text}")

    @staticmethod
    def _checkProgram(program: int, name: str) -> None:
        if glGetProgramiv(program, GL_LINK_STATUS) != GL_FALSE:
            return
        log = glGetProgramInfoLog(program)
        text = log.decode("utf-8", "replace") if isinstance(log, bytes) else str(log)
        ConsoleMessage.printError(text)
        raise RuntimeError(f"着色器链接失败: {name}\n{text}")

