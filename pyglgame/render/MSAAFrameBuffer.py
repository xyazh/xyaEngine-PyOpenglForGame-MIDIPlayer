from OpenGL.GL import *
from ..RenderGlobal import RenderGlobal, backgroundColor
from ..render.RenderBuffer import RenderBuffer
from ..render import RenderState
from ..tools.glDebug import clampSamples, currentContext, isSameContext
from ..xyazhServer.ConsoleMessage import ConsoleMessage


class MSAAFrameBuffer:
    def __init__(self, width: int, height: int, use_depth=False, param: int = GL_NEAREST,
                 data: list = None, samples: int = 0,
                 internal_format: int = GL_RGBA16F, depth_resolve: bool = False) -> None:
        self.width = width
        self.height = height
        self.render_global = RenderGlobal.instance
        self.size = self.render_global.window.size
        self.use_depth = use_depth
        self.param = param
        self.internal_format = internal_format
        self.depth_resolve = depth_resolve
        # 请求的采样数可能超过硬件上限（例如 samples=32 + RGBA32F），必须夹取，
        # 否则帧缓冲不完整，构造时就会抛 RuntimeError。
        self.requested_samples = samples
        self.samples = clampSamples(samples) if samples > 1 else samples
        if self.samples != samples:
            ConsoleMessage.printWarning(
                f"MSAA 采样数 {samples} 超出设备上限，已调整为 {self.samples}")
        self.is_dead = False
        self.depth_is_renderbuffer = self.samples > 0
        self._context = currentContext()
        # 本帧是否已经提前解析过（WBOIT 会在不透明 pass 后立刻解析颜色/深度）
        self._color_resolved = False
        self._depth_resolved = False

        self.framebuffer = 0
        self.texture_id = 0
        self.depth_texture_id = 0
        self.resolve_fbo = 0
        self.resolve_texture = 0
        self.depth_resolve_fbo = 0
        self.resolve_depth_texture = 0

        # 多重采样处理
        if samples > 0:
            # 创建多重采样帧缓冲
            self.framebuffer = glGenFramebuffers(1)
            glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)

            # 创建多重采样颜色缓冲
            self.texture_id = glGenTextures(1)
            if self.samples > 1:
                glBindTexture(GL_TEXTURE_2D_MULTISAMPLE, self.texture_id)
                # 注意：多重采样纹理不能设置 WRAP/FILTER，且必须用
                # GL_TEXTURE_2D_MULTISAMPLE 作为 target，否则会误改到别的纹理对象
                glTexImage2DMultisample(
                    GL_TEXTURE_2D_MULTISAMPLE, self.samples, self.internal_format,
                    width, height, GL_TRUE
                )
                glFramebufferTexture2D(
                    GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                    GL_TEXTURE_2D_MULTISAMPLE, self.texture_id, 0
                )

            else:
                glBindTexture(GL_TEXTURE_2D, self.texture_id)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
                glTexImage2D(GL_TEXTURE_2D, 0, self.internal_format, width, height,
                             0, GL_RGBA, GL_FLOAT, data)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, param)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, param)
                glFramebufferTexture2D(
                    GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                    GL_TEXTURE_2D, self.texture_id, 0
                )

            # 创建多重采样深度/模板缓冲
            if use_depth:
                glEnable(GL_DEPTH_TEST)
                glEnable(GL_STENCIL_TEST)
                self.depth_texture_id = glGenRenderbuffers(1)
                glBindRenderbuffer(GL_RENDERBUFFER, self.depth_texture_id)
                if self.samples > 1:
                    glRenderbufferStorageMultisample(
                        GL_RENDERBUFFER, self.samples,
                        GL_DEPTH24_STENCIL8, width, height
                    )
                else:
                    glRenderbufferStorage(
                        GL_RENDERBUFFER,
                        GL_DEPTH24_STENCIL8, width, height
                    )
                glFramebufferRenderbuffer(
                    GL_FRAMEBUFFER, GL_DEPTH_STENCIL_ATTACHMENT,
                    GL_RENDERBUFFER, self.depth_texture_id
                )
        else:
            # 创建普通帧缓冲
            self.framebuffer = glGenFramebuffers(1)
            glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)

            # 创建颜色纹理附件
            self.texture_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self.texture_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexImage2D(GL_TEXTURE_2D, 0, self.internal_format, width, height,
                         0, GL_RGBA, GL_FLOAT, data)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, param)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, param)
            glFramebufferTexture2D(
                GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                GL_TEXTURE_2D, self.texture_id, 0
            )

            if use_depth:
                glEnable(GL_DEPTH_TEST)
                glEnable(GL_STENCIL_TEST)
                # 创建深度纹理附件
                self.depth_texture_id = glGenTextures(1)
                glBindTexture(GL_TEXTURE_2D, self.depth_texture_id)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
                glTexImage2D(GL_TEXTURE_2D, 0, GL_DEPTH_COMPONENT24,
                             width, height, 0, GL_DEPTH_COMPONENT, GL_FLOAT, None)
                glTexParameteri(
                    GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
                glTexParameteri(
                    GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
                glFramebufferTexture2D(
                    GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
                    GL_TEXTURE_2D, self.depth_texture_id, 0
                )

        # 检查帧缓冲完整性
        self._checkComplete("MSAA")

        glBindFramebuffer(GL_FRAMEBUFFER, 0)

        # 创建解析缓冲（用于多重采样）
        if samples > 1:
            self.resolve_fbo = glGenFramebuffers(1)
            glBindFramebuffer(GL_FRAMEBUFFER, self.resolve_fbo)

            self.resolve_texture = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self.resolve_texture)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexImage2D(
                GL_TEXTURE_2D, 0, self.internal_format,
                width, height, 0, GL_RGBA, GL_FLOAT, None
            )
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, param)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, param)
            glFramebufferTexture2D(
                GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0,
                GL_TEXTURE_2D, self.resolve_texture, 0
            )

            self._checkComplete("Resolve")

            glBindFramebuffer(GL_FRAMEBUFFER, 0)
        else:
            self.resolve_fbo = 0
            self.resolve_texture = 0
        
        if self.samples > 1 and self.use_depth and self.depth_resolve:
            # 创建解析深度 FBO 和纹理（只有显式要求 depth_resolve 时才创建，省显存）
            self.depth_resolve_fbo = glGenFramebuffers(1)
            glBindFramebuffer(GL_FRAMEBUFFER, self.depth_resolve_fbo)

            self.resolve_depth_texture = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self.resolve_depth_texture)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_DEPTH_COMPONENT24,
                        width, height, 0, GL_DEPTH_COMPONENT, GL_FLOAT, None)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST)
            glFramebufferTexture2D(
                GL_FRAMEBUFFER, GL_DEPTH_ATTACHMENT,
                GL_TEXTURE_2D, self.resolve_depth_texture, 0
            )

            self._checkComplete("Depth Resolve")

            glBindFramebuffer(GL_FRAMEBUFFER, 0)
        else:
            self.depth_resolve_fbo = 0
            self.resolve_depth_texture = 0

        # 初始化屏幕矩形
        self.render_buffer = RenderBuffer.getWindownRenderBuffer()

    def __del__(self) -> None:
        try:
            self.cleanup()
        except Exception:
            pass

    def bindTexture(self, uint=GL_TEXTURE0) -> None:
        """绑定纹理对象"""
        glActiveTexture(uint)
        if self.samples > 1:
            glBindTexture(GL_TEXTURE_2D, self.resolve_texture)
            self.render_global.using_shader.uniformTex(uint)
        else:
            glBindTexture(GL_TEXTURE_2D, self.texture_id)
            self.render_global.using_shader.uniformTex(uint)

    def getTexture(self) -> int:
        if self.samples > 1:
            return self.resolve_texture
        else:
            return self.texture_id

    @staticmethod
    def unBindTexture() -> None:
        glBindTexture(GL_TEXTURE_2D, 0)

    def bindDepthTexture(self, uint=GL_TEXTURE0) -> None:
        """绑定深度纹理对象"""
        glActiveTexture(uint)
        if self.samples > 1:
            if not self.resolve_depth_texture:
                ConsoleMessage.printWarning(
                    "MSAAFrameBuffer 未启用 depth_resolve，无法采样深度纹理")
                return
            glBindTexture(GL_TEXTURE_2D, self.resolve_depth_texture)
            self.render_global.using_shader.uniformTex(uint)
        else:
            glBindTexture(GL_TEXTURE_2D, self.depth_texture_id)
            self.render_global.using_shader.uniformTex(uint)


    def bind(self) -> None:
        """绑定帧缓冲对象"""
        glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)

    def getDepthTexture(self) -> int:
        """可采样的深度纹理（WBOIT 需要把它挂到自己的 FBO 上做深度测试）。

        - ``samples > 1``：需要 ``depth_resolve=True``（构造时会建解析 FBO/纹理），
          由 :meth:`resolveDepthNow` 填充
        - ``samples <= 1``：直接返回深度纹理
        """
        if self.samples > 1:
            return self.resolve_depth_texture
        return self.depth_texture_id

    def resolveColorNow(self) -> bool:
        """立刻把多重采样颜色解析到 resolve 纹理（WBOIT 需要在不透明 pass 之后就用它）。"""
        if self._color_resolved:
            return False
        self._color_resolved = True
        if self.samples > 1:
            glBindFramebuffer(GL_READ_FRAMEBUFFER, self.framebuffer)
            glBindFramebuffer(GL_DRAW_FRAMEBUFFER, self.resolve_fbo)
            glBlitFramebuffer(
                0, 0, self.width, self.height,
                0, 0, self.width, self.height,
                GL_COLOR_BUFFER_BIT, GL_NEAREST
            )
        return True

    def resolveDepthNow(self) -> bool:
        """把多重采样深度解析成可采样纹理（需构造时 depth_resolve=True）。

        注意：blit 会把 draw 绑定留在解析 FBO 上，所以结束后**必须**把绑定切回
        本帧缓冲 —— 否则调用方紧接着的 glClear/绘制会作用到解析 FBO（踩过这个坑：
        清深度时把解析纹理清成了 1.0，SSAO 直接失效）。
        """
        if self._depth_resolved:
            return False
        self._depth_resolved = True
        if self.samples > 1 and self.depth_resolve_fbo:
            glBindFramebuffer(GL_READ_FRAMEBUFFER, self.framebuffer)
            glBindFramebuffer(GL_DRAW_FRAMEBUFFER, self.depth_resolve_fbo)
            glBlitFramebuffer(
                0, 0, self.width, self.height,
                0, 0, self.width, self.height,
                GL_DEPTH_BUFFER_BIT, GL_NEAREST
            )
            glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
            glViewport(0, 0, self.width, self.height)
            return True
        return False

    def unbind(self) -> None:
        """解绑帧缓冲对象"""
        glBindFramebuffer(GL_FRAMEBUFFER, 0)

    def drawStart(self) -> None:
        """将当前帧缓冲对象绑定为渲染目标，并绘制到纹理"""
        self.bind()
        glViewport(0, 0, self.width, self.height)
        # 深度清除受 glDepthMask 影响！上一帧的半透明 pass（WBOIT / A 方案）会
        # 把深度写入关掉，如果不在这里强制打开，glClear 就清不掉深度缓冲：
        # 上一帧的深度残留下来，同深度的不透明物体在下一帧会被 GL_LESS 剔除，
        # 表现为"不透明物体不再遮挡任何东西"。
        glDepthMask(GL_TRUE)
        # 天空色 = RenderGlobal.bg（alpha 仍为 0，见 RenderGlobal.backgroundColor）
        glClearColor(*backgroundColor())
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT |
                GL_STENCIL_BUFFER_BIT)
        # 掩码被直接改过，帧内第一次 setDefaults() 必须真的下发 GL 调用
        RenderState.invalidate()
        self._color_resolved = False
        self._depth_resolved = False
        if self.use_depth:
            glEnable(GL_DEPTH_TEST)
            glEnable(GL_STENCIL_TEST)

    def draw(self, fuc, *args, **kw) -> None:
        """将当前帧缓冲对象绑定为渲染目标，并绘制到纹理"""
        self.drawStart()
        fuc(*args, **kw)
        self.drawEnd()

    def drawEnd(self) -> None:
        """将当前帧缓冲对象解绑"""
        if self.samples > 1:
            if not self._color_resolved:
                # Blit color
                glBindFramebuffer(GL_READ_FRAMEBUFFER, self.framebuffer)
                glBindFramebuffer(GL_DRAW_FRAMEBUFFER, self.resolve_fbo)
                glBlitFramebuffer(
                    0, 0, self.width, self.height,
                    0, 0, self.width, self.height,
                    GL_COLOR_BUFFER_BIT, GL_NEAREST
                )
            self._color_resolved = True

            # Blit depth
            if (self.use_depth and self.depth_resolve_fbo
                    and not self._depth_resolved):
                glBindFramebuffer(GL_READ_FRAMEBUFFER, self.framebuffer)
                glBindFramebuffer(GL_DRAW_FRAMEBUFFER, self.depth_resolve_fbo)
                glBlitFramebuffer(
                    0, 0, self.width, self.height,
                    0, 0, self.width, self.height,
                    GL_DEPTH_BUFFER_BIT, GL_NEAREST
                )
            self._depth_resolved = True

        self.unbind()
        glViewport(0, 0, self.size.w, self.size.h)

    def _checkComplete(self, tag: str) -> None:
        """检查当前绑定的帧缓冲是否完整；不完整时给出足够定位问题的信息。"""
        status = glCheckFramebufferStatus(GL_FRAMEBUFFER)
        if status == GL_FRAMEBUFFER_COMPLETE:
            return
        raise RuntimeError(
            f"{tag} 帧缓冲不完整 (status=0x{status:x})："
            f"samples={self.samples}(请求 {self.requested_samples}), "
            f"internal_format=0x{self.internal_format:x}, depth={self.use_depth}, "
            f"size={self.width}x{self.height}")

    def resize(self, width: int, height: int) -> None:
        """窗口尺寸变化时重建（纹理尺寸、解析缓冲都要跟着变）。"""
        if int(width) == self.width and int(height) == self.height:
            return
        kwargs = dict(use_depth=self.use_depth, param=self.param,
                      samples=self.requested_samples,
                      internal_format=self.internal_format,
                      depth_resolve=self.depth_resolve)
        self.cleanup()
        self.is_dead = False
        self.__init__(width, height, **kwargs)

    def cleanup(self) -> None:
        """清理帧缓冲对象及其资源"""
        if self.is_dead:
            return
        self.is_dead = True
        if not isSameContext(self._context):
            # 不在创建它的上下文里（GC/别的线程）：交给上下文销毁时回收
            self.framebuffer = self.texture_id = self.depth_texture_id = 0
            self.resolve_fbo = self.resolve_texture = 0
            self.depth_resolve_fbo = self.resolve_depth_texture = 0
            return
        glBindFramebuffer(GL_FRAMEBUFFER, 0)
        glBindTexture(GL_TEXTURE_2D, 0)
        if self.framebuffer != 0:
            glDeleteFramebuffers(1, [self.framebuffer])
            self.framebuffer = 0
        if self.texture_id != 0:
            glDeleteTextures(1, [self.texture_id])
            self.texture_id = 0
        if self.depth_texture_id != 0:
            # samples>0 时它是 renderbuffer，samples==0 时才是纹理（原实现一律
            # 当成纹理删除，会泄漏 renderbuffer 并误删同名纹理）
            if self.depth_is_renderbuffer:
                glDeleteRenderbuffers(1, [self.depth_texture_id])
            else:
                glDeleteTextures(1, [self.depth_texture_id])
            self.depth_texture_id = 0
        if self.resolve_fbo != 0:
            glDeleteFramebuffers(1, [self.resolve_fbo])
            self.resolve_fbo = 0
        if self.resolve_texture != 0:
            glDeleteTextures(1, [self.resolve_texture])
            self.resolve_texture = 0
        if self.depth_resolve_fbo != 0:
            glDeleteFramebuffers(1, [self.depth_resolve_fbo])
            self.depth_resolve_fbo = 0
        if self.resolve_depth_texture != 0:
            glDeleteTextures(1, [self.resolve_depth_texture])
            self.resolve_depth_texture = 0

    def drawToWindow(self, fuc: int = 1,use_depth: bool = False) -> None:
        """将当前帧缓冲对象绘制到窗口"""
        if self.is_dead:
            return
        if use_depth:
            self.bindDepthTexture()
        else:
            self.bindTexture()
        RenderGlobal.instance.using_shader.uniform1i("fuc", fuc)
        self.render_buffer.draw(False)
