# -*- coding: utf-8 -*-
"""
统一 Token 用量追踪器

跟踪所有 LLM API 调用的 token 消耗和费用：
- 翻译请求（翻译引擎）
- 台本识别（LLM 判断哪些文件是台本）
- 台本分割/清洗（Flash 模型）
- 世界观分析（LLM 分析角色/场景）

按作品（RJ 目录）分组，最终输出完整汇总。
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


# ==================== 单次用量快照 ====================

@dataclass
class TokenUsage:
    """单次 LLM API 调用的用量快照"""

    request_type: str       # "translate" | "scriptbook_id" | "scriptbook_split" | "worldview"
    label: str              # 可读标签: 文件名 / "台本识别" / "台本分割" 等
    work_key: str           # 所属 RJ 作品标识（目录路径字符串）
    prompt_tokens: int      # 总输入 token
    hit_tokens: int         # 缓存命中 token
    miss_tokens: int        # 缓存未命中 token
    completion_tokens: int  # 输出 token
    cost: float             # 本次费用
    elapsed: float          # 耗时（秒）
    reasoning_tokens: int = 0  # 其中思维链 token（已计入 completion，仅分析用）

    @property
    def hit_rate(self) -> float:
        """缓存命中率 (0-100)"""
        total = self.hit_tokens + self.miss_tokens
        if total == 0:
            return 0.0
        return self.hit_tokens / total * 100

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


# ==================== 追踪器 ====================

class TokenTracker:
    """Token 用量追踪器

    收集每次 LLM 调用的 TokenUsage，按作品分组。
    提供单次打印、作品汇总、批次总汇总。
    """

    def __init__(self, pricing: dict | None = None):
        """
        参数:
            pricing: {'hit_per_1m': float, 'miss_per_1m': float, 'completion_per_1m': float}
        """
        pricing = pricing or {}
        self.hit_per_1m = float(pricing.get('hit_per_1m', 0.02))
        self.miss_per_1m = float(pricing.get('miss_per_1m', 1))
        self.completion_per_1m = float(pricing.get('completion_per_1m', 2))

        self.records: list[TokenUsage] = []
        # 按作品分组: {work_key: [TokenUsage, ...]}
        self._work_groups: dict[str, list[TokenUsage]] = {}

    # ── 记录 ──

    def record(self, usage: TokenUsage) -> None:
        """记录一次 LLM 调用"""
        self.records.append(usage)
        self._work_groups.setdefault(usage.work_key, []).append(usage)

    def compute_cost(self, hit: int, miss: int, completion: int) -> float:
        """根据当前定价计算费用"""
        return (
            (hit / 1_000_000) * self.hit_per_1m
            + (miss / 1_000_000) * self.miss_per_1m
            + (completion / 1_000_000) * self.completion_per_1m
        )

    # ── 属性 ──

    @property
    def pricing_available(self) -> bool:
        """定价是否已配置"""
        return self.hit_per_1m > 0 or self.miss_per_1m > 0 or self.completion_per_1m > 0

    @property
    def total_cost(self) -> float:
        return sum(r.cost for r in self.records if r.cost is not None)

    @property
    def total_api_calls(self) -> int:
        return len(self.records)

    @property
    def total_prompt_tokens(self) -> int:
        return sum(r.prompt_tokens for r in self.records)

    @property
    def total_hit_tokens(self) -> int:
        return sum(r.hit_tokens for r in self.records)

    @property
    def total_miss_tokens(self) -> int:
        return sum(r.miss_tokens for r in self.records)

    @property
    def total_completion_tokens(self) -> int:
        return sum(r.completion_tokens for r in self.records)

    @property
    def total_reasoning_tokens(self) -> int:
        return sum(getattr(r, 'reasoning_tokens', 0) or 0 for r in self.records)

    @property
    def overall_hit_rate(self) -> float:
        total = self.total_hit_tokens + self.total_miss_tokens
        if total == 0:
            return 0.0
        return self.total_hit_tokens / total * 100

    @property
    def type_counts(self) -> dict[str, int]:
        """按调用类型统计次数 {'翻译': 13, '台本识别': 1, ...}"""
        counts: dict[str, int] = {}
        TYPE_CN_MAP = {
            'translate': '翻译',
            'scriptbook_id': '台本识别',
            'scriptbook_split': '台本分割',
            'worldview': '世界观',
            'terms': '术语',
        }
        for r in self.records:
            tcn = TYPE_CN_MAP.get(r.request_type, r.request_type)
            counts[tcn] = counts.get(tcn, 0) + 1
        return counts

    @property
    def work_summaries(self) -> list[dict]:
        """按作品(RJ目录)汇总数据，供 Printer 使用"""
        summaries: list[dict] = []
        for work_key, usages in self._work_groups.items():
            work_name = work_key.replace('\\', '/').rstrip('/').split('/')[-1]
            if len(work_name) > 50:
                work_name = work_name[:47] + '...'

            w_type_counts: dict[str, int] = {}
            TYPE_CN_MAP = {
                'translate': '翻译',
                'scriptbook_id': '台本识别',
                'scriptbook_split': '台本分割',
                'worldview': '世界观',
                'terms': '术语',
            }
            for u in usages:
                tcn = TYPE_CN_MAP.get(u.request_type, u.request_type)
                w_type_counts[tcn] = w_type_counts.get(tcn, 0) + 1

            call_types = ' + '.join(f'{k} x{v}' for k, v in sorted(w_type_counts.items()))

            summaries.append({
                'name': work_name,
                'call_count': len(usages),
                'call_types': call_types,
                'prompt_tokens': sum(u.prompt_tokens for u in usages),
                'hit_tokens': sum(u.hit_tokens for u in usages),
                'miss_tokens': sum(u.miss_tokens for u in usages),
                'completion_tokens': sum(u.completion_tokens for u in usages),
                'cost': sum(u.cost for u in usages if u.cost is not None),
            })
        return summaries
