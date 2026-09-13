# HDEG 

> 面向日语音声作品的端到端字幕翻译：以官方台本为准绳，把「转录 → 对齐 → 翻译」做成一条全自动流水线。

`Python` · `Faster-Whisper` · `PyMuPDF` · `pyopenjtalk` · 多厂商 LLM · `PyInstaller`


---

## 一、使用说明

日文音声作品->中文字幕全流程
1. 填写key
2. 确保作品只保留一种形式的音轨（mp3/wav）
3. 拖动文件夹到.bat然后开始翻译，作品内容不需要任何额外改动

---

## 二、核心能力


### 1. 台本提取、识别与分割
- TXT 自动探测编码（UTF-8 / Shift-JIS / cp932）。
- PDF **自动识别横排/竖排并重排**，解决日文竖排台本行序错乱。
- LLM 识别「哪个文件是台本」，判断是否已按音轨预分割；失败回退正则。
- 整本台本按音轨切分时采用**两级定位 + 回退链**：本地 FZ 锚点粗定位 → 模型在 ±50 行内精定位。

### 2. ASR ↔ 台本多对多对齐
- 不是 1:1 硬匹配，而是按读音（pyopenjtalk 汉字→假名）做字符级序列对齐，得到每行 ASR 对应的台本行区间与置信度。
- 低置信行不附台本，避免误导翻译。

### 3. 术语 · alias · 世界观一致性
- 世界观分析抽取角色 / 身份定位 / 设定，注入术语表。
- 术语表（terms）+ ASR 误识别参考表（alias）保证角色名、称呼、身体部位全文统一。
- 变体轨（如 `（ルームver）`）自动继承主轨台本。

### 4. 翻译与润色
- ASR 纠错四步工作流：可信度审查 → 上下文修复 → 中文翻译 。
- 忠实度约束、成人术语直译规范、JSON 输入输出强约束、截断/损坏 JSON 自动修复。
- 可开启翻译后润色（Post-editing）。
- 缓存友好：固定前缀 + 可变后缀，提高提示词缓存命中、降低成本。

### 5. 工程健壮性
- **失败音轨重试队列**：整轮完成后统一重试（默认最多 3 轮）。
- **逐轨日志 + 作品日志 + 批量日志**（含 token 明细、用时、失败原因）。
- **Token 用量与费用统计**，并显示账户余额。

- 层层回退：LLM→正则、JSON 自动修复、pyopenjtalk 可选、转录看门狗。
- LRC / SRT / VTT 读写，日文原版留档 `.ja.lrc`。

---

## 三、多模型 / 多服务商支持

HDEG 不绑定单一厂商。模型行为由 `config.json → api.models` 注册表声明，**加模型只改配置、不改代码**。

### 协议
- `openai`：`/chat/completions`（默认，兼容绝大多数服务）
- `anthropic`：`/messages`
- `responses`：`/responses`

协议判定四级：模型精确名 → 最长前缀 → 全局 `api.protocol` → 名称嗅探兜底。

### 思考强度「方言」自适应
不同厂商的思考开关/强度字段位置不同，全部通过注册表声明，并支持按 `base_url` 自动切换：

| 情形 | 行为 |
| --- | --- |
| `base_url` 含 `opencode`（Zen） | 走 Zen 方言（如 GPT/Grok → `responses`；部分模型 → `reasoning.effort`） |
| `base_url` 命中厂商官方域名（`hosts`） | 走该厂商官方方言 |
| 未识别的网关 | **降级为干净的 openai**（协议 openai，只发标准字段，剔除厂商私有开关） |

已适配的方言示例：

| 模型系列 | 官方方言 | 默认档位 |
| --- | --- | --- |
| DeepSeek Flash | 顶层 `reasoning_effort` + `thinking` 开关 | low |
| GPT / Grok | 顶层 `reasoning_effort` | 中/低 |
| Hy3 / Hy4 | `thinking{type}` + 顶层 `reasoning_effort` | low / high |
| Qwen | `enable_thinking` + `thinking_budget` | medium |
| Kimi K3 | 顶层 `reasoning_effort`（始终思考） | high |
| Kimi K2.x | `thinking{type}` | — |
| GLM | `thinking{type}` + `reasoning_effort` | high |

> 单个模型若声明了 `default_effort`，用户**只换模型即可**，强度自动定档；显式配置则覆盖。

### 服务商预设
`api.presets` 内置经验证配置，按模型名自动匹配，用户通常只需改 `base_url` / `key` / `model`：

- `deepseek-official` — DeepSeek 官方，直连
- `muse-zen` — opencode Zen 的 muse 模型
- `zen-chat` — Zen 多模型（hy / qwen / kimi / glm / gpt / grok）

### 代理
- `api.clear_proxy = true` 强制直连（如 DeepSeek 官方）。
- `false`：有系统代理则走代理、无则直连（外国模型/网关）。
- `network.clear_proxy_on_startup` 控制启动时是否清代理。

### 失败降级
- 请求被拒（400/422）时自动剔除不支持的参数（`reasoning_effort` / `top_k` / `response_format`）后重试。
- 主模型配额耗尽时按 `fallback_models` 链自动切换模型，整条流水线跟随。

---

## 四、工作流程

```text
┌──────────────┐   ┌────────────────────┐   ┌────────────────────────┐
│  音频/视频    │ → │ ① 本地转录 infer.exe │ → │ ② 台本识别/提取/分割    │
│  (+ 官方台本) │   │  生成 .ja.lrc        │   │   （LLM 优先，正则兜底） │
└──────────────┘   └────────────────────┘   └───────────┬────────────┘
                                                         ↓
   ┌──────────────────────────────────────────────────────────────┐
   │ ③ 术语 / alias / 世界观 加载（缓存优先，缺失才 LLM 分析）      │
   │ ④ ASR ↔ 台本多对多区间对齐（sb + 置信度）                      │
   │ ⑤ 逐轨翻译：术语 + 世界观 + 台本参考 + ASR → LLM               │
   │ ⑥ 写回 .lrc，同步 .srt/.vtt，.ja.lrc 留档，输出费用报告         │
   └──────────────────────────────────────────────────────────────┘
```

---

## 五、快速开始（源码 / 开发）

```bash
pip install -r requirements.txt
cp config.example.json config.json
python main.py <作品目录>
```

`config.json` 关键项：

```jsonc
{
  "api": {
    "key": "你的APIKey",
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-flash",
    "active_preset": "deepseek-official",   // 留空则按模型自动匹配预设
    "models":  { /* 模型注册表：协议 + 思考方言，通常无需改动 */ },
    "presets": { /* 服务商预设 */ },
    "generation_params": {
      "temperature": 1, "top_p": 0.9, "max_tokens": 196608,
      "reasoning_effort": "low"
    }
  },
  "transcription": { "infer_exe": "./infer.exe", "model_dir": "./models" },
  "app": { "print_worker_detail": false, "debug": false }
}
```

---

## 六、打包与开箱即用

`build_release.py` 一键打包（PyInstaller 单文件 + 生成启动 .bat + 处理发布配置）：

```bash
python build_release.py
```

发布件（`Hde_G_release/`）默认预设为 **DeepSeek Flash**，用户**只需填 key** 即可开始：

| 项 | 默认值 |
| --- | --- |
| model | `deepseek-flash` |
| base_url | `https://api.deepseek.com` |
| active_preset | `deepseek-official` |
| clear_proxy | `true`（直连） |
| reasoning_effort | `low` |
| debug / print_worker_detail | `false` / `false` |
| key | 空（待填） |

输出以「**错误 + 进度**」为主，默认不打印全量 debug 详情。

> 发布件仅包含 HDEG 引擎本体；前端界面 / 后端服务为独立项目，不随此包分发。

---

## 七、技术架构

| 层 | 模块 | 职责 |
| --- | --- | --- |
| 管道层 | `pipeline/orchestrator.py` | 端到端编排（转录 → 台本 → 术语 → 翻译 → 报告） |
| 核心层 | `core/` | 纯逻辑：台本解析、版面分析、分块、一致性检查 |
| 引擎层 | `engines/` | 翻译、台本清洗/分割、对齐、术语、世界观、OCR、API 客户端 |
| IO 适配层 | `io_adapter/` | 配置加载、文件扫描、字幕读写 |
| 工具层 | `utils/` | 统一分级输出、Token 追踪、文本过滤、峰谷调度 |

```text
main.py                    # 命令行入口
pipeline/orchestrator.py   # 管道编排
core/                      # 台本解析、PDF、版面分析
engines/                   # 翻译 / 台本 / 术语 / 世界观 / API 客户端
io_adapter/                # 配置、扫描、字幕读写
utils/                     # 输出、Token、调度
build_release.py           # 打包脚本
config.example.json        # 配置模板
转录模型/                   # 本地转录运行时（infer.exe + models，不入库）
```

---

## 八、第三方依赖

| 组件 | 用途 | 许可 |
| --- | --- | --- |
| Faster-Whisper-TransWithAI-ChickenRice | 音频转录（`infer.exe`） | MIT |
| PyMuPDF (`fitz`) | 台本 PDF 提取 | AGPL |
| pyopenjtalk | 日文汉字 → 假名（对齐） | MIT |
| 各厂商 LLM API | 翻译 / 台本识别 / 分割 / 世界观 | 各厂商条款 |

---

## 九、免责声明与 License

本项目仅供个人学习与研究使用。请确保使用场景符合当地法律法规，尊重原作者著作权，请勿将翻译输出用于商业用途。

License：[MIT](LICENSE)
