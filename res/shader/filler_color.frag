#version 430 core

/* 色彩类滤镜（逐像素"点操作"）。
 *
 * 一个程序服务多个滤镜 —— 和 SSAO / 体积渲染"一个程序两种模式"的写法一致，
 * u_mode 选择算法：
 *   0 = 黑白（灰度）        1 = 像素化（马赛克）
 *   2 = 色调分离            3 = 曝光过度（Solarize）
 *   4 = 反相                5 = 阈值（二值黑/白）
 *
 * 统一约定（filler 家族都一样）：
 *   - tex0 = 输入纹理（任何格式，采样即可；见 FillerBase.filler 的说明）
 *   - u_texel = 1 / 输入纹理尺寸
 *   - u_mix：0 = 原图，1 = 完整滤镜（给"滑块"用的）
 *   - **alpha 原样保留**：滤镜只改颜色，不改透明度
 */

in vec2 vUV;
out vec4 outColor;

uniform sampler2D tex0;
uniform int   u_mode;
uniform float u_mix;
uniform vec2  u_texel;      // 1 / 输入纹理尺寸
uniform float u_block;      // 像素化：块边长（输入纹理的像素，>=1）
uniform float u_levels;     // 色调分离：色阶数量（>=2）
uniform float u_threshold;  // 阈值 / 曝光过度的分界
uniform float u_luma;       // 黑白：0 = 平均亮度，1 = Rec.709 亮度

const vec3 LUMA709 = vec3(0.2126, 0.7152, 0.0722);

float luminance(vec3 color)
{
    return dot(color, mix(vec3(1.0 / 3.0), LUMA709, clamp(u_luma, 0.0, 1.0)));
}

void main()
{
    vec4 src = texture(tex0, vUV);
    vec3 color = src.rgb;

    if (u_mode == 0) {
        color = vec3(luminance(src.rgb));
    } else if (u_mode == 1) {
        // 像素化：把 uv 吸附到**块中心**再采样 —— 块内颜色完全一致（边缘是硬的）。
        // 末尾的 + u_texel * 0.5 是"半个纹素"：不加它，uv 落在块中心那个纹素的
        // **左边界**上，输入是 LINEAR 时就会混进左边一格（NEAREST 输入看不出来，
        // 所以这类偏差很容易在换了个过滤方式之后才冒出来）。
        // 想要"马赛克"效果就把输入/输出纹理的过滤方式设成 NEAREST。
        vec2 blocks = max(vec2(1.0), 1.0 / max(u_block * u_texel, vec2(1e-6)));
        vec2 uv = (floor(vUV * blocks) + 0.5) / blocks + u_texel * 0.5;
        color = texture(tex0, uv).rgb;
    } else if (u_mode == 2) {
        float levels = max(u_levels, 2.0);
        color = floor(src.rgb * (levels - 1.0) + 0.5) / (levels - 1.0);
    } else if (u_mode == 3) {
        // 曝光过度：亮于阈值的通道反相（PS 的"曝光过度"就是逐通道的 Solarize）
        color = mix(src.rgb, 1.0 - src.rgb, step(vec3(u_threshold), src.rgb));
    } else if (u_mode == 4) {
        color = 1.0 - src.rgb;
    } else if (u_mode == 5) {
        color = vec3(luminance(src.rgb) >= u_threshold ? 1.0 : 0.0);
    }

    outColor = vec4(mix(src.rgb, color, clamp(u_mix, 0.0, 1.0)), src.a);
}
