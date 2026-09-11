# -*- coding: utf-8 -*-
"""
统一 API 调用层（OpenAI 兼容 chat/completions）

所有 LLM 调用（翻译 / 台本分割 / 台本识别 / 世界观 / 术语）都走这一个入口，
模型配额轮换、参数剔除、指数退避重试逻辑只在这里实现一次。

调用方只需：
    api = APIClient(api_config, verbose=...)
    response = api.chat(messages=[...], max_tokens=..., temperature=...)
    # response 即 OpenAI ChatCompletion 对象，自行取 choices[0].message.content / usage
"""

from __future__ import annotations
import time as _time_mod
import threading as _threading_mod


# 并行 worker 线程局部变量：worker 线程设置 _worker_id（如 0,1,2...），
# 该线程内所有日志（含 APIClient 心跳）据此加 [W{n}] 前缀，
# 便于前端按线程分 tab 查看、后端区分主线程与 worker 日志。
worker_local = _threading_mod.local()

# 是否打印 worker 详细日志：
# - False（默认，.bat 直跑单窗口场景）：worker 线程的心跳/详细日志静默，只保留主线程内容
# - True（前端触发，后端在临时 config 设 print_worker_detail=true）：worker 日志输出供前端分 tab
worker_silent = False


def set_worker_silent(silent: bool) -> None:
    """设置 worker 详细日志是否静默（全局）。"""
    global worker_silent
    worker_silent = bool(silent)


def is_worker() -> bool:
    """当前线程是否为并行 worker 线程"""
    return getattr(worker_local, '_worker_id', None) is not None


def should_print_worker() -> bool:
    """当前线程的日志是否应打印到 stdout（worker 且静默时返回 False）"""
    return not (is_worker() and worker_silent)


def log_prefix() -> str:
    """当前线程的 worker 前缀（主线程返回空串）"""
    wid = getattr(worker_local, '_worker_id', None)
    return f"[W{wid}] " if wid is not None else ''


# ==================== 共享打印锁 + 逐轨日志缓冲 ====================
# 多线程下所有控制台输出都应经 _PRINT_LOCK，保证一次 print 调用不被别的线程拦腰截断。
# 逐轨缓冲：worker 翻译单个音轨时，详细日志先攒进线程本地 buffer（带 [+秒数] 时间戳），
# 音轨结束由 orchestrator 一次性写入该轨专属日志文件（单次 write，原子不穿插）。
_PRINT_LOCK = _threading_mod.RLock()
_BUF_LOCK = _threading_mod.Lock()

# debug 直通：app.debug=true 时，逐轨缓冲改为"双写"——详细行同时收录进 buffer
# （作品文件照写）和打印到控制台（旧行为，按 [Wn] 前缀分组查看），且不受
# worker_silent 门控（开了 debug 就是要看全量）。
# 非 debug 时缓冲行只进文件不上控制台，控制台保持干净。
debug_verbose_console = False


def set_debug_console(enabled: bool) -> None:
    """设置 debug 直通开关（由 PipelineContext 按 app.debug 初始化）。"""
    global debug_verbose_console
    debug_verbose_console = bool(enabled)


def start_track_buffer() -> None:
    """开始当前线程的逐轨缓冲（worker 翻译单个音轨前调用）。"""
    worker_local._log_buffer = {'lines': [], 't0': _time_mod.monotonic()}


def stop_track_buffer() -> dict | None:
    """结束当前线程的逐轨缓冲并取回（worker 翻译单个音轨后调用）。"""
    buf = getattr(worker_local, '_log_buffer', None)
    worker_local._log_buffer = None
    return buf


def buffer_active() -> bool:
    """当前线程是否处于逐轨缓冲中"""
    return getattr(worker_local, '_log_buffer', None) is not None


def buffer_line(text: str) -> None:
    """向当前线程的逐轨 buffer 追加行（行级原子，文件内不穿插）。

    含换行的消息按行拆分逐行加时间戳，避免文件里出现空行断裂。
    """
    buf = getattr(worker_local, '_log_buffer', None)
    if buf is None:
        return
    try:
        elapsed = _time_mod.monotonic() - buf.get('t0', _time_mod.monotonic())
        with _BUF_LOCK:
            for _ln in str(text).split('\n'):
                buf['lines'].append(f"[+{elapsed:7.1f}s] {_ln}")
    except Exception:
        pass


def wlog(text: str = '', *, force_console: bool = False, flush: bool = True,
         console_only: bool = False) -> None:
    """worker 感知的统一打印：缓冲收录 + 加锁输出。

    - 缓冲中：无论静默与否都先收录进 buffer（保证日志文件完整）；
      控制台是否输出仍按 force_console / should_print_worker() 决定。
    - 非缓冲：沿用旧语义（worker 且静默时不打印）。
    - console_only=True：只上控制台不进文件（心跳专用；用时仍由完成行/
      文件每轨用时/行级[+秒数]记录，不受影响）。
    """
    if buffer_active() and not console_only:
        buffer_line(text)
        # 缓冲中：常规细节只进文件不上控制台（API过程等），仅
        # force_console（配额/模型切换）与 debug 直通上控制台
        if not force_console and not debug_verbose_console:
            return
    else:
        if not force_console and not debug_verbose_console and not should_print_worker():
            return
    with _PRINT_LOCK:
        print(text, flush=flush)


# OpenAI 兼容 API 可接受的生成参数白名单
_ALLOWED_GEN_PARAMS = (
    'temperature', 'top_p', 'top_k', 'presence_penalty', 'frequency_penalty',
    'stop', 'logit_bias', 'user', 'reasoning_effort', 'response_format',
)

# 未指定哨兵：区分"调用方没传"（继承 config）与"显式传 None"（强制不发）。
# 修复旧 bug：translate_engine 传 reasoning_effort=None 想去掉思考，
# 旧代码把 None 当未指定又从 config 继承回来，重试参数完全没变。
_UNSET = object()
# 部分 OpenAI 兼容服务不接受的扩展参数（被拒后自动剔除重试）。
# response_format 也放进来：换到不支持 json_object 的网关时自动降级为纯文本 JSON，
# 由上层 _extract_json_array 兜底解析，避免硬失败。
_OPTIONAL_PARAMS = ('reasoning_effort', 'top_k', 'response_format')

# 每个进程生成一次 session ID，所有请求共用（kikoeru 每次翻译启动一个 Python 进程）
_RESPONSES_SESSION_ID = f'hdeg-{__import__("os").getpid():x}-{int(__import__("time").time()) & 0xFFFF:04x}'


def _zen_session_headers(base_url: str) -> dict:
    """Zen 网关会话头：opencode 网关全部分支强制要求（缺则 400 MissingSessionID），
    其他端点不发（未知网关收到多余头可能 400）。"""
    try:
        if base_url and 'opencode' in str(base_url).lower():
            return {'x-opencode-session': _RESPONSES_SESSION_ID}
    except Exception:
        pass
    return {}


def _set_dotted(target: dict, path: str, value) -> None:
    """按点分路径写入嵌套字典（openai 协议 extra_body 用）。

    例：_set_dotted(d, "reasoning.effort", "low") → {"reasoning": {"effort": "low"}}
    中间层已存在但非 dict 时覆盖为 dict，保证路径可写。
    """
    parts = [p for p in str(path or '').split('.') if p]
    if not parts:
        return
    cur = target
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


class _AnthropicAPIError(Exception):
    """Anthropic messages API 调用错误（携带 HTTP 状态码，供配额/参数识别复用）"""

    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class _CompatResponse:
    """兼容对象：模拟 OpenAI ChatCompletion，供上层统一提取 content / token 统计。

    字段与 OpenAI SDK 响应对象一致：
        response.choices[0].message.content
        response.choices[0].finish_reason
        response.usage.prompt_tokens / prompt_tokens_details.cached_tokens / completion_tokens
    """

    def __init__(self, text: str, prompt_tokens: int, hit_tokens: int, miss_tokens: int,
                 completion_tokens: int, finish_reason: str = 'stop',
                 reasoning_tokens: int = 0):
        self.choices = [_CompatChoice(text, finish_reason)]
        self.usage = _CompatUsage(prompt_tokens, hit_tokens, miss_tokens,
                                  completion_tokens, reasoning_tokens)


class _CompatChoice:
    def __init__(self, text: str, finish_reason: str = 'stop'):
        self.message = _CompatMessage(text)
        self.finish_reason = finish_reason


class _CompatMessage:
    def __init__(self, text: str):
        self.content = text
        self.reasoning_content = ''


class _CompatUsage:
    def __init__(self, prompt_tokens: int, hit_tokens: int, miss_tokens: int,
                 completion_tokens: int, reasoning_tokens: int = 0):
        self.prompt_tokens = prompt_tokens
        self.prompt_cache_hit_tokens = hit_tokens
        self.prompt_cache_miss_tokens = miss_tokens
        self.completion_tokens = completion_tokens
        # 思维链 token（官方 responses: output_tokens_details.reasoning_tokens，
        # 计入 output_tokens；用于费用分析"输出里多少是思考"）
        self.reasoning_tokens = reasoning_tokens or 0
        self.prompt_tokens_details = _CompatPromptDetails(hit_tokens)


class _CompatPromptDetails:
    def __init__(self, cached_tokens: int):
        self.cached_tokens = cached_tokens


# 模型注册表支持的协议取值
_VALID_PROTOCOLS = ('openai', 'anthropic', 'responses')


def _normalize_registry_entry(entry) -> tuple[str | None, dict]:
    """注册表条目归一化：支持字符串简写（"responses"）或字典
    （{"protocol": "responses", "reasoning": {...}, "note": "..."}）。

    返回: (protocol 或 None, extra 字典)
    """
    if isinstance(entry, str):
        _p = entry.strip().lower()
        return (_p if _p in _VALID_PROTOCOLS else None), {}
    if isinstance(entry, dict):
        _p = str(entry.get('protocol') or '').strip().lower()
        return (_p if _p in _VALID_PROTOCOLS else None), dict(entry)
    return None, {}


def _deep_merge_variant(base: dict, over: dict) -> dict:
    """递归覆盖：over 中的 dict 递归合并，null 表示删除该键，其余直接覆盖。"""
    _out = dict(base or {})
    for _k, _v in (over or {}).items():
        if _v is None:
            _out.pop(_k, None)
        elif isinstance(_v, dict) and isinstance(_out.get(_k), dict):
            _out[_k] = _deep_merge_variant(_out[_k], _v)
        else:
            _out[_k] = _v
    return _out


# 未识别网关回退到"干净 openai"时，要从 reasoning 块里剔掉的厂商私有键
_REASONING_VENDOR_KEYS = (
    'param', 'budget_param', 'enable', 'disable',
    'thinking_switch', 'always_on', 'effort_param', 'opencode',
)


def _generic_openai_entry(entry: dict) -> dict:
    """把厂商条目降级为通用 openai：协议强制 openai，去掉厂商私有思考方言。

    保留 style/values/default_effort/strip_sampling_params/drop_sampling_params
    等标准或安全键（reasoning_effort 是 OpenAI 标准字段）。
    """
    _out = dict(entry or {})
    _out['protocol'] = 'openai'
    _r = _out.get('reasoning')
    if isinstance(_r, dict):
        _r = {k: v for k, v in _r.items() if k not in _REASONING_VENDOR_KEYS}
        if str(_r.get('style', '')).strip().lower() == 'budget':
            # 无 budget_param 就发不出预算，退回标准顶层 effort
            _r['style'] = 'effort'
        _out['reasoning'] = _r
    return _out


def _apply_entry_variant(entry: dict, api_cfg: dict) -> dict:
    """按 base_url 选择注册表条目的方言变体（不改原配置对象）。

    条目可声明：
    - `hosts`: 该厂商官方域名列表（子串匹配）。用于判断 base_url 是否"认识"。
    - `opencode`: base_url 含 "opencode" 时的覆盖块（protocol/json_mode/reasoning
      均可换，null=删键）。

    三种情形：
    1. base_url 含 "opencode" → 用 `opencode` 覆盖（Zen 行为）；
    2. base_url 命中 `hosts` → 保持基础（=官方厂商）方言；
    3. 声明了 `hosts` 但都不命中（未知网关）→ 降级为干净 openai
       （避免往未知网关发 thinking/enable/disable 等私有参数）。

    返回: 合并后的新条目 dict（已移除 'opencode'/'hosts' 标记键）。
    """
    _e = dict(entry or {})
    try:
        _bl = str((api_cfg or {}).get('base_url') or '').lower()
        _is_open = 'opencode' in _bl
        _hosts = _e.get('hosts') if isinstance(_e.get('hosts'), list) else None
        _is_official = bool(_hosts) and any(
            str(h).lower() in _bl for h in _hosts if h)
        _orig_reason = entry.get('reasoning') if isinstance(entry, dict) else None
        # 条目级 opencode 变体（可换 protocol/json_mode/reasoning 等）
        _over = _e.pop('opencode', None)
        if isinstance(_over, dict) and _is_open:
            _e = _deep_merge_variant(_e, _over)
        # 兼容：reasoning.opencode（仅覆盖思考方言）
        _reason = _e.get('reasoning')
        if isinstance(_reason, dict) and isinstance(_orig_reason, dict) \
                and 'opencode' in _orig_reason:
            _clean = {k: v for k, v in _reason.items() if k != 'opencode'}
            _rover = _orig_reason.get('opencode')
            if isinstance(_rover, dict) and _is_open:
                _clean = _deep_merge_variant(_clean, _rover)
            _e['reasoning'] = _clean
        # 未识别网关 → 干净 openai（仅对声明了 hosts 的厂商条目生效）
        if not _is_open and _hosts and not _is_official:
            _e = _generic_openai_entry(_e)
        _e.pop('hosts', None)
    except Exception:
        return dict(entry or {})
    return _e


def model_entry(model_name: str, api_cfg: dict) -> tuple[dict, str]:
    """取注册表整条目：精确名 → 最长前缀（大小写不敏感）。

    返回: (entry_dict, 命中的注册键；未命中返回 ({}, ''))
    """
    def _as_dict(_v):
        if isinstance(_v, dict):
            return _v
        if isinstance(_v, str):
            return {'protocol': _v}
        return {}
    try:
        _models = (api_cfg or {}).get('models') or {}
        if not isinstance(_models, dict) or not _models:
            return {}, ''
        _ml = str(model_name or '').lower()
        _lower_map = {str(_k).lower(): (str(_k), _v) for _k, _v in _models.items()}
        if _ml in _lower_map:
            _k, _v = _lower_map[_ml]
            return _as_dict(_v), _k
        _best = ''
        for _lk in _lower_map:
            if _lk and _ml.startswith(_lk) and len(_lk) > len(_best):
                _best = _lk
        if _best:
            _k, _v = _lower_map[_best]
            return _as_dict(_v), _k
    except Exception:
        pass
    return {}, ''


def model_entry_merged(model_name: str, api_cfg: dict) -> tuple[dict, str]:
    """取注册表条目并按 base_url 应用 opencode 变体（供协议/参数读取共用）。"""
    _entry, _key = model_entry(model_name, api_cfg)
    return _apply_entry_variant(_entry, api_cfg), _key


def resolve_model_protocol(model_name: str, api_cfg: dict) -> tuple[str, str, dict]:
    """按模型注册表判定协议（加新模型只改配置，不改代码）。

    四层优先级（命中即停）：
    1. 精确名：api.models["muse-spark-1.2-contributor"]（大小写不敏感）
    2. 最长前缀：api.models["muse-spark"] 命中 "muse-spark-1.3-xxx"
    3. 全局默认：api.protocol（整个网关一种协议时配这里）
    4. 嗅探兜底：含 claude → anthropic，含 muse-spark → responses，其余 openai

    条目可声明顶层 `opencode` 子块：base_url 含 "opencode" 时递归覆盖
    protocol/json_mode/reasoning（Zen 走 Zen 行为，其他网关走官方行为）。

    返回: (protocol, 来源说明, extra)
    """
    api_cfg = api_cfg or {}
    _model = str(model_name or '')
    _ml = _model.lower()
    # 1) 精确名 / 2) 最长前缀（共用 model_entry，先应用 base_url 变体）
    _entry, _key = model_entry_merged(_model, api_cfg)
    if _entry or _key:
        _p, _ex = _normalize_registry_entry(_entry)
        if _p:
            _suffix = '' if _key.lower() == _ml else '（前缀匹配）'
            return _p, f"模型配置 models['{_key}']{_suffix}", _ex
    # 3) 全局默认
    _g = str(api_cfg.get('protocol') or '').strip().lower()
    if _g in _VALID_PROTOCOLS:
        return _g, '全局 api.protocol', {}
    # 4) 嗅探兜底（保留旧行为；新模型请登记到 models）
    if 'claude' in _ml:
        return 'anthropic', '嗅探兜底（模型名含 claude，建议登记）', {}
    if 'muse-spark' in _ml:
        return 'responses', '嗅探兜底（模型名含 muse-spark，建议登记）', {}
    return 'openai', '嗅探兜底（默认 openai，建议登记）', {}


def model_reasoning_spec(model_name: str, api_cfg: dict) -> dict:
    """取注册表 reasoning 块（dict，无则 {}）。供各协议分支查询扩展字段。"""
    try:
        _spec = (resolve_model_protocol(model_name, api_cfg or {})[2] or {}).get('reasoning') or {}
        return _spec if isinstance(_spec, dict) else {}
    except Exception:
        return {}


def is_thinking_active(api_cfg: dict) -> bool:
    """当前配置思考是否开启（effort 非空/非 none/非 off 且注册表 style 非 off）。

    思考开启时思维链与正文共享输出配额，长文本必须切小块，否则正文被截断。
    """
    try:
        _eff = ((api_cfg or {}).get('generation_params') or {}).get('reasoning_effort')
        _spec = model_reasoning_spec((api_cfg or {}).get('model', ''), api_cfg)
        if not _eff:
            # 未指定时用注册表默认档位判断（与 apply_reasoning_spec 一致）
            _eff = _spec.get('default_effort')
        if not _eff or str(_eff).strip().lower() in ('none', 'off', '0', 'false'):
            return False
        _style = str(_spec.get('style') or 'effort').lower()
        return _style in ('effort', 'budget')
    except Exception:
        return False


def model_thinking_switch(model_name: str, api_cfg: dict) -> bool:
    """该模型发请求时是否附带 thinking 开关（openai 协议 extra_body）。

    注册表 reasoning.thinking_switch=true 即发；另保留旧行为：
    base_url 含 deepseek（原生接口）默认发。
    """
    if model_reasoning_spec(model_name, api_cfg).get('thinking_switch') is True:
        return True
    try:
        return 'deepseek' in ((api_cfg or {}).get('base_url') or '').lower()
    except Exception:
        return False


def apply_reasoning_spec(gen_params: dict, model_name: str, api_cfg: dict) -> dict:
    """按注册表 reasoning 块决定本次请求带不带思考强度（纯函数，可单测）。

    style 语义（厂商维度）：
    - 'effort'（默认）：保留 gen_params['reasoning_effort']，各协议按自家位置发送
      （openai 顶层字段 / responses 的 reasoning.effort；anthropic 忽略）；
    - 'off'：删掉 reasoning_effort，本模型不发送（传了可能被拒）；
    - 'budget'：effort 档位换算成 token 数，供 anthropic 的 thinking.budget_tokens。
      换算表取 reasoning.budgets（如 {"low": 4000, "high": 20000}），
      或直接取 reasoning.budget_tokens；都取不到则删掉并由调用方打 warning。

    未登记 reasoning 块的模型：原样返回（保持旧行为）。
    返回: 新字典（不改输入）。
    """
    _out = dict(gen_params or {})
    # 显式强制不发（chat 传入的哨兵）优先于注册表默认档位
    _force_off = bool(_out.pop('_no_reasoning_effort', False))
    if _force_off:
        # 转成开关哨兵：让"只有 thinking 开关、没有 effort"的模型也能关闭思考
        _out['_thinking_off'] = True
    try:
        _spec = (resolve_model_protocol(model_name, api_cfg or {})[2] or {}).get('reasoning') or {}
    except Exception:
        return _out
    if not isinstance(_spec, dict) or not _spec:
        return _out
    _style = str(_spec.get('style') or 'effort').strip().lower()
    if _style == 'off':
        _out.pop('reasoning_effort', None)
        _out.pop('reasoning_budget', None)
        return _out
    # 模型自带默认档位（reasoning.default_effort）：调用方未指定时兜底，
    # 实现"用户只换 model，思考强度自动定档"。
    if not _force_off and not _out.get('reasoning_effort') and _spec.get('default_effort'):
        _out['reasoning_effort'] = str(_spec['default_effort'])
    if not _out.get('reasoning_effort'):
        # 空字符串/None 一律视为未指定，避免把 "" 发到接口被拒
        _out.pop('reasoning_effort', None)
    if _style == 'budget':
        _eff = _out.get('reasoning_effort')
        _tokens = _spec.get('budget_tokens')
        if _tokens is None and _eff is not None:
            try:
                _budgets = _spec.get('budgets') or {}
                _tokens = _budgets.get(str(_eff))
            except Exception:
                _tokens = None
        try:
            _tokens = int(_tokens) if _tokens is not None else None
        except (TypeError, ValueError):
            _tokens = None
        _out.pop('reasoning_effort', None)
        if _tokens and _tokens > 0:
            _out['reasoning_budget'] = _tokens
        return _out
    return _out


class APIClient:
    """OpenAI 兼容 chat/completions 客户端，内置模型轮换 + 重试 + 参数自适应。

    特性：
    - 主模型配额/用量耗尽时，自动切换到 config['fallback_models'] 中的下一个模型，
      并回写 config['model']，使整条流水线所有后续调用自动跟随新模型。
    - 首次因参数不支持被拒（400/422）→ 自动剔除 reasoning_effort/top_k 后重试。
    - DeepSeek 原生接口自动注入 thinking extra_body。
    """

    def __init__(self, config: dict, verbose: bool = False):
        """
        参数:
            config: API 配置字典（与 config.json 的 api 段同构），会被本类回写 ['model']
            verbose: 是否打印调试信息
        """
        self.config = config
        self.verbose = verbose
        self._client = None
        # 供应商识别：仅 DeepSeek 原生接口额外发送 thinking extra_body
        self._is_deepseek = 'deepseek' in (config.get('base_url') or '').lower()
        # 协议判定：走模型注册表（config api.models），四层优先级
        # 精确名 → 最长前缀 → 全局 api.protocol → 嗅探兜底。
        # 加新模型只改配置，不改代码；reasoning 预留块供阶段二适配器使用。
        _proto, _source, _extra = resolve_model_protocol(
            config.get('model') or '', config)
        self._protocol_source = _source
        self._model_spec = _extra if isinstance(_extra, dict) else {}
        if _proto == 'anthropic':
            self._is_anthropic = True
            self._is_responses = False
        elif _proto == 'responses':
            self._is_anthropic = False
            self._is_responses = True
        else:
            self._is_anthropic = False
            self._is_responses = False
        if _source.startswith('嗅探'):
            try:
                wlog(f"  [协议] 模型 {config.get('model') or ''} 未在 models 注册表登记，"
                     f"已自动推断为 {_proto}，建议在 config.json → api.models 中登记")
            except Exception:
                pass
        # 模型轮换状态：当前在模型链中的索引
        self._model_idx = 0

    # ---------- 客户端初始化 ----------

    def _ensure_client(self):
        """延迟初始化 OpenAI 客户端。

        Anthropic 协议模式（claude 模型）直接使用 requests，保留系统代理。

        代理处理（OpenAI 协议）：默认保留系统代理（部分模型如 muse 需走本地代理）。
        仅当 config.api.clear_proxy=true 时清除代理直连（opencode/deepseek 官方直连场景）。
        """
        if self._is_anthropic or self._is_responses:
            return
        if self._client is not None:
            return
        from openai import OpenAI
        import os as _os
        if self.config.get('clear_proxy', False):
            # 显式清代理直连：删除环境变量中的代理
            for _k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
                _os.environ.pop(_k, None)
            _os.environ['NO_PROXY'] = '*'
        else:
            # 默认保留/启用系统代理：opencode 的 muse 等模型需走本地代理才能连通。
            # openai SDK（httpx）只读环境变量代理、不读 Windows 注册表，因此把系统代理
            # 显式写回环境变量，供 SDK 使用。
            # 注意：
            # 1) 优先读 Windows 注册表代理（真实系统代理），因为环境变量里可能继承无效/旧代理；
            # 2) 直接覆盖（而非 setdefault），避免无效代理残留导致 403 RegionError。
            try:
                _http_proxy = None
                try:
                    import urllib.request as _ur
                    _reg = _ur.getproxies_registry() or {}
                    _http_proxy = _reg.get('http') or _reg.get('https')
                except Exception:
                    pass
                if not _http_proxy:
                    import requests as _req
                    _env = _req.utils.getproxies() or {}
                    _http_proxy = _env.get('http') or _env.get('https')
                if _http_proxy:
                    # 强制 http scheme（本地代理是 HTTP 代理），httpx 用 HTTP_PROXY 走 HTTP/HTTPS 目标
                    _http_proxy = _http_proxy.replace('https://', 'http://')
                    _os.environ['HTTP_PROXY'] = _http_proxy
                    _os.environ['HTTPS_PROXY'] = _http_proxy
                    _os.environ.pop('NO_PROXY', None)
            except Exception:
                pass
        api_key = self.config.get('key') or self.config.get('api_key', 'sk-no-key')
        base_url = self.config.get('base_url', 'http://localhost:8000/v1')
        # config.json 的 timeout 字段单位是「秒」（如 2000 = 2000 秒），
        # 原 translate_engine 一直按秒直用；长文本翻译需数分钟，不能按毫秒换算。
        # 仅对明显过小的值兜底为 120 秒，避免误配导致过早超时。
        timeout = float(self.config.get('timeout', 120))
        if timeout < 30:
            timeout = 120.0
        if self.verbose:
            print(f"  [API初始化] base_url={base_url}, timeout={timeout}s, key={api_key[:12]}...", flush=True)
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            http_client=None,
            default_headers=_zen_session_headers(base_url) or None,
        )

    # ---------- 模型轮换 ----------

    def _model_chain(self) -> list[str]:
        """返回候选模型链：主模型 + fallback_models（去重保序）"""
        primary = self.config.get('model') or 'gpt-4o-mini'
        fallback = self.config.get('fallback_models') or []
        if isinstance(fallback, str):
            fallback = [fallback]
        chain = [primary]
        for m in fallback:
            if m and m not in chain:
                chain.append(m)
        return chain

    @property
    def current_model(self) -> str:
        """当前激活模型（受轮换影响，动态读取）"""
        return self.config.get('model') or 'gpt-4o-mini'

    @property
    def protocol_name(self) -> str:
        """当前协议：'openai' | 'anthropic' | 'responses'（构造时已探测，无网络调用）"""
        if self._is_anthropic:
            return 'anthropic'
        if self._is_responses:
            return 'responses'
        return 'openai'

    @property
    def protocol_endpoint(self) -> str:
        """当前协议的请求路径（用于启动信息展示）"""
        return {
            'anthropic': '/v1/messages',
            'responses': '/v1/responses',
            'openai': '/v1/chat/completions',
        }.get(self.protocol_name, '')

    @staticmethod
    def protocol_source(config: dict, model_name: str = None) -> str:
        """协议来源说明（与 resolve_model_protocol 同口径）。"""
        try:
            _m = model_name if model_name is not None else (config or {}).get('model', '')
            return resolve_model_protocol(_m, config or {})[1]
        except Exception:
            return '未知'

    @staticmethod
    def _mask_proxy_url(url: str) -> str:
        """代理 URL 脱敏（http://user:pass@host → http://user:***@host）。"""
        try:
            from urllib.parse import urlsplit, urlunsplit
            _parts = urlsplit(url)
            if _parts.password:
                _net = _parts.hostname or ''
                if _parts.port:
                    _net += f':{_parts.port}'
                _auth = (_parts.username or '') + ':***@' if _parts.username else ''
                return urlunsplit((_parts.scheme, _auth + _net,
                                   _parts.path, _parts.query, _parts.fragment))
        except Exception:
            pass
        return url

    @staticmethod
    def detect_proxy(api_cfg: dict, network_cfg: dict = None) -> tuple[bool, str]:
        """探测 API 请求实际走不走代理（纯本地判断，无网络调用）。

        顺序：清代理开关 → Windows 系统注册表 → 进程环境变量。
        生效栈：openai 协议走 OpenAI SDK（httpx，只读环境变量；
        启动后 _ensure_client 会把注册表代理写回环境变量）；
        anthropic/responses 协议走 requests 会话（注册表代理直读）。

        返回: (是否走代理, 描述)
        """
        import os as _os
        api_cfg = api_cfg or {}
        network_cfg = network_cfg or {}
        if bool(api_cfg.get('clear_proxy', False)) or \
                bool(network_cfg.get('clear_proxy_on_startup', False)):
            return False, '关（直连：clear_proxy=true）'
        _reg = None
        try:
            import urllib.request as _ur
            _r = _ur.getproxies_registry() or {}
            _reg = _r.get('http') or _r.get('https')
        except Exception:
            _reg = None
        if _reg:
            return True, f'开 {APIClient._mask_proxy_url(_reg)}（来源：系统注册表）'
        _env = (_os.environ.get('HTTP_PROXY') or _os.environ.get('HTTPS_PROXY')
                or _os.environ.get('http_proxy') or _os.environ.get('https_proxy')
                or _os.environ.get('ALL_PROXY') or _os.environ.get('all_proxy'))
        if _env:
            return True, f'开 {APIClient._mask_proxy_url(_env)}（来源：环境变量）'
        if _os.environ.get('NO_PROXY') == '*':
            return False, '关（直连：NO_PROXY=*）'
        return False, '关（直连：未检测到系统代理）'

    def preflight_check(self, timeout: float = 60.0) -> tuple[bool, str, float]:
        """翻译开始前的 API 可用性探测：发一个极小请求，有正常响应即算可用。

        轻量设计：输出上限 16 token、不带 reasoning_effort（不烧思考 token）、
        独立短超时（默认 60s，不沿用翻译用的长 timeout，避免 hung 住）、
        模型链内每个模型只试一次（配额耗尽自动轮换到下一个）。

        返回: (可用, 实际响应的模型名/错误摘要, 耗时秒)
        """
        import copy as _copy
        pre_cfg = _copy.deepcopy(self.config) if isinstance(self.config, dict) else {}
        pre_cfg['timeout'] = timeout
        # 去掉思考强度：预检不需要思考，别烧 token
        _gp = dict(pre_cfg.get('generation_params', {}) or {})
        _gp.pop('reasoning_effort', None)
        _gp.pop('thinking', None)
        pre_cfg['generation_params'] = _gp
        _api = APIClient(pre_cfg, verbose=False)
        _t0 = _time_mod.monotonic()
        try:
            resp = _api.chat(
                messages=[{'role': 'user', 'content': 'ping'}],
                max_tokens=16,
                max_retries=0,  # 每模型只试一次（chat 内 max(0,1)=1 次尝试）
                json_mode=False,  # ping 无 JSON 指示，强制关（官方要求含 json 字样）
                reasoning_effort=None,  # 预检不烧思考 token，也不触发注册表默认档位
            )
            _el = _time_mod.monotonic() - _t0
            try:
                _model = _api.current_model
            except Exception:
                _model = ''
            # 只要无异常返回即算可用（网关/模型正常响应，不校验内容）
            _ = resp
            return True, _model, _el
        except Exception as e:
            _el = _time_mod.monotonic() - _t0
            return False, f"{type(e).__name__}: {e}", _el

    # ---------- 错误识别 ----------

    @staticmethod
    def _looks_like_unsupported_param(e: Exception) -> bool:
        """错误是否源于「服务不支持某请求参数」（应剔除可选参数后重试）"""
        status = getattr(e, 'status_code', None)
        if status in (400, 422):
            return True
        msg = str(e).lower()
        for kw in ('unknown', 'unsupported', 'not support', 'unexpected', 'unrecognized',
                   'invalid argument', 'extra_input', 'bad_request', 'parameter'):
            if kw in msg:
                return True
        return False

    @staticmethod
    def _looks_like_quota_exhausted(e: Exception) -> bool:
        """错误是否源于「配额/用量耗尽」（应切换到下一个模型继续）

        识别策略：优先看 HTTP 状态码（429/402/403），其次按错误消息关键词。
        覆盖 OpenAI、Anthropic、OpenCode Go/Zen、DeepSeek、各家 OpenAI 兼容网关的常见配额耗尽响应。
        """
        status = getattr(e, 'status_code', None)
        if status in (429, 402, 403):
            return True
        msg = str(e).lower()
        _QUOTA_KW = (
            'rate limit', 'rate_limit', 'rate-limit',
            'quota', 'insufficient_quota', 'usage limit', 'usage_limit',
            'limit reached', 'exceeded', 'exhausted',
            'too many requests', 'too_many_requests',
            'credit', 'balance', 'insufficient',
            'permission', 'forbidden', 'not allowed',
            'gone', 'gone quota',
            'plan', 'subscription',
            'no longer', 'unavailable',
        )
        for kw in _QUOTA_KW:
            if kw in msg:
                return True
        return False

    # ---------- 统一调用入口 ----------

    def chat(
        self,
        *,
        messages: list[dict],
        max_tokens: int,
        temperature: float | None = None,
        top_p: float | None = None,
        reasoning_effort: str | None = _UNSET,
        max_retries: int = 3,
        json_mode: bool | None = None,
    ):
        """统一的 chat/completions 调用（内置模型轮换 + 参数剔除 + 指数退避）。

        参数:
            messages: OpenAI messages 列表 [{role, content}, ...]
            max_tokens: 输出 token 上限
            temperature / top_p: 可选生成参数（None=继承 config）
            reasoning_effort: 三态 —— _UNSET(默认)=继承 config；
                具体值=本次使用；None=本次强制不发（再叠加注册表 reasoning 规则）。
                注册表 style=off 的模型一律不发。
            max_retries: 每个模型的重试次数（配额耗尽不计入，直接切模型）
            json_mode: 是否强制 response_format=json_object。
                None(默认)=跟注册表（模型条目 json_mode=true 才开）；
                True=强制开（调用方须保证 prompt 含 JSON 指示，官方强制要求）；
                False=强制关（预检 ping 等非 JSON 任务必须关，否则 400）。

        返回:
            OpenAI ChatCompletion 对象（含 choices / usage）。
            调用方自行提取 response.choices[0].message.content 和 response.usage。

        异常:
            所有模型都配额耗尽或重试耗尽时，抛 RuntimeError。
        """
        self._ensure_client()

        # 构建基础生成参数字典
        gen_params: dict = {}
        if temperature is not None:
            gen_params['temperature'] = temperature
        if top_p is not None:
            gen_params['top_p'] = top_p
        if reasoning_effort is not _UNSET and reasoning_effort is not None:
            gen_params['reasoning_effort'] = reasoning_effort
        # 继承 config.generation_params 默认值（仅"未指定"时；显式 None=强制不发）
        cfg_gen = self.config.get('generation_params', {}) or {}
        for k in ('temperature', 'top_p'):
            if k not in gen_params and k in cfg_gen:
                gen_params[k] = cfg_gen[k]
        if reasoning_effort is _UNSET and cfg_gen.get('reasoning_effort'):
            # 空字符串视为未指定，交由注册表 default_effort 定档
            gen_params['reasoning_effort'] = cfg_gen['reasoning_effort']
        if reasoning_effort is None:
            # 显式强制不发：打哨兵，阻止注册表 default_effort 兜底
            gen_params['_no_reasoning_effort'] = True
        # 输出上限封顶：仅注册表 models[xxx].max_output_tokens（厂商上限，
        # 0/缺省=不限）。全局 max_tokens 保持大窗口，只在真超限时钳制。
        _requested_max = max_tokens

        _stripped_optional = False
        _model_chain = self._model_chain()
        _model_idx = self._model_idx if 0 <= self._model_idx < len(_model_chain) else 0
        _model_tried: set[str] = set()
        last_error: Exception | None = None

        # 心跳线程：长请求时打印等待进度。
        # 在整个 call 期间持续累加（不随重试/切换模型重置），
        # 避免出现"已等待 90 秒后又回到 10 秒"的错觉。
        import threading
        heartbeat_stop = threading.Event()
        _hb_start = _time_mod.monotonic()
        # 心跳线程是独立线程，threading.local 不继承父线程的 worker_id；
        # 这里捕获父线程（当前调用线程）的 worker_id，心跳线程内设置，使 [等待] 日志带正确前缀。
        _hb_worker_id = getattr(worker_local, '_worker_id', None)
        # 心跳线程同样不继承父线程的 buffer，显式传递 buffer 对象引用，
        # 使心跳行收录进同一音轨的日志文件（列表 append 行级原子，文件内不穿插）。
        _hb_buf = getattr(worker_local, '_log_buffer', None)

        def _print_heartbeat():
            if _hb_worker_id is not None:
                worker_local._worker_id = _hb_worker_id
            if _hb_buf is not None:
                worker_local._log_buffer = _hb_buf
            while not heartbeat_stop.is_set():
                heartbeat_stop.wait(10)
                if not heartbeat_stop.is_set():
                    secs = int(_time_mod.monotonic() - _hb_start)
                    # 心跳 debug/非debug 都上控制台，但不记入日志文件
                    # （用时仍有完成行用时/文件每轨用时/行级[+秒数]记录）
                    wlog(f"{log_prefix()}    [等待] 已等待 {secs} 秒...", console_only=True)

        heartbeat_thread = threading.Thread(target=_print_heartbeat, daemon=True)
        heartbeat_thread.start()

        try:
            while _model_idx < len(_model_chain):
                _current_model = _model_chain[_model_idx]
                if _current_model in _model_tried:
                    _model_idx += 1
                    continue
                _model_tried.add(_current_model)
                # 回写当前激活模型，保证所有外部读取 config['model'] 的路径同步
                self.config['model'] = _current_model
                self._model_idx = _model_idx
                # 逐模型输出上限（注册表 max_output_tokens；不设则用请求值）
                max_tokens = _requested_max
                try:
                    _reg_cap = int((model_entry_merged(
                        _current_model, self.config)[0] or {}).get(
                            'max_output_tokens', 0) or 0)
                except (TypeError, ValueError):
                    _reg_cap = 0
                if _reg_cap > 0 and max_tokens > _reg_cap:
                    max_tokens = _reg_cap
                # 按注册表 reasoning 规则裁剪本次请求参数（逐模型：链上模型规则可能不同）
                _req_params = apply_reasoning_spec(gen_params, _current_model, self.config)
                if (self.verbose and 'reasoning_effort' in gen_params
                        and 'reasoning_effort' not in _req_params
                        and 'reasoning_budget' not in _req_params):
                    wlog(f"  [API] 模型 {_current_model} reasoning style=off，本次不发送思考强度")
                if _model_idx > 0 and should_print_worker():
                    # 模型切换影响等待预期，强制上控制台（同时收录进轨日志）
                    wlog(f"  [API] 切换模型 → {_current_model}（第 {_model_idx+1}/{len(_model_chain)} 个）",
                         force_console=True)

                # 尝试次数 = max(max_retries, 1)：max_retries=0 表示不重试，但仍执行首次尝试
                for attempt in range(max(max_retries, 1)):
                    try:
                        if self._is_anthropic:
                            response = self._chat_anthropic(
                                model=_current_model,
                                messages=messages,
                                max_tokens=max_tokens,
                                gen_params=_req_params,
                                stripped_optional=_stripped_optional,
                            )
                            return response

                        if self._is_responses:
                            response = self._chat_responses(
                                model=_current_model,
                                messages=messages,
                                max_tokens=max_tokens,
                                gen_params=_req_params,
                                stripped_optional=_stripped_optional,
                            )
                            return response

                        # 只保留白名单内参数
                        _filtered = {k: v for k, v in _req_params.items() if k in _ALLOWED_GEN_PARAMS}
                        # JSON 模式：保证正文是合法 JSON，专治"模型先聊两句英文
                        # 再贴 JSON"类解析失败。官方强制要求 prompt 含 JSON 指示，
                        # 因此仅 JSON 任务开启：json_mode=True 强制开，False 强制关，
                        # None 跟注册表（模型条目 json_mode=true 才开）。
                        _jm = json_mode
                        if _jm is None:
                            try:
                                _jm = bool((model_entry_merged(
                                    _current_model, self.config)[0] or {}).get('json_mode'))
                            except Exception:
                                _jm = False
                        if _jm:
                            _filtered['response_format'] = {'type': 'json_object'}
                        _filtered['max_tokens'] = max_tokens
                        # 曾因参数不支持被拒 → 剔除可选参数重试
                        if _stripped_optional:
                            for _k in _OPTIONAL_PARAMS:
                                _filtered.pop(_k, None)

                        # ---- 思考强度方言适配（openai 协议）----
                        # 各厂商对思考强度的字段位置/开关方式不同，全部由注册表
                        # reasoning 块声明，代码不写死厂商：
                        #   param        effort 落位路径（默认顶层 reasoning_effort；
                        #                如 "reasoning.effort" = OpenRouter 方言）
                        #   budget_param budget 落位路径（style=budget，如 "thinking_budget"）
                        #   enable/disable 开关 extra_body 片段（如 {"enable_thinking": true}）
                        #   always_on    thinking-only 模型，禁止发送关闭片段
                        #   thinking_switch / strip_sampling_params 沿用旧语义
                        # 曾被拒进入 stripped 重试时不再附带（避免同一参数反复被拒）。
                        _spec = model_reasoning_spec(_current_model, self.config)
                        _eff = _req_params.get('reasoning_effort')
                        _budget = _req_params.get('reasoning_budget')
                        _off = ((_eff is not None and str(_eff).lower() in (
                            'none', 'off', 'false', '0', 'disabled'))
                            or bool(_req_params.get('_thinking_off')))
                        _param = str(_spec.get('param') or 'reasoning_effort').strip() or 'reasoning_effort'
                        _budget_param = str(_spec.get('budget_param') or '').strip()
                        _enable_frag = _spec.get('enable') if isinstance(_spec.get('enable'), dict) else None
                        _disable_frag = _spec.get('disable') if isinstance(_spec.get('disable'), dict) else None
                        _switch = (self._is_deepseek or model_thinking_switch(
                            _current_model, self.config))
                        _extra = {}
                        _thinking_on = False
                        if not _stripped_optional:
                            # 1) effort / budget 按注册表路径落位
                            if _eff is not None and _param != 'reasoning_effort':
                                _filtered.pop('reasoning_effort', None)
                                if not _off:
                                    _set_dotted(_extra, _param, _eff)
                            if _budget is not None and _budget_param:
                                _set_dotted(_extra, _budget_param, _budget)
                            # 2) 思考开关
                            if _spec.get('always_on') is True:
                                # thinking-only 模型：无法关闭；关闭请求时只省略 effort
                                _thinking_on = True
                                if _off:
                                    _filtered.pop('reasoning_effort', None)
                            elif _off:
                                # 关闭：优先注册表 disable 片段；否则旧默认 thinking.disabled
                                if _disable_frag:
                                    _extra.update(_disable_frag)
                                    _filtered.pop('reasoning_effort', None)
                                elif _switch:
                                    _extra.update({'thinking': {'type': 'disabled'}})
                                    _filtered.pop('reasoning_effort', None)
                                _thinking_on = False
                            elif _eff is not None or _budget is not None:
                                # 开启：注册表 enable 片段；否则旧默认 thinking.enabled
                                if _switch:
                                    _extra.update(_enable_frag or {'thinking': {'type': 'enabled'}})
                                _thinking_on = True
                        # 官方文档：思考模式下 temperature/top_p/presence_penalty/
                        # frequency_penalty 不生效（设置不报错）。thinking 开启且注册表
                        # strip_sampling_params=true 的模型，直接剔除，免得误导。
                        if _thinking_on and _spec.get('strip_sampling_params') is True:
                            _dropped = [_k for _k in (
                                'temperature', 'top_p', 'presence_penalty',
                                'frequency_penalty') if _filtered.pop(_k, None) is not None]
                            if _dropped and self.verbose:
                                wlog(f"  [API] 思考模式开启，采样参数不生效，已剔除: "
                                     f"{','.join(_dropped)}")
                        # 注册表 drop_sampling_params=true：该模型完全不接受采样参数
                        # （如 GPT-5 系列），无论思考开关都剔除，避免 400。
                        if _spec.get('drop_sampling_params') is True:
                            for _k in ('temperature', 'top_p',
                                       'presence_penalty', 'frequency_penalty'):
                                _filtered.pop(_k, None)
                        response = self._client.chat.completions.create(
                            model=_current_model,
                            messages=messages,
                            extra_body=_extra if _extra else None,
                            **_filtered,
                        )

                        return response

                    except Exception as e:
                        last_error = e
                        if self.verbose and should_print_worker():
                            wlog(f"  [API] 模型 {_current_model} 尝试 {attempt+1}/{max_retries} 失败: "
                                 f"{type(e).__name__}: {e}")
                        # 参数不支持 → 剔除可选参数后立即重试（同一模型）
                        if not _stripped_optional and self._looks_like_unsupported_param(e):
                            _stripped_optional = True
                            if self.verbose and should_print_worker():
                                wlog("  [API] 服务可能不支持 reasoning_effort/top_k/thinking，自动剔除后重试")
                            continue
                        # 配额耗尽 → 切换到下一个模型继续同一批次
                        # （配额行影响用户等待预期，强制上控制台；同时收录进轨日志）
                        if self._looks_like_quota_exhausted(e):
                            if self.verbose:
                                wlog(f"  [API] 模型 {_current_model} 配额/用量受限，准备切换模型",
                                     force_console=True)
                            break  # 跳出当前模型的 for 循环，外层 while 取下一个模型
                        # 其他错误：指数退避后重试同一模型
                        if attempt < max_retries - 1:
                            _time_mod.sleep(2 ** attempt)

                # 当前模型的 for 循环结束
                if last_error is not None and self._looks_like_quota_exhausted(last_error):
                    _model_idx += 1
                    _stripped_optional = False  # 新模型重新允许可选参数
                    continue
                # 非配额错误：切模型也救不了同一端点的瞬时/逻辑故障，直接抛出
                raise RuntimeError(
                    f"API 调用失败（模型 {_current_model}，{max_retries}次重试后）: {last_error}"
                )

            # 模型链全部用完
            raise RuntimeError(
                f"API 调用失败：所有模型配额耗尽或重试失败。"
                f"模型链={_model_chain}，最后错误: {last_error}"
            )
        finally:
            # 无论成功返回还是抛异常，都停掉心跳线程
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1)

    # ---------- 代理辅助 ----------

    @staticmethod
    def _get_system_proxies() -> dict | None:
        """读取系统代理（http/https），强制 http scheme。

        与 OpenAI 分支（_ensure_client）一致：优先 Windows 注册表代理，
        保证不受进程内 NO_PROXY 环境变量影响（否则 getproxies() 会被
        NO_PROXY=* 压制 → 返回空 → 直连 → 403 RegionError / 地区不可用）。
        仅当注册表无代理时回退到环境变量。
        """
        import urllib.request as _ur
        _http_proxy = None
        try:
            _reg = _ur.getproxies_registry() or {}
            _http_proxy = _reg.get('http') or _reg.get('https')
        except Exception:
            pass
        if not _http_proxy:
            try:
                import requests as _req
                _env = _req.utils.getproxies() or {}
                _http_proxy = _env.get('http') or _env.get('https')
            except Exception:
                pass
        if not _http_proxy:
            return None
        _http_proxy = _http_proxy.replace('https://', 'http://')
        return {'http': _http_proxy, 'https': _http_proxy}

    @staticmethod
    def _proxy_session() -> 'requests.Session':
        """构造带系统代理的 requests.Session。

        策略：先把注册表代理写回进程环境变量（HTTP_PROXY/HTTPS_PROXY），
        再用默认 trust_env=True 创建 Session。这样 requests 会自动从环境变量
        读取代理，无论 _get_system_proxies() 是否能从注册表读到，都能走代理。
        同时清除 NO_PROXY 防止绕过。
        """
        import os as _os
        import requests as _requests
        # 先把注册表代理写入环境变量（与 _ensure_client 一致）
        _px = APIClient._get_system_proxies()
        if _px:
            _os.environ['HTTP_PROXY'] = _px.get('http', '')
            _os.environ['HTTPS_PROXY'] = _px.get('https', '')
            _os.environ.pop('NO_PROXY', None)
        _sess = _requests.Session()
        return _sess

    # ---------- Anthropic messages 协议 ----------

    def _chat_anthropic(self, *, model: str, messages: list[dict], max_tokens: int,
                        gen_params: dict, stripped_optional: bool = False):
        """调用 Anthropic Messages API（/v1/messages）。

        用于 PackyAPI 等中转的 claude 模型（仅支持 anthropic 协议，chat/completions 返回
        protocol_not_supported）。系统提示独立字段 system；temperature/top_p 传 body。

        返回一个兼容对象，模拟 OpenAI ChatCompletion 结构（choices[0].message.content + usage），
        使上层 extract_content / extract_token_stats 无需感知协议差异。
        """
        import json as _json
        import requests as _requests

        # 代理处理：中转（如 PackyAPI）在本机需走本地代理才能连通，直连会 SSLError。
        # 用 trust_env=False 的 Session + 注册表代理，规避进程内 NO_PROXY 干扰。
        _sess = self._proxy_session()

        api_key = self.config.get('key') or self.config.get('api_key', '')
        base_url = self.config.get('base_url', '').rstrip('/')
        if not base_url:
            base_url = 'https://api.anthropic.com'
        timeout = float(self.config.get('timeout', 120))
        if timeout < 30:
            timeout = 120.0

        # 拆分 system 消息（Anthropic 要求 system 独立于 messages）
        system_prompt = ''
        anthropic_messages: list[dict] = []
        for m in messages:
            role = m.get('role', 'user')
            content = m.get('content', '')
            if role == 'system':
                system_prompt = (system_prompt + '\n' + content).strip()
            else:
                anthropic_messages.append({'role': role, 'content': content})

        body: dict = {
            'model': model,
            'max_tokens': max_tokens,
            'messages': anthropic_messages,
        }
        if system_prompt:
            body['system'] = system_prompt
        # Anthropic 支持 temperature（默认 1）；top_p 支持但部分中转可能拒绝，作为可选参数
        _temp = gen_params.get('temperature')
        if _temp is not None:
            body['temperature'] = _temp
        if not stripped_optional:
            _tp = gen_params.get('top_p')
            if _tp is not None:
                body['top_p'] = _tp
            # budget 形态：注册表 reasoning.style=budget 且给出 token 数时，
            # 发 Anthropic 自家的 thinking.budget_tokens；effort 形态在这里无对应字段，
            # 且部分中转分组（如 aws-q）禁用了 thinking（传了 503），故默认不发。
            _budget = gen_params.get('reasoning_budget')
            try:
                _budget = int(_budget) if _budget is not None else None
            except (TypeError, ValueError):
                _budget = None
            if _budget and _budget > 0:
                body['thinking'] = {'type': 'enabled', 'budget_tokens': _budget}
            # 厂商方言位置覆盖：如 deepseek 在 anthropic 协议下强度走
            # output_config.effort（注册表 effort_param.anthropic 指定）。
            _eff_path = ''
            try:
                _eff_path = str((model_reasoning_spec(
                    model, self.config).get('effort_param') or {}).get('anthropic') or '')
            except Exception:
                _eff_path = ''
            if _eff_path == 'output_config.effort':
                _ev = gen_params.get('reasoning_effort')
                if _ev is not None:
                    body['output_config'] = {'effort': str(_ev)}
        # (reasoning_effort 在 anthropic 协议下不传——部分中转不支持 thinking，传了会被拒)

        # 鉴权头：anthropic 协议一律 x-api-key（官方标准，不考虑中转站）。
        url = f'{base_url}/messages'
        headers = {
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01',
            'x-api-key': api_key,
        }
        headers.update(_zen_session_headers(base_url))

        if self.verbose and should_print_worker():
            wlog(f"{log_prefix()}  [Anthropic] POST {url} model={model} max_tokens={max_tokens} "
                 f"messages={len(anthropic_messages)} system={len(system_prompt)}字符")

        resp = _sess.post(url, json=body, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            # 包装为异常，让外层重试/参数剔除/配额识别逻辑复用
            msg = resp.text[:500]
            raise _AnthropicAPIError(resp.status_code, msg)

        data = resp.json()
        # 提取文本
        text = ''
        for block in data.get('content', []) or []:
            if block.get('type') == 'text':
                text += block.get('text', '')
        usage = data.get('usage') or {}
        input_tokens = usage.get('input_tokens', 0)
        output_tokens = usage.get('output_tokens', 0)
        # cache 统计（Anthropic cache_read_input_tokens / cache_creation_input_tokens）。
        # 注意 Anthropic 口径与 OpenAI 不同：input_tokens 仅指未缓存部分，
        # 总输入 = input + read + write。旧代码按 OpenAI 口径相减，
        # 缓存命中大时算出负数 miss（如实测 miss=-90），污染费用统计。
        cache_read = usage.get('cache_read_input_tokens', 0) or 0
        cache_write = usage.get('cache_creation_input_tokens', 0) or 0
        try:
            cache_read = max(0, int(cache_read))
            cache_write = max(0, int(cache_write))
            input_tokens = max(0, int(input_tokens or 0))
        except (TypeError, ValueError):
            cache_read, cache_write = 0, 0
        hit = cache_read
        prompt_total = input_tokens + cache_read + cache_write
        miss = max(0, prompt_total - hit)
        stop_reason = data.get('stop_reason') or 'end_turn'

        return _CompatResponse(text, prompt_total, hit, miss, output_tokens, finish_reason=stop_reason)

    # ---------- OpenAI Responses 协议 ----------

    def _chat_responses(self, *, model: str, messages: list[dict], max_tokens: int,
                        gen_params: dict, stripped_optional: bool = False):
        """调用 OpenAI Responses API（/v1/responses）。

        用于 opencode Zen 网关等只支持 responses 协议的模型（如 muse-spark-1.2-contributor，
        走 chat/completions 会 500 Internal server error）。文本从 output[].content[].text 提取。

        返回一个兼容对象，模拟 OpenAI ChatCompletion 结构（choices[0].message.content + usage），
        使上层 extract_content / extract_token_stats 无需感知协议差异。
        """
        import requests as _requests

        # 代理处理：与 Anthropic 分支一致，用 trust_env=False 的 Session + 注册表代理，
        # 规避进程内 NO_PROXY 干扰（否则 getproxies() 被 NO_PROXY=* 压制 → 直连 → RegionError）。
        _sess = self._proxy_session()

        api_key = self.config.get('key') or self.config.get('api_key', '')
        base_url = self.config.get('base_url', '').rstrip('/')
        if not base_url:
            base_url = 'https://api.openai.com'
        timeout = float(self.config.get('timeout', 120))
        if timeout < 30:
            timeout = 120.0

        # 构建 responses 格式的 input：system 拆出，其余按 role 序列化为 string 内容
        system_prompt = ''
        input_items: list[dict] = []
        for m in messages:
            role = m.get('role', 'user')
            content = m.get('content', '')
            if role == 'system':
                system_prompt = (system_prompt + '\n' + content).strip()
            else:
                # 简单字符串内容即可（Responses 也接受纯字符串）
                input_items.append({'role': role, 'content': content})

        body: dict = {
            'model': model,
            'input': input_items,
        }
        if system_prompt:
            # 顶层 instructions 字段承载系统提示
            body['instructions'] = system_prompt
        # 宽松 max_output_tokens：responses 协议的 reasoning tokens 计入输出配额，
        # 思考型模型（muse 等）可能消耗大量 token，传小值会导致思考吃光配额、
        # output 为空/截断。优先取 config.generation_params.max_tokens（用户配置，通常很大），
        # 否则对传入值放大数倍兜底，保证翻译/分割等流程拿到完整输出。
        _cfg_max = int((self.config.get('generation_params') or {}).get('max_tokens', 0) or 0)
        if _cfg_max >= max_tokens:
            _out_tokens = _cfg_max
        else:
            _out_tokens = max(max_tokens, 32768)
        body['max_output_tokens'] = _out_tokens
        _temp = gen_params.get('temperature')
        if _temp is not None:
            body['temperature'] = _temp
        if not stripped_optional:
            _tp = gen_params.get('top_p')
            if _tp is not None:
                body['top_p'] = _tp
        # 思考强度：Responses 协议用顶层 reasoning.effort 控制（非 reasoning_effort 顶层字段）。
        # 映射自 gen_params.reasoning_effort（继承 config.generation_params.reasoning_effort，如 high）。
        # DeepSeek 官方：effort=none 即关闭思考。
        _re = gen_params.get('reasoning_effort')
        if _re and not stripped_optional:
            body['reasoning'] = {'effort': str(_re)}
        # 官方文档：思考模式下 temperature/top_p 不生效。思考开启（effort 非 none）
        # 且注册表 strip_sampling_params=true 的模型，直接剔除。
        _re_on = isinstance(body.get('reasoning'), dict) and str(
            body['reasoning'].get('effort', '')).lower() != 'none'
        if (_re_on and model_reasoning_spec(
                model, self.config).get('strip_sampling_params') is True):
            _dropped_r = [_k for _k in ('temperature', 'top_p') if _k in body]
            for _k in _dropped_r:
                body.pop(_k, None)
            if _dropped_r and self.verbose:
                wlog(f"  [API] 思考模式开启，采样参数不生效，已剔除: "
                     f"{','.join(_dropped_r)}")
        # 注册表 drop_sampling_params=true：该模型完全不接受采样参数（如 GPT-5 系列）
        if model_reasoning_spec(model, self.config).get('drop_sampling_params') is True:
            for _k in ('temperature', 'top_p'):
                body.pop(_k, None)
        url = f'{base_url}/responses'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        }
        headers.update(_zen_session_headers(base_url))

        if self.verbose and should_print_worker():
            wlog(f"{log_prefix()}  [Responses] POST {url} model={model} max_output_tokens={_out_tokens} "
                 f"input={len(input_items)} instructions={len(system_prompt)}字符")

        resp = _sess.post(url, json=body, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            msg = resp.text[:500]
            raise _AnthropicAPIError(resp.status_code, msg)

        data = resp.json()
        # 提取翻译文本：output 数组里 type=='message' 的 item，其 content 中 type=='output_text' 的 text
        text = ''
        for item in data.get('output', []) or []:
            if item.get('type') != 'message':
                continue
            for block in item.get('content', []) or []:
                if block.get('type') == 'output_text':
                    text += block.get('text', '')
        # 若 message 块里没有 output_text（罕见），回退拼接 message 的 content 字符串
        if not text:
            for item in data.get('output', []) or []:
                if item.get('type') == 'message':
                    for block in item.get('content', []) or []:
                        if isinstance(block, str):
                            text += block
        # 极端兜底：无 message item 时，尝试拼接 reasoning item 的 summary（思维链摘要）
        if not text:
            for item in data.get('output', []) or []:
                if item.get('type') == 'reasoning':
                    _summary = item.get('summary') or []
                    for _s in _summary:
                        if isinstance(_s, str):
                            text += _s
                        elif isinstance(_s, dict):
                            text += _s.get('text', '')
        usage = data.get('usage') or {}
        input_tokens = usage.get('input_tokens', 0)
        output_tokens = usage.get('output_tokens', 0)
        # cache 统计：input_tokens_details.cached_tokens（官方文档确认字段）
        hit = 0
        _details = usage.get('input_tokens_details') or {}
        if isinstance(_details, dict):
            hit = _details.get('cached_tokens', 0) or 0
        miss = (input_tokens - hit) if input_tokens else 0
        # 思维链 token：output_tokens_details.reasoning_tokens（官方文档确认字段，
        # 已计入 output_tokens；transit 网关可能不返回，缺省 0）
        _reasoning = 0
        _o_details = usage.get('output_tokens_details') or {}
        if isinstance(_o_details, dict):
            try:
                _reasoning = int(_o_details.get('reasoning_tokens', 0) or 0)
            except (TypeError, ValueError):
                _reasoning = 0
        stop_reason = data.get('status') or 'completed'
        if stop_reason == 'completed':
            stop_reason = 'stop'

        return _CompatResponse(text, input_tokens, hit, miss, output_tokens,
                               reasoning_tokens=_reasoning, finish_reason=stop_reason)

    # ---------- token 统计提取（统一 helper，供调用方复用） ----------

    @staticmethod
    def extract_token_stats(usage) -> dict:
        """从 OpenAI response.usage 提取统一格式的 token 统计。

        兼容：
        - DeepSeek 原生: usage.prompt_cache_hit_tokens / prompt_cache_miss_tokens
        - OpenAI 代理层: usage.prompt_tokens_details.cached_tokens
        """
        if usage is None:
            return {'hit_tokens': 0, 'miss_tokens': 0, 'prompt_tokens': 0,
                    'completion_tokens': 0, 'reasoning_tokens': 0}
        hit = getattr(usage, 'prompt_cache_hit_tokens', None)
        miss = getattr(usage, 'prompt_cache_miss_tokens', None)
        if hit is None:
            details = getattr(usage, 'prompt_tokens_details', None)
            hit = details.cached_tokens if details else 0
            miss = (usage.prompt_tokens - hit) if usage.prompt_tokens else 0
        try:
            _reasoning = int(getattr(usage, 'reasoning_tokens', 0) or 0)
        except (TypeError, ValueError):
            _reasoning = 0
        if not _reasoning:
            # OpenAI SDK 对象：usage.completion_tokens_details.reasoning_tokens
            #（官方文档确认字段；思考模型的思维链占比从这里看）
            try:
                _cd = getattr(usage, 'completion_tokens_details', None)
                _reasoning = int(getattr(_cd, 'reasoning_tokens', 0) or 0) if _cd is not None else 0
            except (TypeError, ValueError):
                _reasoning = 0
        return {
            'hit_tokens': hit or 0,
            'miss_tokens': miss or 0,
            'prompt_tokens': usage.prompt_tokens or 0,
            'completion_tokens': usage.completion_tokens or 0,
            'reasoning_tokens': _reasoning,
        }

    @staticmethod
    def extract_content(msg) -> str:
        """从 response.choices[0].message 提取文本内容（仅最终正文）。

        禁止回退到 reasoning_content：思维链是过程不是答案，
        拿它当结果会导致 JSON 解析失败、世界观存入垃圾（已实测）。
        content 为空一律返回 ''，由调用方按失败处理（重试队列/正则回退）。
        """
        try:
            return msg.content or ''
        except Exception:
            return ''