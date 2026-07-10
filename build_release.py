"""
打包发布脚本
功能：
1. 使用 PyInstaller 打包 translate.py
2. 创建 Hde_G_release 文件夹
3. 复制 exe 到发布文件夹
4. 收集以 Hde鸡 开头的所有 .bat 文件
5. 提取 _DEFAULT_SYSTEM_PROMPT 写入 提示词.txt
6. 复制 config.json 并抹去 key 信息，关闭 debug，设置台本翻译模式
7. 【新增】自动从转录模型目录复制 PaddleOCR 模型到发布文件夹
"""

import os
import sys
import shutil
import re
import subprocess
import importlib.util
from pathlib import Path

# 获取脚本所在目录
SCRIPT_DIR = Path(__file__).parent.resolve()
RELEASE_DIR = SCRIPT_DIR / "Hde_G_release"


def get_package_path(package_name):
    """获取包的安装路径"""
    try:
        spec = importlib.util.find_spec(package_name)
        if spec and spec.origin:
            return Path(spec.origin).parent
    except Exception:
        pass
    return None


def run_pyinstaller():
    """运行 PyInstaller 打包"""
    print("=" * 50)
    print("步骤 1: 运行 PyInstaller 打包")
    print("=" * 50)
    
    # 动态获取包路径
    fugashi_path = get_package_path("fugashi")
    unidic_path = get_package_path("unidic_lite")
    
    if not fugashi_path:
        print(f"❌ 找不到 fugashi 包，请确保已安装: pip install fugashi")
        sys.exit(1)
    
    if not unidic_path:
        print(f"❌ 找不到 unidic_lite 包，请确保已安装: pip install unidic-lite")
        sys.exit(1)
    
    print(f"  fugashi 路径: {fugashi_path}")
    print(f"  unidic_lite 路径: {unidic_path}")
    
    # 查找 fugashi.libs 目录（包含 DLL 和 .load-order 文件）
    fugashi_libs_path = fugashi_path.parent / "fugashi.libs"
    
    # 查找 pyopenjtalk 词典目录
    pyopenjtalk_path = get_package_path("pyopenjtalk")
    pyopenjtalk_dic_path = None
    
    if pyopenjtalk_path:
        # 词典在 pyopenjtalk/open_jtalk_dic_utf_8-1.11 目录
        for item in pyopenjtalk_path.iterdir():
            if item.is_dir() and "open_jtalk_dic" in item.name:
                pyopenjtalk_dic_path = item
                print(f"  pyopenjtalk 词典: {pyopenjtalk_dic_path}")
                break
    
    # 构建 PyInstaller 命令
    cmd = [
        "pyinstaller",
        "--clean",
        "--onefile",
        # 排除不需要的大型库（减小体积）
        "--exclude-module", "PyQt5",
        "--exclude-module", "PyQt6",
        "--exclude-module", "PySide2",
        "--exclude-module", "PySide6",
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "numpy.f2py",
        "--exclude-module", "numpy.testing",
        "--exclude-module", "pandas",
        "--exclude-module", "scipy",
        "--exclude-module", "unittest",
        # "--exclude-module", "pydoc",  # nltk 依赖，不能排除
        "--exclude-module", "distutils",
        "--exclude-module", "setuptools",
        "--exclude-module", "IPython",
        "--exclude-module", "jupyter",
        "--exclude-module", "notebook",
        "--exclude-module", "sphinx",
        "--exclude-module", "pytest",
        "--exclude-module", "sympy",
        "--exclude-module", "tornado",
        "--exclude-module", "boto3",
        "--exclude-module", "google",
        "--exclude-module", "azure",
        # 排除 PaddlePaddle 不需要的模块（仅保留推理相关）
        "--exclude-module", "paddle.distribution",
        "--exclude-module", "paddle.autograd",
        "--exclude-module", "paddle.optimizer",
        "--exclude-module", "paddle.fluid",
        "--exclude-module", "paddle.incubate",
        "--exclude-module", "paddle.dataset",
        "--exclude-module", "paddle.audio",
        "--exclude-module", "paddle.hapi",
        "--exclude-module", "paddle.quantization",
        "--exclude-module", "paddle.fleet",
        "--exclude-module", "paddle.distributed",
        # 排除 OpenCV contrib 模块
        "--exclude-module", "cv2.contrib",
        # 收集 fugashi, unidic_lite, pyopenjtalk
        "--collect-all", "fugashi",
        "--collect-data", "unidic_lite",
        "--collect-all", "pyopenjtalk",
        # 收集 OCR 相关包（延迟导入需要显式收集）
        "--collect-all", "paddleocr",
        "--collect-data", "paddlepaddle",
        # 收集 PDF 处理相关包（延迟导入需要显式收集）
        "--collect-data", "fitz",  # PyMuPDF
        "--collect-data", "pdfplumber",
        "--collect-data", "pypdf",
        "--collect-data", "pdf2docx",
        "--collect-data", "docx",  # python-docx
        # 收集 OpenCV（延迟导入需要显式收集）
        "--collect-data", "cv2",
    ]
    
    # 添加 fugashi.libs 目录中的所有文件（DLL 和 .load-order 文件）
    if fugashi_libs_path.exists():
        print(f"  fugashi.libs 目录: {fugashi_libs_path}")
        for f in fugashi_libs_path.iterdir():
            print(f"    - {f.name}")
            # 添加为二进制文件（DLL）或数据文件（.load-order）
            if f.suffix == '.dll':
                cmd.extend(["--add-binary", f"{f};fugashi.libs"])
            else:
                cmd.extend(["--add-data", f"{f};fugashi.libs"])
    else:
        print("  ⚠ 未找到 fugashi.libs 目录，打包后可能无法运行 fugashi")
    
    # 添加 pyopenjtalk 词典（整个目录）
    if pyopenjtalk_dic_path:
        cmd.extend(["--add-data", f"{pyopenjtalk_dic_path};pyopenjtalk/{pyopenjtalk_dic_path.name}"])
    
    # 【新增】添加 PaddleOCR 模型目录（优先从本地 models/paddleocr 加载）
    models_dir = SCRIPT_DIR / "models" / "paddleocr"
    if models_dir.exists():
        print(f"  PaddleOCR 模型目录(本地): {models_dir}")
        total_size = sum(f.stat().st_size for f in models_dir.rglob('*') if f.is_file())
        print(f"  模型总大小: {total_size / 1024 / 1024:.1f} MB")
        cmd.extend(["--add-data", f"{models_dir};models/paddleocr"])
    else:
        print("  ⚠ 未找到本地 PaddleOCR 模型目录，将尝试从转录模型目录复制...")
        # 尝试从 config.json 中读取转录模型目录
        copied_models = copy_paddleocr_from_transcription_dir()
        if copied_models:
            print(f"  已从转录模型目录复制 PaddleOCR 模型到: {copied_models}")
            total_size = sum(f.stat().st_size for f in copied_models.rglob('*') if f.is_file())
            print(f"  模型总大小: {total_size / 1024 / 1024:.1f} MB")
            cmd.extend(["--add-data", f"{copied_models};models/paddleocr"])
        else:
            print("  ⚠ 未找到 PaddleOCR 模型目录，OCR 功能将需要联网下载模型")
    
    cmd.append("main.py")
    
    print(f"执行命令: {' '.join(cmd)}")
    print()
    
    result = subprocess.run(cmd, cwd=SCRIPT_DIR)
    
    if result.returncode != 0:
        print("❌ PyInstaller 打包失败！")
        sys.exit(1)
    
    print("✓ PyInstaller 打包完成")
    print()


def copy_paddleocr_from_transcription_dir():
    """从 config.json 中读取转录模型目录，复制 PaddleOCR 模型到本地"""
    import json
    
    config_path = SCRIPT_DIR / "config.json"
    if not config_path.exists():
        print("  ⚠ config.json 不存在，无法读取转录模型目录")
        return None
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        model_dir = config.get('transcription', {}).get('model_dir', '')
        if not model_dir:
            print("  ⚠ config.json 中未配置 transcription.model_dir")
            return None
        
        model_path = Path(model_dir)
        if not model_path.is_dir():
            print(f"  ⚠ 转录模型目录不存在: {model_path}")
            return None
        
        # 检查转录模型目录中是否有 PaddleOCR 模型
        # 可能的路径: <model_dir>/models/paddleocr 或 <model_dir>/../models/paddleocr
        paddleocr_sources = [
            model_path / "models" / "paddleocr",
            model_path / "paddleocr",
            model_path.parent / "models" / "paddleocr",
        ]
        
        src_dir = None
        for src in paddleocr_sources:
            if src.exists() and any(src.iterdir()):
                src_dir = src
                print(f"  找到源 PaddleOCR 模型: {src_dir}")
                break
        
        if not src_dir:
            print(f"  在转录模型目录中未找到 PaddleOCR 模型")
            return None
        
        # 复制到本地
        local_dir = SCRIPT_DIR / "models" / "paddleocr"
        local_dir.parent.mkdir(parents=True, exist_ok=True)
        
        if local_dir.exists():
            print(f"  本地已有 PaddleOCR 模型，跳过复制")
            return local_dir
        
        print(f"  正在复制 PaddleOCR 模型: {src_dir} -> {local_dir}")
        shutil.copytree(src_dir, local_dir)
        print(f"  ✓ PaddleOCR 模型复制完成")
        return local_dir
        
    except Exception as e:
        print(f"  ⚠ 复制 PaddleOCR 模型失败: {e}")
        import traceback
        traceback.print_exc()
        return None


def create_release_dir():
    """创建发布文件夹"""
    print("=" * 50)
    print("步骤 2: 创建发布文件夹")
    print("=" * 50)
    
    if RELEASE_DIR.exists():
        print(f"  清理旧发布文件夹: {RELEASE_DIR}")
        shutil.rmtree(RELEASE_DIR)
    
    RELEASE_DIR.mkdir(parents=True)
    print(f"✓ 创建发布文件夹: {RELEASE_DIR}")
    print()


def copy_exe():
    """复制 exe 到发布文件夹"""
    print("=" * 50)
    print("步骤 3: 复制 exe 文件")
    print("=" * 50)
    
    # --onefile 模式下，exe 在 dist/main.exe
    exe_src = SCRIPT_DIR / "dist" / "main.exe"
    exe_dst = RELEASE_DIR / "main.exe"
    
    if not exe_src.exists():
        print(f"❌ 找不到打包后的 exe: {exe_src}")
        sys.exit(1)
    
    shutil.copy2(exe_src, exe_dst)
    print(f"✓ 复制: {exe_src} -> {exe_dst}")

    # 复制一份重命名为 translate.exe（兼容 bat 文件中的调用名）
    translate_dst = RELEASE_DIR / "translate.exe"
    shutil.copy2(exe_src, translate_dst)
    print(f"✓ 复制: {exe_src} -> {translate_dst}")
    print()


def collect_bat_files():
    """收集以 Hde鸡 开头的 .bat 文件"""
    print("=" * 50)
    print("步骤 4: 收集 Hde鸡 开头的 .bat 文件")
    print("=" * 50)
    
    bat_files = list(SCRIPT_DIR.glob("Hde鸡*.bat"))

    # 也尝试从 config.json 中的转录模型目录收集 bat 文件
    import json
    config_path = SCRIPT_DIR / "config.json"
    if config_path.exists():
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            model_dir = config.get('transcription', {}).get('model_dir', '')
            if model_dir:
                model_path = Path(model_dir)
                # model_dir 可能是 "E:\转录模型\models"，需要取其父目录
                if model_path.name.lower() == "models":
                    parent_dir = model_path.parent
                else:
                    parent_dir = model_path
                if parent_dir.is_dir():
                    extra_bats = list(parent_dir.glob("Hde鸡*.bat"))
                    for b in extra_bats:
                        if b not in bat_files:
                            bat_files.append(b)
        except Exception:
            pass

    if not bat_files:
        print("  ⚠ 未找到任何 Hde鸡 开头的 .bat 文件")
        return
    
    for bat_src in bat_files:
        bat_dst = RELEASE_DIR / bat_src.name
        shutil.copy2(bat_src, bat_dst)
        print(f"  复制: {bat_src.name}")
    
    print(f"✓ 共收集 {len(bat_files)} 个 .bat 文件")
    print()


def extract_system_prompt():
    """提取 _DEFAULT_SYSTEM_PROMPT 写入 提示词.txt"""
    print("=" * 50)
    print("步骤 5: 提取 _DEFAULT_SYSTEM_PROMPT")
    print("=" * 50)
    
    translate_engine_py = SCRIPT_DIR / "engines" / "translate_engine.py"
    
    if not translate_engine_py.exists():
        print(f"⚠ 找不到: {translate_engine_py}，跳过提示词提取")
        print()
        return
    
    with open(translate_engine_py, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 提取 _FORMAT_REQUIREMENTS（类属性中的元组格式）
    format_start = content.find('_FORMAT_REQUIREMENTS = (')
    if format_start == -1:
        print("⚠ 无法找到 _FORMAT_REQUIREMENTS，跳过提示词提取")
        print()
        return
    
    # 找到配对括号结束位置（处理嵌套引号）
    depth = 0
    i = format_start
    for i in range(format_start, len(content)):
        if content[i] == '(':
            depth += 1
        elif content[i] == ')':
            depth -= 1
            if depth == 0:
                break
    
    format_block = content[format_start:i + 1]
    
    # 提取括号内的字符串内容
    string_matches = re.findall(r'"((?:[^"\\]|\\.)*)"', format_block)
    format_requirements = ''.join(string_matches).strip()
    
    # 构建提示词文档
    full_prompt = f"""=== 格式要求 ===
{format_requirements}

=== 系统提示词（角色设定） ===
你是精通中日双语的字幕翻译专家。你的任务是将日文字幕/台词逐行翻译成自然流畅的中文。
你必须严格遵守格式要求。
使用自然口语化的中文，符合ASMR/广播剧的中文表达习惯。
"""
    
    # 写入 提示词.txt
    prompt_file = RELEASE_DIR / "提示词.txt"
    with open(prompt_file, 'w', encoding='utf-8') as f:
        f.write(full_prompt)
    
    print(f"✓ 提取系统提示词写入: {prompt_file}")
    print(f"  提示词长度: {len(full_prompt)} 字符")
    print()


def copy_config():
    """复制 config.json 并抹去 key 信息，关闭 debug，设置台本翻译模式"""
    print("=" * 50)
    print("步骤 6: 处理 config.json")
    print("=" * 50)
    
    import json
    
    config_src = SCRIPT_DIR / "config.json"
    config_dst = RELEASE_DIR / "config.json"
    
    with open(config_src, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # 抹去 API key
    if "api" in config and "key" in config["api"]:
        original_key = config["api"]["key"]
        config["api"]["key"] = ""  # 清空 key
        print(f"  抹去 API key: {original_key[:10]}... -> (空)")
    
    # 关闭 debug
    if "app" in config and "debug" in config["app"]:
        original_debug = config["app"]["debug"]
        config["app"]["debug"] = False
        print(f"  关闭 debug: {original_debug} -> False")
    
    # 设置台本翻译参数（新参数名）
    if "app" in config:
        # 开启台本翻译
        config["app"]["use_scriptbook_for_translation"] = True
        print(f"  开启台本翻译: True")
        
        # 设置台本模式为 full（全量模式）
        config["app"]["scriptbook_mode"] = "full"
        print(f"  设置台本模式: full")
        
        # 设置翻译模式为 per_track（逐音轨翻译）
        config["app"]["translation_mode"] = "per_track"
        print(f"  设置翻译模式: per_track")
        
        # 开启 ja.lrc 输出
        config["app"]["export_ja_lrc"] = True
        print(f"  开启 ja.lrc 输出: True")
        
        # 移除旧的废弃参数（如果存在）
        for old_param in ["scriptbook_full_mode", "full_mode_one_request", "full_mode_batch_all"]:
            if old_param in config["app"]:
                del config["app"][old_param]
                print(f"  移除废弃参数: {old_param}")
    
    # 设置 OCR 参数
    if "ocr" in config:
        # 默认关闭 GPU（CPU 模式）
        config["ocr"]["use_gpu"] = False
        print(f"  设置 OCR 为 CPU 模式: use_gpu = False")
    
    # 【新增】更新转录配置中的 infer.exe 路径为发布后的相对路径
    if "transcription" in config:
        # 发布后 main.exe 与 infer.exe 在同一个目录（E:\转录模型\），
        # 设置 infer_exe 为 .\\infer.exe（相对路径）
        config["transcription"]["infer_exe"] = ".\\infer.exe"
        config["transcription"]["model_dir"] = ".\\models"
        print(f"  更新转录配置: infer_exe -> .\\infer.exe, model_dir -> .\\models")
    
    with open(config_dst, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    
    print(f"✓ 处理后的 config.json 已保存到: {config_dst}")
    print()


def copy_paddleocr_to_release():
    """复制 PaddleOCR 模型到发布文件夹"""
    print("=" * 50)
    print("步骤 7: 复制 PaddleOCR 模型到发布目录")
    print("=" * 50)
    
    # 优先使用本地的 PaddleOCR 模型
    local_models = SCRIPT_DIR / "models" / "paddleocr"
    
    if local_models.exists() and any(local_models.iterdir()):
        dst = RELEASE_DIR / "models" / "paddleocr"
        if not dst.exists():
            print(f"  复制本地 PaddleOCR 模型: {local_models} -> {dst}")
            shutil.copytree(local_models, dst)
            total_size = sum(f.stat().st_size for f in dst.rglob('*') if f.is_file())
            print(f"  ✓ PaddleOCR 模型已复制到发布目录 ({total_size / 1024 / 1024:.1f} MB)")
        else:
            print(f"  发布目录中已有 PaddleOCR 模型")
        print()
        return
    
    # 如果本地没有，尝试从转录模型目录复制
    models = copy_paddleocr_from_transcription_dir()
    if models:
        dst = RELEASE_DIR / "models" / "paddleocr"
        if not dst.exists():
            print(f"  复制 PaddleOCR 模型到发布目录: {models} -> {dst}")
            shutil.copytree(models, dst)
            total_size = sum(f.stat().st_size for f in dst.rglob('*') if f.is_file())
            print(f"  ✓ PaddleOCR 模型已复制到发布目录 ({total_size / 1024 / 1024:.1f} MB)")
    else:
        print("  ⚠ 未找到 PaddleOCR 模型，发布后 OCR 功能将需要联网下载模型")
    
    print()


def main():
    print()
    print("=" * 60)
    print("  Hde_G 翻译工具打包发布脚本")
    print("=" * 60)
    print()
    
    # 步骤 1: 运行 PyInstaller
    run_pyinstaller()
    
    # 步骤 2: 创建发布文件夹
    create_release_dir()
    
    # 步骤 3: 复制 exe
    copy_exe()
    
    # 步骤 4: 收集 .bat 文件
    collect_bat_files()
    
    # 步骤 5: 提取 system prompt
    extract_system_prompt()
    
    # 步骤 6: 处理 config.json
    copy_config()
    
    # 步骤 7: 复制 PaddleOCR 模型
    copy_paddleocr_to_release()
    
    print("=" * 60)
    print("  打包发布完成！")
    print("=" * 60)
    print()
    print(f"发布文件夹: {RELEASE_DIR}")
    print()
    print("发布内容:")
    for item in sorted(RELEASE_DIR.iterdir()):
        if item.is_file():
            size = item.stat().st_size
            size_str = f"{size / 1024 / 1024:.2f} MB" if size > 1024 * 1024 else f"{size / 1024:.2f} KB"
            print(f"  - {item.name} ({size_str})")
        else:
            # 目录：计算总大小
            total = sum(f.stat().st_size for f in item.rglob('*') if f.is_file())
            size_str = f"{total / 1024 / 1024:.2f} MB" if total > 1024 * 1024 else f"{total / 1024:.2f} KB"
            print(f"  - {item.name}/ ({size_str})")
    print()


if __name__ == "__main__":
    main()