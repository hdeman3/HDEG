# -*- coding: utf-8 -*-
"""本地 mock LLM 服务（CI 用）：按阶段路由并回放「录制」的真实模型响应。

设计目标
--------
- 让整条 pipeline 走**真实的 APIClient 调用路径**（协议判定 / 参数过滤 /
  SDK 序列化 / 响应解析），但**不联网、不花 token**。
- 按「阶段」路由，而不是「整段 prompt 哈希」：改一句 prompt 模板不会误挂测试。
- 多轨翻译是并发的，调用顺序不确定；因此 translate/polish 用 `<asr>` 块的
  哈希做 key，与调用顺序无关。

路由 key
--------
- identify / split / worldview / terms_role / terms_dict / preflight → 直接用阶段名
- translate / polish → ``<stage>:<sha1(<asr> 块)>``

录制缺失时的兜底
----------------
返回一份「结构合法但内容保守」的响应，保证管道能跑通（用于 bootstrap）。
真实保真度由录制的真实响应提供，见 ``scripts/record_fixtures.py``。
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


# ==================== prompt 解析 / 阶段判定 ====================

def _messages_text(messages: list, role: str) -> str:
    parts: list[str] = []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        if m.get('role') != role:
            continue
        content = m.get('content')
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for seg in content:
                if isinstance(seg, dict) and isinstance(seg.get('text'), str):
                    parts.append(seg['text'])
    return '\n'.join(parts)


def detect_stage(messages: list, max_tokens: int = 0) -> str:
    """根据 prompt 特征判定当前 LLM 调用属于哪个阶段。"""
    system = _messages_text(messages, 'system')
    user = _messages_text(messages, 'user')

    if user.strip() == 'ping':
        return 'preflight'
    if 'scriptbook_indices' in user:
        return 'identify'
    if '【音轨列表】' in user:
        return 'split'
    if 'body_y_max' in user or ('reading_order' in user and 'col_gap' in user):
        return 'pdf_layout'
    if '音声作品的分析专家' in system:
        return 'worldview'
    if '角色语言学和ASR纠错' in system:
        return 'terms_role'
    if '字幕翻译术语' in system:
        return 'terms_dict'
    if '日语文学译者' in system:
        return 'polish'
    if '<asr>' in user or '<scriptbook>' in user:
        return 'translate'
    return 'unknown'


_ASR_RE = re.compile(r'<asr>(.*?)</asr>', re.S)


def extract_asr_block(user_text: str) -> str:
    """提取 <asr>...</asr> 内容（翻译阶段的稳定输入指纹）。"""
    m = _ASR_RE.search(user_text)
    return m.group(1) if m else user_text


def compute_key(stage: str, messages: list, max_tokens: int = 0) -> str:
    """计算路由 key。"""
    if stage in ('translate', 'polish'):
        user = _messages_text(messages, 'user')
        digest = hashlib.sha1(extract_asr_block(user).encode('utf-8')).hexdigest()[:16]
        return f'{stage}:{digest}'
    return stage


# ==================== 兜底合成 ====================

_LINES_RE = re.compile(r'"index"\s*:\s*(\d+)\s*,\s*"text"')


def synthesize(stage: str, messages: list) -> str:
    """无录制时的保守兜底响应（结构合法，内容不追求质量）。"""
    if stage == 'preflight':
        return 'pong'
    if stage == 'identify':
        # 保守：不认领任何台本文件（走 ASR-only 路径，不崩）
        return json.dumps({
            'scriptbook_indices': [],
            'is_pre_split': False,
            'file_track_mapping': {},
            'file_track_groups': {},
            'reasoning': 'mock-fallback',
        }, ensure_ascii=False)
    if stage == 'split':
        return json.dumps({'tracks': {}}, ensure_ascii=False)
    if stage == 'pdf_layout':
        return json.dumps({
            'orientation': 'vertical', 'reading_order': 'right-to-left',
            'col_gap': 24, 'body_y_min': 0, 'body_y_max': 1000,
        }, ensure_ascii=False)
    if stage in ('worldview', 'terms_role', 'terms_dict'):
        return json.dumps({}, ensure_ascii=False)
    if stage in ('translate', 'polish'):
        user = _messages_text(messages, 'user')
        n = len(_LINES_RE.findall(extract_asr_block(user)))
        n = max(n, 1)
        return json.dumps({'zh': ['（占位译文）' for _ in range(n)]}, ensure_ascii=False)
    return ''


# ==================== HTTP 服务 ====================

class _Handler(BaseHTTPRequestHandler):
    server_version = 'HdegMockLLM/1.0'

    def log_message(self, *args):  # 静音默认访问日志
        pass

    def _read_json(self) -> dict:
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b'{}'
        try:
            return json.loads(raw.decode('utf-8'))
        except Exception:
            return {}

    def _send_json(self, obj: dict, status: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        payload = self._read_json()
        messages = payload.get('messages') or payload.get('input') or []
        if isinstance(messages, str):
            messages = [{'role': 'user', 'content': messages}]
        max_tokens = payload.get('max_tokens') or payload.get('max_output_tokens') or 0

        stage = detect_stage(messages, max_tokens)
        key = compute_key(stage, messages, max_tokens)
        content = self.server.lookup(stage, key, messages)
        self.server.record_call(path=self.path, stage=stage, key=key)

        self._send_json(_openai_completion(content))
        return

    def do_GET(self):
        # 余额查询（被强制 https，一般到不了这里）：返回一个合法结构即可
        self._send_json({'is_available': True, 'balance_infos': []})


def _openai_completion(content: str) -> dict:
    return {
        'id': 'chatcmpl-mock',
        'object': 'chat.completion',
        'created': 0,
        'model': 'mock-llm',
        'choices': [{
            'index': 0,
            'message': {'role': 'assistant', 'content': content},
            'finish_reason': 'stop',
        }],
        'usage': {
            'prompt_tokens': 1,
            'completion_tokens': 1,
            'total_tokens': 2,
            'prompt_tokens_details': {'cached_tokens': 0},
        },
    }


class MockLLMServer:
    """本地 mock LLM 服务。

    参数:
        responses: {key: content}，key 见模块 docstring；命中 ``*`` 亦可。
        host/port: 默认 127.0.0.1 随机端口。
    属性:
        base_url: 形如 ``http://127.0.0.1:<port>/v1``
        calls:    已处理的调用列表 [{'path','stage','key'}]
        missing:  录制缺失的 (stage, key) 列表
    """

    def __init__(self, responses: dict = None, host: str = '127.0.0.1', port: int = 0):
        self._responses = dict(responses or {})
        self.calls: list = []
        self.missing: list = []
        self._lock = threading.Lock()
        self._httpd = ThreadingHTTPServer((host, port), _Handler)
        self._httpd.lookup = self._lookup  # type: ignore[attr-defined]
        self._httpd.record_call = self._record_call  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[0], self._httpd.server_address[1]
        return f'http://{host}:{port}/v1'

    def _lookup(self, stage: str, key: str, messages: list) -> str:
        with self._lock:
            if key in self._responses:
                return self._responses[key]
            if stage in self._responses:
                return self._responses[stage]
            if '*' in self._responses:
                return self._responses['*']
            self.missing.append((stage, key))
        return synthesize(stage, messages)

    def _record_call(self, path: str, stage: str, key: str):
        with self._lock:
            self.calls.append({'path': path, 'stage': stage, 'key': key})

    def stages(self) -> list:
        with self._lock:
            return [c['stage'] for c in self.calls]

    def start(self) -> 'MockLLMServer':
        self._thread.start()
        return self

    def stop(self):
        try:
            self._httpd.shutdown()
        except Exception:
            pass
        try:
            self._httpd.server_close()
        except Exception:
            pass

    def __enter__(self) -> 'MockLLMServer':
        return self.start()

    def __exit__(self, *exc):
        self.stop()


# ==================== 录制文件读写 ====================

def load_recordings(path) -> dict:
    """读取录制文件 → {key: content}。文件不存在时返回空 dict。"""
    import os
    if not path or not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    out: dict = {}
    for item in data.get('calls', []):
        out[item['key']] = item['content']
    return out


def new_recording_plan() -> dict:
    """新建一份录制计划。"""
    return {'calls': []}
