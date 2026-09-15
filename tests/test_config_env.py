# -*- coding: utf-8 -*-
"""配置加载：环境变量覆盖（CI 注入凭证用）。"""

from __future__ import annotations

import json

from io_adapter.config_loader import load_config


def _write_cfg(tmp_path, api: dict):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({'api': api}, ensure_ascii=False), encoding='utf-8')
    return path


def test_env_overrides_api_fields(tmp_path, monkeypatch):
    path = _write_cfg(tmp_path, {'key': 'file-key', 'model': 'file-model'})
    monkeypatch.setenv('HDEG_API_KEY', 'env-key')
    monkeypatch.setenv('HDEG_MODEL', 'env-model')
    monkeypatch.setenv('HDEG_BASE_URL', 'http://127.0.0.1:9/v1')
    monkeypatch.setenv('HDEG_PROTOCOL', 'openai')

    cfg = load_config(path)
    assert cfg['api']['key'] == 'env-key'
    assert cfg['api']['model'] == 'env-model'
    assert cfg['api']['base_url'] == 'http://127.0.0.1:9/v1'
    assert cfg['api']['protocol'] == 'openai'


def test_file_value_used_when_env_absent(tmp_path, monkeypatch):
    path = _write_cfg(tmp_path, {'key': 'file-key', 'model': 'file-model'})
    for name in ('HDEG_API_KEY', 'HDEG_MODEL', 'HDEG_BASE_URL', 'HDEG_PROTOCOL'):
        monkeypatch.delenv(name, raising=False)

    cfg = load_config(path)
    assert cfg['api']['key'] == 'file-key'
    assert cfg['api']['model'] == 'file-model'


def test_empty_env_ignored(tmp_path, monkeypatch):
    path = _write_cfg(tmp_path, {'key': 'file-key'})
    monkeypatch.setenv('HDEG_API_KEY', '   ')
    cfg = load_config(path)
    assert cfg['api']['key'] == 'file-key'


def test_hdeg_config_env_selects_path(tmp_path, monkeypatch):
    path = _write_cfg(tmp_path, {'key': 'from-hdeg-config', 'model': 'm'})
    monkeypatch.setenv('HDEG_CONFIG', str(path))
    cfg = load_config()  # 不传 path
    assert cfg['api']['key'] == 'from-hdeg-config'
