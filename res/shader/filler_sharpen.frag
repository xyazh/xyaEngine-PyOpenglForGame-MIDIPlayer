#version 430 core

/* USM 锐化（Unsharp Mask）：detail = 原图 - 低通图，再按 amount 加回去。
 *
 *   tex0 = 原图（输入纹理）
 *   tex1 = 低通图（SharpenFilter 内部用 GaussianBlurFilter 算好的一张模糊图）
 *
 * u_threshold：只锐化"差异够明显"的像素 —— 平坦区域（噪点、渐变里的色带）
 * 不动，否则锐化会把噪声一起放大。阈值附近用 smoothstep 过渡，
 * 避免出现"这个像素锐化、旁边不锐化"的硬边。
 */

in vec2 vUV;
out vec4 outColor;

uniform sampler2D tex0;
uniform sampler2D tex1;
uniform float u_amount;
uniform float u_threshold;
uniform float u_mix;

const vec3 LUMA709 = vec3(0.2126, 0.7152, 0.0722);

void main()
{
    vec4 src = texture(tex0, vUV);
    vec3 detail = src.rgb - texture(tex1, vUV).rgb;
    float magnitude = abs(dot(detail, LUMA709));
    float gate = smoothstep(u_threshold, u_threshold * 2.0 + 1e-4, magnitude);
    vec3 sharpened = max(src.rgb + detail * u_amount * gate, vec3(0.0));
    outColor = vec4(mix(src.rgb, sharpened, clamp(u_mix, 0.0, 1.0)), src.a);
}
