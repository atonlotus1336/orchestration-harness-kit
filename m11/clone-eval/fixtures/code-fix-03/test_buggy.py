from buggy import ConstraintValidator


class CustomConfig:
    max_prompt_growth = 0.2


def validator():
    return ConstraintValidator(CustomConfig())


def test_acceptable_growth():
    baseline = "x" * 1000
    evolved = "x" * 1100
    result = validator().check_growth(evolved, baseline, "skill")
    assert result.passed
    assert result.constraint_name == "growth_limit"


def test_growth_at_exact_limit_passes():
    baseline = "x" * 1000
    evolved = "x" * 1200
    result = validator().check_growth(evolved, baseline, "skill")
    assert result.passed


def test_excessive_growth_fails():
    baseline = "x" * 1000
    evolved = "x" * 1300
    result = validator().check_growth(evolved, baseline, "skill")
    assert not result.passed
    assert "Growth exceeded" in result.message


def test_shrinkage_is_ok():
    baseline = "x" * 1000
    evolved = "x" * 800
    result = validator().check_growth(evolved, baseline, "skill")
    assert result.passed


def test_empty_baseline_does_not_crash():
    result = validator().check_growth("abc", "", "skill")
    assert not result.passed
    assert result.constraint_name == "growth_limit"
