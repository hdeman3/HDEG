# 🎙️ Faster Whisper 转录工具 - 发行说明

## ⚠️ 重要声明

> **本软件为开源软件**
>
> 🔗 **开源地址**: https://github.com/TransWithAI/Faster-Whisper-TransWithAI-ChickenRice
>
> 👥 **开发团队**: AI汉化组 (https://t.me/transWithAI)

---

## 📦 发行包说明

本发行版包含多个变体版本，请根据您的显卡型号选择合适的版本：

### 🎯 版本类型说明

#### 无主模型版（No-Model Package）
- **文件后缀**：`-nomodel`
- **包含内容**：
  - ✅ 所有 GPU 依赖项
  - ✅ 音声优化 VAD（语音活动检测）ONNX 模型
  - ✅ whisper-base 特征提取文件（离线使用）
  - ✅ 翻译和转录两套 `.bat` 启动脚本
  - ❌ 不含主 Whisper 模型（需自行下载或替换）
- **适用场景**：需要使用自定义模型的用户

#### 翻译版（Translate Package）
- **文件后缀**：`-translate`
- **包含内容**：
  - ✅ 所有 GPU 依赖项
  - ✅ 音声优化 VAD（语音活动检测）ONNX 模型
  - ✅ whisper-base 特征提取文件（离线使用）
  - ✅ **"海南鸡v2 5000小时"** 日文转中文优化模型
  - ✅ 仅包含翻译 `.bat` 启动脚本
- **适用场景**：开箱即用的日文转中文翻译
- **模型说明**：包含经过5000小时音频数据训练的海南鸡v2版本模型，专门优化日文转中文翻译

#### 转录版（Transcribe Package）
- **文件后缀**：`-transcribe`
- **包含内容**：
  - ✅ 所有 GPU 依赖项
  - ✅ 音声优化 VAD（语音活动检测）ONNX 模型
  - ✅ whisper-base 特征提取文件（离线使用）
  - ✅ **Jim6789/whisper-ja-1.5B-ct2** 日文原文转录模型
  - ✅ 仅包含转录 `.bat` 启动脚本
- **适用场景**：开箱即用的日文原文转录

### 📌 文件命名规则

| 文件名格式 | CUDA/ROCm | 模型类型 |
|-----------|---------|---------|
| `*_cu118-nomodel.zip` | CUDA 11.8 | 无主模型版 |
| `*_cu118-translate.zip` | CUDA 11.8 | 翻译版 |
| `*_cu118-transcribe.zip` | CUDA 11.8 | 转录版 |
| `*_cu122-nomodel.zip` | CUDA 12.2 | 无主模型版 |
| `*_cu122-translate.zip` | CUDA 12.2 | 翻译版 |
| `*_cu122-transcribe.zip` | CUDA 12.2 | 转录版 |
| `*_cu128-nomodel.zip` | CUDA 12.8 | 无主模型版 |
| `*_cu128-translate.zip` | CUDA 12.8 | 翻译版 |
| `*_cu128-transcribe.zip` | CUDA 12.8 | 转录版 |
| `*_gfx101x_dgpu-{nomodel,translate,transcribe}.zip` | AMD ROCm/HIP (gfx101X) | 三种模型类型 |
| `*_gfx103x_dgpu-{nomodel,translate,transcribe}.zip` | AMD ROCm/HIP (gfx103X) | 三种模型类型 |
| `*_gfx110x_all-{nomodel,translate,transcribe}.zip` | AMD ROCm/HIP (gfx110X) | 三种模型类型 |
| `*_gfx120x_all-{nomodel,translate,transcribe}.zip` | AMD ROCm/HIP (gfx120X) | 三种模型类型 |

> AMD 版本仍然使用 `--device=cuda`（这是 CTranslate2 在 HIP/ROCm 后端下的公开 API 约定）。命令行也可使用 `--device=amd`（等同于 `--device=cuda`）。
> 暂不提供 `gfx115x` iGPU/mobile 版本（请使用 CPU 版或 Modal 云端推理）。

---

## 🔍 如何选择正确的版本

### NVIDIA 显卡 —— 选择 CUDA 版本

#### 方法一：通过 nvidia-smi 查询

1. 打开命令提示符或终端
2. 输入命令：`nvidia-smi`
3. 查看输出中的 **Driver Version** 和 **CUDA Version**

```
+-------------------------------------------------------------------------+
| NVIDIA-SMI 570.00       Driver Version: 570.00       CUDA Version: 12.8|
+-------------------------------------------------------------------------+
```

#### 方法二：通过显卡型号和驱动版本对照表

#### 📊 NVIDIA 驱动版本与 CUDA 版本兼容性表

| CUDA 版本 | 最低驱动要求（Windows） | 最低驱动要求（Linux） | 推荐使用场景 |
|----------|------------------------|---------------------|------------|
| **CUDA 11.8** | ≥452.39 | ≥450.80.02 | 较旧的显卡（GTX 10系列、RTX 20/30系列） |
| **CUDA 12.2** | ≥525.60.13 | ≥525.60.13 | RTX 30/40系列，较新的驱动 |
| **CUDA 12.8** | ≥570.65 | ≥570.26 | RTX 40/50系列，最新驱动 |

#### 🎮 显卡型号推荐表

| 显卡系列 | 推荐 CUDA 版本 | 说明 |
|---------|--------------|------|
| GTX 10系列（1060/1070/1080等） | **CUDA 11.8** | 兼容性最好 |
| GTX 16系列（1650/1660等） | **CUDA 11.8** | 兼容性最好 |
| RTX 20系列（2060/2070/2080等） | **CUDA 11.8** 或 **12.2** | 根据驱动版本选择 |
| RTX 30系列（3060/3070/3080/3090等） | **CUDA 12.2** | 推荐使用 |
| RTX 40系列（4060/4070/4080/4090等） | **CUDA 12.2** 或 **12.8** | 最新驱动用12.8 |
| **RTX 50系列（5090/5080/5070等）** | **🔴 必须使用 CUDA 12.8** | ⚠️ 注意：RTX 50系列必须使用CUDA 12.8版本 |

#### ⚠️ 重要提示

- **RTX 50系列用户**：由于新架构要求，**必须使用 CUDA 12.8 版本**，驱动版本必须 ≥570.00
- **驱动版本查询**：在 nvidia-smi 中显示的 CUDA Version 是您的驱动**支持的最高**CUDA版本
- **向下兼容**：高版本驱动可以运行低版本CUDA程序（例如：570驱动可以运行CUDA 11.8程序）
- **性能考虑**：使用与驱动匹配的CUDA版本可获得最佳性能

### AMD 显卡 —— 选择 ROCm/HIP 版本

AMD 显卡用户请下载带有 `gfx***` 后缀的版本。AMD 版本已内置 ROCm/HIP 运行时 DLL，一般无需单独安装 ROCm。

**快速自查：**

| 显卡型号 | 下载后缀 |
|---|---|
| RX 5300 / RX 5500 / RX 5600 / RX 5700 系列 | `gfx101x_dgpu` |
| RX 6400 / RX 6500 XT / RX 6600 / RX 6700 / RX 6800 / RX 6900 系列 | `gfx103x_dgpu` |
| RX 7600 / RX 7700 XT / RX 7800 XT / RX 7900 系列 | `gfx110x_all` |
| RX 9060 / RX 9060 XT / RX 9070（含 GRE/XT） | `gfx120x_all` |
| iGPU: Radeon 890M / 8060S / 860M (gfx115x) | ⚠️ 暂不支持 |

> 不知道自己的显卡型号？打开 "任务管理器 -> 性能 -> GPU" 或 "设备管理器 -> 显示适配器"

---

## 📥 模型下载说明

### 无主模型版用户（需自行下载模型）

无主模型版包含VAD模型和两套启动脚本，但**不包含**主 Whisper 语音识别/翻译模型。您需要：

1. **从 Hugging Face 下载模型**
   - 示例模型地址：https://huggingface.co/chickenrice0721/whisper-large-v2-translate-zh-v0.2-st-ct2
   - 这是"海南鸡v2 5000小时"版本的日文转中文优化模型
   - 日文转录模型地址：https://huggingface.co/Jim6789/whisper-ja-1.5B-ct2

2. **放置模型文件**
   ```
   如不修改 .bat，请将模型文件直接放入：
   faster_whisper_transwithai_chickenrice/
   └── models/
       ├── config.json
       ├── model.bin / model.safetensors
       └── ...
   ```

   如果保留 `models\模型文件夹\` 结构，请在对应 `.bat` 的 `infer.exe` 后加入：
   `--model_name_or_path="models\模型文件夹"`

3. **其他可用模型**
   - OpenAI Whisper官方模型
   - 其他社区优化模型

### 翻译版用户（开箱即用）

翻译版已包含：
- ✅ 音声优化 VAD 语音活动检测模型
- ✅ whisper-base 特征提取文件（离线使用）
- ✅ "海南鸡v2 5000小时"日文转中文优化版Whisper模型
- ✅ 所有必要的配置文件

**无需额外下载**，解压后直接运行即可使用！

### 转录版用户（开箱即用）

转录版已包含：
- ✅ 音声优化 VAD 语音活动检测模型
- ✅ whisper-base 特征提取文件（离线使用）
- ✅ Jim6789/whisper-ja-1.5B-ct2 日文原文转录模型
- ✅ 所有必要的配置文件

**无需额外下载**，解压后直接运行 `运行(转录)...bat` 即可使用！

---

## 🚀 快速开始指南

### 1. 选择版本
根据上述表格，选择适合您显卡的CUDA版本

### 2. 下载对应版本
- 自定义模型：下载无主模型版（`-nomodel`）+ 自行下载模型
- 日文转中文：下载翻译版（`-translate`）
- 日文原文转录：下载转录版（`-transcribe`）

### 3. 解压并运行
```bash
# 翻译 GPU模式（推荐）
将音视频文件拖放到 "运行(翻译)(GPU).bat"

# 翻译 CPU模式（无显卡用户）
将音视频文件拖放到 "运行(翻译)(CPU).bat"

# 转录 GPU模式
将音视频文件拖放到 "运行(转录)(GPU).bat"

# 转录 CPU模式
将音视频文件拖放到 "运行(转录)(CPU).bat"

# 低显存模式（int8_float16，4GB显存）
将音视频文件拖放到 "运行(翻译)(GPU,低显存模式).bat"
```

---

## 💡 常见问题

**Q: 我应该选择哪个CUDA版本？**
A: 运行 `nvidia-smi` 查看您的驱动版本，然后对照上表选择。

**Q: 三种模型包有什么区别？**
A: 无主模型版不含主 Whisper 模型但包含两套启动脚本；翻译版包含海南鸡日文转中文模型和翻译脚本；转录版包含 Jim 日文转录模型和转录脚本。

**Q: RTX 4090 应该用哪个版本？**
A: 推荐使用 CUDA 12.2 或 12.8 版本，取决于您的驱动版本。

**Q: 显存不足怎么办？**
A: 使用"低显存模式"批处理文件，或切换到CPU模式。

---

## 📝 更新日志

### v1.9 (2026-06-12)
- 🎯 **转录时间轴稳定性修复**：改用外部 VAD（`VadModelManager`）预先计算语音区间，并以 `clip_timestamps` 显式传入 faster-whisper，替代其内部 `vad_filter`；在批处理与非批处理模式下都能避免 Whisper 时间戳坍缩/错乱，字幕时间轴更稳定

### v1.8 (2026-06-10)
- 📝 **日文原文转录支持**：新增 [Jim6789/whisper-ja-1.5B-ct2](https://huggingface.co/Jim6789/whisper-ja-1.5B-ct2) 日文原文转录模型，无需翻译即可直接输出日文字幕
- 📦 **三种发行包变体**：将原"基础版/海南鸡版"重构为 **无主模型版（`-nomodel`）**、**翻译版（`-translate`）**、**转录版（`-transcribe`）** 三种打包，按需下载
- 🎛️ **翻译/转录任务选择**：新增 `--task` 参数（`translate`/`transcribe`），并拆分为 `运行(翻译)(...)` 与 `运行(转录)(...)` 两套启动脚本
- 🖱️ **拖放交互提示**：直接双击 `.bat` 启动脚本（未拖入文件）时，会提示将音视频文件拖入窗口后回车
- 🌐 **下载更稳定**：huggingface.co 下载失败时自动回退到 hf-mirror.com 镜像，并对 HTTP 429 限流自动退避重试
- 🐔 **海南鸡模型地址更新**：翻译模型改用 CT2 版本仓库 [whisper-large-v2-translate-zh-v0.2-st-ct2](https://huggingface.co/chickenrice0721/whisper-large-v2-translate-zh-v0.2-st-ct2)
- 🔧 **稳定性修复**：修复 CTranslate2 运行时 setuptools/pkg_resources 导入、CUDA 11.8 兼容性、批处理检测时的本地化等问题

**📦 发行包选择**：自定义模型选 `-nomodel`，日文转中文选 `-translate`，日文原文转录选 `-transcribe`（详见上方"版本类型说明"）。

### v1.7 (2026-02-24)
- 🔴 **AMD ROCm/HIP GPU 支持**：新增 AMD 显卡支持（RDNA1–RDNA4），覆盖 RX 5000 / 6000 / 7000 / 9000 系列
- 📦 **多架构构建**：提供 gfx101x（RDNA1）、gfx103x（RDNA2）、gfx110x（RDNA3）、gfx120x（RDNA4）四种架构的打包版本
- 🔧 **ROCm 运行时内置**：AMD 版本内置 ROCm/HIP 运行时 DLL，无需单独安装 ROCm
- 🎮 **设备别名**：命令行支持 `--device=amd`（等同于 `--device=cuda`，CTranslate2 HIP 后端约定）
- ⚡ **自动设备检测增强**：改进 GPU 检测逻辑，支持 AMD HIP 设备自动识别

**📋 AMD 版本选择**：参考上方 "AMD 显卡 —— 选择 ROCm/HIP 版本" 或 README 中的完整型号列表。

### v1.6 (2026-01-12)
- ☁️ **Modal 云端推理支持**：新增 `modal_infer.exe`，无需本地 GPU 也能使用云端 GPU 进行推理（感谢 [@Randomless](https://github.com/Randomless) 贡献）
- 🎮 **多 GPU 选择**：支持 T4、L4、A10G、A100、H100 等多种云端 GPU
- 💰 **新用户福利**：Modal 新用户每月 $30 免费额度，T4 GPU 推荐性价比最高
- 🔧 **交互式配置**：通过命令行交互选择 GPU 类型、模型、批处理等参数
- 📤 **自动文件传输**：自动上传本地音频、下载生成的字幕到本地
- 🗃️ **模型缓存**：模型文件在 Modal Volume 中缓存，后续运行更快
- 📦 **环境整合**：Modal 依赖已集成到主环境文件，无需单独安装

**☁️ 使用方法**：
1. 注册 Modal 账号：https://modal.com/
2. 配置 Token：运行 `modal token new`
3. 运行推理：`modal_infer.exe` 或 `python modal_infer.py`

**💡 使用建议**：
- 本地无 GPU 或显存不足时使用云端推理
- T4 GPU 性价比最高，适合一般转录任务
- 大量任务可选择 A10G/A100 加速处理

### v1.5 (2025-12-26)
- 🔧 **字幕合并后处理**：新增 `segment_merge` 配置选项，智能合并重复/重叠的字幕片段
- ⚙️ **灵活配置**：在 `generation_config.json5` 中配置合并参数（启用/禁用、最大间隔、最大时长）
- 🎛️ **命令行覆盖**：支持通过命令行参数覆盖配置文件设置
  - `--merge_segments` / `--no_merge_segments`：启用/禁用合并
  - `--merge_max_gap_ms`：设置允许合并的最大片段间隔（毫秒）
  - `--merge_max_duration_ms`：设置合并后单条字幕的最大时长（毫秒）
- 🚀 **算法优化**：改进合并算法，通过间隔和时长限制避免产生过长的字幕

**💡 使用建议**：
- 如遇到"单条字幕持续时间异常过长"的情况，可调小 `max_gap_ms` 或 `max_duration_ms`
- 如需完全禁用合并功能，将 `enabled` 设为 `false` 或使用 `--no_merge_segments` 参数

### v1.4 (2025-11-25)
- 🚀 **批处理推理支持**：新增批处理推理模式（--enable_batching），大幅提升处理速度
- ⚡ **智能批次大小自动检测**：程序启动时自动测试不同批次大小（1-8），找到显存允许的最大批次
- 🎯 **手动批次大小控制**：支持通过 --batch_size 参数手动指定批次大小，跳过自动检测
- 🔧 **运行时自适应调整**：处理过程中如遇到显存不足（OOM），自动降低批次大小（每次减少20%）继续处理
- 📈 **最大批次大小配置**：通过 --max_batch_size 参数控制自动检测的上限（默认8，可根据显存调整）
- 📦 **新增高显存加速模式**：提供 "运行(翻译)(GPU,高显存加速模式).bat" 专门为8GB+显存用户优化
- 🔨 **修复批处理兼容性**：应用补丁修复faster-whisper批处理的max_initial_timestamp参数传递问题
- 🌐 **批处理日志国际化**：为批处理功能添加完整的中英文本地化消息，便于调试和使用

**📊 批处理模式说明**：
- **并行处理优势**：批处理模式下，多个音频片段并行转录，每个片段独立处理，不依赖前面片段的结果
- **精度权衡**：批处理可能略微降低转录精度（由于失去了条件生成的上下文信息）
- **特殊场景优化**：在某些场景下批处理反而效果更好，因为避免了条件生成可能带来的错误传播
  - 噪声较多的音频：避免噪声片段影响后续转录
  - 多说话人场景：减少不同说话人之间的相互干扰
  - 长音频文件：防止错误累积效应

**🎮 使用建议**：
- 8GB+ 显存：使用 "运行(翻译)(GPU,高显存加速模式).bat"，自动检测最优批次大小
- 4-8GB 显存：手动设置较小批次大小，如 --batch_size=2 或 4
- 追求最高精度：使用常规模式（不加 --enable_batching 参数）
- 追求处理速度：启用批处理模式，接受轻微的精度权衡

### v1.3 (2025-11-17)
- 🤖 智能计算类型选择：自动检测设备并选择最优计算类型（bfloat16 > float16 > int8 > float32）
- 🔍 增强设备自动检测：改进CUDA可用性检测，支持CUDA_VISIBLE_DEVICES环境变量
- 🔇 抑制警告信息：添加TRANSFORMERS_NO_ADVISORY_WARNINGS环境变量，减少日志噪音
- 🎯 简化批处理文件：移除硬编码的计算类型设置，全部使用自动检测模式
- 📊 改进日志记录：添加自动检测设备和计算类型的详细日志信息
- 🐛 修复日志重复问题：移除重复的根日志处理器，避免日志重复输出
- 🌐 增强国际化支持：为自动检测功能添加完整的中英文本地化消息

### v1.2 (2025-11-15)
- ⚡ CPU模式性能优化：添加 int16 计算类型支持，提升CPU处理速度
- 🎮 GPU兼容性改进：强制使用 float16 替代模型精度 bfloat16，提升显卡兼容性
- 🔧 精简批处理文件：合并视频翻译功能到主批处理文件中
- 📝 新增日志记录功能：自动保存运行日志到 latest.log 文件，方便问题反馈

### v1.1 (2025-11-14)
- 🌐 离线支持改进：预下载 whisper-base 模型文件，实现完全离线运行
- 📥 自动模型管理：WhisperFeatureExtractor 优先使用本地模型，避免网络超时
- 🔧 优化下载流程：支持从已有模型文件夹复制，减少重复下载
- 🚀 提升稳定性：解决网络不稳定环境下的 HuggingFace 连接超时问题

### v1.0 (2025-11-13)
- 🎯 支持多CUDA版本（11.8/12.2/12.8）
- 🚀 优化的日文转中文翻译效果（海南鸡v2版本）
- 🔊 音声优化的VAD语音活动检测
- 💾 改进的缓存机制，加快CI/CD构建速度
- 📦 分离的基础版和完整版，满足不同需求
- 🔧 自动VAD模型下载和管理

---

## 📞 技术支持

如遇到问题，请：
1. 检查显卡驱动是否为最新版本
2. 确认选择了正确的CUDA版本
3. 查看控制台输出的错误信息
4. 提交Issue到项目仓库: https://github.com/TransWithAI/Faster-Whisper-TransWithAI-ChickenRice

### 🔗 官方链接
- **GitHub仓库**: https://github.com/TransWithAI/Faster-Whisper-TransWithAI-ChickenRice
- **海南鸡翻译模型**: https://huggingface.co/chickenrice0721/whisper-large-v2-translate-zh-v0.2-st-ct2
- **Jim 日文转录模型**: https://huggingface.co/Jim6789/whisper-ja-1.5B-ct2
- **音声优化 VAD 模型**: https://huggingface.co/TransWithAI/Whisper-Vad-EncDec-ASMR-onnx
- **Telegram群组**: https://t.me/transWithAI
- **开发团队**: AI汉化组

---

## 🙏 致谢

- 🚀 基于 [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) 开发
- 🐔 使用 [chickenrice0721/whisper-large-v2-translate-zh-v0.2-st-ct2](https://huggingface.co/chickenrice0721/whisper-large-v2-translate-zh-v0.2-st-ct2) 日文转中文优化模型
- 📝 可使用 [Jim6789/whisper-ja-1.5B-ct2](https://huggingface.co/Jim6789/whisper-ja-1.5B-ct2) 日文原文转录模型
- 🔊 使用 [TransWithAI/Whisper-Vad-EncDec-ASMR-onnx](https://huggingface.co/TransWithAI/Whisper-Vad-EncDec-ASMR-onnx) 音声优化 VAD 模型
- 🎙️ [OpenAI Whisper](https://github.com/openai/whisper) 原始项目
- ☁️ 感谢 [@Randomless](https://github.com/Randomless) 贡献 Modal 云端推理功能
- 💪 **感谢某匿名群友的算力和技术支持**

---

*本工具基于 Faster Whisper 开发，海南鸡模型经过5000小时音频数据优化训练，专门针对日文转中文翻译场景。*
*由AI汉化组开源维护，永久免费。*
