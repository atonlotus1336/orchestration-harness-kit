"""Portable review facts and budgets; execution packets remain host-owned."""
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ReviewBudget:
    max_files: int
    max_context_bytes: int
    max_model_calls: int
    max_estimated_input_tokens: int
    max_tool_calls: int
    max_elapsed_seconds: int
    max_lens_excerpts: int = 0
    max_output_tokens: int = 0
    max_total_tokens: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, int]) -> "ReviewBudget":
        kwargs = {name: value.get(name, field.default) for name, field in cls.__dataclass_fields__.items()}
        if any(type(item) is not int or item < 0 for item in kwargs.values()):
            raise ValueError("review budgets must be non-negative integers")
        budget = cls(**kwargs)
        if any(getattr(budget, key) < 1 for key in (
            "max_files", "max_context_bytes", "max_tool_calls", "max_elapsed_seconds"
        )):
            raise ValueError("file, context, tool, and elapsed budgets must be positive")
        return budget


@dataclass(frozen=True)
class ReviewFact:
    key: str
    required_level: str
    provenance: str = ""
    load_bearing: bool = False
    known: bool = True
