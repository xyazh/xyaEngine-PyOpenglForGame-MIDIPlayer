#version 430 core

/* 拷贝滤镜：tex0 → 输出附件。
 *
 * 看着"没用"，其实是最常用的一个：
 *   1. **格式/尺寸转换**：把 HDR 缓冲搬进 LDR 纹理（写到 RGBA8 时由 GL 负责 clamp），
 *      或者把 512² 的结果缩放进 256² 的目标纹理（双线性）；
 *   2. 需要 compute shader 产出结果的滤镜（FFT）最后都靠它落到"调用方给的"目标纹理上
 *      —— compute 只能写 image unit（格式必须匹配），fragment 写 FBO 附件则任意格式都行。
 */

in vec2 vUV;
out vec4 outColor;

uniform sampler2D tex0;

void main()
{
    outColor = texture(tex0, vUV);
}
