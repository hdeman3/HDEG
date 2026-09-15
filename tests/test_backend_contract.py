# -*- coding: utf-8 -*-
"""后端契约测试。

后端 kikoeru-express 的 `translate_worker.py` 直接 import HDEG 的 Python 符号
（不是走 CLI）。这些符号在 HDEG 内部可能「看起来没被调用」，但对后端是公开 API，
**绝不能被死代码清理误删**。本测试把它们钉死为契约。

若后端改动了 import，请同步更新此文件。
"""

from __future__ import annotations


def test_backend_worker_symbols_importable():
    # 后端 translate_worker.py 的 import（逐字对应）
    from engines.translate_engine import OpenAICompatEngine  # noqa: F401

    from pipeline.orchestrator import (
        PipelineContext,              # noqa: F401
        _run_transcription_if_needed,  # noqa: F401
        scan_subtitle_files,          # noqa: F401
        _load_scriptbook,             # noqa: F401
        _load_worldview,              # noqa: F401
        _analyze_work_terms,          # noqa: F401
    )

    from io_adapter.lrc_handler import (
        parse_subtitle_file,          # noqa: F401
        write_subtitle_file,          # noqa: F401
        detect_subtitle_language,     # noqa: F401
    )

    from io_adapter.config_loader import load_terms_from_config  # noqa: F401


def test_backend_worker_symbols_callable():
    from pipeline.orchestrator import _load_worldview, scan_subtitle_files
    from io_adapter.lrc_handler import parse_subtitle_file, write_subtitle_file

    assert callable(_load_worldview)
    assert callable(scan_subtitle_files)
    assert callable(parse_subtitle_file)
    assert callable(write_subtitle_file)
