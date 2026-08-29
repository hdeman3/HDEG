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


# OpenAI 兼容 API 可接受的生成参数白名单
_ALLOWED_GEN_PARAMS = (
    'temperature', 'top_p', 'top_k', 'presence_penalty', 'frequency_penalty',
    'stop', 'logit_bias', 'user', 'reasoning_effort',
)
# 部分 OpenAI 兼容服务不接受的扩展参数（被拒后自动剔除重试）
_OPTIONAL_PARAMS = ('reasoning_effort', 'top_k')


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
                 completion_tokens: int, finish_reason: str = 'stop'):
        self.choices = [_CompatChoice(text, finish_reason)]
        self.usage = _CompatUsage(prompt_tokens, hit_tokens, miss_tokens, completion_tokens)


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
                 completion_tokens: int):
        self.prompt_tokens = prompt_tokens
        self.prompt_cache_hit_tokens = hit_tokens
        self.prompt_cache_miss_tokens = miss_tokens
        self.completion_tokens = completion_tokens
        self.prompt_tokens_details = _CompatPromptDetails(hit_tokens)


class _CompatPromptDetails:
    def __init__(self, cached_tokens: int):
        self.cached_tokens = cached_tokens


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
        # 协议探测：三态 —— 'openai'（chat/completions）/ 'anthropic'（messages）/ 'responses'（/v1/responses）。
        # 支持 config 显式覆盖 api.protocol = 'openai' | 'anthropic' | 'responses'；
        # 未显式指定时按模型名推断：含 claude → anthropic，含 muse-spark-1.2-contributor → responses，
        # 其余默认 openai（chat/completions）。
        _proto = (config.get('protocol') or '').lower()
        if _proto == 'anthropic':
            self._is_anthropic = True
            self._is_responses = False
        elif _proto == 'responses':
            self._is_anthropic = False
            self._is_responses = True
        elif _proto == 'openai':
            self._is_anthropic = False
            self._is_responses = False
        else:
            _model_l = (config.get('model') or '').lower()
            self._is_anthropic = 'claude' in _model_l
            # 注意顺序：muse-spark-1.2-contributor 在 opencode Zen 网关只支持 responses 协议
            #（走 chat/completions 会 500 Internal server error），故单独命中为 responses。
            self._is_responses = 'muse-spark-1.2-contributor' in _model_l and not self._is_anthropic
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
        reasoning_effort: str | None = None,
        max_retries: int = 3,
    ):
        """统一的 chat/completions 调用（内置模型轮换 + 参数剔除 + 指数退避）。

        参数:
            messages: OpenAI messages 列表 [{role, content}, ...]
            max_tokens: 输出 token 上限
            temperature / top_p / reasoning_effort: 可选生成参数
            max_retries: 每个模型的重试次数（配额耗尽不计入，直接切模型）

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
        if reasoning_effort is not None:
            gen_params['reasoning_effort'] = reasoning_effort
        # 也允许从 config.generation_params 继承默认值（调用方未显式传入时）
        cfg_gen = self.config.get('generation_params', {}) or {}
        for k in ('temperature', 'top_p', 'reasoning_effort'):
            if k not in gen_params and k in cfg_gen:
                gen_params[k] = cfg_gen[k]

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

        def _print_heartbeat():
            if _hb_worker_id is not None:
                worker_local._worker_id = _hb_worker_id
            while not heartbeat_stop.is_set():
                heartbeat_stop.wait(10)
                if not heartbeat_stop.is_set():
                    secs = int(_time_mod.monotonic() - _hb_start)
                    # worker 静默模式下心跳不打印（只保留主线程内容）
                    if should_print_worker():
                        print(f"{log_prefix()}    [等待] 已等待 {secs} 秒...", flush=True)

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
                if _model_idx > 0 and should_print_worker():
                    print(f"  [API] 切换模型 → {_current_model}（第 {_model_idx+1}/{len(_model_chain)} 个）", flush=True)

                # 尝试次数 = max(max_retries, 1)：max_retries=0 表示不重试，但仍执行首次尝试
                for attempt in range(max(max_retries, 1)):
                    try:
                        if self._is_anthropic:
                            response = self._chat_anthropic(
                                model=_current_model,
                                messages=messages,
                                max_tokens=max_tokens,
                                gen_params=gen_params,
                                stripped_optional=_stripped_optional,
                            )
                            return response

                        if self._is_responses:
                            response = self._chat_responses(
                                model=_current_model,
                                messages=messages,
                                max_tokens=max_tokens,
                                gen_params=gen_params,
                                stripped_optional=_stripped_optional,
                            )
                            return response

                        # 只保留白名单内参数
                        _filtered = {k: v for k, v in gen_params.items() if k in _ALLOWED_GEN_PARAMS}
                        _filtered['max_tokens'] = max_tokens
                        # 曾因参数不支持被拒 → 剔除可选参数重试
                        if _stripped_optional:
                            for _k in _OPTIONAL_PARAMS:
                                _filtered.pop(_k, None)

                        # 思考模式 extra_body：仅 DeepSeek 原生接口使用
                        _extra = {}
                        if (not _stripped_optional and self._is_deepseek
                                and 'reasoning_effort' in _filtered):
                            _extra = {'thinking': {'type': 'enabled'}}
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
                            print(f"  [API] 模型 {_current_model} 尝试 {attempt+1}/{max_retries} 失败: "
                                  f"{type(e).__name__}: {e}", flush=True)
                        # 参数不支持 → 剔除可选参数后立即重试（同一模型）
                        if not _stripped_optional and self._looks_like_unsupported_param(e):
                            _stripped_optional = True
                            if self.verbose and should_print_worker():
                                print("  [API] 服务可能不支持 reasoning_effort/top_k/thinking，自动剔除后重试", flush=True)
                            continue
                        # 配额耗尽 → 切换到下一个模型继续同一批次
                        if self._looks_like_quota_exhausted(e):
                            if self.verbose:
                                print(f"  [API] 模型 {_current_model} 配额/用量受限，准备切换模型", flush=True)
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

        trust_env=False → 完全忽略环境变量（含 NO_PROXY），只使用显式代理，
        确保中转（muse/claude 等）即使进程环境残留 NO_PROXY 也强制走代理。
        """
        import requests as _requests
        _sess = _requests.Session()
        _sess.trust_env = False
        _px = APIClient._get_system_proxies()
        if _px:
            _sess.proxies.update(_px)
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
            # Anthropic 无 reasoning_effort 参数（用 thinking），且部分中转分组（如 aws-q）
            # 禁用了 thinking 参数（传了会 503 model_not_found）。因此 anthropic 模式下忽略。
        # (reasoning_effort 在 anthropic 协议下不传——部分中转不支持 thinking，传了会被拒)

        url = f'{base_url}/messages'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
            'anthropic-version': '2023-06-01',
        }

        if self.verbose and should_print_worker():
            print(f"{log_prefix()}  [Anthropic] POST {url} model={model} max_tokens={max_tokens} "
                  f"messages={len(anthropic_messages)} system={len(system_prompt)}字符", flush=True)

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
        # cache 统计（Anthropic cache_read_input_tokens / cache_creation_input_tokens）
        cache_read = usage.get('cache_read_input_tokens', 0)
        cache_write = usage.get('cache_creation_input_tokens', 0)
        hit = cache_read
        miss = input_tokens - cache_read - cache_write
        stop_reason = data.get('stop_reason') or 'end_turn'

        return _CompatResponse(text, input_tokens, hit, miss, output_tokens, finish_reason=stop_reason)

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
        _re = gen_params.get('reasoning_effort')
        if _re and not stripped_optional:
            body['reasoning'] = {'effort': str(_re)}

        url = f'{base_url}/responses'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
        }

        if self.verbose and should_print_worker():
            print(f"{log_prefix()}  [Responses] POST {url} model={model} max_output_tokens={_out_tokens} "
                  f"input={len(input_items)} instructions={len(system_prompt)}字符", flush=True)

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
        # cache 统计：input_tokens_details.cached_tokens
        hit = 0
        _details = usage.get('input_tokens_details') or {}
        if isinstance(_details, dict):
            hit = _details.get('cached_tokens', 0) or 0
        miss = (input_tokens - hit) if input_tokens else 0
        stop_reason = data.get('status') or 'completed'
        if stop_reason == 'completed':
            stop_reason = 'stop'

        return _CompatResponse(text, input_tokens, hit, miss, output_tokens, finish_reason=stop_reason)

    # ---------- token 统计提取（统一 helper，供调用方复用） ----------

    @staticmethod
    def extract_token_stats(usage) -> dict:
        """从 OpenAI response.usage 提取统一格式的 token 统计。

        兼容：
        - DeepSeek 原生: usage.prompt_cache_hit_tokens / prompt_cache_miss_tokens
        - OpenAI 代理层: usage.prompt_tokens_details.cached_tokens
        """
        if usage is None:
            return {'hit_tokens': 0, 'miss_tokens': 0, 'prompt_tokens': 0, 'completion_tokens': 0}
        hit = getattr(usage, 'prompt_cache_hit_tokens', None)
        miss = getattr(usage, 'prompt_cache_miss_tokens', None)
        if hit is None:
            details = getattr(usage, 'prompt_tokens_details', None)
            hit = details.cached_tokens if details else 0
            miss = (usage.prompt_tokens - hit) if usage.prompt_tokens else 0
        return {
            'hit_tokens': hit or 0,
            'miss_tokens': miss or 0,
            'prompt_tokens': usage.prompt_tokens or 0,
            'completion_tokens': usage.completion_tokens or 0,
        }

    @staticmethod
    def extract_content(msg) -> str:
        """从 response.choices[0].message 提取文本内容。

        兼容 DeepSeek V4 reasoning: content 为空时回退到 reasoning_content。
        """
        content = msg.content or ''
        if not content and hasattr(msg, 'reasoning_content') and msg.reasoning_content:
            content = msg.reasoning_content
        return content