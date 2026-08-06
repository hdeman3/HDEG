# HDEG

面向日语ASMR的字幕翻译工具。
1. 对官方台本信息的充分利用,支持多数台本格式
2. 全程自动化，告别古法网页翻译
3. 内置经过验证的提示词，无需自行编写


## 环境要求

- Python 3.10+
- DeepSeek API Key，暂时仅对deepseek api支持
- 可运行的海南鸡转录模型

## 安装

```bash
pip install -r requirements.txt
```

> `pyopenjtalk` 在 Windows 上可能需要预编译 wheel，安装失败时项目会自动回退到纯假名/汉字比较，不影响主流程。

## 快速开始

### 1. 配置

```bash
cp config.example.json config.json
```

编辑 `config.json`：

- `api.key` — 填入你的 DeepSeek API Key
- `transcription` — 配置 `infer_exe`的路径

### 2. 运行

```bash
python main.py <作品目录>
```
或将作品文件夹直接拖到 `翻译_debug.bat`。

### 输出

- 作品目录下生成中文 `.lrc`，日文原版留档为 `.ja.lrc`
- 台本清洗结果导出为 `_scriptbook_clean.json` 与 `_split_tracks/`
- 命令行末尾输出 Token 用量 / 费用统计

## 目录结构

```
main.py                    # 命令行入口
pipeline/orchestrator.py   # 端到端管道编排（转录 → 台本 → 翻译）
core/                      # 台本解析、PDF 提取、版面分析
engines/                   # 翻译引擎、台本清洗/分割、术语、世界观
io_adapter/                # 配置加载、文件扫描、字幕读写
utils/                     # 输出、Token 追踪、文本过滤
build_release.py           # 打包脚本
main.spec                  # PyInstaller 配置
config.example.json        # 配置模板（复制为 config.json 使用）
```

## 第三方依赖

| 组件 | 用途 | 许可 |
|---|---|---|
| DeepSeek API | 翻译 / 台本识别 / 分割 | 商业 API |
| Faster-Whisper-TransWithAI-ChickenRice | 音频转录（`infer.exe` + 模型，需自行下载放入 `转录模型/`） | MIT |
| PyMuPDF (`fitz`) | 台本 PDF 文本提取 | AGPL |
| pyopenjtalk | 日文汉字→假名读音 | MIT |

## 免责声明

本项目仅供个人学习与研究使用。请确保使用场景符合当地法律法规，尊重原作者著作权；请勿将翻译输出用于商业用途。

## License

[MIT](LICENSE)
