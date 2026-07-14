"""
模型路径配置模块

用于配置 PaddleOCR 等模型的加载路径，支持从打包后的 exe 同级目录加载
"""
import os
import sys
from pathlib import Path


def get_resource_dir() -> Path:
    """获取资源目录路径

    优先级：
    1. 环境变量 MODEL_DIR（手动指定，如 E:\转录模型\）
    2. config.json 中的 transcription.model_dir
    3. 打包后环境：exe 同级目录
    4. 开发环境：当前脚本所在目录
    """
    # 1. 环境变量最高优先级
    env_dir = os.environ.get('MODEL_DIR', '')
    if env_dir:
        p = Path(env_dir.strip('"').strip("'"))
        if p.is_dir():
            return p

    # 2. 从 config.json 读取 transcription.model_dir
    json_model_dir = _read_model_dir_from_config()
    if json_model_dir:
        return json_model_dir

    if getattr(sys, 'frozen', False):
        # 3. 打包后环境
        return Path(sys.executable).parent
    else:
        # 4. 开发环境
        return Path(__file__).parent


def _read_model_dir_from_config() -> Path | None:
    """从 config.json 读取 transcription.model_dir"""
    import json
    # 尝试多个位置查找 config.json
    search_paths = [
        Path('config.json'),  # 当前工作目录
        Path(__file__).parent / 'config.json',  # 脚本所在目录
    ]
    if getattr(sys, 'frozen', False):
        search_paths.insert(0, Path(sys.executable).parent / 'config.json')

    for config_path in search_paths:
        try:
            if config_path.exists():
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                model_dir = config.get('transcription', {}).get('model_dir', '')
                if model_dir:
                    p = Path(model_dir)
                    if p.is_dir():
                        return p
        except Exception:
            continue

    return None


def _find_paddleocr_models_dir(resource_dir: Path) -> Path | None:
    """在 resource_dir 下查找 PaddleOCR 模型目录

    兼容两种 resource_dir 格式：
    - resource_dir 是根目录（如 E:\\转录模型\\）→ 查找 resource_dir/models/paddleocr
    - resource_dir 是 models 子目录（如 E:\\转录模型\\models）→ 查找 resource_dir/paddleocr

    返回找到的 PaddleOCR 模型目录路径，未找到返回 None
    """
    candidates = [
        resource_dir / "models" / "paddleocr",   # resource_dir 是根目录
        resource_dir / "paddleocr",               # resource_dir 已经是 models 目录
    ]
    for c in candidates:
        if c.exists() and any(c.iterdir()):
            return c
    return None


def _find_resource_root(resource_dir: Path) -> Path:
    """找到 resource_root（用于设置 PADDLE_HOME 等环境变量）

    PADDLE_HOME 应该是 resource_dir 的根目录（即包含 models/paddleocr 的那一层）
    如果 resource_dir 以 models 结尾，则返回其父目录
    """
    if resource_dir.name.lower() == "models":
        return resource_dir.parent
    return resource_dir


def setup_paddleocr_env():
    """配置 PaddleOCR 环境，使其从本地 models/paddleocr 加载模型"""
    resource_dir = get_resource_dir()
    models_dir = _find_paddleocr_models_dir(resource_dir)

    if models_dir:
        # 使用 resource_root 设置环境变量（包含 models/ 的那一层目录）
        resource_root = _find_resource_root(resource_dir)
        os.environ["PADDLE_HOME"] = str(resource_root)
        os.environ["PADDLEX_HOME"] = str(resource_root)
        os.environ["PADDLEOCR_HOME"] = str(resource_root)

        # 创建 .paddlex 软链接/目录指向本地
        paddlex_dir = resource_root / ".paddlex" / "official_models"
        if not paddlex_dir.exists():
            paddlex_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                # Windows 下使用 junction 或复制
                import subprocess
                subprocess.run(["mklink", "/J", str(paddlex_dir), str(models_dir)],
                               shell=True, check=False, capture_output=True)
            except Exception:
                # 如果 junction 失败，直接复制
                import shutil
                shutil.copytree(models_dir, paddlex_dir, dirs_exist_ok=True)

        return models_dir
    return None


def get_paddleocr_model_dir(model_name: str) -> str:
    """获取 PaddleOCR 模型目录路径"""
    resource_dir = get_resource_dir()
    models_dir = _find_paddleocr_models_dir(resource_dir)
    if models_dir:
        model_dir = models_dir / model_name
        if model_dir.exists():
            return str(model_dir)
    return None
