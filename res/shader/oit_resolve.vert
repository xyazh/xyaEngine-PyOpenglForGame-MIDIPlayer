#version 430 core
// WBOIT 合成用的全屏四边形（顶点布局与 POS|TEX 一致：loc0=vec3 位置, loc2=vec2 UV）
layout(location = 0) in vec3 aPos;
layout(location = 2) in vec2 aTexCoord;

out vec2 vUV;

void main()
{
    vUV = aTexCoord;
    gl_Position = vec4(aPos, 1.0);
}
