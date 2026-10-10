#version 430 core

// 格式标记
const int POS = 1;
const int COL = 2;
const int TEX = 4;
const int NOR = 8;
const int LIT = 16;
const int SIZ = 32;
// 2D 光照的材质开关位（Phase 7）：与 pyglgame/render/BufferBuilder.py 一一对应，
// 只影响片元着色器（不产生新顶点属性）：N2D -> tex3 是 2D 法线贴图，
// S2D -> tex4 是 2D 高光贴图。见 docs/2D光照与阴影.md
const int N2D = 64;
const int S2D = 128;
// 2D 光照的"整批开关"（与 pyglgame/render/BufferBuilder.py 的 AMB2D / UNLIT2D 一一对应）
const int AMB2D = 256;    // 只吃环境光：直接光 / 高光 / 阴影整段跳过
const int UNLIT2D = 512;  // 完全不受光照：环境光也不加，只输出颜色 / 贴图

const int DIS = 0;
const int WID = 1;
const int HDR = 2;
const int PIN = 3;
const int BLM = 4;
const int MIX = 5;
const int OIT = 6;        // WBOIT：世界空间半透明（走 dis 的矩阵变换）
const int OIT_WID = 7;    // WBOIT：屏幕空间半透明（走 wid）
const int SHADOW = 8;     // 阴影贴图 pass：只做 光空间 * 模型矩阵 变换，只写深度
const int SHADOW2D = 9;   // 2D 阴影遮罩 pass：与 DIS 相同的世界空间变换（见 ShadowMask2D.py）
const int OBJECT_ID = 10; // Object ID / Mask pass：与 DIS 相同的世界空间变换（见 ObjectIdBuffer.py）
const int OBJECT_ID_DEBUG = 11; // Object ID 调试显示：全屏 NDC 直通（与 WID 相同）
const int OBJECT_ID_SKIP = 12;  // ID pass 里的屏幕空间绘制（全屏背景 / UI）：全屏覆盖后丢弃
// 顶点属性
layout(location = 0) in vec3 aPos;
layout(location = 1) in vec4 aColor;
layout(location = 2) in vec2 aTexCoord;
layout(location = 3) in vec3 aNormal;
layout(location = 4) in vec2 aLightMap;
layout(location = 5) in float aSize;

// Uniforms
uniform int fuc;
uniform int formatType;
uniform mat4 scale;
uniform mat4 rotate;
uniform mat4 translate;
uniform mat4 view;
uniform mat4 projection;
uniform vec2 win_wh;
// 阴影 pass（fuc == SHADOW）用：世界空间 -> 光空间 的 view * projection
uniform mat4 u_lightSpaceMatrix;

// 输出
out vec4 fragColor;
out vec2 fragTexCoord;
out vec3 fragNormal;
out vec2 fragLightMap;
// 世界坐标：光照（点光/聚光的位置相关衰减、阴影贴图投影）需要它
out vec3 fragWorldPos;
flat out int fragFormatType;

void dis()
{
    // 模型变换先在 GPU 上算出来：光照需要世界坐标，所以这里保留中间结果
    vec4 world = translate * rotate * scale * vec4(aPos, 1.0);
    fragWorldPos = world.xyz;
    if ((formatType & POS) != 0) {
        gl_Position = projection * view * world;
    } else {
        gl_Position = vec4(0.0);
    }

    fragColor      = ((formatType & COL) != 0) ? aColor : vec4(0.0);
    fragTexCoord   = ((formatType & TEX) != 0) ? aTexCoord : vec2(0.0);
    fragNormal     = ((formatType & NOR) != 0) ? aNormal : vec3(0.0, 0.0, 1.0);
    fragLightMap   = ((formatType & LIT) != 0) ? aLightMap : vec2(1.0);
    fragFormatType = formatType;

    if ((formatType & SIZ) != 0) {
        gl_PointSize = aSize / max(gl_Position.z, 0.001);
    }
}

void shadow()
{
    // 阴影贴图 pass：只关心深度，片段着色器不写颜色（glColorMask 也关掉）
    vec4 world = translate * rotate * scale * vec4(aPos, 1.0);
    fragWorldPos = world.xyz;
    if ((formatType & POS) != 0) {
        gl_Position = u_lightSpaceMatrix * world;
    } else {
        gl_Position = vec4(0.0);
    }
    fragColor      = ((formatType & COL) != 0) ? aColor : vec4(0.0);
    fragTexCoord   = vec2(0.0);
    fragNormal     = ((formatType & NOR) != 0) ? aNormal : vec3(0.0, 0.0, 1.0);
    fragLightMap   = vec2(1.0);
    fragFormatType = formatType;
}

void wid()
{
    // 用于屏幕空间绘制，例如后处理、GUI 等
    gl_Position    = vec4(aPos, 1.0); 
    fragColor      = ((formatType & COL) != 0) ? aColor : vec4(0.0);
    fragTexCoord   = ((formatType & TEX) != 0) ? aTexCoord : vec2(0.0);
    fragFormatType = formatType;
}

void bloom(){
    gl_Position    = vec4(aPos, 1.0); 
    fragColor      = ((formatType & COL) != 0) ? aColor : vec4(0.0);
    fragTexCoord   = ((formatType & TEX) != 0) ? aTexCoord : vec2(0.0);
    fragFormatType = formatType;
}

void main()
{
    if (fuc == DIS) {
        dis();
    } else if (fuc == WID) {
        wid();
    } else if (fuc == HDR) {
        wid();
    } else if (fuc == PIN) {
        wid();
    }else if (fuc == BLM) {
        wid();
    }else if (fuc == MIX) {
        wid();
    }else if (fuc == OIT) {
        // WBOIT 半透明：与 DIS 相同的世界空间变换（顶点属性布局一致）
        dis();
    }else if (fuc == OIT_WID) {
        wid();
    }else if (fuc == SHADOW) {
        shadow();
    }else if (fuc == SHADOW2D) {
        // 2D 阴影遮罩：顶点已经是世界坐标（模型矩阵复位成单位阵），走与 DIS 一样的变换
        dis();
    }else if (fuc == OBJECT_ID) {
        // Object ID / Mask pass：记录屏幕像素对应的物体 ID，走与 DIS 一样的变换
        // （深度测试保证重叠物体只留下当前摄像机看到的最前面的那个）
        dis();
    }else if (fuc == OBJECT_ID_DEBUG) {
        // 把 ID 纹理画到屏幕：全屏 NDC 直通，和 WID 一样
        wid();
    }else if (fuc == OBJECT_ID_SKIP) {
        // ID pass 里的屏幕空间绘制（全屏背景 / UI）：顶点按 NDC 直通铺满屏幕，
        // 片元在 frag 里直接 discard —— 既不写 ID 也不写深度
        wid();
    }else  {
        // 未来扩展：其他绘制函数
        gl_Position = vec4(0.0); 
    }
}
