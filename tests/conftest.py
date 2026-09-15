# -*- coding: utf-8 -*-
"""pytest 公共夹具：mock LLM 服务、临时作品目录、测试配置。"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

# 保证可以 import 项目根目录下的包（pipeline/engines/...）
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.mock_llm_server import MockLLMServer, load_recordings  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / 'fixtures'
WORKS_DIR = FIXTURES_DIR / 'works'
RECORDINGS_DIR = FIXTURES_DIR / 'recordings'

# 测试素材清单（顺序即覆盖面：PDF 未分割 / 无台本 / TXT 预分割 / TXT 未分割 / PDF 预分割）
WORK_NAMES = ['RJ01615872', 'RJ01702644', 'RJ01653978', 'RJ01671823', 'RJ01654985']

# 冷路径：复制作品时排除一切缓存/产物，保证每次从零走 LLM 路径
_EXCLUDE_NAMES = {
    '_scriptbook_clean.json',
    '_scriptbook_export.txt',
    '.terms.json',
    '.alias.json',
    '.worldview.json',
    'translate_logs',
}
_EXCLUDE_DIR_PREFIX = ('_split_tracks',)


def _ignore(_dir, names):
    ignored = []
    for n in names:
        if n in _EXCLUDE_NAMES or n.startswith(_EXCLUDE_DIR_PREFIX):
            ignored.append(n)
    return ignored


@pytest.fixture(scope='session')
def repo_root() -> Path:
    return _REPO_ROOT


@pytest.fixture(scope='session')
def works_dir() -> Path:
    return WORKS_DIR


def list_works() -> list:
    if not WORKS_DIR.exists():
        return []
    return sorted(p.name for p in WORKS_DIR.iterdir() if p.is_dir())


def copy_work(name: str, dest: Path) -> Path:
    """把 fixture 作品复制到临时目录（排除缓存，模拟全新作品）。"""
    src = WORKS_DIR / name
    dst = dest / name
    shutil.copytree(src, dst, ignore=_ignore)
    return dst


def write_config(tmp_path: Path, base_url: str) -> Path:
    """写一份指向 mock LLM 的测试配置。"""
    cfg = {
        'api': {
            'key': 'mock-key',
            'base_url': base_url,
            'protocol': 'openai',
            'model': 'mock-model',
            'timeout': 30,
            'clear_proxy': True,
            'generation_params': {
                'temperature': 1.0, 'top_p': 0.9, 'max_tokens': 8192,
                'max_retries': 0,
            },
        },
        'app': {
            'translation_mode': 'per_track',
            'translation_parallel': 1,
            'polish_after_translate': False,
            'api_preflight': False,
            'export_ja_lrc': True,
            'export_scriptbook_content': True,
            'save_track_logs': True,
            'track_log_detail': False,
            'delay_translate_to_offpeak': False,
            'print_worker_detail': False,
            'debug': False,
        },
        'network': {'clear_proxy_on_startup': True},
        'pricing': {'hit_per_1m': 0.02, 'miss_per_1m': 1, 'completion_per_1m': 2},
    }
    path = tmp_path / 'config.test.json'
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
    return path


@pytest.fixture
def mock_llm():
    """工厂：`mock_llm(work_name)` 启动一个回放该作品录制的 mock 服务。"""
    servers: list = []

    def _make(work_name: str) -> MockLLMServer:
        responses = load_recordings(RECORDINGS_DIR / f'{work_name}.json')
        srv = MockLLMServer(responses).start()
        servers.append(srv)
        return srv

    yield _make

    for s in servers:
        s.stop()


@pytest.fixture(autouse=True)
def _clean_proxy_env(monkeypatch):
    """测试期禁用系统代理，避免本地请求被代理拦截。"""
    for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv('NO_PROXY', '*')
    yield
    # 清理测试可能留下的环境变量
    for k in ('HDEG_API_KEY', 'HDEG_BASE_URL', 'HDEG_MODEL', 'HDEG_PROTOCOL', 'HDEG_CONFIG'):
        os.environ.pop(k, None)
