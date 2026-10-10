#version 430 core

// 格式标记（保持与 CPU 和 VS 一致）
const int POS = 1;
const int COL = 2;
const int TEX = 4;
const int NOR = 8;
const int LIT = 16;
const int SIZ = 32;
// 2D 光照的材质开关位（Phase 7）：与 pyglgame/render/BufferBuilder.py 一一对应。
// N2D -> tex3 是 2D 法线贴图；S2D -> tex4 是 2D 高光贴图。
const int N2D = 64;
const int S2D = 128;
// 2D 光照的"整批开关"（与 pyglgame/render/BufferBuilder.py 的 AMB2D / UNLIT2D 一一对应）
const int AMB2D = 256;    // 只吃环境光：直接光 / 高光 / 阴影整段跳过
const int UNLIT2D = 512;  // 完全不受光照：环境光也不加，只输出颜色 / 贴图

const int DIS = 0;
const int WID = 1;
const int HDR = 2;
const int PIN = 3;
const int BLM = 4;
const int MIX = 5;
const int OIT = 6;        // WBOIT：世界空间半透明
const int OIT_WID = 7;    // WBOIT：屏幕空间半透明
const int SHADOW = 8;     // 阴影贴图 pass（只写深度，本函数不产生颜色）
const int SHADOW2D = 9;   // 2D 阴影遮罩 pass：遮挡体的阴影三角形写 0（见 ShadowMask2D.py）
const int OBJECT_ID = 10;      // Object ID / Mask pass：片元只输出"当前物体唯一 ID"
const int OBJECT_ID_DEBUG = 11;// 把 ID 纹理画到屏幕的调试模式（伪彩色）
const int OBJECT_ID_SKIP = 12; // ID pass 里的屏幕空间绘制（全屏背景 / UI）：直接丢弃

// 输入
in vec4 fragColor;
in vec2 fragTexCoord;
in vec3 fragNormal;
in vec2 fragLightMap;
in vec3 fragWorldPos;
flat in int fragFormatType;

// Uniforms
uniform vec2 win_wh;
uniform sampler2D tex0;
uniform sampler2D tex1;
uniform sampler2D tex2;
uniform sampler2D tex3;
uniform sampler2D tex4;
uniform sampler2D tex5;

// === WBOIT（加权混合顺序无关透明）===
uniform float u_near;
uniform float u_far;
uniform float u_ortho;        // 1 = 正交/2D 投影
uniform float u_depthScale;   // 深度权重缩放（默认 1/200）

uniform int fuc;

// === 光照（前向，最多 MAX_LIGHTS 盏；u_lightCount == 0 时整段跳过）===
#define MAX_LIGHTS 8
uniform int  u_lightCount;
uniform int  u_lightKind[MAX_LIGHTS];     // 0 方向光 / 1 点光 / 2 聚光
uniform vec3 u_lightDir[MAX_LIGHTS];      // 光线传播方向（从光源射出）
uniform vec3 u_lightPos[MAX_LIGHTS];      // 点光/聚光位置
uniform vec3 u_lightColor[MAX_LIGHTS];    // 颜色 * 强度
uniform vec4 u_lightParam[MAX_LIGHTS];    // x=半径 y=聚光半角余弦 z=投影阴影 w=高光强度
uniform vec4 u_lightParam2[MAX_LIGHTS];   // x=距离衰减指数 n(默认 2 = 1/d²) y/z/w=保留
// 半球环境光：只与环境项相乘（不参与直接光，避免整体发灰）
uniform vec3 u_ambientSky;
uniform vec3 u_ambientGround;
uniform vec3 u_cameraPos;                 // 高光需要视线方向

// === 阴影（Phase 2：单张方向光阴影贴图）===
uniform int       u_shadowLight;          // 使用 u_shadowMap0 的光源下标，-1 = 关闭
uniform sampler2D u_shadowMap0;
uniform mat4      u_lightSpaceMatrix;     // 与顶点着色器同名（同一个 program 的 uniform）
uniform float     u_shadowTexel;          // 1.0 / 阴影贴图分辨率
uniform float     u_shadowBias;           // 归一化深度偏置（[0,1] 深度空间）
uniform float     u_shadowNormalOffset;   // 法线偏移的世界单位（= 纹素世界尺寸 × 倍数）

// === 环境光遮蔽（Phase 3：SSAO 结果，按屏幕坐标采样）===
uniform sampler2D u_aoTex;
uniform int       u_aoEnabled;
uniform float     u_aoStrength;

// === 2D 光照（Phase 7）：法线贴图 / 高光贴图 / 平环境光 ===
// 约定：u_light2D == 0 且本批次没有 N2D/SPC2 位时，下面这些 uniform 一律不参与
// 计算 —— 所以 3D 画面与加这期之前**逐像素一致**。详见 docs/2D光照与阴影.md
uniform int       u_light2D;          // 1 = 2D 模式：法线来自贴图、环境光用 u_ambientFlat
uniform vec3      u_ambientFlat;      // 2D 模式的环境光（精灵的 N.y 没有物理意义，不用半球）
uniform float     u_normalStrength;   // 法线贴图强度（0 = 完全平；1 = 原样）
uniform vec2      u_materialScale;    // 材质 UV 变换：uv = fragTexCoord * scale + offset
uniform vec2      u_materialOffset;
uniform float     u_materialRotation; // 2D 物体自身的旋转（度）→ 法线跟着转
uniform vec3      u_specularColor;    // 高光颜色（会与高光贴图的 rgb 相乘）
uniform float     u_specularStrength; // 高光强度总系数（与每盏光的 specular 相乘）
uniform float     u_specularShininess;// Blinn-Phong 指数（默认 32，与 3D 一致）
uniform int       u_specularMode;     // 高光贴图的语义：0 = 镜面光斑（默认）、1 = 直接光亮度倍率
uniform float     u_specularContrast; // 高光贴图对比度：以 a=0.5 为中点拉对比（1 = 原样）

// === 2D 阴影（Phase 7）：每盏投影灯一层**屏幕空间**遮挡遮罩 ===
// 遮罩 1 = 该像素被这盏灯照到 / 0 = 被物体挡住（见 pyglgame/render/ShadowMask2D.py）。
// u_shadow2DCast[i] == 0（默认）时这一路完全不参与计算 —— 3D 场景与加这期之前逐像素一致。
uniform sampler2DArray u_shadowMask2D; // 层 i = 第 i 盏灯的遮挡遮罩
uniform int            u_shadow2DCast[MAX_LIGHTS]; // 1 = 第 i 盏灯要采样遮罩
uniform float          u_receiveShadow;// 0 = 该材质不接收 2D 阴影（"物体层"自己的精灵）

// === Object ID / Mask（GPU Picking）===
// > 这里复用同一个 uber shader：fuc == OBJECT_ID 时片元只写物体的唯一 ID，
//   后处理也可以用 objectIdMaskAt() 按 ID 做马赛克 / 描边 / 高亮。
//   见 pyglgame/render/ObjectIdBuffer.py 与 ObjectIdRegistry.py。
uniform float     u_objectId;           // 当前绘制的物体 ID（0 = 背景）
uniform float     u_objectIdAlphaTest;  // 1 = ID pass 里丢弃贴图透明（a<0.5）的像素
uniform sampler2D u_objectIdTex;        // 上一帧 / 本帧的 ID 纹理（调试显示 + 后处理掩码）
uniform int       u_objectIdMask;       // 后处理掩码：>0 时只保留该 ID 的像素（0 = 关闭）
uniform vec3      u_objectIdHighlight;  // 命中 u_objectIdMask 的像素要高亮成的颜色
uniform float     u_objectIdHighlightStrength; // 高亮强度（0 = 不高亮，默认 0）

// 前向声明：objectIdMaskAt() 定义在文件后半段，dis() 的高亮要用它
float objectIdMaskAt(vec2 uv);

// 输出颜色
out vec4 outColor;
// WBOIT 第二个输出：累积透过率 ∏(1-a)，只有在绑定两个 draw buffer 时才写入
layout(location = 1) out vec4 outReveal;

// 默认颜色
const vec4 defaultColor = vec4(1.0);


vec3 toneMap(vec3 hdrColor) {
    return hdrColor / (hdrColor + vec3(1.0));
}

vec3 toneMapACES(vec3 x) {
    const float a = 2.51;
    const float b = 0.03;
    const float c = 2.43;
    const float d = 0.59;
    const float e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), 0.0, 1.0);
}


// === 光照 ===

// 3x3 PCF + normal offset（阴影贴图 pass 见 ShadowMap.py）
float shadowFactor(vec3 worldPos, vec3 N, float ndotl)
{
    if (u_shadowLight < 0) {
        return 1.0;
    }
    // normal offset：沿法线把采样点推离表面（世界单位，见 ShadowMap.normal_offset_scale），
    // 斜率越大推得越多。这比单纯加大深度偏置更不容易出现自阴影（acne）
    float slope = clamp(1.0 - ndotl, 0.0, 1.0);
    vec3 offsetPos = worldPos + N * (u_shadowNormalOffset * slope);
    vec4 lightPos = u_lightSpaceMatrix * vec4(offsetPos, 1.0);
    vec3 proj = lightPos.xyz / lightPos.w * 0.5 + 0.5;
    // 光锥/正交范围之外一律认为被照亮（否则边缘会出现一圈黑）
    if (proj.z > 1.0 || proj.x < 0.0 || proj.x > 1.0
            || proj.y < 0.0 || proj.y > 1.0) {
        return 1.0;
    }
    // 斜率偏置：正面（ndotl→0）时加大，抑制阴影痤疮
    float bias = max(u_shadowBias * (1.0 - ndotl), u_shadowBias * 0.25);
    float lit = 0.0;
    for (int x = -1; x <= 1; ++x) {
        for (int y = -1; y <= 1; ++y) {
            float depth = texture(u_shadowMap0,
                proj.xy + vec2(float(x), float(y)) * u_shadowTexel).r;
            lit += (proj.z - bias > depth) ? 0.0 : 1.0;
        }
    }
    return lit / 9.0;
}

// 环境光遮蔽（SSAO 结果，按屏幕坐标采样）：只压低间接光
float ambientOcclusion()
{
    if (u_aoEnabled == 0) {
        return 1.0;
    }
    vec2 uv = gl_FragCoord.xy / win_wh;
    float ao = texture(u_aoTex, uv).r;
    return mix(1.0, ao, clamp(u_aoStrength, 0.0, 1.0));
}

// 材质 UV：默认 (1,1)/(0,0) → 就等于顶点 UV，所以 3D 材质与旧路径完全不受影响
vec2 materialUV()
{
    return fragTexCoord * u_materialScale + u_materialOffset;
}

// 片元法线：3D 用顶点法线；2D 模式（u_light2D == 1）且本批次带 N2D 位时用法线贴图。
// 2D 精灵默认躺在 XY 平面上、朝向 +Z，所以切线空间的三个轴就是
// (x = 世界 +X, y = 世界 +Y, z = 朝相机)；被镜像（负 scale）或旋转过的精灵
// 由 u_materialScale / u_materialRotation 修正，保证高光跟着图形一起转。
vec3 shadeNormal()
{
    if (u_light2D == 0 || (fragFormatType & N2D) == 0) {
        return normalize(fragNormal);
    }
    vec3 n = texture(tex3, materialUV()).xyz * 2.0 - 1.0;
    n.xy *= u_normalStrength * sign(u_materialScale);
    if (u_materialRotation != 0.0) {
        float rad = radians(u_materialRotation);
        float c = cos(rad);
        float s = sin(rad);
        n.xy = mat2(c, -s, s, c) * n.xy;
    }
    // z 由 xy 反推（不是直接用贴图的 z）：强度>1 时也保持单位长度，
    // 而且永远 >= 0 → 不会出现"背对相机"的死黑面
    n.z = sqrt(max(0.0, 1.0 - min(dot(n.xy, n.xy), 1.0)));
    return normalize(n);
}

// 前向光照：半球环境光（2D 用平环境光）+ 每盏光（Lambert 漫反射 + Blinn-Phong 高光 + 阴影）
vec3 shadeLighting(vec3 albedo, vec3 N, vec3 worldPos, vec3 viewDir, float ao)
{
    float up = clamp(N.y * 0.5 + 0.5, 0.0, 1.0);
    // 间接光：受环境光遮蔽影响（直接光不受，否则缝隙外也会变暗）。
    // 2D 模式换成"平环境光"：精灵的法线朝 +Z，拿 N.y 去混天空/地面没有意义。
    vec3 ambient = (u_light2D == 1)
        ? u_ambientFlat
        : mix(u_ambientGround, u_ambientSky, up);
    vec3 color = albedo * ambient * ao;

    // 只吃环境光（AMB2D 位，逐 draw call）：直接光 / 高光 / **阴影** 整段跳过。
    // 阴影只作用在直接光上，所以「只受环境光」天然也不受阴影影响。
    if ((fragFormatType & AMB2D) != 0) {
        return color;
    }

    for (int i = 0; i < u_lightCount; ++i) {
        float attenuation = 1.0;
        vec3 L;                            // 表面 -> 光源
        if (u_lightKind[i] == 0) {
            // 方向光：u_lightDir 是光线传播方向，所以取反
            L = -normalize(u_lightDir[i]);
        } else {
            vec3 toLight = u_lightPos[i] - worldPos;
            float dist = length(toLight);
            L = toLight / max(dist, 1e-5);
            float radius = u_lightParam[i].x;
            // 距离衰减：指数 n 可调（"1/dⁿ" 里的那个 n），默认 2 = 平方反比。
            // n=2 时直接写 d*d / (d/r)²（省 pow，且与幂律一致）；n 是别的数才走 pow()。
            float exponent = max(u_lightParam2[i].x, 0.00001);   // n <= 0 没有意义（负指数会爆亮）
            if (radius > 0.0) {
                if (exponent == 2.0) {
                    // 到 radius 处正好归零的平滑截断 (1 - (d/r)²)²（不会除零爆亮）
                    float f = clamp(1.0 - (dist * dist) / (radius * radius), 0.0, 1.0);
                    attenuation = f * f;
                } else {
                    // 同一个形状，指数换成 n：n 越大"内部越平、到 radius 处掉得越干脆"
                    // （像聚光灯）；n 越小"从中心到边缘越均匀地暗下去"
                    float f = clamp(1.0 - pow(dist / radius, exponent), 0.0, 1.0);
                    attenuation = pow(f, exponent);
                }
            } else if (exponent == 2.0) {
                // 1/(1 + d²)：平方反比（"+1" 防 d→0 爆亮）
                attenuation = 1.0 / (1.0 + dist * dist);
            } else {
                // 1/(1 + dⁿ)：n=1 线性（≈ 1/d）、0.5 = 更慢（≈ 1/√d）、0.25 = 更慢
                attenuation = 1.0 / (1.0 + pow(dist, exponent));
            }
            if (u_lightKind[i] == 2) {
                // 聚光：片段方向与光锥轴的夹角
                float cosAngle = dot(normalize(-L), normalize(u_lightDir[i]));
                float cosHalf = u_lightParam[i].y;
                attenuation *= smoothstep(cosHalf, mix(cosHalf, 1.0, 0.5), cosAngle);
            }
        }
        if (attenuation <= 0.0) {
            continue;
        }
        float ndotl = dot(N, L);
        if (ndotl <= 0.0) {
            continue;
        }
        float shadow = (i == u_shadowLight) ? shadowFactor(worldPos, N, ndotl) : 1.0;
        // 2D 阴影：按**屏幕坐标**查第 i 盏灯的遮挡遮罩（1 = 照到 / 0 = 被物体挡住）。
        // 与阴影贴图不同，这一路既不比较深度也不管视角：物体背后就是没光。
        // u_receiveShadow = 0 的材质（例如"物体层"自己的精灵）不接收这一路。
        if (u_shadow2DCast[i] == 1) {
            vec2 shadowUV = gl_FragCoord.xy / win_wh;
            shadow *= mix(1.0, texture(u_shadowMask2D, vec3(shadowUV, float(i))).r,
                          u_receiveShadow);
        }
        if (shadow <= 0.0) {
            continue;
        }
        vec3 radiance = u_lightColor[i] * attenuation * shadow;
        color += albedo * radiance * ndotl;
        // 高光贴图（tex4，S2D 位）。两种语义，由 u_specularMode 选：
        //   0 = Blinn-Phong 镜面光斑（默认）：a = 光斑强度、rgb = 光斑颜色 —— 只在反射方向
        //       对上的地方可见（视角相关，shininess 决定光斑大小）；
        //   1 = 直接光亮度倍率：a × 2 = 该像素**直接光**的倍率（0.5 = 不变、1 = 亮一倍、
        //       0 = 全灭），rgb = 染色；跟视角无关，只要被照亮（N·L > 0）就逐像素生效。
        //       环境光不受影响（它不在这里，只在循环外面加过一次）。见 docs/2D光照与阴影.md
        bool hasSpecularTex = ((fragFormatType & S2D) != 0);
        vec4 specularTex = vec4(1.0);
        if (hasSpecularTex) {
            specularTex = texture(tex4, materialUV());
        }
        // 高光贴图**对比度**：以 a=0.5 为中点拉对比（1 = 原样；>1 更锐利、<1 更平）。
        // 只有真的用了高光贴图（S2D 位）才参与 —— 3D 对象没有贴图、a 恒为 1，不受影响。
        float specularAlpha = specularTex.a;
        if (hasSpecularTex && u_specularContrast != 1.0) {
            specularAlpha = clamp((specularAlpha - 0.5) * u_specularContrast + 0.5, 0.0, 1.0);
        }
        if (u_specularMode == 1 && hasSpecularTex) {
            // 直接光本来已经加过 albedo * radiance * ndotl，这里补上"倍率相对 1.0 的差"，
            // 等价于把直接光整体乘 (tint * boost) —— 但不用重排上面的式子。
            vec3  tint = u_specularColor * specularTex.rgb;
            float boost = 1.0 + (specularAlpha * 2.0 - 1.0) * u_specularStrength;
            color += albedo * radiance * ndotl * (tint * boost - 1.0);
        } else {
            float specularStrength = u_lightParam[i].w * u_specularStrength;
            vec3 specularColor = u_specularColor;
            if (hasSpecularTex) {
                specularStrength *= specularAlpha;
                specularColor *= specularTex.rgb;
            }
            if (specularStrength > 0.0) {
                vec3 H = normalize(L + viewDir);
                float spec = pow(max(dot(N, H), 0.0), max(u_specularShininess, 1.0));
                color += radiance * specularColor * (spec * specularStrength);
            }
        }
    }
    return color;
}

vec4 dis()
{
    vec4 color = vec4(1.0);

    if ((fragFormatType & COL) != 0) {
        color *= fragColor;
    }

    if ((fragFormatType & TEX) != 0) {
        color *= texture(tex0, fragTexCoord);
    }

    if ((fragFormatType & LIT) != 0) {
        color *= texture(tex5, fragLightMap);
    }

    // 光照：只有"场景里确实有光源"**且本批次不是 UNLIT2D**（完全不受光照）时才启用 ——
    // 没有光源时逐像素保持旧结果（向前兼容：所有老 demo/测试画面完全不变）
    if (u_lightCount > 0 && (fragFormatType & UNLIT2D) == 0) {
        // 3D 用顶点法线；2D 模式带法线贴图时由 shadeNormal() 改用法线贴图
        vec3 N = shadeNormal();
        vec3 viewDir = normalize(u_cameraPos - fragWorldPos);
        color.rgb = shadeLighting(color.rgb, N, fragWorldPos, viewDir,
                                  ambientOcclusion());
    }

    // === Object ID → 高亮（默认关，需求 10 的示例效果）===
    // u_objectIdMask > 0 时把命中该物体 ID 的像素朝 u_objectIdHighlight 混；
    // 默认（u_objectIdMask = 0 / 强度 0）时 objectIdMaskAt() 恒返回 0，
    // 画面与加这期之前逐像素一致。
    if (u_objectIdHighlightStrength > 0.0) {
        vec2 idUV = gl_FragCoord.xy / max(win_wh, vec2(1.0));
        float idMask = objectIdMaskAt(idUV);
        color.rgb = mix(color.rgb, u_objectIdHighlight,
                        idMask * u_objectIdHighlightStrength);
    }

    return color;
}

vec4 wid()
{
    vec4 color = vec4(1.0);

    if ((fragFormatType & COL) != 0) {
        color *= fragColor;
    }

    if ((fragFormatType & TEX) != 0) {
        color *= texture(tex0, fragTexCoord);
    }

    return color;
}

vec4 hdr()
{
    vec4 color = vec4(1.0);

    if ((fragFormatType & COL) != 0) {
        color *= fragColor;
    }

    if ((fragFormatType & TEX) != 0) {
        color *= texture(tex0, fragTexCoord);
    }

    float brightness = dot(color.rgb, vec3(0.2126, 0.7152, 0.0722));
    if (brightness < 1.0) {
        return vec4(0.0, 0.0, 0.0, 1.0); // 舍弃非高亮区域
    }

    vec3 mapped = toneMapACES(color.rgb);
    return vec4(mapped, 1.0);
}


vec4 pin()
{
    vec4 color = vec4(1.0);
    if ((fragFormatType & COL) != 0) {
        color *= fragColor;
    }
    if ((fragFormatType & TEX) != 0) {
        vec2 pixTex = fragTexCoord * win_wh;
        vec2 offsets[5] = vec2[](
            vec2(0.0, 0.0),
            vec2(5.0, 5.0),
            vec2(-5.0, 5.0),
            vec2(5.0, -5.0),
            vec2(-5.0, -5.0)
        );
        vec3 sum = vec3(0.0);
        for (int i = 0; i < 5; ++i) {
            vec2 uv = (pixTex + offsets[i]) / win_wh;
            sum += texture(tex0, uv).rgb;
        }

        vec3 avg = sum / 5.0;
        color *= vec4(avg, 1.0);
    }
    return color;
}

vec4 blm()
{
    vec4 color = vec4(1.0);

    if ((fragFormatType & COL) != 0) {
        color *= fragColor;
    }

    if ((fragFormatType & TEX) != 0) {
        vec4 hdrColor = texture(tex0, fragTexCoord);
        vec3 mapped = toneMap(hdrColor.rgb);
        color *= vec4(mapped, 1.0);
    }

    return color;
}


vec4 mix()
{
    vec4 color = vec4(1.0);

    if ((fragFormatType & COL) != 0) {
        color *= fragColor;
    }

    if ((fragFormatType & TEX) != 0) {
        vec3 base = texture(tex0, fragTexCoord).rgb;  // 原始图像
        vec3 bloom = texture(tex1, fragTexCoord).rgb; // 模糊后的高亮图
        vec3 combined = base + bloom; // 简单相加

        vec3 mapped = toneMapACES(combined);
        color *= vec4(mapped, 1.0);
    }

    return color;
}

// 2D 阴影遮罩 pass：只写"被挡住"（0）；没画到的像素保持清屏时的 1.0（= 被照亮）。
// 遮挡体把"沿光源方向扫出的阴影三角形"画进来，重叠区域反复写 0 也不会出错。
vec4 shadow2d()
{
    return vec4(0.0, 0.0, 0.0, 1.0);
}

// === WBOIT：加权混合顺序无关透明 ===
// 把片元看作"带权重的半透明层"：accum += color * a * w，accum.a += a * w，
// reveal *= (1 - a)。最终由 oit_resolve 合成，与绘制顺序无关。
float oitLinearDepth()
{
    float ndc = gl_FragCoord.z * 2.0 - 1.0;
    if (u_ortho > 0.5) {
        return u_near + (ndc * 0.5 + 0.5) * (u_far - u_near);
    }
    // 透视：由非线性深度还原到视空间距离
    return (2.0 * u_near * u_far) / (u_far + u_near - ndc * (u_far - u_near));
}

float oitWeight(float alpha)
{
    float z = oitLinearDepth() * u_depthScale;
    // Morgan McGuire 的权重函数：抑制近处/远处的半透明，突出中间深度
    return alpha * max(0.03, min(1.0, 10.0 / (1e-5 + z * z + z * z * z * z)));
}

vec4 oit(bool screenSpace)
{
    vec4 color = screenSpace ? wid() : dis();
    float a = clamp(color.a, 0.0, 1.0);
    float w = oitWeight(a);
    outReveal = vec4(a, 0.0, 0.0, 0.0);
    return vec4(color.rgb * a * w, a * w);
}

// === Object ID / Mask ===
vec4 objectId()
{
    // 只输出 ID，不做任何光照 / 材质。ID 写在 .r（GL_R32F）。
    // 带贴图的物体默认做一次 alpha 测试：完全透明的像素不写 ID，
    // 这样 ID 纹理同时就是"物体轮廓掩码"（马赛克 / 描边 / 高亮要用）。
    if (u_objectIdAlphaTest > 0.5 && (fragFormatType & TEX) != 0) {
        if (texture(tex0, fragTexCoord).a < 0.5) {
            discard;
        }
    }
    return vec4(u_objectId, 0.0, 0.0, 1.0);
}

vec4 objectIdDebug()
{
    // 把 ID 纹理按 ID 哈希成伪彩色，方便肉眼调试（背景画成深灰）。
    float id = texture(u_objectIdTex, fragTexCoord).r;
    if (id < 0.5) {
        return vec4(0.06, 0.06, 0.08, 1.0);
    }
    float h = fract(sin(id * 12.9898 + 4.1414) * 43758.5453);
    vec3 color = 0.55 + 0.45 * cos(6.2831853 * (h + vec3(0.0, 0.333, 0.667)));
    return vec4(color, 1.0);
}

// 后处理可直接复用的掩码：uv 处像素属于 u_objectIdMask 指定的物体时返回 1，否则 0。
// u_objectIdMask <= 0（默认）时恒为 0 —— 不开这个功能的后处理与以前逐像素一致。
float objectIdMaskAt(vec2 uv)
{
    if (u_objectIdMask <= 0) {
        return 0.0;
    }
    float id = texture(u_objectIdTex, uv).r;
    return (abs(id - float(u_objectIdMask)) < 0.5) ? 1.0 : 0.0;
}


void main()
{
    outReveal = vec4(0.0);
    if (fuc == DIS) {
        outColor = dis();
    } else if (fuc == WID) {
        outColor = wid();
    } else if (fuc == HDR) {
        outColor = hdr();
    } else if (fuc == PIN) {
        outColor = pin();
    } else if (fuc == BLM) {
        outColor = blm();
    } else if (fuc == MIX) {
        outColor = mix();
    } else if (fuc == OIT) {
        outColor = oit(false);
    } else if (fuc == OIT_WID) {
        outColor = oit(true);
    } else if (fuc == SHADOW) {
        // 阴影 pass：颜色输出无意义（glColorMask 已关闭），只为驱动深度写入
        outColor = vec4(0.0);
    } else if (fuc == SHADOW2D) {
        // 2D 阴影遮罩 pass：把遮挡体的阴影三角形写成 0（= 挡住）
        outColor = shadow2d();
    } else if (fuc == OBJECT_ID) {
        // Object ID / Mask pass：只写物体 ID（不做光照 / 材质）
        outColor = objectId();
    } else if (fuc == OBJECT_ID_DEBUG) {
        // 调试显示：把 ID 纹理伪彩色地画到屏幕
        outColor = objectIdDebug();
    } else if (fuc == OBJECT_ID_SKIP) {
        // ID pass 里的屏幕空间绘制（全屏背景 / UI 覆盖层）：不写 ID、不写深度。
        // 少了这个分支，全屏背景会把整个 ID 缓冲写上一个"假 ID"（而且深度写入
        // 会挡住后面的物体）→ GPU Picking 直接失效。
        discard;
    } else {
        outColor = vec4(1.0, 0.0, 1.0, 1.0); // 未知渲染模式：高亮粉色
    }
}
