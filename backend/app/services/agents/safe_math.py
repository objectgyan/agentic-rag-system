"""Arithmetic without ``eval``: parse to an AST and interpret only a whitelist of node types.

The standard pattern for any tool that turns model output into computation. Anything outside
the whitelist (attribute access, calls to non-math names, comprehensions, lambdas, ...) is
rejected before execution, and exponent/size limits stop CPU/memory blow-ups like ``9**9**9``.
"""

from __future__ import annotations

import ast
import math
import operator
from typing import Any, Callable, Dict, Union

Number = Union[int, float]


class SafeMathError(ValueError):
    pass


_BIN_OPS: Dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: Dict[type, Callable[[Any], Any]] = {ast.UAdd: operator.pos, ast.USub: operator.neg}

_FUNCS: Dict[str, Callable[..., Number]] = {
    name: getattr(math, name)
    for name in (
        "sqrt", "log", "log10", "log2", "exp", "sin", "cos", "tan", "asin", "acos", "atan",
        "floor", "ceil", "fabs", "factorial", "radians", "degrees", "hypot",
    )
}
_FUNCS.update({"abs": abs, "round": round, "min": min, "max": max})
_CONSTS: Dict[str, float] = {"pi": math.pi, "e": math.e, "tau": math.tau}

_MAX_EXPONENT = 1000
_MAX_FACTORIAL = 1000
_MAX_LEN = 500
_MAX_RESULT_BITS = 100_000


def evaluate_expression(expression: str) -> Number:
    expression = expression.strip()
    if not expression:
        raise SafeMathError("empty expression")
    if len(expression) > _MAX_LEN:
        raise SafeMathError("expression too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise SafeMathError(f"invalid syntax: {e.msg}") from None
    try:
        return _eval(tree.body)
    except ZeroDivisionError:
        raise SafeMathError("division by zero") from None
    except OverflowError:
        raise SafeMathError("result too large") from None


def _eval(node: ast.AST) -> Number:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > _MAX_EXPONENT:
                raise SafeMathError("exponent too large")
            # (10**999)**999 passes the exponent check but builds a ~3M-digit integer.
            if isinstance(left, int) and isinstance(right, int) and left.bit_length() * abs(right) > _MAX_RESULT_BITS:
                raise SafeMathError("result too large")
        return _BIN_OPS[type(node.op)](left, right)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
        and not node.keywords
    ):
        args = [_eval(a) for a in node.args]
        if node.func.id == "factorial" and args and args[0] > _MAX_FACTORIAL:
            raise SafeMathError("factorial argument too large")
        return _FUNCS[node.func.id](*args)
    raise SafeMathError(f"unsupported expression element: {type(node).__name__}")
