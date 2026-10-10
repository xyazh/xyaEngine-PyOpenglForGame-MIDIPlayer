#version 430 core

// 体积渲染（雾 / 体积光）全屏 pass：与引擎的全屏四边形（POS|TEX）顶点布局一致
layout(location = 0) in vec3 aPos;
layout(location = 2) in vec2 aTexCoord;

out vec2 vUV;

void main()
{
    vUV = aTexCoord;
    gl_Position = vec4(aPos, 1.0);
}
