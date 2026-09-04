from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime


def expanding_folds(
    start: datetime,
    end: datetime,
    *,
    initial_train: timedelta,
    test_window: timedelta,
    embargo: timedelta,
) -> list[WalkForwardFold]:
    if not start < end or initial_train <= timedelta(0) or test_window <= timedelta(0):
        raise ValueError("invalid walk-forward boundaries")
    folds: list[WalkForwardFold] = []
    train_end = start + initial_train
    while train_end + embargo + test_window <= end:
        test_start = train_end + embargo
        folds.append(WalkForwardFold(start, train_end, test_start, test_start + test_window))
        train_end += test_window
    return folds
