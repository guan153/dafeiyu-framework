"""BEAM-AI 内核包（beams_core）。

Body-Inspired Emotion-Action-Mind Architecture for AI
身体给情绪 grounding，情绪给动机权重，动机给认知优先级，
元认知给情绪解释框架，双向总线让高层调参数而不是下命令。

公开入口：
    from beams_core import BeamsKernel, express, schema
"""
from .kernel import BeamsKernel  # noqa: F401
from . import (affect2, body, bus, drive, express, intero, kernel, meta,  # noqa: F401
               safety, schema)

__version__ = "0.1.0"