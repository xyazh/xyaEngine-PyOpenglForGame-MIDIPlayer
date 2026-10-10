#version 330 core

const int POS = 1;
const int COL = 2;
const int TEX = 4;
const int SIZ = 32;

layout(location = 0) in vec3 aPos;       // 位置
layout(location = 1) in vec4 aColor;     // 颜色
layout(location = 2) in vec2 aTexCoord;  // UV
layout(location = 3) in vec3 aNormal;    // 法线
layout(location = 4) in vec2 aLightMap;  // 光照贴图
layout(location = 5) in float aSize;     // 点尺寸

uniform int formatType;  // 控制布局的类型
uniform mat4 scale;
uniform mat4 rotate;
uniform mat4 translate;
uniform mat4 view;
uniform mat4 projection;

out vec2 fragTexCoord;
flat out int fragFormatType;


void main()
{
    if ((formatType & POS) != 0) {
        gl_Position = projection * (view * (translate * (rotate * (scale * vec4(aPos, 1.0)))));
    }

    if ((formatType & SIZ) != 0) {
        gl_PointSize = aSize / gl_Position.z;
    }

    if ((formatType & TEX) != 0) {
        fragTexCoord = aTexCoord;
    }
    fragFormatType = formatType;
}
