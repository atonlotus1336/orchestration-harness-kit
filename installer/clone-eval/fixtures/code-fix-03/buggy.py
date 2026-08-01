"""Constraint growth validator.

Derived from evolution.core.constraints.ConstraintValidator._check_growth.
Self-contained so the eval runner needs no repo install.
"""

from dataclasses import dataclass


@dataclass
class ConstraintResult:
    passed: bool
    constraint_name: str
    message: str


class Config:
    max_prompt_growth = 0.2


class ConstraintValidator:
    def __init__(self, config=None):
        self.config = config or Config()

    def check_growth(self, text: str, baseline: str, artifact_type: str) -> ConstraintResult:
        growth = (len(text) - len(baseline)) / len(baseline)
        max_growth = self.config.max_prompt_growth

        if growth < max_growth:
            return ConstraintResult(
                passed=True,
                constraint_name="growth_limit",
                message=f"Growth OK: {growth:+.1%} (max {max_growth:+.1%})",
            )
        return ConstraintResult(
            passed=False,
            constraint_name="growth_limit",
            message=f"Growth exceeded: {growth:+.1%} (max {max_growth:+.1%})",
        )
