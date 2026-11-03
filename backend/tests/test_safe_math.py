"""The agent calculator must compute arithmetic and refuse everything else (no eval)."""

import math

import pytest

from app.services.agents.safe_math import SafeMathError, evaluate_expression


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("2 + 2", 4),
        ("(17.5 * 3) / 2", 26.25),
        ("-3 ** 2", -9),
        ("7 // 2 + 7 % 2", 4),
        ("sqrt(16) + log10(1000)", 7.0),
        ("round(pi, 2)", 3.14),
        ("max(1, 5, 3)", 5),
    ],
)
def test_arithmetic(expr, expected):
    assert evaluate_expression(expr) == pytest.approx(expected)


@pytest.mark.parametrize(
    "attack",
    [
        "().__class__.__bases__[0].__subclasses__()",  # classic eval-sandbox escape
        "__import__('os').system('id')",
        "open('/etc/passwd').read()",
        "[x for x in range(10)]",
        "lambda: 1",
        "math.sqrt(4)",  # attribute access
        "sqrt(x=4)",
        "'a' * 10",
        "True + 1",
    ],
)
def test_rejects_non_arithmetic(attack):
    with pytest.raises(SafeMathError):
        evaluate_expression(attack)


@pytest.mark.parametrize("bomb", ["9 ** 9 ** 9", "(10 ** 999) ** 999", "factorial(100000)", "1" * 600])
def test_rejects_resource_bombs(bomb):
    with pytest.raises(SafeMathError):
        evaluate_expression(bomb)


def test_errors_are_reported_not_raised_as_other_types():
    with pytest.raises(SafeMathError, match="division by zero"):
        evaluate_expression("1 / 0")
    assert evaluate_expression("e") == pytest.approx(math.e)
