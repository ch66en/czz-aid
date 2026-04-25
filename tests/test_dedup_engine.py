"""验证去重引擎的基础行为。"""

from agent.core.dedup_engine import DedupEngine


def test_dedup_engine_is_stable() -> None:
    """相同输入应生成完全一致的指纹。"""
    engine = DedupEngine()
    assert engine.fingerprint("abc") == engine.fingerprint("abc")
