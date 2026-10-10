#version 430 core
// WBOIT 合成：把加权混合累加缓冲合成回不透明画面
//   accum  存 sum(color * a * w) 与 sum(a * w)
//   reveal 存 ∏(1 - a)
//   color  存不透明 pass 的颜色
in vec2 vUV;

uniform sampler2D u_accum;
uniform sampler2D u_reveal;
uniform sampler2D u_color;
// 可选：给"很薄"的半透明一点可见度（0 = 关闭，默认关闭）
uniform float u_alphaFloor;

out vec4 outColor;

void main()
{
    vec4 accum = texture(u_accum, vUV);
    float reveal = texture(u_reveal, vUV).r;
    vec3 base = texture(u_color, vUV).rgb;

    // 加权平均颜色；accum.a 为 0 表示该像素没有半透明内容
    vec3 color = accum.rgb / max(accum.a, 1e-5);
    float alpha = clamp(1.0 - reveal, 0.0, 1.0);
    alpha = max(alpha, u_alphaFloor);
    // 没有半透明内容时 alpha=0 -> 输出就是原不透明画面，完全不受影响
    outColor = vec4(mix(base, color, alpha), 1.0);
}
