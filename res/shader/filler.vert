#version 430 core

// 滤镜族（pyglgame/filler）共用的全屏 pass 顶点着色器。
//
// 顶点布局必须和引擎的全屏四边形一致（RenderBuffer.getWindowRenderBuffer，
// 格式 POS|TEX）：location 0 = vec3 aPos（已经是 NDC），location 2 = vec2 aTexCoord。
// 中间空出来的编号是 COL/NOR/LIT（BufferBuilder 里属性顺序固定），不能改。
// 所有滤镜的 fragment shader 都用 filler/<名字>.frag，这里不重复写同一份顶点代码。
layout(location = 0) in vec3 aPos;
layout(location = 2) in vec2 aTexCoord;

out vec2 vUV;

void main()
{
    vUV = aTexCoord;
    gl_Position = vec4(aPos, 1.0);
}
