# pyglgame/openal —— 音频后端（OpenAL）

这个包把 **OpenAL** 接进引擎：`Device`（设备/上下文）→ `Player`（source 池）→
`AudioResource`（PCM/WAV）→ `Audio`（面向游戏的便捷层），外加 `synth/` 里的钢琴音源。
`al.py` / `alc.py` / `library_loader.py` 是 OpenAL 的 ctypes 绑定（源自 PyOpenAL，
作者 Zuzu_Typ，PyPI 标注 *Public Domain*），**不受许可限制**。

## 随包分发的二进制不是本项目的代码（LGPL，别删声明）

| 文件 | 说明 |
|---|---|
| `lib/OpenAL64.dll` | OpenAL Soft，内嵌版本串 `1.1 ALSOFT 1.15.1`（Windows 64 位） |
| `lib/OpenAL32.dll` | OpenAL Soft，内嵌版本串 `1.1 ALSOFT 1.15.1`（Windows 32 位） |
| `lib/libopenal32.so` | OpenAL Soft（Linux） |
| `soft_oal_64.dll` | OpenAL Soft，内嵌版本串 `1.1 ALSOFT 1.18.2` |

* 上游：<https://github.com/kcat/openal-soft>（官网 <https://openal-soft.org/>）
* 许可：**LGPL**（上游 `COPYING` 是 *GNU Library General Public License v2*）
* 修改情况：**未修改**；由 `Device` 在运行期用 `ctypes` 动态加载（见 `Device.py`），
  所以用户可以**自行替换**这些 DLL —— 这正是 LGPL 要的"可重新链接"能力。
* **就地声明**：[`LICENSE-openal-soft.txt`](LICENSE-openal-soft.txt)（与
  `LICENSES/openal-soft/LICENSE-openal-soft.txt` 同一份，由
  `tools/collect_licenses.py` 生成）。发行时它要跟包走。

## 为什么把 DLL 放在包里而不是系统目录

引擎按**模块所在目录**找 DLL（`Device.py` 用 `dirname(__file__)/lib/<名字>`），
这样换台机器、不装 OpenAL 也能跑；`library_loader` 还会按
`soft_oal` → `OpenAL32` → `openal` 依次尝试，找不到就抛
`ExternalLibraryError`（`AudioResource.AudioUnavailableError` 是它的上层包装）。

## 相关

* `tools/collect_licenses.py` —— 生成上面的 LICENSE 与 `LICENSES/`
* `THIRD_PARTY_NOTICES.md` / `docs/第三方许可.md` —— 汇总与做法
* `docs/` 其余文档：音频/合成器用法（`openal/synth/` 的 docstring 里有例子）
