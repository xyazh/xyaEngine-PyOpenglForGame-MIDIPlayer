#version 430 core

/* SSAO（屏幕空间环境光遮蔽，Phase 3）。
 *
 * 一个程序两种模式（省一个 shader，和引擎的 fuc 风格一致）：
 *   u_mode == 0  采样 AO（半分辨率，半球核 + 逐像素随机旋转）
 *   u_mode == 1  双边模糊（用深度做权重，避免跨物体边缘糊开）
 *
 * 输入是深度纹理（相机帧缓冲的 depth_resolve / 深度纹理），
 * 不依赖法线缓冲：法线用重建出来的视空间位置做叉积得到。
 *
 * 输出是 [0,1] 的遮蔽系数（1 = 完全不遮蔽），由 dis.frag 的 ambientOcclusion()
 * 按屏幕坐标采样，只压低间接光（环境项）。
 */

in vec2 vUV;
out float outAO;

uniform int  u_mode;

// === 公共 ===
uniform sampler2D u_depth;        // 相机深度纹理
uniform sampler2D u_ao;           // 模糊模式的输入
uniform mat4 u_projection;
uniform mat4 u_invProjection;

// === 采样模式 ===
uniform vec3  u_kernel[16];       // 半球采样核（CPU 侧生成，见 AmbientOcclusion._buildKernel）
uniform int   u_samples;          // 实际使用的采样数（<= 16）
uniform float u_radius;           // 采样半径（视空间单位）
uniform float u_bias;             // 深度偏置，按采样半径的比例（掠射角自遮蔽的抑制量）
uniform float u_intensity;        // 遮蔽强度
uniform float u_power;            // 对比度（>1 更"锐"）

// === 模糊模式 ===
uniform vec2  u_aoTexel;          // 1 / 半分辨率尺寸
uniform float u_blurDepthScale;   // 深度权重系数（越大越"不跨边"）


// 由深度纹理重建视空间坐标（视空间里可见几何的 z 为负）
vec3 viewPosFromDepth(vec2 uv)
{
    float depth = texture(u_depth, uv).r;
    vec4 clip = vec4(uv * 2.0 - 1.0, depth * 2.0 - 1.0, 1.0);
    vec4 view = u_invProjection * clip;
    return view.xyz / max(view.w, 1e-6);
}

// 屏幕坐标 hash：逐像素旋转采样核，省掉一张噪声纹理
float hash12(vec2 p)
{
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

float sampleAO(vec3 origin, vec3 normal)
{
    // 用屏幕坐标 hash 出一个随机切向量，构造 TBN 基 —— 半球必须**对齐到表面法线**。
    // （只绕视空间 Z 轴旋转是错误的：那样样本会撒进屏幕里，平地也会整片自遮蔽）
    float angle = hash12(gl_FragCoord.xy) * 6.2831853;
    vec3 randomVector = vec3(cos(angle), sin(angle), 0.0);
    vec3 tangent = normalize(randomVector - normal * dot(randomVector, normal));
    vec3 bitangent = cross(normal, tangent);
    mat3 tbn = mat3(tangent, bitangent, normal);

    float originDistance = -origin.z;
    // 偏置按半径成比例：掠射角下"抬离表面的采样点"必然比它正下方的几何更靠近相机，
    // 这是 SSAO 的自遮蔽（acne）来源，误差量级与半径同阶，所以偏置也必须跟半径成比例
    float bias = u_radius * u_bias;
    float occlusion = 0.0;
    int samples = clamp(u_samples, 1, 16);
    for (int i = 0; i < samples; ++i) {
        // 采样点：法线半球内（核的 z >= 0，经 TBN 变换到世界/视空间朝向）
        vec3 samplePos = origin + tbn * u_kernel[i] * u_radius;
        vec4 clip = u_projection * vec4(samplePos, 1.0);
        if (clip.w <= 0.0) {
            continue;
        }
        vec2 sampleUV = clip.xy / clip.w * 0.5 + 0.5;
        if (sampleUV.x < 0.0 || sampleUV.x > 1.0
                || sampleUV.y < 0.0 || sampleUV.y > 1.0) {
            continue;
        }
        vec3 realPos = viewPosFromDepth(sampleUV);
        float realDistance = -realPos.z;
        float sampleDistance = -samplePos.z;
        // 该屏幕位置的真实几何比采样点更近 => 被遮挡
        float occluded = (realDistance < sampleDistance - bias) ? 1.0 : 0.0;
        // 距离衰减：远处的遮挡物不该产生 AO（否则会出现黑边）
        float rangeCheck = smoothstep(0.0, 1.0,
            u_radius / max(abs(originDistance - realDistance), 1e-4));
        occlusion += occluded * rangeCheck;
    }
    float ao = 1.0 - (occlusion / float(samples)) * u_intensity;
    return clamp(pow(clamp(ao, 0.0, 1.0), u_power), 0.0, 1.0);
}

float blurAO()
{
    float centerDistance = -viewPosFromDepth(vUV).z;
    float sum = 0.0;
    float weightSum = 0.0;
    for (int x = -2; x <= 2; ++x) {
        for (int y = -2; y <= 2; ++y) {
            vec2 uv = vUV + vec2(float(x), float(y)) * u_aoTexel;
            float value = texture(u_ao, clamp(uv, vec2(0.0), vec2(1.0))).r;
            float distance = -viewPosFromDepth(clamp(uv, vec2(0.0), vec2(1.0))).z;
            float depthWeight = exp(-abs(distance - centerDistance) * u_blurDepthScale);
            float spatialWeight = exp(-float(x * x + y * y) * 0.12);
            float weight = depthWeight * spatialWeight;
            sum += value * weight;
            weightSum += weight;
        }
    }
    return sum / max(weightSum, 1e-5);
}

void main()
{
    if (u_mode == 1) {
        outAO = blurAO();
        return;
    }
    vec3 origin = viewPosFromDepth(vUV);
    // 背景（深度 1.0）不产生遮蔽
    if (-origin.z <= 0.0) {
        outAO = 1.0;
        return;
    }
    // 法线：由重建的视空间位置做屏幕空间叉积（免掉法线缓冲）
    vec3 normal = normalize(cross(dFdx(origin), dFdy(origin)));
    if (dot(normal, -origin) < 0.0) {
        normal = -normal;
    }
    outAO = sampleAO(origin, normal);
}
