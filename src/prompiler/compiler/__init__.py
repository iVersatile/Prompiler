"""prompiler.compiler — synthesise Pydantic models / prompts / schemas from specs."""

from __future__ import annotations

from prompiler.compiler.model import synthesize_model

__all__ = ["synthesize_model"]
