# -*- coding: utf-8 -*-
"""
翻译工具主入口（瘦身版）

统一入口，兼容原 translate.py 的命令行接口。
实际逻辑已迁移到各层模块：core/ engines/ io_adapter/ pipeline/

直接运行：
    python main.py
    python main.py --gpu
    python main.py --work 作品名

也可以通过 pipeline 模块运行：
    python -m pipeline.orchestrator 本編/
"""

import sys
import io
import os
from pathlib import Path

# 强制 UTF-8 输出（兼容 Windows 控制台 GBK 编码）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

# 清理网络代理（避免代理干扰 API 直连）
try:
    _cfg_path = Path(__file__).parent / 'config.json'
    if _cfg_path.exists():
        import json as _json_proxy
        _cfg = _json_proxy.loads(_cfg_path.read_text(encoding='utf-8'))
        if _cfg.get('network', {}).get('clear_proxy_on_startup'):
            for _key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
                os.environ.pop(_key, None)
except Exception:
    pass

# ==================== unidic_lite 字典配置（与 translate.py 兼容） ====================
# 此部分保留在 main.py 因为它需要在任何 import 之前设置环境变量

import importlib.util


def _find_unidic_dicdir() -> str:
    """动态查找 unidic_lite 字典目录"""
    dicdir_path = None
    
    try:
        spec = importlib.util.find_spec('unidic_lite')
        if spec and spec.origin:
            unidic_package = Path(spec.origin).parent
            candidate = unidic_package / 'dicdir'
            if candidate.exists() and candidate.is_dir():
                if (candidate / 'char.bin').exists():
                    dicdir_path = candidate
    except Exception:
        pass
    
    if dicdir_path is None and hasattr(sys, '_MEIPASS'):
        candidate = Path(sys._MEIPASS) / 'unidic_lite' / 'dicdir'
        if candidate.exists() and candidate.is_dir():
            if (candidate / 'char.bin').exists():
                dicdir_path = candidate
    
    if dicdir_path is None:
        if getattr(sys, 'frozen', False):
            base_dir = Path(sys.executable).parent
        else:
            base_dir = Path(__file__).parent
        candidate = base_dir / 'unidic_lite' / 'dicdir'
        if candidate.exists() and candidate.is_dir():
            if (candidate / 'char.bin').exists():
                dicdir_path = candidate
    
    if dicdir_path:
        result = str(dicdir_path).replace('\\', '/')
        result = result.rstrip('/') + '/'
        return result
    return ""


dic_path_safe = _find_unidic_dicdir()

if dic_path_safe:
    os.environ['UNIDIC_DIR'] = dic_path_safe
    mecabrc_path = dic_path_safe + 'mecabrc'
    os.environ['MECABRC'] = mecabrc_path
    os.environ['UNIDIC_DICDIR'] = dic_path_safe

if hasattr(sys, '_MEIPASS'):
    import types
    _unidic_mock = types.ModuleType('unidic_lite')
    _unidic_mock.DICDIR = dic_path_safe if dic_path_safe else ""
    _unidic_mock.VERSION = 'unknown'
    sys.modules['unidic_lite'] = _unidic_mock


# ==================== 主入口 ====================

if __name__ == '__main__':
    from pipeline.orchestrator import run_pipeline

    # --- 确定工作目录 ---
    # 优先级：input_path.txt（由 .bat 写入） > 命令行参数 > 当前目录
    SCRIPT_DIR = Path(__file__).parent.resolve()
    root = SCRIPT_DIR  # 默认
    
    # 1. 尝试读取 input_path.txt（由翻译_debug.bat 写入）
    input_path_file = SCRIPT_DIR / 'input_path.txt'
    if input_path_file.exists():
        try:
            content = input_path_file.read_text(encoding='utf-8').strip()
            if content:
                root = Path(content)
                print(f"[入口] 从 input_path.txt 读取工作目录: {root}")
        except Exception:
            pass

    # 2. 命令行参数可以覆盖（但 input_path.txt 优先）
    import argparse
    parser = argparse.ArgumentParser(
        description='翻译工具 - 日文字幕翻译',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python main.py                           # 处理当前目录/input_path.txt指定目录
    python main.py 本編/                     # 处理指定目录
    python main.py 本編/ --config config.json # 指定配置
        """,
    )
    parser.add_argument('root', nargs='?', default=None, help='作品根目录（默认由 input_path.txt 或当前目录决定）')
    parser.add_argument('--config', help='配置文件路径（默认 config.json）')
    parser.add_argument('--gpu', action='store_true', help='启用 GPU 加速（已废弃，由 config.json 控制）')

    args = parser.parse_args()

    # 命令行参数在无 input_path.txt 时生效
    if args.root is not None and not input_path_file.exists():
        root = Path(args.root)

    config_path = Path(args.config) if args.config else None

    run_pipeline(
        root,
        config_path=config_path,
        use_gpu=args.gpu,
    )
