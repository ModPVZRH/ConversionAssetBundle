# ConversionCulib

把 Unity **PC AssetBundle** 还原后，再打成 **Android AssetBundle** 的一键脚本。

流程：扫描原包 → AssetRipper 还原工程 → 导入 Unity → 按原包名重新打包 Android。

这是还原后再构建，不是改 AB 文件头。贴图、Shader、脚本引用都可能和原包有差异。

## 环境要求

- Windows + PowerShell
- Python 3.10+（建议 `pip install -r scripts/requirements.txt`，用于更完整的资源清单）
- [AssetRipper](https://github.com/AssetRipper/AssetRipper/releases)（`AssetRipper.GUI.Free.exe`）
- 与原包版本接近的 Unity Editor，并已安装 **Android Build Support**（SDK/NDK）

原包 Unity 版本以 inventory 阶段打印的 `unityVersion` 为准。版本差太大会导致序列化或 Shader 异常。

## 目录

```text
ConversionCulib/
  convert.ps1              一键入口
  config.json              路径和压缩参数
  input/                   放入 PC AssetBundle（可带 .manifest）
  output/android/          输出的 Android AssetBundle
  scripts/                 inventory / rip / import
  tools/AssetRipper/       放置 AssetRipper.GUI.Free.exe
  unity-template/          Editor 打包脚本模板
  work/                    中间产物（可删，阶段失败后可从对应步重跑）
```

## 使用

1. 把 PC AB 放到 `input/`（旁边的 `.manifest` 一并放进去更好，依赖更准）。
2. 把 AssetRipper 解压到 `tools/AssetRipper/`，保证存在：

   `tools/AssetRipper/AssetRipper.GUI.Free.exe`

3. 用终端界面改 `config.json`（至少确认 `unityEditor`、`unityVersion`）：

```powershell
.\config.ps1
```

或：

```powershell
pip install textual
python scripts/config_tui.py
```

快捷键：`Ctrl+S` 保存，`Ctrl+R` 重载，`Esc` 退出。也可直接改 `config.json`。

4. 一键转换：

```powershell
.\convert.ps1
```

某步失败后可以只重跑后面的阶段，不必从头 Rip：

```powershell
.\convert.ps1 -Stage inventory
.\convert.ps1 -Stage rip
.\convert.ps1 -Stage import
.\convert.ps1 -Stage build
```

指定配置文件：

```powershell
.\convert.ps1 -Config config.json
```

## 四个阶段

| 阶段 | 做什么 |
|---|---|
| `inventory` | 扫描 `input/`，写出 `work/inventory/mapping.json`（包名、资源、依赖、Unity 版本） |
| `rip` | 启动 AssetRipper 无界面 Web API，`LoadFolder` + `Export/UnityProject`，结果在 `work/ripped/` |
| `import` | 把还原工程灌进 `work/unity-project`，写入 Editor 脚本和分包名 |
| `build` | Unity `-batchmode` 打 Android AB 到 `output/android/` |

当前 AssetRipper **没有** CLI `export` 子命令。`rip` 使用：

- `POST /LoadFolder`
- `POST /Export/UnityProject`

## config.json

| 字段 | 说明 |
|---|---|
| `unityEditor` | Unity.exe 绝对路径 |
| `unityVersion` | 与 Editor 一致。若和包头版本不同，inventory 会警告 |
| `assetRipper` | `AssetRipper.GUI.Free.exe` 路径 |
| `python` | Python 可执行文件，默认 `python` |
| `inputDir` | PC AB 目录 |
| `outputDir` | Android AB 输出目录 |
| `unityProjectMode` | `ripped`：直接用 AssetRipper 导出工程打包（默认）；`template`：先复制 `unity-template/`，资源进 `Assets/Ripped/` |
| `androidTexture` | `ASTC`（推荐）/ `ETC2` / `DXT` |
| `astcBlockSize` | 仅 ASTC 有效：`4x4` `5x5` `6x6` `8x8` `10x10` `12x12`。数字越大体积越小、画质越差。无法识别时按 `8x8` |
| `maxTextureSize` | Android 贴图最长边上限。`0` 表示不限制 |
| `compression` | AB 压缩：`lzma` 最小、加载较慢；`lz4` 较大、加载较快；`none` 不压缩 |

体积优先示例：

```json
"androidTexture": "ASTC",
"astcBlockSize": "8x8",
"maxTextureSize": 1024,
"compression": "lzma"
```

画质优先示例：

```json
"astcBlockSize": "6x6",
"maxTextureSize": 2048,
"compression": "lz4"
```

改压缩参数后只需：

```powershell
.\convert.ps1 -Stage build
```

## 常见问题

**inventory 提示 Detected Unity 与 config 不一致**  
把 `unityEditor` / `unityVersion` 改成包头那个版本，或接受用现有 Editor 打开可能带来的差异。

**rip 报 Too many arguments**  
不要把输入路径和 `-o` 传给 `AssetRipper.GUI.Free.exe`。本仓库的 `convert.ps1` 已改为走 Web API。

**Android 包比 PC 包还大**  
PC 贴图多为 DXT，默认 ASTC 4x4/6x6 可能更大。提高 `astcBlockSize` 或降低 `maxTextureSize`。

**Spine 预制件动画不播 / 透明变黑**  
AssetRipper 会把 Spine 脚本和 `Spine/Skeleton` 导出成空壳，引用丢失，透明通道也会按不透明 Shader 画成黑色。import 阶段会盖上带序列化字段的脚本桩和 PMA Shader；打包时 Spine 图集关闭 `Alpha Is Transparency`。改完后重新 `.\convert.ps1 -Stage import` 再 `-Stage build`。运行时仍需要游戏里的官方 spine-unity，桩脚本只为把引用打进包。

**粉红材质 / 丢脚本字段**  
Shader 和 MonoBehaviour 依赖原工程脚本。有 DLL 可放进工程 `Assets/Plugins`；没有则只能用 AssetRipper 的占位脚本。

**运行时 hash / CRC 对不上**  
新包的校验值和原 PC 包一定不同。加载器不要沿用旧 hash。

**Addressables**  
本工具按普通 `BuildPipeline.BuildAssetBundles` 打包，不走 Addressables 构建管线。

## 许可与范围

只转换你有权处理的资源。AssetRipper 与 Unity 均为第三方工具，使用时遵守各自许可。
