#version 430 core

/* 模糊类滤镜（单趟）。
 *
 *   u_mode == 0  方框模糊（Box）            半径 = 网格半宽（像素）
 *   u_mode == 1  方向模糊 / 动感模糊（Motion）半径 = 拖尾长度（像素）
 *   u_mode == 2  径向模糊 / 缩放模糊（Radial）半径 = 向中心收缩的幅度（像素）
 *   u_mode == 3  表面模糊（Surface，保边）    半径 = 邻域步长，u_threshold = 色差容限
 *
 * 和 SSAO 的"一个程序两种模式"同一种写法。高斯模糊不在这里 ——
 * 它要跑两趟（可分离），见 filler_gaussian.frag。
 *
 * 这一族**连 alpha 一起模糊**（RGB 与 A 用同一个系数混合）：半透明精灵的
 * 边缘本来就该跟着糊，只糊颜色会留下一条硬边。
 */

in vec2 vUV;
out vec4 outColor;

uniform sampler2D tex0;
uniform int   u_mode;
uniform vec2  u_texel;
uniform float u_radius;
uniform int   u_samples;
uniform vec2  u_dir;        // 方向模糊：方向（内部归一化）
uniform vec2  u_center;     // 径向模糊：中心（uv 坐标）
uniform float u_threshold;  // 表面模糊：色差容限（越大越接近普通模糊）
uniform float u_mix;

const vec3 LUMA709 = vec3(0.2126, 0.7152, 0.0722);
// 采样数上限：和类里的 MAX_SAMPLES 一致，循环写成常数上界 + early break，
// 避免"动态循环次数"在某些驱动上被完全展开成巨大代码
const int MAX_SAMPLES = 32;

void main()
{
    vec4 src = texture(tex0, vUV);
    int count = clamp(u_samples, 1, MAX_SAMPLES);
    float radius = max(u_radius, 0.0);
    vec4 sum = vec4(0.0);
    float weightSum = 0.0;

    if (u_mode == 0) {
        // 方框模糊：count×count 网格平均（半径 = 网格半宽）
        for (int y = 0; y < MAX_SAMPLES; y++) {
            if (y >= count) break;
            float fy = (count == 1) ? 0.0 : (float(y) / float(count - 1) * 2.0 - 1.0);
            for (int x = 0; x < MAX_SAMPLES; x++) {
                if (x >= count) break;
                float fx = (count == 1) ? 0.0 : (float(x) / float(count - 1) * 2.0 - 1.0);
                sum += texture(tex0, vUV + vec2(fx, fy) * radius * u_texel);
                weightSum += 1.0;
            }
        }
    } else if (u_mode == 1) {
        // 方向模糊：沿 u_dir 对称取 count 个点（t 从 -1 到 1）
        vec2 dir = length(u_dir) < 1e-6 ? vec2(1.0, 0.0) : normalize(u_dir);
        for (int i = 0; i < MAX_SAMPLES; i++) {
            if (i >= count) break;
            float t = (count == 1) ? 0.0 : (float(i) / float(count - 1) * 2.0 - 1.0);
            sum += texture(tex0, vUV + dir * u_texel * radius * t);
            weightSum += 1.0;
        }
    } else if (u_mode == 2) {
        // 径向模糊：从当前像素朝中心收缩着采 count 个点。
        // 像素半径 → 比例：量级按纹理较长边算（512² 上半径 64 ≈ 收缩 12.5%）
        float amount = clamp(radius * max(u_texel.x, u_texel.y), 0.0, 0.95);
        for (int i = 0; i < MAX_SAMPLES; i++) {
            if (i >= count) break;
            float t = (count == 1) ? 0.0 : (float(i) / float(count - 1));
            sum += texture(tex0, mix(vUV, u_center, t * amount));
            weightSum += 1.0;
        }
    } else if (u_mode == 3) {
        // 表面模糊：权重 = exp(-|色差| / 容限)，色差大的邻居几乎不参与
        // → 平坦区域被抹平、边缘两侧各留各的（保边）
        float tolerance = max(u_threshold, 1e-4);
        float step = max(radius, 1.0);
        for (int y = 0; y < MAX_SAMPLES; y++) {
            if (y >= count) break;
            float fy = (count == 1) ? 0.0 : (float(y) / float(count - 1) * 2.0 - 1.0);
            for (int x = 0; x < MAX_SAMPLES; x++) {
                if (x >= count) break;
                float fx = (count == 1) ? 0.0 : (float(x) / float(count - 1) * 2.0 - 1.0);
                vec4 color = texture(tex0, vUV + vec2(fx, fy) * step * u_texel);
                float weight = exp(-abs(dot(color.rgb - src.rgb, LUMA709)) / tolerance);
                sum += color * weight;
                weightSum += weight;
            }
        }
    }

    vec4 blurred = sum / max(weightSum, 1e-6);
    float blend = clamp(u_mix, 0.0, 1.0);
    outColor = vec4(mix(src.rgb, blurred.rgb, blend), mix(src.a, blurred.a, blend));
}
