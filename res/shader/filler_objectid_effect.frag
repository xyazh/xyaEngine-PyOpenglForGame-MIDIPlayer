#version 430 core

/* Object ID / Mask 后处理（Game15 的测试滤镜 res/shader/filler_objectid_effect.frag）
 *
 * tex0        = 相机颜色（滤镜链的输入，filler.vert 的 vUV 采样）
 * tex1        = Object ID / Mask 纹理（GL_R32F，每像素一个物体 ID；0 = 背景）
 * u_texel     = 1 / 输入尺寸（FillerBase.USES_TEXEL 上传）
 * u_objectId  = 要作用的物体 ID（<= 0 = 不作用）
 * u_effectMode= 0 关 / 1 马赛克 / 2 描边
 *
 * 只对"ID 命中"的像素做处理，其它像素原样输出 —— 这就是"ID 当 Mask 用"。
 */

in vec2 vUV;
out vec4 outColor;

uniform sampler2D tex0;
uniform sampler2D tex1;
uniform vec2  u_texel;
uniform int   u_objectId;
uniform int   u_effectMode;

bool hitId(vec2 uv)
{
    if (u_objectId <= 0) {
        return false;
    }
    return abs(texture(tex1, uv).r - float(u_objectId)) < 0.5;
}

void main()
{
    vec4 color = texture(tex0, vUV);

    if (u_effectMode == 1 && hitId(vUV)) {
        // 马赛克：命中 ID 的像素按 BLOCK x BLOCK 的块取样（块内颜色一致）
        const float BLOCK = 8.0;
        vec2 block = u_texel * BLOCK;
        vec2 uv = (floor(vUV / block) + 0.5) * block;
        color = texture(tex0, uv);
    } else if (u_effectMode == 2 && hitId(vUV)) {
        // 描边：命中像素的 3x3 邻域里一旦出现"非命中"（含背景）就画边
        float edge = 0.0;
        for (int i = -1; i <= 1; ++i) {
            for (int j = -1; j <= 1; ++j) {
                vec2 uv = vUV + vec2(float(i), float(j)) * u_texel;
                if (!hitId(uv)) {
                    edge = 1.0;
                }
            }
        }
        if (edge > 0.5) {
            color = vec4(1.0, 0.85, 0.15, 1.0);
        }
    }

    outColor = color;
}
