"""
打包发布脚本
功能：
1. 使用 PyInstaller 打包 translate.py
2. 创建 Hde_G_release 文件夹
3. 复制 exe 到发布文件夹
4. 收集以 Hde鸡 开头的所有 .bat 文件
5. 提取 _DEFAULT_SYSTEM_PROMPT 写入 提示词.txt
6. 复制 config.json 并抹去 key 信息，关闭 debug
"""

import os
import sys
import shutil
import re
import subprocess
from pathlib import Path

# 获取脚本所在目录
SCRIPT_DIR = Path(__file__).parent.resolve()
RELEASE_DIR = SCRIPT_DIR / "Hde_G_release"

def run_pyinstaller():
    """运行 PyInstaller 打包"""
    print("=" * 50)
    print("步骤 1: 运行 PyInstaller 打包")
    print("=" * 50)
    
    cmd = [
        "pyinstaller",
        "--clean",
        "--onefile",
        "--exclude-module", "PyQt5",
        "--exclude-module", "PyQt6",
        "--exclude-module", "PySide2",
        "--exclude-module", "PySide6",
        "--collect-all", "fugashi",
        "--add-binary", r"D:\anaconda_2022\Lib\site-packages\fugashi.libs\*;.",
        "--add-data", r"D:\anaconda_2022\Lib\site-packages\unidic_lite\dicdir;unidic_lite/dicdir",
        "translate.py"
    ]
    
    print(f"执行命令: {' '.join(cmd)}")
    print()
    
    result = subprocess.run(cmd, cwd=SCRIPT_DIR)
    
    if result.returncode != 0:
        print("❌ PyInstaller 打包失败！")
        sys.exit(1)
    
    print("✓ PyInstaller 打包完成")
    print()

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
    
    exe_src = SCRIPT_DIR / "dist" / "translate.exe"
    exe_dst = RELEASE_DIR / "translate.exe"
    
    if not exe_src.exists():
        print(f"❌ 找不到打包后的 exe: {exe_src}")
        sys.exit(1)
    
    shutil.copy2(exe_src, exe_dst)
    print(f"✓ 复制: {exe_src} -> {exe_dst}")
    print()

def collect_bat_files():
    """收集以 Hde鸡 开头的 .bat 文件"""
    print("=" * 50)
    print("步骤 4: 收集 Hde鸡 开头的 .bat 文件")
    print("=" * 50)
    
    bat_files = list(SCRIPT_DIR.glob("Hde鸡*.bat"))
    
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
    
    translate_py = SCRIPT_DIR / "translate.py"
    
    with open(translate_py, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 提取 _FORMAT_REQUIREMENTS（使用 () 括号格式）
    # 格式：_FORMAT_REQUIREMENTS = ("line1\n" "line2\n" ...)
    format_start = content.find('_FORMAT_REQUIREMENTS = (')
    if format_start == -1:
        print("❌ 无法找到 _FORMAT_REQUIREMENTS")
        sys.exit(1)
    
    # 找到括号结束位置
    format_end = content.find(')', format_start)
    format_block = content[format_start:format_end + 1]
    
    # 提取括号内的字符串内容，拼接成完整文本
    # 格式：("line1\n" "line2\n" ...) 需要提取并拼接
    import re
    string_matches = re.findall(r'"([^"]*(?:\\.[^"]*)*)"', format_block)
    format_requirements = ''.join(string_matches).strip()
    
    # 提取 _DEFAULT_SYSTEM_PROMPT
    # 格式：_DEFAULT_SYSTEM_PROMPT = ("part1\n" + _FORMAT_REQUIREMENTS + "part2\n" ...)
    prompt_start = content.find('_DEFAULT_SYSTEM_PROMPT = (')
    if prompt_start == -1:
        print("❌ 无法找到 _DEFAULT_SYSTEM_PROMPT")
        sys.exit(1)
    
    # 找到括号结束位置
    prompt_end = content.find(')', prompt_start)
    prompt_block = content[prompt_start:prompt_end + 1]
    
    # 提取括号内的字符串内容
    # 注意：_DEFAULT_SYSTEM_PROMPT 中包含 + _FORMAT_REQUIREMENTS +，需要特殊处理
    # 策略：提取所有字符串，然后找到 + _FORMAT_REQUIREMENTS + 的位置，分别处理前后部分
    
    # 分割：找到 "+ _FORMAT_REQUIREMENTS +" 的位置
    split_marker = '+ _FORMAT_REQUIREMENTS +'
    split_pos = prompt_block.find(split_marker)
    
    if split_pos == -1:
        print("❌ 无法在 _DEFAULT_SYSTEM_PROMPT 中找到 _FORMAT_REQUIREMENTS 引用")
        sys.exit(1)
    
    # 前半部分
    part1_block = prompt_block[:split_pos]
    part1_matches = re.findall(r'"([^"]*(?:\\.[^"]*)*)"', part1_block)
    part1 = ''.join(part1_matches).strip()
    
    # 后半部分
    part2_block = prompt_block[split_pos + len(split_marker):]
    part2_matches = re.findall(r'"([^"]*(?:\\.[^"]*)*)"', part2_block)
    part2 = ''.join(part2_matches).strip()
    
    # 构建完整的 prompt
    full_prompt = part1 + "\n" + format_requirements + "\n" + part2
    
    # 写入 提示词.txt
    prompt_file = RELEASE_DIR / "提示词.txt"
    with open(prompt_file, 'w', encoding='utf-8') as f:
        f.write(full_prompt)
    
    print(f"✓ 提取完整 prompt 写入: {prompt_file}")
    print(f"  提示词长度: {len(full_prompt)} 字符")
    print()

def copy_config():
    """复制 config.json 并抹去 key 信息，关闭 debug"""
    print("=" * 50)
    print("步骤 6: 处理 config.json")
    print("=" * 50)
    
    import json
    
    config_src = SCRIPT_DIR / "config.json"
    config_dst = RELEASE_DIR / "config.json"
    
    with open(config_src, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # 抹去 key 信息
    if "api" in config and "key" in config["api"]:
        original_key = config["api"]["key"]
        config["api"]["key"] = ""  # 清空 key
        print(f"  抹去 API key: {original_key[:10]}... -> (空)")
    
    # 关闭 debug
    if "app" in config and "debug" in config["app"]:
        original_debug = config["app"]["debug"]
        config["app"]["debug"] = False
        print(f"  关闭 debug: {original_debug} -> False")
    
    with open(config_dst, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    
    print(f"✓ 处理后的 config.json 已保存到: {config_dst}")
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
    
    print("=" * 60)
    print("  打包发布完成！")
    print("=" * 60)
    print()
    print(f"发布文件夹: {RELEASE_DIR}")
    print()
    print("发布内容:")
    for item in sorted(RELEASE_DIR.iterdir()):
        size = item.stat().st_size if item.is_file() else 0
        size_str = f"{size / 1024 / 1024:.2f} MB" if size > 1024 * 1024 else f"{size / 1024:.2f} KB"
        print(f"  - {item.name} ({size_str})")
    print()

if __name__ == "__main__":
    main()
