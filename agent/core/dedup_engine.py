from __future__ import annotations

"""提供文本去重相关能力。"""

import hashlib


class DedupEngine:
    """基于稳定哈希算法生成文本指纹。"""

    def fingerprint(self, text: str) -> str:
        """根据输入文本生成去重指纹。"""
        normalized_text = text.strip()
        return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
