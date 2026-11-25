from decimal import Decimal

from eval.matching import results_match


def test_numeric_types_and_rounding():
    assert results_match([[Decimal("12.50")]], [[12.5]])
    assert results_match([[12.504]], [[12.5]])
    assert results_match([[3]], [[3.0]])
    assert not results_match([[12.6]], [[12.5]])


def test_row_order_ignored():
    assert results_match([["a", 1], ["b", 2]], [["b", 2], ["a", 1]])


def test_column_order_ignored():
    assert results_match([[1, "Manhattan"]], [["Manhattan", 1]])


def test_column_count_must_match():
    assert not results_match([["Manhattan"]], [["Manhattan", 10]])


def test_row_count_must_match():
    assert not results_match([["a"]], [["a"], ["b"]])
    assert results_match([], [])


def test_duplicates_counted():
    assert not results_match([["a"], ["a"]], [["a"], ["b"]])
