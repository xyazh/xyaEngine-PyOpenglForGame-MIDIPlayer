#version 430 core

/* 可分离高斯模糊的**单趟**：GaussianBlurFilter 会跑两趟（先横向、再纵向）。
 *
 * 核不预计算：sigma 现场算 exp(-i²/2σ²)，所以参数随便改都不用重建 uniform 数组
 * （Bloom 那边是因为 compute 里要按半径查表才写死了两组权重）。
 * u_radius 是采样半径（像素），经验上 3σ 就够（类里默认按 3σ 推）。
 *
 * alpha 和 RGB 一起模糊（同上一个文件的说明）。
 */

in vec2 vUV;
out vec4 outColor;

uniform sampler2D tex0;
uniform sampler2D tex1;      // 第二趟的"混合基准"（= 原始输入，只在 u_mixSource == 1 时采样）
uniform vec2  u_dir;         // (1,0) 横向 / (0,1) 纵向 —— 单位方向，texel 缩放内部做
uniform vec2  u_texel;
uniform float u_sigma;
uniform int   u_radius;
uniform float u_mix;
uniform int   u_mixSource;   // 1 = 用 tex1（原图）当混合基准，0 = 用本趟输入

const int MAX_RADIUS = 32;

void main()
{
    vec4 src = texture(tex0, vUV);
    // 混合基准：默认是本趟的输入；第二趟给 tex1（原图）时，mix 的语义就是
    // "原图 ↔ 完全模糊后的图"——这正是"滑块"该有的行为
    vec4 base = u_mixSource == 1 ? texture(tex1, vUV) : src;
    float blend = clamp(u_mix, 0.0, 1.0);
    int radius = clamp(u_radius, 0, MAX_RADIUS);
    if (radius == 0) {
        // 半径 0：只做"基准 ↔ 输入"的混合（u_mixSource == 0 时基准就是输入，
        // 于是等价于原样输出）。**大半径模糊的最后一步（放大 + 混合）走的就是
        // 这条**：tex0 = 小尺寸上的模糊结果（双线性放大），tex1 = 原图。
        outColor = vec4(mix(base.rgb, src.rgb, blend),
                        mix(base.a, src.a, blend));
        return;
    }

    float sigma = max(u_sigma, 1e-3);
    float twoSigma2 = 2.0 * sigma * sigma;
    vec4 sum = src;
    float weightSum = 1.0;
    // 只取半边的和（±i 权重相同），最后一次性乘 2 —— 采样数减半
    for (int i = 1; i <= MAX_RADIUS; i++) {
        if (i > radius) break;
        float weight = exp(-float(i * i) / twoSigma2);
        vec2 offset = u_dir * u_texel * float(i);
        sum += (texture(tex0, vUV + offset) + texture(tex0, vUV - offset)) * weight;
        weightSum += 2.0 * weight;
    }

    vec4 blurred = sum / weightSum;
    outColor = vec4(mix(base.rgb, blurred.rgb, blend),
                    mix(base.a, blurred.a, blend));
}
