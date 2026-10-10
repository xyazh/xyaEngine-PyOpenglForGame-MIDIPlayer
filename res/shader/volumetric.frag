#version 430 core

/* 体积渲染（体积雾 + 体积光 / god rays，Phase 4）。
 *
 * 一个程序两种模式（与 SSAO 同一套约定）：
 *   u_mode == 0  半分辨率射线步进：输出 vec4(散射颜色, 透过率) 到 u_scatter 目标
 *   u_mode == 1  全分辨率合成：scene * T + scattered（**必须在 tone mapping 之前**，
 *                因为它是一个加性 HDR 项）
 *
 * 为什么能"顺便"拿到 god rays：步进时对每个采样点查方向光的阴影贴图，
 * 被挡住的地方散射就为 0 —— 也就是体积阴影。所以体积渲染和阴影贴图是同一套数据。
 *
 * 依赖的输入（相机侧已经准备好）：
 *   - 场景颜色纹理（HDR，不透明 + 半透明都画完）
 *   - 可采样深度纹理（depth_resolve / 深度纹理）
 *   - 逆视投影矩阵（由屏幕 uv + 深度重建世界坐标）
 *   - 方向光的阴影贴图（可选，u_shadowEnabled）
 *
 * Phase 5 追加了"雾层模式"（u_fogLayer == 1）：平板步进 + 3D 噪声驱动的密度，
 * 用来做干冰那种**低、浓、边缘分明**的雾。密度场在 fogDensityAt() 里，
 * 射线与雾层平板的求交在 fogSlab() 里；u_fogLayer == 0 时两者都不参与，
 * 密度退化成原来的纯高度雾（老场景逐像素不变）。
 *
 * 雾顶是"一层有厚度的壳"，不是一张纸：顶面过渡带（u_topSoftness）比起伏幅度
 * （u_fogRange.z）还薄时，掠射视角会把壳的轮廓压成沿视线的一条条拉丝 —— 这就是
 * 雾本身"一条一条"的主因（不是噪声、也不是步数），见 fogDensityAt() 里的 topSoft。
 *
 * Phase 6 追加了"**世界体积盒**"（u_volumeEnabled == 1）：同一个密度场可以做成
 * FogVolume 那样的**可放置对象** —— 射线与轴对齐盒子求交（boxIntersect）只步进
 * 盒内那一段，密度再按"到侧面的距离"淡出（boxSideFade，宽度用噪声扰动，否则雾的
 * 外缘会是一圈笔直的盒棱），盒子的上下沿直接就是雾层的底/顶（Python 侧从盒子的
 * 上下沿推出来）。多个体积由引擎按"远→近"依次合成，每个体积各跑一次本 pass。
 * u_volumeEnabled == 0 时上面这些全部不参与 → 老路径（雾是相机属性）逐像素不变。
 *
 * 雾的**重复度**与**颗粒感**是两个独立的旋钮（都在 fogDensityAt 里）：
 *   - 重复度：一张可平铺贴图有固有周期 1/u_noiseScale（0.2 → 5 米）。单层采样时
 *     整片雾每 5 米原样复制 —— 所以这里叠三个**不可通约**的尺度 + 三个旋转角
 *     （u_noiseTiling），再用宽层做域扭曲（u_noiseWarp）把坐标推开。
 *     合成周期 = 各层公倍数 ≫ 雾池本身，观感上不再"循环"。
 *   - 颗粒感：贴图侧先把超过奈奎斯特的倍频层砍掉
 *     （computeVolumeNoise.comp 的 nzOctaveBudget），着色器侧再压 u_noiseGrain /
 *     u_fogSharpen、放软 u_edgeSoftness。
 */

in vec2 vUV;
out vec4 outColor;

uniform int u_mode;

// === 公共 ===
uniform sampler2D u_color;       // 场景颜色（HDR）
uniform sampler2D u_depth;       // 相机深度
uniform sampler2D u_scatter;     // 半分辨率散射结果（合成模式用）
uniform mat4 u_invViewProj;      // clip -> world
uniform vec3 u_cameraPos;
uniform vec2 u_win;              // 全分辨率尺寸（抖动需要，避免条带）

// === 射线步进 ===
uniform float u_density;         // 基础雾密度（越大越浓）
uniform float u_heightFalloff;   // 高度雾衰减：密度 ∝ exp(-y * falloff)
uniform float u_extinction;      // 消光系数（透过率衰减速度）
uniform float u_scattering;      // 散射强度
uniform float u_maxDistance;     // 最远步进距离（世界单位）
uniform int   u_steps;           // 步进次数（8..64）
uniform float u_frameIndex;      // 帧号：步进起点抖动
uniform float u_ambientScatter;  // 各向同性环境散射（让雾不至于全黑）

// === 雾层（Phase 5：3D 噪声驱动的"干冰雾"）===
// u_fogLayer == 0 时下面这一整组都不参与计算，密度退化成上面那条纯高度雾公式
// （老场景逐像素不变）。== 1 时：
//   密度 = 高度剖面(顶面可被噪声抬成起伏的"云顶") × 3D 噪声(两层错动流动 + 阈值化)
uniform int   u_fogLayer;        // 1 = 启用雾层
uniform sampler3D u_noiseTex;    // 3D 噪声（TextureVolumeNoise，必须可平铺）
uniform float u_noiseScale;      // 世界坐标 → 纹理坐标的缩放（1/图案尺寸）
uniform float u_noiseStrength;   // 噪声对密度的调制强度（1 = 全靠噪声）
uniform float u_noiseDetail;     // 第二层（G 通道）权重 → 错动流动感
uniform float u_noiseGrain;      // B 通道（Worley）权重 → 颗粒/气泡感
uniform vec3  u_noiseTiling;     // 三个附加层的尺度（相对 u_noiseScale）：
                                 // (次层, 三层, 宽层)。要与 1.0 不可通约（如 0.61/0.29/0.13）
                                 // —— 单层采样时整片雾每 1/u_noiseScale 米原样重复一次
uniform float u_noiseWarp;       // 低频域扭曲的最大位移（米，0 = 关掉）：把贴图的
                                 // 固有周期打散，也给出"翻涌"而不是"整体平移"的观感
uniform vec2  u_wind;            // 主层风速（xz 平面，单位/秒）
uniform vec2  u_wind2;           // 次层风速（方向/速度都不同才有"翻涌"）
uniform vec3  u_fogRange;        // (底, 顶, 顶面起伏幅度)
uniform float u_topSoftness;     // 顶/底过渡厚度（越小顶面越"分明"）。
                                 // 顶面那一侧会被 max(它, 起伏×0.4) 抬厚：
                                 // 过渡带比起伏还薄 = 掠射时一条条（见 fogDensityAt）
uniform float u_edge;            // 噪声阈值（越大空隙越多、边界越锐）
uniform float u_edgeSoftness;    // 阈值过渡宽度
uniform float u_fogSharpen;      // 噪声幂（>1 更硬边）
uniform float u_noiseTime;       // 流动相位（秒）
uniform vec2  u_fogCenter;       // 雾池中心（xz）
uniform vec2  u_fogSpread;       // (起始半径, 结束半径)；结束 <= 起始 = 不限范围
uniform int   u_emitterCount;    // 喷口数量（0..4）
uniform vec4  u_emitter[4];      // xyz = 位置，w = 半径
uniform float u_emitterPower;    // 喷口浓度增益（干冰从容器口冒出来）

// === 世界体积盒（FogVolume：把"一片雾"当成可放置的 GameObject）===
// u_volumeEnabled == 0：老路径（雾是相机的属性）逐像素不变 —— 射线步进 0..maxDistance
//                       （雾层模式则是 u_fogRange 那层平板），密度只看高度与噪声；
// u_volumeEnabled == 1：射线只步进**盒子**里那一段，密度再按"到侧面的距离"淡出
//                       （u_volumeSoftness 是软边宽度，用噪声扰动），盒子上下沿由
//                       u_fogRange 给出（Python 侧从盒子的上下沿推出来）。
//                       于是同一套密度场可以放很多份、放在任何地方、随便移动。
uniform int   u_volumeEnabled;
uniform vec3  u_volumeMin;       // 盒子的世界坐标（轴对齐）
uniform vec3  u_volumeMax;
uniform float u_volumeSoftness;  // 四个侧面的软边宽度（米；0 = 硬边）
uniform int   u_volumeAnchor;    // 1 = 噪声锚定在体积上（跟着体积走）/ 0 = 锚定世界
uniform float u_volumeYaw;       // 噪声采样绕体积中心的旋转（弧度）

// === 方向光 / 阴影 ===
uniform vec3      u_lightDir;    // 光线传播方向
uniform vec3      u_lightColor;  // 颜色 × 强度
uniform int       u_shadowEnabled;
uniform sampler2D u_shadowMap;
uniform mat4      u_lightSpaceMatrix;
uniform float     u_shadowTexel;
uniform float     u_shadowBias;

vec3 worldFromDepth(vec2 uv, float depth)
{
    vec4 clip = vec4(uv * 2.0 - 1.0, depth * 2.0 - 1.0, 1.0);
    vec4 world = u_invViewProj * clip;
    return world.xyz / max(world.w, 1e-6);
}

float hash12(vec2 p)
{
    vec3 p3 = fract(vec3(p.xyx) * 0.1031);
    p3 += dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

// 该世界点对方向光是否可见（查阴影贴图，2x2 PCF 让体积阴影柔和些）
float lightVisibility(vec3 worldPos)
{
    if (u_shadowEnabled == 0) {
        return 1.0;
    }
    vec4 lightPos = u_lightSpaceMatrix * vec4(worldPos, 1.0);
    vec3 proj = lightPos.xyz / lightPos.w * 0.5 + 0.5;
    if (proj.z > 1.0 || proj.x < 0.0 || proj.x > 1.0
            || proj.y < 0.0 || proj.y > 1.0) {
        return 1.0;
    }
    float depth = 0.0;
    for (int x = 0; x < 2; ++x) {
        for (int y = 0; y < 2; ++y) {
            vec2 offset = (vec2(float(x), float(y)) - 0.5) * u_shadowTexel;
            depth += texture(u_shadowMap, proj.xy + offset).r;
        }
    }
    depth *= 0.25;
    return (proj.z - u_shadowBias > depth) ? 0.0 : 1.0;
}

// === 密度场 ===
// 绕 y 轴旋转采样坐标：三套（各自可平铺的）格点错开角度后不再重合，合成图案的
// 周期 = 各层周期的最小公倍数（远大于雾池本身）→ 看不到"同一团雾又出现"。
vec3 rotY(vec3 p, float angle)
{
    float s = sin(angle);
    float c = cos(angle);
    return vec3(c * p.x - s * p.z, p.y, s * p.x + c * p.z);
}

// === 世界体积：采样空间 / 盒子淡出 ===
// 把世界坐标映射到"噪声采样坐标"：
//   - u_volumeEnabled == 0（老路径）：原样返回 → 逐像素不变
//   - 锚定体积（u_volumeAnchor == 1）：先在体积局部空间旋转 → 体积移动/旋转时
//     噪声跟着走（"这团雾"是同一个东西）；适合会动的雾团、绑在某个物体上的雾
//   - 锚定世界（== 0）：图案固定在世界里（风把它吹过体积），yaw 绕世界原点旋转
vec3 noiseSpace(vec3 pos)
{
    if (u_volumeEnabled == 0) {
        return pos;
    }
    if (u_volumeAnchor == 1) {
        return rotY(pos - (u_volumeMin + u_volumeMax) * 0.5, u_volumeYaw);
    }
    return rotY(pos, u_volumeYaw);
}

// 一维平板求交（d = 射线方向分量，lo/hi = 两个盒子面相对原点的距离）。
// 方向与板面平行（d≈0）时：原点在板外 → 直接判为无交（tExit < tEnter）。
void slabAxis(float d, float lo, float hi, inout float tEnter, inout float tExit)
{
    if (abs(d) < 1e-6) {
        if (lo > 0.0 || hi < 0.0) {
            tEnter = 1.0;
            tExit = 0.0;
        }
        return;
    }
    float t0 = lo / d;
    float t1 = hi / d;
    tEnter = max(tEnter, min(t0, t1));
    tExit = min(tExit, max(t0, t1));
}

// 射线与轴对齐盒子求交，返回 (tEnter, tExit)；无交时 tExit < tEnter。
// 三个轴各做一次平板裁剪，结果就是"射线在盒子里的那一段" —— 体积盒的射线步进
// 只需要这一段。拿不到这一段时整条射线都不参与，等于这个体积在这一像素上不存在。
vec2 boxIntersect(vec3 origin, vec3 direction, vec3 bmin, vec3 bmax)
{
    float tEnter = -1e30;
    float tExit = 1e30;
    slabAxis(direction.x, bmin.x - origin.x, bmax.x - origin.x, tEnter, tExit);
    slabAxis(direction.y, bmin.y - origin.y, bmax.y - origin.y, tEnter, tExit);
    slabAxis(direction.z, bmin.z - origin.z, bmax.z - origin.z, tEnter, tExit);
    return vec2(tEnter, tExit);
}

// 到盒子四个侧面的最近距离（米）：0 = 贴着盒面，< 0 = 在盒外
float boxSideDistance(vec3 pos)
{
    vec2 d0 = pos.xz - u_volumeMin.xz;
    vec2 d1 = u_volumeMax.xz - pos.xz;
    return min(min(d0.x, d1.x), min(d0.y, d1.y));
}

// 侧面软边：0 = 盒面处，1 = 深入软边宽度之后。
// `wobble` 用噪声调制软边宽度（0.75~1.25）：宽度固定的话，盒子的四条棱会在雾的
// 外缘留下一圈笔直的边界 —— 那和"干冰雾"的观感完全冲突。
float boxSideFade(vec3 pos, float wobble)
{
    float width = max(u_volumeSoftness * wobble, 1e-3);
    return smoothstep(0.0, width, boxSideDistance(pos));
}

// 上下软边：只有"盒子里的纯高度雾"（u_fogLayer == 0）需要 —— 雾层模式的纵向剖面
// 本来就有 bottom/top 两侧的 softness，再乘一次就是双重淡出。
float boxVerticalFade(vec3 pos)
{
    float soft = max(u_topSoftness, 1e-3);
    return smoothstep(u_volumeMin.y, u_volumeMin.y + soft, pos.y)
         * (1.0 - smoothstep(u_volumeMax.y - soft, u_volumeMax.y, pos.y));
}

// u_fogLayer == 0：老的高度雾（逐像素与前几期完全一致）
// u_fogLayer == 1：干冰雾层 —— 薄薄的平板 + 3D 噪声 + 阈值化
float fogDensityAt(vec3 pos)
{
    if (u_fogLayer == 0) {
        float density = u_density * exp(-max(pos.y, 0.0) * u_heightFalloff);
        if (u_volumeEnabled == 1) {
            // 盒子里的纯高度雾：四个侧面 + 上下沿都按软边淡出（老路径不变）
            density *= boxSideFade(pos, 1.0) * boxVerticalFade(pos);
        }
        return density;
    }

    float bottom = u_fogRange.x;
    float top = u_fogRange.y;
    // 顶面过渡带**不能比顶面起伏还薄**：起伏 ±u_fogRange.z 的一层"薄壳"在掠射
    // 视角下会沿视线拉成一条条 —— 壳的轮廓几乎与视线平行，屏幕上就是"雾本身
    // 一条一条"（加步数、关噪声都救不了：那是壳的几何，不是采样走样）。
    // 过渡带至少和起伏同量级，薄壳就变成有厚度的雾；u_fogRange.z == 0（顶面不
    // 起伏）时不受影响，仍是原来的锐利顶面。
    float topSoft = max(u_topSoftness, u_fogRange.z * 0.4);

    // 保守的纵向早退：这一步一次纹理都不采，直接判断该点是否**可能**落在雾层
    // 的纵向范围内（含两侧过渡带）。雾层很薄，掠射时大量步进点都在层外 ——
    // 下面三层错动比单层多采两次纹理，靠这个早退补回来。
    if (pos.y < bottom - u_topSoftness
            || pos.y > top + u_fogRange.z + topSoft) {
        return 0.0;
    }

    // === 去周期采样：三层错动 + 域扭曲 ===
    // 一张可平铺的 3D 贴图有**固有周期** = 1/u_noiseScale（0.2 → 5 米）。单层采样
    // 时整片雾每 5 米原样复制一次，俯视一眼就能认出"同一团雾又出现了"（这就是
    // "重复度太高"）。做法：
    //   - 三个**互不可通约**的尺度（1.0 / noiseTiling.x / noiseTiling.y），
    //     各绕 y 轴转一个不同角度 → 三套格点不重合，合成周期 = 公倍数 ≫ 雾池；
    //   - 再加一层**宽层**（周期最长的那个）当低频域扭曲，把坐标整体推开：
    //     取消残余的周期性，观感上是"翻涌"而不是"整体平移"。
    vec3 flow1 = vec3(u_wind.x, 0.0, u_wind.y) * u_noiseTime;
    vec3 flow2 = vec3(u_wind2.x, 0.0, u_wind2.y) * u_noiseTime;
    // 宽层的流向与另外两层都不同（wind 转 90° 再减速）：三层不会一起平移
    vec3 flow3 = vec3(-u_wind.y, 0.0, u_wind.x) * (u_noiseTime * 0.5);
    float wideScale = max(u_noiseTiling.z, 1e-3);
    // 采样空间：世界体积模式下可以锚定在体积上并绕体积中心旋转（见 noiseSpace）
    vec3 samplePos = noiseSpace(pos);
    vec4 layer0 = texture(u_noiseTex, rotY(samplePos, 0.37)
                                   * (u_noiseScale * wideScale) + flow3);
    // 域扭曲：宽层的另外三个通道当"推动方向"（低频 → 整块雾缓慢地拧一下）
    vec3 warped = samplePos + (vec3(layer0.g, layer0.b, layer0.a) - 0.5)
                             * (2.0 * u_noiseWarp);

    // 主层：低频大团块（R 通道）既决定浓淡，也把"云顶"抬成起伏的曲面
    vec4 layer1 = texture(u_noiseTex, rotY(warped, 0.0) * u_noiseScale + flow1);
    // 云顶用"主层 + 宽层"混合：只用主层的话云顶是 5 米一格的重复波浪
    float cap = top + (mix(layer1.r, layer0.r, 0.35) - 0.5) * 2.0
               * u_fogRange.z;

    // 高度剖面：底面之上很快铺满，顶面之前很快收干（softness 越小越"分明"）
    float profile = smoothstep(bottom, bottom + u_topSoftness, pos.y)
                  * (1.0 - smoothstep(cap - topSoft, cap, pos.y));
    if (profile <= 1e-4) {
        return 0.0;
    }

    // 世界体积（FogVolume）：四个侧面的软边。宽度用宽层噪声调制 —— 和"雾池半径"
    // 那套一样，边界被噪声咬出缺口，所以看到的是团块边缘而不是盒子的一条直边。
    float sideFade = 1.0;
    if (u_volumeEnabled == 1) {
        float expand = mix(layer0.r, layer1.r, 0.4);
        sideFade = boxSideFade(pos, 0.75 + 0.5 * expand);
        if (sideFade <= 1e-4) {
            return 0.0;
        }
    }

    // 次层 / 三层：不同尺度 + 不同旋转角 + 不同风速 → 三层错动，看起来像在翻涌。
    // 尺度倍率必须与 1.0 不可通约（默认 0.61 / 0.29）：是整数倍的话三层会同时对齐，
    // 合成图案又落回基础周期上（等于没改）。
    vec4 layer2 = texture(u_noiseTex, rotY(warped, 0.95)
                          * (u_noiseScale * u_noiseTiling.x) + flow2);
    vec4 layer3 = texture(u_noiseTex, rotY(warped, -1.31)
                          * (u_noiseScale * u_noiseTiling.y) + flow3);
    float billow = mix(layer1.r, mix(layer2.g, layer3.g, 0.5), u_noiseDetail);

    // 阈值化：把噪声压成"有边界"的团块 —— 干冰雾那种分明边缘就靠这一步，
    // 不阈值就是一坨均匀灰雾（u_edge 越大空隙越多，u_edgeSoftness 是过渡宽度）
    float edge = clamp(u_edge, 0.0, 0.95);
    float transition = max(u_edgeSoftness, 1e-3);
    float shaped = clamp((billow - edge) / transition, 0.0, 1.0);
    shaped = pow(shaped, max(u_fogSharpen, 1e-3));
    // 细颗粒（Worley 通道）：乘性调制，只影响"表皮"的颗粒感。
    // 三层各取一点（混在一起 → 颗粒本身也不带基周期）；u_noiseGrain = 0 即完全关掉，
    // 想要"雾面平滑、只有大团块"的观感就把它压到 0.1 以下。
    float grainSrc = mix(mix(layer1.b, layer2.b, 0.5), layer3.a, 0.4);
    float grain = mix(1.0, grainSrc * 1.6, u_noiseGrain);
    shaped *= grain;
    float noiseTerm = mix(1.0, shaped, clamp(u_noiseStrength, 0.0, 1.0));

    // 层内仍然可以有一点"越贴地越浓"的梯度（u_heightFalloff 沿用旧含义）
    float inside = exp(-max(pos.y - bottom, 0.0) * u_heightFalloff);
    // 雾池范围：干冰雾是"一滩"——从容器口向外铺开、越远越薄（也不至于铺满地图）。
    // 边界用**宽层为主**的噪声扰动：只用基础层的话，雾池边缘会是 5 米一格的波浪边
    //（周期性最藏不住的地方就是这种长边界）。
    float spread = 1.0;
    if (u_fogSpread.y > u_fogSpread.x) {
        float expand = mix(layer0.r, layer1.r, 0.4);
        float radius = length(pos.xz - u_fogCenter) * (0.75 + 0.5 * expand);
        spread = 1.0 - smoothstep(u_fogSpread.x, u_fogSpread.y, radius);
    }
    float density = u_density * profile * noiseTerm * inside * spread * sideFade;

    // 喷口：干冰从容器口/台阶上冒出来 → 附近加浓（脉动让它是"冒"而不是"亮灯"）
    for (int i = 0; i < 4; ++i) {
        if (i >= u_emitterCount) {
            break;
        }
        vec3 center = u_emitter[i].xyz;
        float radius = max(u_emitter[i].w, 1e-3);
        float d = distance(pos, center) / radius;
        float puff = 0.85 + 0.15 * sin(u_noiseTime * 2.3 + float(i) * 1.7);
        density += u_density * u_emitterPower * exp(-d * d) * profile
                 * spread * sideFade * puff;
    }
    return density;
}

// 射线与雾层平板 [yLow, yHigh] 求交，返回 (tEnter, tExit)；无交时 tExit < tEnter。
// 低雾层必须只步进这一段：同样的步数下，40 步铺在 2 米高的雾里比铺在 100 米
// 远的整条射线上细 50 倍 —— "雾顶边缘看得清"靠的就是它。
vec2 fogSlab(vec3 origin, vec3 direction, float yLow, float yHigh)
{
    if (abs(direction.y) < 1e-5) {
        if (origin.y < yLow || origin.y > yHigh) {
            return vec2(1.0, 0.0);
        }
        return vec2(0.0, 1e6);
    }
    float t0 = (yLow - origin.y) / direction.y;
    float t1 = (yHigh - origin.y) / direction.y;
    return vec2(min(t0, t1), max(t0, t1));
}

// 半分辨率：射线步进，输出 (散射, 透过率)
// tStart/tEnd 是"需要步进的那一段"（老模式是 0..maxDistance；雾层模式只给雾层那一段）
vec4 march(vec3 origin, vec3 direction, float tStart, float tEnd, float jitter)
{
    float span = max(tEnd - tStart, 0.0);
    float step = span / float(max(u_steps, 1));
    vec3 scattered = vec3(0.0);
    vec3 transmittance = vec3(1.0);
    for (int i = 0; i < u_steps; ++i) {
        float t = tStart + (float(i) + jitter) * step;
        if (t > tEnd) {
            break;
        }
        vec3 pos = origin + direction * t;
        // 密度场：老模式 = 高度雾；雾层模式 = 平板剖面 × 3D 噪声（见 fogDensityAt）
        float density = fogDensityAt(pos);
        if (density <= 1e-5) {
            continue;
        }
        // 体积光：只有被太阳照到的地方才发亮（阴影贴图就是体积阴影）
        float visibility = lightVisibility(pos);
        // 前向散射相位：迎着太阳看时更亮（这就是"丁达尔光"的观感来源）。
        // u_lightDir 是光线传播方向，所以朝向太阳 = -u_lightDir
        float phase = 0.25 + 0.75 * pow(max(dot(direction, -u_lightDir), 0.0), 4.0);
        vec3 inScatter = (u_lightColor * visibility * phase
                          + vec3(u_ambientScatter)) * density;
        scattered += transmittance * inScatter * step;
        transmittance *= exp(-density * u_extinction * step);
    }
    return vec4(scattered, dot(transmittance, vec3(1.0 / 3.0)));
}

void main()
{
    if (u_mode == 1) {
        // 全分辨率合成：散射是加性 HDR 项，必须在 tone mapping 之前叠加
        vec4 scatter = texture(u_scatter, vUV);
        vec3 color = texture(u_color, vUV).rgb * scatter.a
                     + scatter.rgb * u_scattering;
        outColor = vec4(color, 1.0);
        return;
    }

    // 半分辨率：由深度还原这条射线的终点距离
    float depth = texture(u_depth, vUV).r;
    vec3 worldFar = worldFromDepth(vUV, depth);
    vec3 ray = worldFar - u_cameraPos;
    float sceneDistance = length(ray);
    vec3 direction = sceneDistance > 1e-5 ? ray / sceneDistance
                                          : vec3(0.0, 0.0, -1.0);
    float maxDistance = min(sceneDistance, u_maxDistance);
    float tStart = 0.0;
    if (u_volumeEnabled == 1) {
        // 世界体积（FogVolume）：只步进"射线与盒子相交的那一段"。
        //   - 盒子外（射线擦过去、或完全在别的方向）→ 整个循环都跳过，零纹理采样
        //   - 上下各放一点余量：云顶起伏（u_fogRange.z）与顶面过渡带（u_topSoftness）
        //     要能"冒到盒顶之上"，否则噪声抬起的云顶会被盒子削平成一条直线
        vec3 pad = vec3(0.0, u_topSoftness + u_fogRange.z, 0.0);
        vec2 box = boxIntersect(u_cameraPos, direction,
                                u_volumeMin - pad, u_volumeMax + pad);
        if (box.y <= box.x || box.x > maxDistance || box.y <= 0.0) {
            outColor = vec4(0.0, 0.0, 0.0, 1.0);   // 透过率 1 = 没有雾
            return;
        }
        tStart = max(box.x, 0.0);
        maxDistance = min(maxDistance, box.y);
    } else if (u_fogLayer == 1) {
        // 老路径：只步进"射线穿过雾层平板的那一段"：
        //   - 雾层很薄（几米），同样的步数下分辨率高一个数量级 → 顶面边缘清晰
        //   - 射线完全在雾层外（比如一直朝上看）→ 直接跳过整个循环
        float yLow = u_fogRange.x - u_topSoftness;
        float yHigh = u_fogRange.y + u_fogRange.z + u_topSoftness;
        vec2 slab = fogSlab(u_cameraPos, direction, yLow, yHigh);
        if (slab.y <= slab.x || slab.x > maxDistance || slab.y <= 0.0) {
            outColor = vec4(0.0, 0.0, 0.0, 1.0);   // 透过率 1 = 没有雾
            return;
        }
        tStart = max(slab.x, 0.0);
        maxDistance = min(maxDistance, slab.y);
    }
    // 逐像素 + 逐帧抖动步进起点：把条带（banding）打散成噪声
    float jitter = hash12(vUV * u_win + vec2(u_frameIndex * 5.588238, 0.0));
    outColor = march(u_cameraPos, direction, tStart, maxDistance, jitter);
}
