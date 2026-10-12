from decimal import Decimal
from types import SimpleNamespace

from validation.gates_bench import costs_page_check, playground_check, reprice_check


def S(model, cost):
    return SimpleNamespace(model=model, total_cost=cost)


def test_reprice_check_ok():
    assert reprice_check({"a": Decimal("1")}, [S("a", Decimal("2"))]).ok


def test_reprice_check_fails_not_raises():
    assert not reprice_check({"a": Decimal("1")}, []).ok
    assert not reprice_check({"a": Decimal("1")}, [S("a", None)]).ok
    assert not reprice_check({"a": None}, [S("a", Decimal("2"))]).ok
    assert not reprice_check({}, [S("a", Decimal("2"))]).ok
    assert not reprice_check({"a": Decimal("1")}, [S("a", Decimal("3"))]).ok


def AT(exception=None, markdown=(), metrics=None, dataframes=(), warnings=()):
    return SimpleNamespace(
        exception=exception,
        markdown=[SimpleNamespace(value=v) for v in markdown],
        metric=[SimpleNamespace(label=k, value=v) for k, v in (metrics or {}).items()],
        dataframe=[SimpleNamespace(value=d) for d in dataframes],
        warning=[SimpleNamespace(value=w) for w in warnings],
    )


def test_playground_check():
    assert playground_check(AT(markdown=["pong"], metrics={"cost": "$0.000070", "tokens in": "39"})).ok
    assert not playground_check(AT(markdown=["pong"], metrics={"cost": "-", "tokens in": "39"})).ok
    assert not playground_check(AT(markdown=[" "], metrics={"cost": "$1", "tokens in": "39"})).ok
    assert not playground_check(AT(markdown=["x"], metrics={"cost": "$1", "tokens in": "0"})).ok


def test_costs_page_check():
    assert costs_page_check(AT(dataframes=[[1]])).ok
    assert costs_page_check(AT(warnings=["Cost allocation tags not active"])).ok
    assert not costs_page_check(AT()).ok
    assert not costs_page_check(AT(exception="boom", dataframes=[[1]])).ok
