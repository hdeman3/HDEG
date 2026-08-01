# -*- coding: utf-8 -*-
"""
台本分割与清洗引擎

使用 Flash 模型（低价）将原始台本按音轨分割并清洗：
  1. 按音轨标题切分（LLM 语义匹配）
  2. 拼接 PDF 断行
  3. 丢弃舞台指示/音效描述/注释
  4. 返回 {音轨名: [清洁台词]} 映射
"""

from __future__ import annotations
import json as _json
import time as _time_mod
from pathlib import Path
from typing import Any


# ==================== Flash 分割 Prompt ====================

SPLIT_SYSTEM_PROMPT = (
    "你是一位日文音声台本整理助手。"
    "你的任务是将原始台本按音轨分割并清洗为纯台词。"
    "严格按照 JSON 格式输出，不要输出任何其他内容。"
)

# ══ V1 (已废弃) — 全文本输出方案 ══
# _SPLIT_USER_TEMPLATE = """【音轨列表】（必须使用以下名称作为输出的 key）
# {track_names_json}
# ...
# 仅输出 JSON（无任何解释）：
# {{"tracks": {{"音轨名1": ["台词1", "台词2"], "音轨名2": [...]}}}}
# 如果某个音轨在台本中找不到对应内容，其值设为空数组 []。"""


# ══ V2 (已废弃) — 全文本输出 + 清洗规则 ══
# _SPLIT_USER_TEMPLATE_V2 = """..."""
# 由 V3 行号范围方案替代


# ── V3 Prompt（行号范围输出，不做清洗，只做定位） ──

_SPLIT_USER_TEMPLATE_V3 = """【音轨列表】（必须使用以下名称作为输出的 key）
{track_names_json}

【音轨 ASR 样本】（每条音轨的前几句实际台词，用于辅助定位台本中的对应段落。⚠ ASR 识别可能有误，仅作语义锚点参考，不要逐字匹配）
{track_samples_text}

【原始台本】（每行已编号，格式为 行号|内容。使用行号引用区间）
{numbered_scriptbook}

【规则】
1. 按音轨标题语义 + ASR 样本定位每个音轨在台本中的起止位置
2. 输出 [起始行号, 结束行号]（均为整数，1-based，闭区间）
3. 找不到内容的音轨设为空数组 []
4. 只做定位，不做清洗、不做翻译、不拼接断行

仅输出 JSON（无任何解释）：
{{"tracks": {{"track_name": [start, end], ...}}}}"""


# ── 预分割台本清洗 Prompt（已按文件分轨，只需清洗） ──

_PRE_SPLIT_CLEAN_TEMPLATE = """【音轨文件】（每个文件对应一个音轨，文件名即音轨名）
{track_files_json}

【规则 —— 删除以下内容】
1. **SE（音效）**：ＳＥ：开头的音效描述
2. **演技指示**：（）包裹的演技注记（如 (驚いた様子で)）、■ 开头的指示行
3. **场景描述/设定**：⚪︎⚫︎标记的场景说明、角色设定、＊...＊ 包裹的演出注释
4. **非台词叙述**：主人公「...」格式的叙述行、纯场景描述行

【规则 —— 保留以下内容】
- 【角色名】标记的台词行及其后续对话行（无【角色名】但紧跟台词的叙述除外）
- 台词中的娇喘/发声
- ♥♡ 等语气符号
- **每句对话作为独立的数组元素，不要将多句合并为一个字符串**

仅做清洗，不做翻译、不做纠错。**台词按说话顺序**，适当拆句。
输出 JSON（数组元素必须逐句拆分）：
{{"tracks": {{"track_name": ["台词1", "台词2"], ...}}}}

台本不一定存在——找不到内容或文件为空的音轨设为空数组 []，不强求。"""


# ── 保守预清洗（仅删 100% 确定的噪音） ──

def _conservative_pre_clean(text: str) -> str:
    """只删除确定不是台词的内容，不做启发式判断"""
    import re as _re
    # 页面标记
    text = _re.sub(r'<!--\s*page\s+\d+\s*-->', '', text)
    # SE 音效行
    text = _re.sub(r'ＳＥ：[^\n]*', '', text)
    text = _re.sub(r'SE\s*[:：][^\n]*', '', text, flags=_re.IGNORECASE)
    # 纯页码行
    text = _re.sub(r'^\d{1,4}\s*$', '', text, flags=_re.MULTILINE)
    text = _re.sub(r'^\d+/\d+\s*$', '', text, flags=_re.MULTILINE)
    # 连续空行压缩
    text = _re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _merge_short_lines(text: str, min_len: int = 10) -> str:
    """合并短行：将 < min_len 字的行拼接到上行，降低 PDF 提取碎片度

    跳过角色标记（◆）、演技指示（■）、音响标记（○●）开头的行。
    返回合并后的文本。
    """
    lines = text.split('\n')
    merged: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            merged.append('')
            continue
        if merged and merged[-1] and len(s) < min_len and s[0] not in ('◆', '■', '○', '●'):
            merged[-1] = merged[-1] + s
        else:
            merged.append(s)
    return '\n'.join(m for m in merged if m)


# ==================== 分割器实现 ====================

class ScriptbookSplitter:
    """台本分割器 —— 一次 Flash 调用完成分割+清洗

    独立于翻译引擎：始终使用 Flash 模型（低价快速），
    仅共享 API key 和 base_url，不读取 config 中的翻译模型名。
    """

    # 固定使用 Flash 模型（不跟随 config 的翻译模型设置）
    SPLIT_MODEL = 'deepseek-v4-flash'
    # 输出窗口：flash 模型有内部推理 token 开销，给足余量
    SPLIT_MAX_TOKENS = 262144

    def __init__(self, config: dict, verbose: bool = False):
        """
        参数:
            config: API 配置字典（仅读取 key 和 base_url；model 固定为 Flash）
            verbose: 是否输出调试信息
        """
        self.api_key = config.get('key') or config.get('api_key', 'sk-no-key')
        self.base_url = config.get('base_url', 'http://localhost:8000/v1')
        # 分割超时对标翻译（但截取下限，避免太短）
        self.timeout = max(config.get('timeout', 300), 300)
        self.verbose = verbose
        self._client = None

    def _ensure_client(self):
        """延迟初始化客户端"""
        if self._client is not None:
            return
        from openai import OpenAI
        import os as _os
        for _k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy'):
            _os.environ.pop(_k, None)
        _os.environ['NO_PROXY'] = '*'
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.timeout,
        )

    # ══ V1 (已废弃) — 全文本输出，LLM 返回清洗+分割后的完整台本 ══
    # def split_and_clean(self, track_names, raw_scriptbook, *, max_retries=2):
    #     """一次 Flash 调用完成台本分割+清洗。"""
    #     ... (已废弃，由 V3 行号范围方案替代)

    def split_and_clean_all_in_one(
        self,
        track_names: list[str],
        raw_scriptbook: str,
        *,
        track_samples: dict[str, str] = None,
        max_retries: int = 2,
    ) -> dict[str, list[str]]:
        """
        V3: 一次 Flash 调用完成「定位」（行号范围输出）。

        LLM 只返回每个音轨对应台本的行号区间 [start, end]，
        文本提取和清洗全部在本地完成，输出 token 减少 ~99%。

        参数:
            track_names: 音轨名列表（LRC 文件 stem）
            raw_scriptbook: 原始台本全文
            track_samples: 每条音轨的前几句 ASR 台词样本

        返回:
            {track_name: [clean_lines]}
        """
        if not track_names or not raw_scriptbook.strip():
            return {}

        # 1. 保守预清洗
        cleaned = _conservative_pre_clean(raw_scriptbook)
        if self.verbose:
            red_pct = (1 - len(cleaned) / max(len(raw_scriptbook), 1)) * 100
            print(f"  [台本分割V3] 预清洗: {len(raw_scriptbook)} → {len(cleaned)} 字符 ({red_pct:.0f}% 减少)")

        # 1.5 短行合并 (降低 PDF 碎片度)
        cleaned = _merge_short_lines(cleaned)
        if self.verbose:
            print(f"  [台本分割V3] 短行合并后: {len(cleaned)} 字符")

        # 2. 编号行号
        numbered_text, original_lines = self._number_scriptbook_lines(cleaned)
        if self.verbose:
            print(f"  [台本分割V3] 编号 {len(original_lines)} 行")

        # 3. 构建 ASR 样本参考文本
        if track_samples:
            sample_parts = []
            for name in track_names:
                sample_text = track_samples.get(name, '')
                if sample_text:
                    sample_parts.append(f'【{name}】\n{sample_text}')
                else:
                    sample_parts.append(f'【{name}】\n（无样本）')
            track_samples_text = '\n\n'.join(sample_parts)
        else:
            track_samples_text = '（无 ASR 样本，仅根据音轨名匹配）'

        # 4. 构建 V3 prompt
        track_names_json = _json.dumps(track_names, ensure_ascii=False)
        user_prompt = _SPLIT_USER_TEMPLATE_V3.format(
            track_names_json=track_names_json,
            track_samples_text=track_samples_text,
            numbered_scriptbook=numbered_text,
        )

        if self.verbose:
            print(f"  [台本分割V3] {len(track_names)} 个音轨, prompt {len(user_prompt)} 字符, "
                  f"max_tokens={self.SPLIT_MAX_TOKENS}")

        self._ensure_client()

        last_error = None
        for attempt in range(max_retries):
            try:
                # 心跳线程：长请求时打印等待进度
                import threading
                heartbeat_stop = threading.Event()
                heartbeat_count = [0]

                def _print_heartbeat():
                    while not heartbeat_stop.is_set():
                        heartbeat_stop.wait(10)
                        if not heartbeat_stop.is_set():
                            heartbeat_count[0] += 10
                            print(f"    [等待] 已等待 {heartbeat_count[0]} 秒...", flush=True)

                heartbeat_thread = threading.Thread(target=_print_heartbeat, daemon=True)
                heartbeat_thread.start()

                try:
                    response = self._client.chat.completions.create(
                        model=self.SPLIT_MODEL,
                        messages=[
                            {'role': 'system', 'content': SPLIT_SYSTEM_PROMPT},
                            {'role': 'user', 'content': user_prompt},
                        ],
                        temperature=0.1,
                        max_tokens=self.SPLIT_MAX_TOKENS,
                    )
                finally:
                    heartbeat_stop.set()
                    heartbeat_thread.join(timeout=1)

                content = response.choices[0].message.content or ''
                finish = response.choices[0].finish_reason or 'unknown'
                usage_info = f"prompt={response.usage.prompt_tokens if response.usage else '?'}, completion={response.usage.completion_tokens if response.usage else '?'}" if response.usage else ''
                if self.verbose:
                    print(f"  [台本分割V3] finish_reason={finish}, {usage_info}")
                    preview = content[:500] + ('...' if len(content) > 500 else '')
                    print(f"  [台本分割V3] LLM 响应 ({len(content)} 字符): {preview}")
                    if len(content) > 500:
                        print(f"  [台本分割V3] ...末尾: {content[-200:]}")
                result = self._parse_response(content, track_names, original_lines=original_lines)
                if result:
                    if self.verbose:
                        total_lines = sum(len(v) for v in result.values())
                        print(f"  [台本分割V3] 成功: {len(result)}/{len(track_names)} 个音轨, "
                              f"共 {total_lines} 行台词")
                    return result

            except Exception as e:
                last_error = e
                if self.verbose:
                    print(f"  [台本分割V3] 尝试 {attempt+1}/{max_retries} 失败: {e}")
                if attempt < max_retries - 1:
                    _time_mod.sleep(2 ** attempt)

        if self.verbose:
            print(f"  [台本分割V3] 全部尝试失败: {last_error}")
        return {}

    # ═════════════════════════════════════════════════════════
    # V2 (注释保留): 全文本输出方案，LLM 返回完整清洗后台本。
    # 如需回退，取消下方注释并注释掉上面的 V3 实现。
    # ═════════════════════════════════════════════════════════
    # def split_and_clean_all_in_one_v2(
    #     self,
    #     track_names: list[str],
    #     raw_scriptbook: str,
    #     *,
    #     track_samples: dict[str, str] = None,
    #     max_retries: int = 2,
    # ) -> dict[str, list[str]]:
    #     """V2: 一次 Flash 调用完成「清洗 + 分割」（全文本输出）"""
    #     if not track_names or not raw_scriptbook.strip():
    #         return {}
    #
    #     cleaned = _conservative_pre_clean(raw_scriptbook)
    #     if self.verbose:
    #         red_pct = (1 - len(cleaned) / max(len(raw_scriptbook), 1)) * 100
    #         print(f"  [台本分割V2] 预清洗: {len(raw_scriptbook)} → {len(cleaned)} 字符 ({red_pct:.0f}% 减少)")
    #
    #     if track_samples:
    #         sample_parts = []
    #         for name in track_names:
    #             sample_text = track_samples.get(name, '')
    #             if sample_text:
    #                 sample_parts.append(f'【{name}】\n{sample_text}')
    #             else:
    #                 sample_parts.append(f'【{name}】\n（无样本）')
    #         track_samples_text = '\n\n'.join(sample_parts)
    #     else:
    #         track_samples_text = '（无 ASR 样本，仅根据音轨名匹配）'
    #
    #     track_names_json = _json.dumps(track_names, ensure_ascii=False)
    #     user_prompt = _SPLIT_USER_TEMPLATE_V2.format(
    #         track_names_json=track_names_json,
    #         track_samples_text=track_samples_text,
    #         raw_scriptbook=cleaned,
    #     )
    #
    #     if self.verbose:
    #         print(f"  [台本分割V2] {len(track_names)} 个音轨, prompt {len(user_prompt)} 字符")
    #
    #     self._ensure_client()
    #
    #     last_error = None
    #     for attempt in range(max_retries):
    #         try:
    #             response = self._client.chat.completions.create(
    #                 model=self.SPLIT_MODEL,
    #                 messages=[
    #                     {'role': 'system', 'content': SPLIT_SYSTEM_PROMPT},
    #                     {'role': 'user', 'content': user_prompt},
    #                 ],
    #                 temperature=0.1,
    #                 max_tokens=max(self.SPLIT_MAX_TOKENS, 65536),
    #             )
    #             content = response.choices[0].message.content or ''
    #             result = self._parse_response(content, track_names)
    #             if result:
    #                 if self.verbose:
    #                     total_lines = sum(len(v) for v in result.values())
    #                     print(f"  [台本分割V2] 成功: {len(result)}/{len(track_names)} 个音轨, "
    #                           f"共 {total_lines} 行台词")
    #                 return result
    #         except Exception as e:
    #             last_error = e
    #             if self.verbose:
    #                 print(f"  [台本分割V2] 尝试 {attempt+1}/{max_retries} 失败: {e}")
    #             if attempt < max_retries - 1:
    #                 _time_mod.sleep(2 ** attempt)
    #
    #     if self.verbose:
    #         print(f"  [台本分割V2] 全部尝试失败: {last_error}")
    #     return {}

    def clean_pre_split_tracks(
        self,
        track_files: dict[str, str],
        *,
        max_retries: int = 2,
    ) -> dict[str, list[str]]:
        """
        预分割台本清洗：每个文件已对应一个音轨，只需清洗。

        参数:
            track_files: {track_name: file_content} 映射

        返回:
            {track_name: [clean_lines]}
        """
        if not track_files:
            return {}

        # 不做事先本地清洗（用户要求跳过，避免误伤；清洗全部交给 Flash）
        active_files = {k: v for k, v in track_files.items() if v.strip()}
        if not active_files:
            return {name: [] for name in track_files}

        # 将所有文件内容拼接（每个文件标注所属音轨）
        combined = "\n\n".join(
            f"=== Track {name} ===\n{content}"
            for name, content in active_files.items()
        )

        track_files_json = _json.dumps(
            {k: f"{len(v)} 字符" for k, v in active_files.items()},
            ensure_ascii=False,
        )

        user_prompt = _PRE_SPLIT_CLEAN_TEMPLATE.format(
            track_files_json=track_files_json,
        ) + f"\n\n【完整台本内容】\n{combined}"

        if self.verbose:
            print(f"  [预分割清洗] {len(active_files)} 个文件, prompt {len(user_prompt)} 字符")

        self._ensure_client()

        last_error = None
        for attempt in range(max_retries):
            try:
                response = self._client.chat.completions.create(
                    model=self.SPLIT_MODEL,
                    messages=[
                        {'role': 'system', 'content': SPLIT_SYSTEM_PROMPT},
                        {'role': 'user', 'content': user_prompt},
                    ],
                    temperature=0.1,
                    max_tokens=self.SPLIT_MAX_TOKENS,
                )
                content = response.choices[0].message.content or ''
                result = self._parse_response(content, list(active_files.keys()))
                if result:
                    # 补全缺失的音轨
                    for name in track_files:
                        if name not in result:
                            result[name] = []
                    if self.verbose:
                        total_lines = sum(len(v) for v in result.values())
                        print(f"  [预分割清洗] 成功: {len(result)} 个音轨, 共 {total_lines} 行台词")
                    return result
            except Exception as e:
                last_error = e
                if self.verbose:
                    print(f"  [预分割清洗] 尝试 {attempt+1}/{max_retries} 失败: {e}")
                if attempt < max_retries - 1:
                    _time_mod.sleep(2 ** attempt)

        if self.verbose:
            print(f"  [预分割清洗] 全部尝试失败: {last_error}")
        return {}

    @staticmethod
    def _number_scriptbook_lines(text: str) -> tuple[str, list[str]]:
        """给台本每行加上行号前缀，供 LLM 引用

        返回:
            numbered_text: "00001|行内容\\n00002|行内容..."
            original_lines: 原始行列表（用于后续按行号切片）
        """
        original_lines = text.split('\n')
        numbered_lines = [f"{i+1:05d}|{line}" for i, line in enumerate(original_lines)]
        return '\n'.join(numbered_lines), original_lines

    @staticmethod
    def _extract_lines_by_range(
        original_lines: list[str],
        start: int,
        end: int,
    ) -> list[str]:
        """从原始行列表中按 1-indexed 闭区间提取并清洗行

        参数:
            original_lines: 未编号的原始行列表
            start: 起始行号（1-indexed，包含）
            end: 结束行号（1-indexed，包含）

        返回:
            清洗后的行列表
        """
        if start < 1 or end < start or start > len(original_lines):
            return []
        end = min(end, len(original_lines))
        extracted = original_lines[start - 1 : end]
        # 对整个片段做保守预清洗
        cleaned = _conservative_pre_clean('\n'.join(extracted))
        lines = cleaned.split('\n')
        return [
            line for line in lines
            if line.strip() and not ScriptbookSplitter._is_noise_line(line)
        ]

    def _parse_response(
        self,
        content: str,
        track_names: list[str],
        original_lines: list[str] = None,
    ) -> dict[str, list[str]] | None:
        """解析 LLM 返回的 JSON，校验并返回结果

        支持两种输出格式：
        - V3 行号范围: {"tracks": {"name": [start, end]}}
        - V2/V1 全文本: {"tracks": {"name": ["line1", "line2"]}}
        """
        # 提取 JSON
        json_text = content.strip()
        if '```json' in json_text:
            json_text = json_text.split('```json')[1].split('```')[0].strip()
        elif '```' in json_text:
            json_text = json_text.split('```')[1].split('```')[0].strip()

        # 查找最外层 { }
        brace_start = json_text.find('{')
        brace_end = json_text.rfind('}')
        if brace_start == -1 or brace_end == -1:
            return None
        json_text = json_text[brace_start:brace_end + 1]

        try:
            data = _json.loads(json_text)
        except _json.JSONDecodeError:
            return None

        if 'tracks' not in data or not isinstance(data['tracks'], dict):
            return None

        raw_tracks: dict = data['tracks']
        result: dict[str, list[str]] = {}

        # 建立 track_names 的标准化索引（用于模糊匹配）
        name_index = {self._normalize(n): n for n in track_names}

        for key, value in raw_tracks.items():
            if not isinstance(value, list):
                continue

            # ── 检测格式：V3 行号范围 vs V2/V1 全文本 ──
            is_range_format = (
                len(value) == 2
                and all(isinstance(v, (int, float)) for v in value)
            )

            # 标准化匹配
            norm_key = self._normalize(key)
            matched_name = name_index.get(norm_key)
            if matched_name is None:
                # 模糊匹配：尝试在 track_names 中查找包含关系
                for orig_name in track_names:
                    if self._is_similar(norm_key, self._normalize(orig_name)):
                        matched_name = orig_name
                        break
            if matched_name is None:
                if self.verbose:
                    print(f"  [台本分割] 跳过未匹配的音轨: {key}")
                continue

            if is_range_format and original_lines is not None:
                # V3 格式: [start, end] → 本地提取+清洗
                start, end = int(value[0]), int(value[1])
                clean_lines = self._extract_lines_by_range(original_lines, start, end)
            else:
                # V2/V1 格式: list[str] → 直接过滤
                clean_lines = [
                    line for line in value
                    if isinstance(line, str) and line.strip()
                    and not self._is_noise_line(line)
                ]
            result[matched_name] = clean_lines

        # 确保所有 track_names 都有条目（缺失的给空数组）
        for name in track_names:
            if name not in result:
                result[name] = []

        return result

    @staticmethod
    def _normalize(name: str) -> str:
        """标准化音轨名：去空格、去特殊符号、小写，用于匹配"""
        import re
        # 只保留中日文字符、英文、数字
        cleaned = re.sub(r'[\s_\-・·／／《》「」【】（）\(\)\[\]{}「」]', '', name)
        return cleaned.lower()

    @staticmethod
    def _is_similar(a: str, b: str) -> bool:
        """粗略判断两个标准化后的名称是否相似"""
        if not a or not b:
            return False
        # 包含关系
        if a in b or b in a:
            return True
        # 共同子串长度
        common = sum(1 for ca, cb in zip(a, b) if ca == cb)
        min_len = min(len(a), len(b))
        if min_len == 0:
            return False
        return common / min_len > 0.5

    @staticmethod
    def _is_noise_line(line: str) -> bool:
        """判断是否为噪声行（纯拟声/纯符号/注释）"""
        import re
        s = line.strip()
        if not s:
            return True
        # ※ 开头的注释
        if s.startswith('※'):
            return True
        # 纯拟声词行（只含假名 + ♥ + …… + っ），阈值降到 8 避免误伤短对话
        if re.match(r'^[ぁ-んァ-ン♥……っ！!～~。、\s]+$', s) and len(s) < 8:
            return True
        return False


# ==================== 正则兜底分割 ====================

def split_scriptbook_regex(
    raw_text: str,
    track_names: list[str],
) -> dict[str, list[str]]:
    """
    正则分割兜底：按「トラック\d」「Track\d」等标记切分。
    用于 LLM 分割失败时的回退。
    """
    import re

    # 按音轨标记切分（支持全角数字１２３和半角数字123）
    _FULLWIDTH = '０１２３４５６７８９'
    _HALFWIDTH = '0123456789'
    _DIGIT_CLASS = f'[0-9０-９]'
    track_pattern = re.compile(
        rf'(?:《|【|■|□)?トラック\s*({_DIGIT_CLASS}+)|'
        rf'(?:《|【|■|□)?Track\s*(\d+)',
        re.IGNORECASE,
    )

    # 找到所有分割点
    _FW_TO_HW = str.maketrans('０１２３４５６７８９', '0123456789')
    splits: list[tuple[int, int]] = []  # [(track_num, pos), ...]
    for m in track_pattern.finditer(raw_text):
        raw_num = m.group(1) or m.group(2)
        track_num = int(raw_num.translate(_FW_TO_HW))
        splits.append((track_num, m.start()))

    if not splits:
        # 无法分割，将所有内容归入第一个音轨
        if track_names:
            lines = [l.strip() for l in raw_text.split('\n') if l.strip() and not l.startswith('※')]
            return {track_names[0]: lines}
        return {}

    splits.sort(key=lambda x: x[0])  # 按音轨号排序

    # 提取每段内容
    result: dict[str, list[str]] = {}
    for i, (track_num, pos) in enumerate(splits):
        next_pos = splits[i + 1][1] if i + 1 < len(splits) else len(raw_text)
        section = raw_text[pos:next_pos]

        # 清理
        lines = [l.strip() for l in section.split('\n') if l.strip()]
        lines = [l for l in lines if not l.startswith('※')]
        lines = [l for l in lines if not re.match(r'^[《【].*[》】]$', l)]  # 去纯标题行

        # 匹配 track_name
        matched_name = None
        for name in track_names:
            norm_name = ScriptbookSplitter._normalize(name)
            if str(track_num) in norm_name or f'tr{track_num:02d}' in norm_name.lower():
                matched_name = name
                break
        if matched_name is None and track_num - 1 < len(track_names):
            # 按索引回退
            matched_name = track_names[track_num - 1]

        if matched_name:
            result[matched_name] = lines

    # 确保所有 track_names 都有条目
    for name in track_names:
        if name not in result:
            result[name] = []

    return result
