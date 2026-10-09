"""Tools for the sample tools-agent: a safe calculator and a static order lookup."""

from __future__ import annotations

import ast
import json
import operator

from strands import tool

ORDERS = {
    "A100": {"status": "shipped", "eta_days": 2, "total_dkk": 349.0},
    "A101": {"status": "processing", "eta_days": 5, "total_dkk": 1299.5},
    "A102": {"status": "delivered", "eta_days": 0, "total_dkk": 89.0},
}

_BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY = {ast.USub: operator.neg, ast.UAdd: operator.pos}


def _eval(node: ast.AST) -> float | int:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent too large")
        return _BINARY[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    raise ValueError("unsupported expression")


def calculate(expression: str) -> str:
    try:
        value = _eval(ast.parse(expression, mode="eval"))
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError) as exc:
        return f"error: {exc}"
    if isinstance(value, float):
        return f"{round(value, 10):g}"
    return str(value)


def order_status(order_id: str) -> str:
    key = order_id.strip().upper()
    info = ORDERS.get(key)
    return json.dumps({"order_id": key, **info}) if info else f"no order with id {order_id.strip()}"


@tool
def calculator(expression: str) -> str:
    """Evaluate an arithmetic expression with + - * / % ** and parentheses.

    Args:
        expression: The expression, for example "0.17 * 2340".
    """
    return calculate(expression)


@tool
def lookup_order(order_id: str) -> str:
    """Look up an order's status, ETA in days and total in DKK.

    Args:
        order_id: The order id, for example "A101".
    """
    return order_status(order_id)
