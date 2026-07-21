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

# ==================== 日志文件 ====================
# 将 stdout 同时写入文件（与 main.exe/main.py 同级 translate_logs/ 目录）
# 初始化失败时静默回退（不影响正常翻译流程）
try:
    _script_dir = Path(__file__).parent.resolve()
    _log_dir = _script_dir / 'translate_logs'
    _log_dir.mkdir(exist_ok=True)

    from datetime import datetime as _dt
    _log_file = _log_dir / f'run_{_dt.now().strftime("%Y-%m-%d_%H-%M-%S")}.log'

    # 打开日志文件句柄，atexit 确保退出时关闭
    _log_fh = open(_log_file, 'wb', buffering=0)

    class _TeeWriter:
        """同时写入原始 stdout 和日志文件（二进制层）"""
        def __init__(self, original, log_fh):
            self.original = original
            self.log = log_fh
        def write(self, data):
            try: self.original.write(data)
            except: pass
            try: self.log.write(data)
            except: pass
        def flush(self):
            try: self.original.flush()
            except: pass
            try: self.log.flush()
            except: pass
        def readable(self): return False
        def writable(self): return True
        def seekable(self): return False
        @property
        def closed(self): return getattr(self.original, 'closed', False)
        def fileno(self):
            if hasattr(self.original, 'fileno'):
                return self.original.fileno()
            raise OSError('fileno not available')

    _tee = _TeeWriter(sys.stdout.buffer, _log_fh)
    sys.stdout = io.TextIOWrapper(_tee, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(_tee, encoding='utf-8')

    print(f"[日志] 输出文件: {_log_file}")
except Exception:
    import traceback
    # 日志初始化失败，用原始 stderr 输出错误，然后继续
    _err_msg = traceback.format_exc()
    try:
        sys.stderr.buffer.write(f"[日志] 初始化失败，继续运行（无日志文件）:\n{_err_msg}\n".encode('utf-8'))
    except Exception:
        pass  # 连 stderr 都不可用，放弃

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

# 清理网络代理（无条件清除，避免代理干扰 API 直连）
for _key in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
    os.environ.pop(_key, None)
os.environ['NO_PROXY'] = '*'

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
    import argparse
    parser = argparse.ArgumentParser(
        description='翻译工具 - 日文字幕翻译',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python main.py                           # 处理当前目录/input_path.txt指定目录
    python main.py 本編/                     # 处理指定目录
    python main.py 本編/ --config config.json # 指定配置
    python main.py --run-script my_script.py  # 执行内联 Python 脚本（打包模式用）
        """,
    )
    parser.add_argument('root', nargs='?', default=None, help='作品根目录（默认由 input_path.txt 或当前目录决定）')
    parser.add_argument('--config', help='配置文件路径（默认 config.json）')
    parser.add_argument('--gpu', action='store_true', help='启用 GPU 加速（已废弃，由 config.json 控制）')
    parser.add_argument('--run-script', nargs=argparse.REMAINDER, help='执行指定的 Python 脚本（打包模式下 Electron 调用）')

    args = parser.parse_args()

    # --run-script 子命令：执行外部 Python 脚本（用于 review 等辅助功能）
    if args.run_script:
        script_path = args.run_script[0]
        # 添加项目根目录和脚本目录到 sys.path
        SCRIPT_DIR = Path(__file__).parent.resolve()
        sys.path.insert(0, str(SCRIPT_DIR))
        script_file = Path(script_path)
        if script_file.exists():
            sys.path.insert(0, str(script_file.parent))
            with open(script_file, 'r', encoding='utf-8') as f:
                exec(compile(f.read(), script_path, 'exec'))
        else:
            print(f'[错误] 脚本不存在: {script_path}', file=sys.stderr)
            sys.exit(1)
    else:
        # 正常翻译流程
        from pipeline.orchestrator import run_pipeline

        # --- 确定工作目录 ---
        # 优先级：
        #   kikoeru 后台模式（同时指定 --config 和 root）→ 命令行参数最优先
        #   input_path.txt（bat 拖放模式）
        #   当前目录（默认）
        SCRIPT_DIR = Path(__file__).parent.resolve()
        root = SCRIPT_DIR  # 默认

        # 1. kikoeru 后台模式：--config 和 root 同时指定时，命令行参数优先
        input_path_file = SCRIPT_DIR / 'input_path.txt'
        if args.config is not None and args.root is not None:
            root = Path(args.root)
            print(f"[入口] 从命令行参数读取工作目录: {root}")
        elif input_path_file.exists():
            # 2. 传统 bat 拖放模式：input_path.txt 优先
            try:
                content = input_path_file.read_text(encoding='utf-8').strip()
                if content:
                    root = Path(content)
                    print(f"[入口] 从 input_path.txt 读取工作目录: {root}")
            except Exception:
                pass
        elif args.root is not None:
            root = Path(args.root)

        config_path = Path(args.config) if args.config else None

        run_pipeline(
            root,
            config_path=config_path,
            use_gpu=args.gpu,
        )
