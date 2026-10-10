#version 430 core

/* Camera Raw 风格的整体显影（把"面板上那一排滑块"打包成一个滤镜）。
 *
 * 处理顺序刻意和 Camera Raw 的面板一致（每一步都建立在上一步的输出上）：
 *   曝光 → 白平衡(色温/色调) → 高光/阴影/白色/黑色 → 对比度
 *   → 清晰度/锐化 → 鲜艳度 → 饱和度 → 暗角 → 颗粒
 *
 * 几点说明：
 *   - 输入按**线性 HDR**处理：曝光用 2^EV（物理正确），其余滑块是观感向的
 *     归一化参数，范围 [-1, 1]（0 = 不动）。HDR 值 > 1 也能正确处理，
 *     最后写进 LDR 目标纹理时由 GL 负责 clamp。
 *   - 清晰度与锐化**共用同一张低通图** tex1（省一趟高斯模糊）：
 *     u_hasLowpass == 0 时这一段整段跳过，所以只用曝光/颜色类滑块时
 *     根本不需要额外的模糊纹理（CameraRawFilter 是按需生成的）。
 *   - 颗粒用屏幕空间 hash（和 ssao.frag 同一个 hash12）。
 */

in vec2 vUV;
out vec4 outColor;

uniform sampler2D tex0;
uniform sampler2D tex1;        // 低通（清晰度/锐化用）
uniform int   u_hasLowpass;
uniform vec2  u_texel;
uniform float u_mix;

uniform float u_exposure;      // EV（2^EV 倍）
uniform float u_contrast;      // 对比度
uniform float u_highlights;    // 高光：>0 提亮、<0 压暗
uniform float u_shadows;       // 阴影：>0 提亮、<0 压暗
uniform float u_whites;        // 白色阶
uniform float u_blacks;        // 黑色阶
uniform float u_temperature;   // 色温：>0 偏暖（红↑蓝↓）
uniform float u_tint;          // 色调：>0 偏洋红、<0 偏绿
uniform float u_vibrance;      // 鲜艳度（低饱和的颜色多提一点）
uniform float u_saturation;    // 饱和度
uniform float u_clarity;       // 清晰度（中间调的局部对比）
uniform float u_sharpen;       // 锐化（全图的局部对比）
uniform float u_vignette;      // 暗角：>0 压暗四周
uniform float u_grain;         // 颗粒

const vec3 LUMA709 = vec3(0.2126, 0.7152, 0.0722);

float hash12(vec2 p)
{
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

void main()
{
    vec4 src = texture(tex0, vUV);
    vec3 color = src.rgb;

    // 1) 曝光
    color *= exp2(clamp(u_exposure, -8.0, 8.0));

    // 2) 白平衡（归一化色温/色调 → 通道增益）
    color.r *= 1.0 + 0.40 * u_temperature + 0.15 * u_tint;
    color.g *= 1.0 - 0.30 * u_tint;
    color.b *= 1.0 - 0.40 * u_temperature + 0.15 * u_tint;

    // 3) 影调：按亮度分区加权（HDR 下取 max(0) 免得负值参与判断）。
    //    阴影/黑色用 "1 - smoothstep(0, 上界, luma)" 而不是 smoothstep(0.5, 0.0, ...)：
    //    后者给的是 edge0 > edge1，GLSL 规范里属于未定义行为（虽有驱动能跑）。
    float luma = dot(max(color, vec3(0.0)), LUMA709);
    color *= 1.0 + u_highlights * smoothstep(0.5, 1.0, luma) * 0.8;
    color *= 1.0 + u_shadows * (1.0 - smoothstep(0.0, 0.5, luma)) * 0.8;
    color += u_whites * smoothstep(0.7, 1.0, luma) * 0.25;
    color += u_blacks * (1.0 - smoothstep(0.0, 0.3, luma)) * 0.25;

    // 4) 对比度（绕 0.5 展开）
    color = (color - vec3(0.5)) * (1.0 + u_contrast) + vec3(0.5);

    // 5) 清晰度 / 锐化（局部对比；清晰度只作用在中间调上）
    if (u_hasLowpass == 1) {
        vec3 detail = color - texture(tex1, vUV).rgb;
        float midtone = 1.0 - abs(2.0 * clamp(dot(color, LUMA709), 0.0, 1.0) - 1.0);
        color += detail * (u_clarity * 0.8 * midtone + u_sharpen);
    }

    // 6) 鲜艳度 / 饱和度
    float maxChannel = max(color.r, max(color.g, color.b));
    float minChannel = min(color.r, min(color.g, color.b));
    float saturation = (maxChannel - minChannel) / max(maxChannel, 1e-4);
    vec3 gray = vec3(dot(color, LUMA709));
    color = mix(gray, color,
                max(1.0 + u_vibrance * (1.0 - saturation) + u_saturation, 0.0));

    // 7) 暗角（uv 到画面中心的距离，1.414 ≈ 角落——所以角落最先变暗）
    float radial = distance(vUV, vec2(0.5)) * 1.41421;
    color *= 1.0 - u_vignette * smoothstep(0.35, 1.0, radial);

    // 8) 颗粒（vUV / u_texel = 像素坐标 → hash 在屏幕空间上是稳定的）
    color += (hash12(vUV / u_texel) - 0.5) * u_grain * 0.2;

    outColor = vec4(mix(src.rgb, max(color, vec3(0.0)), clamp(u_mix, 0.0, 1.0)),
                    src.a);
}
