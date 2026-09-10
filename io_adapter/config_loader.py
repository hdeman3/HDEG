# -*- coding: utf-8 -*-
"""
配置加载器（IO适配层）

统一管理所有配置的读写，迁移自 translate.py 中的配置加载逻辑。
"""

from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Any


# ==================== 路径工具 ====================

def get_app_dir() -> Path:
    """获取程序所在目录（兼容直接运行和 PyInstaller 打包）"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).parent.resolve()
    else:
        return Path(__file__).resolve().parent.parent


def get_config_path() -> Path:
    """获取 config.json 路径"""
    return get_app_dir() / 'config.json'


# ==================== 配置加载 ====================

def load_config(path: Path = None) -> dict[str, Any]:
    """
    加载 config.json

    参数:
        path: 配置文件路径，默认 SCRIPT_DIR/config.json

    返回:
        配置字典，包含:
        - api: API 配置
        - ocr: OCR 配置
        - translation: 翻译配置
        - output: 输出配置
    """
    if path is None:
        path = get_config_path()

    # 默认配置（当 config.json 不存在或缺少字段时使用）
    default_config = {
        "api": {"timeout": 2000, "protocol": "", "models": {}},
        "app": {
            "translation_mode": "per_track",
            "translation_parallel": 10,
            "print_worker_detail": False,
            "scriptbook_mode": "full",
            "export_ja_lrc": True,
            "retry_count": 4,
            "delay_translate_to_offpeak": False,
            "polish_after_translate": False,
            "save_track_logs": True,
            "track_log_detail": True,
            "api_preflight": True,
        },
        "pricing": {"hit_per_1m": 0.02, "miss_per_1m": 1, "completion_per_1m": 2},
        "network": {"clear_proxy_on_startup": False},
    }

    if not path.exists():
        print(f"[配置] 配置文件不存在，生成默认模板: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, ensure_ascii=False, indent=2)
        print(f"[配置] 请编辑 {path} 填入 API key 后重新运行")
        return default_config

    print(f"[配置] 读取: {path}")
    with open(path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    # 深层合并默认值
    for section, defaults in default_config.items():
        if section not in config:
            config[section] = defaults
        elif isinstance(defaults, dict) and isinstance(config[section], dict):
            for k, v in defaults.items():
                if k not in config[section]:
                    config[section][k] = v

    # 服务商预设合并：api.active_preset 指向 api.presets 中的一组经验证配置
    # （协议/思考/鉴权/代理等），用户只需改 key / model / base_url 三项。
    # 规则：显式非空值优先（用户写的赢），空值/缺失由 preset 补齐；
    # 字典型字段（models/generation_params）按 key 合并，用户同名 key 覆盖。
    _apply_api_preset(config)

    return config


def _is_empty_value(v) -> bool:
    """空值判定：None/空字符串/空列表/空字典视为未填写，由 preset 补齐。"""
    if v is None:
        return True
    if isinstance(v, str) and not v.strip():
        return True
    if isinstance(v, (list, dict)) and len(v) == 0:
        return True
    return False


def _preset_match_model(preset: dict, model: str) -> bool:
    """preset 是否覆盖该模型：models 键精确命中或为其前缀（大小写不敏感）。

    base_url 是用户的事，preset 里不放地址、不按地址匹配。
    """
    try:
        if not isinstance(preset, dict) or not model:
            return False
        _ml = str(model).lower()
        _models = preset.get('models') or {}
        if not isinstance(_models, dict):
            return False
        for _k in _models.keys():
            _lk = str(_k).lower()
            if _lk and (_ml == _lk or _ml.startswith(_lk)):
                return True
    except Exception:
        pass
    return False


def _apply_api_preset(config: dict) -> None:
    """应用服务商预设（就地合并进 config['api']）。

    选择顺序：显式 active_preset 优先；为空时按 model 自动匹配
    models 覆盖到它的 preset。因此切服务商只需改 key / model /
    base_url，方言（协议/思考/鉴权/参数）自动跟上。都不命中则无操作。
    """
    try:
        api = config.get('api')
        if not isinstance(api, dict):
            return
        presets = api.get('presets') or {}
        if not isinstance(presets, dict):
            return
        name = str(api.get('active_preset') or '').strip()
        if not name:
            # 自动匹配：找 models 覆盖当前 model 的 preset
            _model = str(api.get('model') or '')
            if _model:
                for _pn, _pv in presets.items():
                    if _preset_match_model(_pv, _model):
                        name = _pn
                        print(f"[配置] 按模型自动匹配预设: {name}")
                        break
        if not name:
            return
        if name not in presets:
            print(f"[配置] active_preset='{name}' 在 presets 中不存在，忽略")
            return
        preset = presets[name]
        if not isinstance(preset, dict):
            return
        for k, v in preset.items():
            if k in ('presets', 'active_preset'):
                continue
            cur = api.get(k)
            if isinstance(v, dict) and isinstance(cur, dict):
                for sk, sv in v.items():
                    if sk not in cur or _is_empty_value(cur[sk]):
                        cur[sk] = sv
            elif _is_empty_value(cur):
                api[k] = v
        print(f"[配置] 已应用服务商预设: {name}")
    except Exception as e:
        print(f"[配置] preset 合并失败（沿用原配置）: {e}")


def save_config(config: dict, path: Path = None) -> None:
    """保存配置到文件"""
    if path is None:
        path = get_config_path()

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def get_api_config(config: dict = None) -> dict:
    """提取 API 配置

    参数:
        config: 完整配置，为 None 时自动加载

    返回:
        API 配置字典
    """
    if config is None:
        config = load_config()
    return config.get('api', {})


def get_ocr_config(config: dict = None) -> dict:
    """提取 OCR 配置"""
    if config is None:
        config = load_config()
    return config.get('ocr', {})


def get_translation_config(config: dict = None) -> dict:
    """提取翻译配置"""
    if config is None:
        config = load_config()
    return config.get('translation', {})


def get_output_config(config: dict = None) -> dict:
    """提取输出配置"""
    if config is None:
        config = load_config()
    return config.get('output', {})


# ==================== 术语表加载 ====================

def load_terms_from_config(config: dict = None) -> dict[str, str]:
    """
    从配置中加载术语对照表

    返回:
        {日文: 中文} 对照字典
    """
    if config is None:
        config = load_config()

    terms = config.get('terms', {})
    if isinstance(terms, list):
        # 列表格式: [{"ja": "xxx", "zh": "yyy"}, ...]
        return {item['ja']: item['zh'] for item in terms if 'ja' in item and 'zh' in item}
    return dict(terms)


# ==================== 模型配置 ====================

def get_model_config(config: dict = None) -> dict:
    """获取模型配置"""
    if config is None:
        config = load_config()
    return config.get('model', {})


def get_transcription_config(config: dict = None) -> dict:
    """获取转录配置（infer.exe 路径、设备参数等）

    返回:
        {infer_exe, model_dir, device, compute_type, audio_suffixes, sub_formats}
    """
    if config is None:
        config = load_config()
    return config.get('transcription', {})


def get_generation_params(config: dict = None) -> dict:
    """获取生成参数

    返回:
        {temperature, top_p, max_tokens, reasoning_effort}
    """
    if config is None:
        config = load_config()
    return config.get('generation_params', {})


# ==================== 作品目录配置 ====================

def get_work_dirs(config: dict = None) -> list[str]:
    """获取作品目录列表"""
    if config is None:
        config = load_config()
    return config.get('work_dirs', [])


# ==================== 配置热更新 ====================

def update_config(key: str, value: Any, config: dict = None, save: bool = False) -> dict:
    """
    更新配置项

    参数:
        key: 配置键（支持点号分隔的嵌套键，如 'api.model'）
        value: 新值
        config: 配置字典（为 None 则加载）
        save: 是否持久化保存

    返回:
        更新后的完整配置
    """
    if config is None:
        config = load_config()

    keys = key.split('.')
    target = config
    for k in keys[:-1]:
        if k not in target:
            target[k] = {}
        target = target[k]
    target[keys[-1]] = value

    if save:
        save_config(config)

    return config