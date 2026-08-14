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


# OpenAI 兼容 API 可接受的生成参数白名单
_ALLOWED_GEN_PARAMS = (
    'temperature', 'top_p', 'top_k', 'presence_penalty', 'frequency_penalty',
    'stop', 'logit_bias', 'user', 'reasoning_effort',
)
# 部分 OpenAI 兼容服务不接受的扩展参数（被拒后自动剔除重试）
_OPTIONAL_PARAMS = ('reasoning_effort', 'top_k')


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
        # 模型轮换状态：当前在模型链中的索引
        self._model_idx = 0

    # ---------- 客户端初始化 ----------

    def _ensure_client(self):
        """延迟初始化 OpenAI 客户端（清理代理环境变量后创建）"""
        if self._client is not None:
            return
        from openai import OpenAI
        import os as _os
        for _k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
            _os.environ.pop(_k, None)
        _os.environ['NO_PROXY'] = '*'
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

        def _print_heartbeat():
            while not heartbeat_stop.is_set():
                heartbeat_stop.wait(10)
                if not heartbeat_stop.is_set():
                    secs = int(_time_mod.monotonic() - _hb_start)
                    print(f"    [等待] 已等待 {secs} 秒...", flush=True)

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
                if _model_idx > 0:
                    print(f"  [API] 切换模型 → {_current_model}（第 {_model_idx+1}/{len(_model_chain)} 个）", flush=True)

                for attempt in range(max_retries):
                    try:
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
                        if self.verbose:
                            print(f"  [API] 模型 {_current_model} 尝试 {attempt+1}/{max_retries} 失败: "
                                  f"{type(e).__name__}: {e}", flush=True)
                        # 参数不支持 → 剔除可选参数后立即重试（同一模型）
                        if not _stripped_optional and self._looks_like_unsupported_param(e):
                            _stripped_optional = True
                            if self.verbose:
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