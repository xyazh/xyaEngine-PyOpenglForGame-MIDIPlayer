#version 430 core

/* 频域掩码：按"到零频的距离"给每个频点乘一个系数（低通 / 高通 / 带通 / 陷波）。
 *
 * 用法：作用在**复频谱**上 —— 也就是 ``FFTFilter(view="complex")`` 的输出
 * （R = 实部、G = 虚部、fftshift 居中），或者 ``Spectrum2D`` 的实部/虚部纹理。
 * R/G 乘**同一个**系数 → 幅度变、相位不变 → 逆变换回来就是常说的"频域滤波"
 * （柔化 / 锐化 / 去网纹 / 抽线 / 保留轮廓……）。
 *
 * 坐标系：vUV 是频谱图的 uv，零频在 u_center；半径用**每轴归一化**的距离
 * ``length((fract(uv - center + 0.5) - 0.5) * 2)``：d = 1 是最高频，所以
 * ``cutoff = 0.25`` 表示"每轴保留 25% 以内的频率"（非方形图上是椭圆 —— 这正是
 * "频率半径"的正确形状，圆的画法会让两个轴的高频截止不一样）。
 * 那个 ``fract(...)`` 是**环绕感知**：频谱的索引空间是环面（高频会在边界绕回），
 * 所以同一套公式在两种布局下都对：
 *
 *   - ``FFTFilter(view="complex")`` 的输出 / ``Spectrum2D`` 的**显示**图：
 *     零频在中心 → ``u_center = (0.5, 0.5)``（默认）
 *   - ``Spectrum2D`` 里的实部/虚部纹理（**未 fftshift**，零频在角上）：
 *     ``u_center = (0.0, 0.0)``
 *
 * u_mode：0 低通 1 高通 2 带通 3 陷波
 * u_shape：0 理想（硬边，会有振铃）1 高斯（软，推荐）2 巴特沃斯（阶数可调）
 */

in vec2 vUV;
out vec4 outColor;

uniform sampler2D tex0;
uniform int   u_mode;
uniform int   u_shape;
uniform float u_cutoff;   // 归一化截止半径
uniform float u_width;    // 带通 / 陷波的带宽
uniform float u_order;    // 巴特沃斯阶数
uniform vec2  u_center;   // 零频位置（默认 0.5, 0.5）
uniform float u_mix;

float lowMask(float d, float cutoff)
{
    if (u_shape == 0) {
        return step(d, cutoff);                        // 理想：硬边
    } else if (u_shape == 1) {
        float sigma = max(cutoff * 0.5, 1e-4);         // 高斯
        float ratio = d / sigma;
        return exp(-0.5 * ratio * ratio);
    }
    float ratio = d / max(cutoff, 1e-4);               // 巴特沃斯
    return 1.0 / (1.0 + pow(ratio, 2.0 * max(u_order, 0.1)));
}

float ringMask(float d, float center, float width)
{
    if (u_shape == 0) {
        float halfWidth = max(width, 1e-4) * 0.5;
        return step(d, center + halfWidth) * step(center - halfWidth, d);
    } else if (u_shape == 1) {
        float sigma = max(width, 1e-4) * 0.5;
        float delta = (d - center) / sigma;
        return exp(-0.5 * delta * delta);              // 高斯环
    }
    float delta = (d * d - center * center)
                  / max(d * max(width, 1e-4), 1e-6);   // 巴特沃斯陷波/带通
    return 1.0 / (1.0 + pow(abs(delta), 2.0 * max(u_order, 0.1)));
}

void main()
{
    vec4 src = texture(tex0, vUV);
    // 环绕感知的距离：频谱索引空间是环面，高频会从边界绕回；fract(...) 让同一套
    // 公式在"零频居中"和"零频在角上"两种布局下都给出正确的频率半径
    vec2 offset = fract(vUV - u_center + 0.5) - 0.5;
    float distance = length(offset * 2.0);
    float mask;
    if (u_mode == 0) {
        mask = lowMask(distance, u_cutoff);
    } else if (u_mode == 1) {
        mask = 1.0 - lowMask(distance, u_cutoff);
    } else if (u_mode == 2) {
        mask = ringMask(distance, u_cutoff, u_width);
    } else {
        mask = 1.0 - ringMask(distance, u_cutoff, u_width);
    }
    outColor = vec4(mix(src.rgb, src.rgb * mask, clamp(u_mix, 0.0, 1.0)), src.a);
}
