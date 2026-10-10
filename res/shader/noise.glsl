// === 程序化噪声库（计算着色器噪声贴图 + 体积雾共用）===
//
// 这个文件没有 #version、没有 main()：它由 ShaderManager 的 #include 机制在编译前
// 内联进宿主着色器（见 pyglgame/shader/ShaderManager.py）。共用一份实现的好处是
// **不会出现两份慢慢跑偏的噪声**：改这里的函数，所有引用者一起变。
//
// 三个设计要点：
//
// 1. **可平铺（seamless）**：所有格点哈希都接受一个 ``period``，对格点坐标取模。
//    纹理 wrap 成 REPEAT 时看不到接缝 —— 体积雾是滚动采样一张 3D 贴图的，只要
//    贴图本身不平铺，每跨过一个整数边界就会出现一道**可见的硬接缝**。
// 2. **种类可切换**：``nzNoise()`` 按 ``kind`` 分支，每种都把结果归一化到 0..1，
//    这样阈值 / 对比度 / 通道打包可以统一处理（调用方不用为每种噪声调参数）。
// 3. **种类编号是契约**：必须与 pyglgame/render/TextureFractalNoise.py 的
//    ``NOISE_KINDS`` 一致，tests/test_engine.py::NoiseShaderContractTest 读源码守着。
//
// 复杂度提示：Worley 是 3x3x3 邻域搜索、curl 是 18 次 Perlin 采样。它们只在
// **生成贴图**时跑（每帧最多一次），不在每个像素的步进里跑。

// === 噪声种类（= pyglgame/render/TextureFractalNoise.py 的 NOISE_KINDS）===
const int NOISE_VALUE = 0;         // 值噪声：最便宜，团块感
const int NOISE_PERLIN = 1;        // 梯度噪声：结构更自然
const int NOISE_RIDGED = 2;        // 山脊：1-|n| → 翻涌的"山脊线"（干冰的翻卷感）
const int NOISE_BILLOW = 3;        // 湍流/团絮：|n| → 棉花团
const int NOISE_WORLEY = 4;        // 细胞噪声 F1：颗粒/气泡
const int NOISE_WORLEY_EDGE = 5;   // 细胞噪声 F2-F1：裂纹/蜂窝边界
const int NOISE_WARP = 6;          // 域扭曲 fBm：烟雾的丝状结构
const int NOISE_CURL = 7;          // 旋度噪声：无散度向量场（用长度当标量）
const int NOISE_COUNT = 8;

// === 通道打包模式（= pyglgame/render/TextureFractalNoise.py 的 PACK_MODES）===
const int PACK_GRAY_MUL = 0;    // RGB 同值 = 两层相乘，A = 1（默认，团块灰度）
const int PACK_RGB_KINDS = 1;   // RGB = 三种不同种类 → 一张贴图三种结构
const int PACK_RGB_OCTAVE = 2;  // RGB = 低频/中频/高频 → 细节分层
const int PACK_RGB_TIME = 3;    // RGB = 三个时间切片 → 三帧流动
const int PACK_RGBA_LAYER = 4;  // RGB = 低频×高频，A = 高频（侵蚀/遮罩）
const int PACK_COUNT = 5;

// 倍频上限（int uniform 不能当循环上界，所以用常量 + break）
const int NZ_MAX_OCTAVES = 8;

// 格点坐标按周期取模：period 分量 <= 0 表示该轴不平铺
vec3 nzWrap(vec3 cell, vec3 period)
{
    return vec3(period.x > 0.0 ? mod(cell.x, period.x) : cell.x,
                period.y > 0.0 ? mod(cell.y, period.y) : cell.y,
                period.z > 0.0 ? mod(cell.z, period.z) : cell.z);
}

// 标量哈希（值噪声用），带周期
float nzHash31(vec3 cell, vec3 period)
{
    vec3 p = nzWrap(cell, period);
    return fract(sin(dot(p, vec3(127.1, 311.7, 157.3))) * 437576.54123);
}

// 梯度哈希（Perlin 用），返回 [-1,1]^3，带周期
vec3 nzHash33(vec3 cell, vec3 period)
{
    vec3 p = nzWrap(cell, period);
    p = vec3(dot(p, vec3(127.1, 311.7, 74.7)),
             dot(p, vec3(269.5, 183.3, 246.1)),
             dot(p, vec3(113.5, 271.9, 124.6)));
    return fract(sin(p) * 43758.5453123) * 2.0 - 1.0;
}

// 值噪声（原 computeFractalNoise.comp 里的 noise3，补上 period）
float nzValue(vec3 p, vec3 period)
{
    vec3 i = floor(p);
    vec3 f = p - i;
    vec3 u = f * f * (3.0 - 2.0 * f);          // 平滑插值
    float a = nzHash31(i, period);
    float b = nzHash31(i + vec3(1.0, 0.0, 0.0), period);
    float c = nzHash31(i + vec3(0.0, 1.0, 0.0), period);
    float d = nzHash31(i + vec3(1.0, 1.0, 0.0), period);
    float e = nzHash31(i + vec3(0.0, 0.0, 1.0), period);
    float f1 = nzHash31(i + vec3(1.0, 0.0, 1.0), period);
    float g = nzHash31(i + vec3(0.0, 1.0, 1.0), period);
    float h = nzHash31(i + vec3(1.0, 1.0, 1.0), period);
    float xy0 = mix(a, b, u.x) + (c - a) * u.y * (1.0 - u.x) + (d - b) * u.x * u.y;
    float xy1 = mix(e, f1, u.x) + (g - e) * u.y * (1.0 - u.x) + (h - f1) * u.x * u.y;
    return mix(xy0, xy1, u.z);
}

// Perlin 梯度噪声（quintic 插值），输出约 [-1,1]
float nzPerlin(vec3 p, vec3 period)
{
    vec3 i = floor(p);
    vec3 f = p - i;
    vec3 u = f * f * f * (f * (f * 6.0 - 15.0) + 10.0);
    float n000 = dot(nzHash33(i + vec3(0.0, 0.0, 0.0), period), f - vec3(0.0, 0.0, 0.0));
    float n100 = dot(nzHash33(i + vec3(1.0, 0.0, 0.0), period), f - vec3(1.0, 0.0, 0.0));
    float n010 = dot(nzHash33(i + vec3(0.0, 1.0, 0.0), period), f - vec3(0.0, 1.0, 0.0));
    float n110 = dot(nzHash33(i + vec3(1.0, 1.0, 0.0), period), f - vec3(1.0, 1.0, 0.0));
    float n001 = dot(nzHash33(i + vec3(0.0, 0.0, 1.0), period), f - vec3(0.0, 0.0, 1.0));
    float n101 = dot(nzHash33(i + vec3(1.0, 0.0, 1.0), period), f - vec3(1.0, 0.0, 1.0));
    float n011 = dot(nzHash33(i + vec3(0.0, 1.0, 1.0), period), f - vec3(0.0, 1.0, 1.0));
    float n111 = dot(nzHash33(i + vec3(1.0, 1.0, 1.0), period), f - vec3(1.0, 1.0, 1.0));
    float x00 = mix(n000, n100, u.x);
    float x10 = mix(n010, n110, u.x);
    float x01 = mix(n001, n101, u.x);
    float x11 = mix(n011, n111, u.x);
    return mix(mix(x00, x10, u.y), mix(x01, x11, u.y), u.z) * 1.4;
}

// 细胞（沃利）噪声：返回 vec2(F1, F2)，jitter = 特征点随机位移比例 [0,1]
vec2 nzWorley(vec3 p, vec3 period, float jitter)
{
    vec3 cell = floor(p);
    vec3 local = p - cell;
    float first = 8.0;
    float second = 8.0;
    for (int x = -1; x <= 1; ++x) {
        for (int y = -1; y <= 1; ++y) {
            for (int z = -1; z <= 1; ++z) {
                vec3 offset = vec3(float(x), float(y), float(z));
                vec3 feature = offset + nzHash33(cell + offset, period) * (jitter * 0.5) + 0.5;
                float d = length(feature - local);
                if (d < first) {
                    second = first;
                    first = d;
                } else if (d < second) {
                    second = d;
                }
            }
        }
    }
    return vec2(first, second);
}

// 旋度噪声：对三个 Perlin 势场取旋度 → 无散度向量场（烟雾/流体的扰动方向）
vec3 nzCurl(vec3 p, vec3 period)
{
    const float e = 0.5;
    vec3 a = vec3(13.1, 7.7, 3.3);
    vec3 b = vec3(29.3, 11.9, 19.7);
    vec3 c = vec3(41.5, 23.3, 37.1);
    vec3 dx = vec3(e, 0.0, 0.0);
    vec3 dy = vec3(0.0, e, 0.0);
    vec3 dz = vec3(0.0, 0.0, e);
    // (dPz/dy - dPy/dz, dPx/dz - dPz/dx, dPy/dx - dPx/dy) / 2e
    float x = (nzPerlin(p + dy + c, period) - nzPerlin(p - dy + c, period))
            - (nzPerlin(p + dz + b, period) - nzPerlin(p - dz + b, period));
    float y = (nzPerlin(p + dz + a, period) - nzPerlin(p - dz + a, period))
            - (nzPerlin(p + dx + c, period) - nzPerlin(p - dx + c, period));
    float z = (nzPerlin(p + dx + b, period) - nzPerlin(p - dx + b, period))
            - (nzPerlin(p + dy + a, period) - nzPerlin(p - dy + a, period));
    return vec3(x, y, z) / (2.0 * e);
}

// 单次噪声采样：种类 → 归一化到 0..1
// period 用"当前频率下的格点周期"；warp > 0 时先做域扭曲
float nzNoise(vec3 p, int kind, vec3 period, float jitter, float warp)
{
    if (kind == NOISE_PERLIN) {
        return clamp(nzPerlin(p, period) * 0.5 + 0.5, 0.0, 1.0);
    }
    if (kind == NOISE_RIDGED) {
        return clamp(1.0 - abs(nzPerlin(p, period)), 0.0, 1.0);
    }
    if (kind == NOISE_BILLOW) {
        // |n| 的值集中在 0 附近（直接输出是一片黑），开方把它抬到能用的区间
        return clamp(pow(abs(nzPerlin(p, period)), 0.45), 0.0, 1.0);
    }
    if (kind == NOISE_WORLEY || kind == NOISE_WORLEY_EDGE) {
        vec2 cells = nzWorley(p, period, jitter);
        if (kind == NOISE_WORLEY) {
            return clamp(1.0 - cells.x, 0.0, 1.0);
        }
        // F2-F1（细胞边界）同样集中在 0 附近 → 开方拉开动态范围
        return clamp(pow(clamp(cells.y - cells.x, 0.0, 1.0), 0.45), 0.0, 1.0);
    }
    if (kind == NOISE_CURL) {
        return clamp(length(nzCurl(p, period)) * 0.6, 0.0, 1.0);
    }
    if (kind == NOISE_WARP && warp > 0.0) {
        // 域扭曲：用一层值噪声把坐标推开（丝状结构就来自这里）
        vec3 push = vec3(nzValue(p, period) - 0.5,
                         nzValue(p + vec3(5.2, 1.3, 2.7), period) - 0.5,
                         nzValue(p + vec3(9.1, 8.4, 4.6), period) - 0.5);
        return clamp(nzValue(p + push * warp, period), 0.0, 1.0);
    }
    return clamp(nzValue(p, period), 0.0, 1.0);   // NOISE_VALUE（默认）
}

// 多倍频 fBm：octaves 层、频率 ×lacunarity、振幅 ×gain，输出归一化到 0..1。
// period 是"基础频率下的格点周期"，每层按频率自动放大 —— 否则高倍频处会露接缝。
float nzFbm(vec3 p, int kind, int octaves, float lacunarity, float gain,
            vec3 period, float jitter, float warp)
{
    float value = 0.0;
    float total = 0.0;
    float amplitude = 1.0;
    float frequency = 1.0;
    for (int i = 0; i < NZ_MAX_OCTAVES; ++i) {
        if (i >= octaves) {
            break;
        }
        value += amplitude * nzNoise(p * frequency, kind, period * frequency,
                                     jitter, warp);
        total += amplitude;
        amplitude *= gain;
        frequency *= lacunarity;
    }
    return total > 1e-6 ? clamp(value / total, 0.0, 1.0) : 0.0;
}

// 形状映射：阈值（softness > 0 才生效）→ 对比度 → 反相
// softness = 0 时是恒等映射（默认，保持"原始噪声"的观感）
float nzShape(float n, float threshold, float softness, float contrast, int invert)
{
    n = clamp(n, 0.0, 1.0);
    if (softness > 1e-4) {
        n = smoothstep(threshold, threshold + softness, n);
    }
    if (contrast != 1.0) {
        n = pow(n, max(contrast, 1e-3));
    }
    return invert != 0 ? 1.0 - n : n;
}
