# -*- coding: utf-8 -*-
"""ASR ↔ 台本 多对多区间对齐（限定单轨台本，顺序保持，字符级序列对齐）

背景：ASR 行与台本行不是一一对应，可能是多对多（ASR 合并/拆分/即兴/漏识）。
因此不做"1行→1行"匹配，而是把整轨 ASR 与整轨台本各自拼成字符串做字符级对齐，
再把字符区间映射回行号 —— 每个 ASR 行得到一个【台本行区间 [lo..hi]】。

顺序保持：后一个 ASR 行的区间起点 >= 前一个的区间终点，绝不允许回溯，
避免"把某行锁到台本里另一个位置"。
"""
import re
import difflib

try:
    import pyopenjtalk  # 汉字→假名读音（未安装时回退到纯假名/汉字比较）
except ImportError:
    pyopenjtalk = None

_SYM_RE = re.compile(r'[♡♢♥☆＊※〜～…・…、。\s"“”‘’「」『』（）()!！?？·\-—_~^]')
_KATA_BASE = ''.join(chr(0x30A1 + i) for i in range(0x56))  # ァ..ヶ
_HIRA_BASE = ''.join(chr(0x3041 + i) for i in range(0x56))
_KATA2HIRA = str.maketrans(_KATA_BASE, _HIRA_BASE)


def normalize(s: str) -> str:
    """去符号 → 汉字→假名读音（pyopenjtalk）→ 统一平假名。"""
    s = _SYM_RE.sub('', s)
    if not s:
        return ''
    if pyopenjtalk:
        try:
            s = pyopenjtalk.g2p(s, kana=True)  # 输出片假名读音
        except Exception:
            pass
    return s.translate(_KATA2HIRA)


def bigram_dice(a: str, b: str) -> float:
    """字符 bigram Dice 相似度（0..1）。"""
    if not a or not b:
        return 0.0
    ab = set(a[i:i + 2] for i in range(len(a) - 1))
    bb = set(b[i:i + 2] for i in range(len(b) - 1))
    if not ab or not bb:
        return 0.0
    inter = len(ab & bb)
    return 2.0 * inter / (len(ab) + len(bb))


def _build_chunks(lines_norm, sep):
    """拼接归一化行 → (concat_str, pos_to_line[字符位] -> 行号)。"""
    parts, pos2line = [], []
    for i, t in enumerate(lines_norm):
        parts.append(t)
        pos2line.extend([i] * len(t))
        if i < len(lines_norm) - 1:
            parts.append(sep)
            pos2line.append(-1)  # 分隔符占位
    return ''.join(parts), pos2line


def span_align(A_norm, S_norm):
    """顺序保持的多对多区间对齐。

    参数: A_norm/S_norm 为归一化后的行列表。
    返回: (mapping, blocks, A_pos2line, S_pos2line, S_len)
        mapping: {asr_idx: (sb_lo, sb_hi)} 闭区间
        blocks: difflib 匹配块列表
    """
    sep = '␟'
    A_concat, A_pos2line = _build_chunks(A_norm, sep)
    S_concat, S_pos2line = _build_chunks(S_norm, sep)
    if not A_concat or not S_concat:
        return {}, [], A_pos2line, S_pos2line, len(S_concat)

    sm = difflib.SequenceMatcher(a=A_concat, b=S_concat, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size > 0]
    A_len, S_len = len(A_concat), len(S_concat)

    def f(a_pos):
        """ASR 字符位 -> SB 字符位（匹配块内一一对应；空隙线性插值）。"""
        for (a0, b0, sz) in blocks:
            if a0 <= a_pos < a0 + sz:
                return b0 + (a_pos - a0)
        prev = nxt = None
        for (a0, b0, sz) in blocks:
            if a0 + sz <= a_pos:
                prev = (a0 + sz, b0 + sz)
            if a0 > a_pos and nxt is None:
                nxt = (a0, b0)
        if prev and nxt:
            (ae, be), (as_, bs) = prev, nxt
            if as_ > ae:
                return be + (a_pos - ae) * (bs - be) / (as_ - ae)
        if prev and not nxt:
            (ae, be) = prev
            if A_len > ae:
                return be + (a_pos - ae) * (S_len - be) / (A_len - ae)
        if nxt and not prev:
            (as_, bs) = nxt
            if as_ > 0:
                return bs * (a_pos / as_)
        return 0.0

    def snap(pos, direction):
        pos = max(0, min(len(S_pos2line) - 1, int(pos)))
        if S_pos2line[pos] != -1:
            return S_pos2line[pos]
        while 0 < pos < len(S_pos2line) - 1 and S_pos2line[pos] == -1:
            pos += direction
        return S_pos2line[pos]

    mapping = {}
    for i in range(len(A_norm)):
        if A_pos2line.count(i) == 0:
            continue
        lo = A_pos2line.index(i)
        hi = max(p for p, l in enumerate(A_pos2line) if l == i)
        sb_lo = snap(f(lo), +1)
        sb_hi = snap(f(hi), -1)
        if sb_lo < 0:
            sb_lo = 0
        if sb_hi < 0:
            sb_hi = len(S_norm) - 1
        if sb_lo <= sb_hi:
            mapping[i] = (sb_lo, sb_hi)
    return mapping, blocks, A_pos2line, S_pos2line, S_len


def align_asr_scriptbook(asr_lines: list[str], sb_lines: list[str],
                         max_sb: int = 6) -> dict:
    """对外入口：把 ASR 行对齐到单轨台本，返回 {asr_idx: {sb, conf, span}}。

    - sb: 该行对应的台本原文（区间内多行用｜连接，最多 max_sb 行）
    - conf: 该 sb 与 ASR 行的相似度（0..1）；低置信表示该行 ASR 可能乱码/即兴，
            sb 由邻接锚点插值得来，仅作推断参考
    """
    A_norm = [normalize(t) for t in asr_lines]
    S_norm = [normalize(s) for s in sb_lines]
    mapping, _, _, _, _ = span_align(A_norm, S_norm)
    result = {}
    for i, (lo, hi) in mapping.items():
        span_text = '｜'.join(sb_lines[lo:hi + 1][:max_sb])
        conf = bigram_dice(A_norm[i], normalize(span_text))
        result[i] = {'sb': span_text, 'conf': round(conf, 3), 'span': (lo, hi)}
    return result
