# -*- coding: utf-8 -*-
"""
统一分级输出工具

项目中唯一的 self._logp() 入口。所有输出统一经过 Printer 实例。

层级规范:
  section()   ===== 标题 =====           (顶级分隔)
  step()      ----- 第 N 步: xxx -----   (步骤标题)
  info()        [标签] 普通信息           (2空格)
  detail()        [子标签] 详情           (4空格)
  fine()            具体数值              (6空格)

debug 控制:
  所有调用 printer.debug() 的内容仅在 debug=True 时输出。
  用户设置 config.json → app.debug 控制。
"""

from __future__ import annotations
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from utils.token_tracker import TokenTracker


class Printer:
    """统一分级输出"""

    def __init__(self, debug: bool = False):
        self.debug_enabled = debug

    def _logp(self, *args, **kwargs):
        """带 worker 前缀的 print：worker 线程日志自动加 [W{n}] 前缀，与 _log/_p 一致。"""
        try:
            from engines.api_client import log_prefix
            prefix = log_prefix()
        except Exception:
            prefix = ''
        if prefix and args and isinstance(args[0], str):
            s = args[0]
            idx = 0
            while idx < len(s) and s[idx] == '\n':
                idx += 1
            args = (s[:idx] + prefix + s[idx:],) + args[1:]
        elif prefix:
            args = (prefix,) + args
        print(*args, **kwargs)

    # ── 顶级结构 ──

    def section(self, title: str) -> None:
        """===== 顶级标题 ====="""
        self.blank()
        self._logp(f"{'='*60}", flush=True)
        self._logp(f"  {title}", flush=True)
        self._logp(f"{'='*60}", flush=True)

    def step(self, num: int | str, title: str) -> None:
        """----- 第 N 步: xxx -----"""
        self.blank()
        self._logp(f"{'-'*60}", flush=True)
        self._logp(f"  第 {num} 步: {title}", flush=True)
        self._logp(f"{'-'*60}", flush=True)

    # ── 用户可见信息 ──

    def info(self, msg: str) -> None:
        """普通信息（2空格缩进）"""
        self._logp(f"  {msg}", flush=True)

    def detail(self, msg: str) -> None:
        """子信息（4空格缩进）"""
        self._logp(f"    {msg}", flush=True)

    def fine(self, msg: str) -> None:
        """最细粒度（6空格缩进，token 行等）"""
        self._logp(f"      {msg}", flush=True)

    def ok(self, msg: str = "") -> None:
        """成功"""
        if msg:
            self._logp(f"    OK  {msg}", flush=True)
        else:
            self._logp(f"  OK", flush=True)

    def warn(self, msg: str) -> None:
        """警告"""
        self._logp(f"  WARN  {msg}", flush=True)

    def error(self, msg: str) -> None:
        """错误"""
        self._logp(f"  ERROR  {msg}", flush=True)

    def hr(self) -> None:
        """分隔线"""
        self._logp(f"  {'-'*56}", flush=True)

    def blank(self) -> None:
        """空行"""
        self._logp(flush=True)

    # ── Token 用量（始终输出）──

    @staticmethod
    def _fmt_tok(n: int) -> str:
        """格式化 token 数"""
        if n >= 10000:
            return f"{n:,}"
        return str(n)

    @staticmethod
    def _fmt_cost(n: float) -> str:
        """格式化费用"""
        if n < 0.0001:
            return f"¥{n:.6f}"
        elif n < 0.01:
            return f"¥{n:.4f}"
        else:
            return f"¥{n:.2f}"

    TYPE_CN: dict[str, str] = {
        'translate': '翻译',
        'scriptbook_id': '台本识别',
        'scriptbook_split': '台本分割',
        'worldview': '世界观',
        'terms': '术语',
    }

    def token(self, type_cn: str, label: str, elapsed: float,
              prompt_tokens: int, hit_tokens: int, miss_tokens: int,
              completion_tokens: int, cost: float | None) -> None:
        """单次 API 调用的 token 用量行"""
        elapsed_str = f"{elapsed:.1f}s" if elapsed >= 1 else f"{elapsed*1000:.0f}ms"
        hit_rate = (hit_tokens / (hit_tokens + miss_tokens) * 100) if (hit_tokens + miss_tokens) > 0 else 0

        cost_str = self._fmt_cost(cost) if cost is not None else "N/A"

        self.detail(f"[{type_cn}] {label}  ({elapsed_str})")
        self.fine(
            f"输入 {self._fmt_tok(prompt_tokens)} | "
            f"Cache命中 {self._fmt_tok(hit_tokens)} ({hit_rate:.0f}%) | "
            f"Cache未命中 {self._fmt_tok(miss_tokens)}"
        )
        self.fine(
            f"输出 {self._fmt_tok(completion_tokens)} | "
            f"费用 {cost_str}"
        )

    def token_summary(self, tracker: TokenTracker, elapsed_total: float = 0.0) -> None:
        """批次最终汇总"""
        if not tracker.records:
            self.info("(无 API 调用记录)")
            return

        hit = tracker.total_hit_tokens
        miss = tracker.total_miss_tokens
        comp = tracker.total_completion_tokens
        prompt_tok = tracker.total_prompt_tokens
        total_tok = prompt_tok + comp

        pricing = tracker.pricing_available
        if pricing:
            c_hit = (hit / 1_000_000) * tracker.hit_per_1m
            c_miss = (miss / 1_000_000) * tracker.miss_per_1m
            c_comp = (comp / 1_000_000) * tracker.completion_per_1m
            c_total = c_hit + c_miss + c_comp
        else:
            c_hit = c_miss = c_comp = c_total = None

        # 按类型统计
        type_counts = tracker.type_counts
        type_detail = ' + '.join(
            f"{k} x{v}" for k, v in sorted(type_counts.items())
        )

        elapsed_min = elapsed_total / 60 if elapsed_total > 0 else 0

        self.blank()
        self.section("API Token 用量 & 费用统计")
        self.info(f"API 调用: {tracker.total_api_calls} 次  ({type_detail})")
        if elapsed_min > 0:
            self.info(f"总耗时: {elapsed_min:.1f} 分钟")
        self.info(f"总 Token: {total_tok:,}")
        self.info(
            f"Cache命中:  {hit:>10,} tokens ({tracker.overall_hit_rate:.1f}%)"
            + (f"  x ¥{tracker.hit_per_1m}/M = {self._fmt_cost(c_hit)}" if pricing else "")
        )
        self.info(
            f"Cache未命中: {miss:>10,} tokens"
            + (f"  x ¥{tracker.miss_per_1m}/M = {self._fmt_cost(c_miss)}" if pricing else "")
        )
        self.info(
            f"输出Token:  {comp:>10,} tokens"
            + (f"  x ¥{tracker.completion_per_1m}/M = {self._fmt_cost(c_comp)}" if pricing else "")
        )
        self.hr()
        if c_total is not None:
            self.info(f"总费用: {self._fmt_cost(c_total)}")
        else:
            self.info(f"总费用: N/A (未配置定价)")

        # 核对累计
        if c_total is not None:
            accumulated = sum(r.cost for r in tracker.records if r.cost is not None)
            if abs(accumulated - c_total) > 0.0001:
                self.warn(f"费用核对不一致: 记录累计={self._fmt_cost(accumulated)} vs 汇总={self._fmt_cost(c_total)}")

        # 按作品汇总（多作品时显示）
        works = tracker.work_summaries
        if len(works) > 1:
            self.blank()
            self.info("作品用量明细:")
            for w in works:
                w_hit_rate = (w['hit_tokens'] / (w['hit_tokens'] + w['miss_tokens']) * 100) \
                    if (w['hit_tokens'] + w['miss_tokens']) > 0 else 0
                w_cost_str = self._fmt_cost(w['cost']) if w['cost'] is not None else "N/A"

                self.detail(f"{w['name']}")
                self.fine(
                    f"调用 {w['call_count']} 次 ({w['call_types']}) | "
                    f"输入 {self._fmt_tok(w['prompt_tokens'])} | "
                    f"输出 {self._fmt_tok(w['completion_tokens'])}"
                )
                self.fine(
                    f"Cache命中 {self._fmt_tok(w['hit_tokens'])} ({w_hit_rate:.0f}%) | "
                    f"费用 {w_cost_str}"
                )
        self.blank()

    # ── Token 内联输出（简化版，用于非 orchesterator 上下文中）──

    def token_inline(self, usage) -> None:
        """TokenTracker 记录的快速内联输出（兼容旧调用风格）"""
        from utils.token_tracker import TokenUsage
        if isinstance(usage, TokenUsage):
            type_cn = self.TYPE_CN.get(usage.request_type, usage.request_type)
            self.token(
                type_cn=type_cn,
                label=usage.label,
                elapsed=usage.elapsed,
                prompt_tokens=usage.prompt_tokens,
                hit_tokens=usage.hit_tokens,
                miss_tokens=usage.miss_tokens,
                completion_tokens=usage.completion_tokens,
                cost=usage.cost,
            )

    # ── 仅 debug 模式 ──

    def debug(self, msg: str) -> None:
        """调试信息"""
        if self.debug_enabled:
            self._logp(f"    [DEBUG] {msg}", flush=True)

    def debug_llm_response(self, text: str, max_chars: int = 300) -> None:
        """LLM 原始响应预览"""
        if not self.debug_enabled:
            return
        preview = text[:max_chars]
        suffix = '...' if len(text) > max_chars else ''
        self._logp(f"    [DEBUG-LLM] ({len(text)}字符) {preview}{suffix}", flush=True)

    def debug_json_parse(self, success: bool, parsed_count: int, input_count: int,
                         non_empty: int = 0) -> None:
        """JSON 解析结果"""
        if not self.debug_enabled:
            return
        if success:
            self._logp(f"    [DEBUG-JSON] 解析成功: {parsed_count}条, 非空{non_empty}条 (输入{input_count}行)", flush=True)
        else:
            self._logp(f"    [DEBUG-JSON] 解析失败 (输入{input_count}行)", flush=True)
