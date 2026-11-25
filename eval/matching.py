"""Execution-match comparison between predicted and gold query results.

Two queries match when they return the same multiset of rows after
normalisation:
- numbers are compared after rounding to 2 decimals (ints, floats and
  Decimals compare equal when numerically equal),
- row order is ignored (top-N questions are still checked, because LIMIT
  decides which rows are present),
- column order is ignored, but the number of columns must match.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from decimal import Decimal
from typing import Any


def _norm_value(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        f = round(float(v), 2)
        return 0.0 if f == 0 else f  # fold -0.0
    if isinstance(v, str):
        return v.strip()
    return v


def _norm_row(row: Sequence[Any]) -> tuple:
    values = [_norm_value(v) for v in row]
    # Sort within the row so column order does not matter.
    return tuple(sorted(values, key=lambda x: (type(x).__name__, repr(x))))


def results_match(predicted: list[list[Any]], gold: list[list[Any]]) -> bool:
    if len(predicted) != len(gold):
        return False
    if predicted and gold and len(predicted[0]) != len(gold[0]):
        return False
    return Counter(map(_norm_row, predicted)) == Counter(map(_norm_row, gold))
