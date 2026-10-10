#version 430 core

/* 卷积类滤镜（3×3 邻域，采样输入纹理自身）。
 *
 *   u_mode == 0  Sobel 边缘提取     u_mode == 1  Laplacian（拉普拉斯）
 *   u_mode == 2  浮雕（Emboss）     u_mode == 3  查找边缘（Find Edges）
 *   u_mode == 4  等高线（Contour，按亮度等值带画线）
 *
 * u_gray / u_strength / u_levels / u_width 在不同模式下的含义写在各个分支里 ——
 * 因为这几类"边缘"滤镜在 PS 里本来就不统一（有的输出灰度、有的保留原色）。
 *
 * 边界采样靠输入纹理自己的 wrap 模式（Texture/TextureStorage2D 默认
 * GL_CLAMP_TO_EDGE），所以图像边缘不会采样到对面的像素。
 */

in vec2 vUV;
out vec4 outColor;

uniform sampler2D tex0;
uniform int   u_mode;
uniform vec2  u_texel;
uniform float u_strength;   // 倍率：越大边缘越亮 / 越硬
uniform float u_mix;
uniform float u_gray;       // 1 = 灰度输出（0 = 用原图颜色调制，见分支注释）
uniform float u_levels;     // 等高线：亮度等值带数量（>=2）
uniform float u_width;      // 等高线：线的宽度（0..1，占一个"带"的比例）

const vec3 LUMA709 = vec3(0.2126, 0.7152, 0.0722);

const float KX[9] = float[9](-1.0, -2.0, -1.0,  0.0, 0.0, 0.0,  1.0, 2.0, 1.0);
const float KY[9] = float[9](-1.0,  0.0,  1.0, -2.0, 0.0, 2.0, -1.0, 0.0, 1.0);

vec3 sampleOffset(int x, int y)
{
    return texture(tex0, vUV + vec2(float(x), float(y)) * u_texel).rgb;
}

// Sobel：返回 (gx, gy)，按通道卷积（u_gray == 1 时也只需要亮度，调用方自己点乘）
void sobel(out vec3 gx, out vec3 gy)
{
    gx = vec3(0.0);
    gy = vec3(0.0);
    for (int i = 0; i < 9; i++) {
        int x = i % 3 - 1;
        int y = i / 3 - 1;
        vec3 color = sampleOffset(x, y);
        gx += color * KX[i];
        gy += color * KY[i];
    }
}

void main()
{
    vec4 src = texture(tex0, vUV);
    vec3 color = src.rgb;

    if (u_mode == 0 || u_mode == 3) {
        vec3 gx;
        vec3 gy;
        sobel(gx, gy);
        if (u_mode == 0) {
            // 边缘提取：输出边缘强度（灰度）。u_gray == 0 时用原图颜色调制
            // —— 彩色线条、暗背景，方便看清边缘落在哪里。
            float magnitude = length(vec2(dot(gx, LUMA709), dot(gy, LUMA709))) * u_strength;
            color = mix(src.rgb * magnitude, vec3(magnitude), clamp(u_gray, 0.0, 1.0));
        } else {
            // 查找边缘：PS 的语义是"白底 + 彩色边缘" —— 逐通道取梯度，
            // 用 1 - |∇| 把边缘压成暗线。u_gray == 1 时退化成黑白线稿。
            vec3 edge = clamp(vec3(1.0) - (abs(gx) + abs(gy)) * u_strength, 0.0, 1.0);
            color = mix(edge, vec3(dot(edge, LUMA709)), clamp(u_gray, 0.0, 1.0));
        }
    } else if (u_mode == 1) {
        float laplacian = 0.0;
        for (int i = 0; i < 9; i++) {
            int x = i % 3 - 1;
            int y = i / 3 - 1;
            float weight = (x == 0 && y == 0) ? -4.0 : ((x == 0 || y == 0) ? 1.0 : 0.0);
            laplacian += dot(sampleOffset(x, y), LUMA709) * weight;
        }
        color = vec3(clamp(abs(laplacian) * u_strength, 0.0, 1.0));
    } else if (u_mode == 2) {
        // 浮雕：左上 - 右下 的亮度差 + 0.5 的灰底。
        // u_gray == 0 时保留原图颜色（彩色浮雕）。
        vec3 delta = sampleOffset(-1, -1) - sampleOffset(1, 1);
        color = mix(clamp(src.rgb + delta * u_strength, 0.0, 1.0),
                    vec3(0.5) + vec3(dot(delta, LUMA709) * u_strength),
                    clamp(u_gray, 0.0, 1.0));
    } else if (u_mode == 4) {
        // 等高线：把亮度量化成 u_levels 个带，带边缘（离中心最远的部分）画线。
        float band = abs(fract(dot(src.rgb, LUMA709) * max(u_levels, 2.0)) - 0.5) * 2.0;
        float line = step(1.0 - clamp(u_width, 0.0, 0.5), band);
        color = mix(mix(vec3(1.0), vec3(0.0), line),
                    mix(src.rgb, vec3(0.0), line),
                    1.0 - clamp(u_gray, 0.0, 1.0));
    }

    outColor = vec4(mix(src.rgb, color, clamp(u_mix, 0.0, 1.0)), src.a);
}
