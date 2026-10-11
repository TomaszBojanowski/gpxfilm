"""Small helpers shared by all modules."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import numpy as np

from .task import log  # noqa: F401 – the modules of the engine take it from here


def clamp(v, a=0.0, b=1.0):
    return a if v < a else b if v > b else v


def km1(m: float) -> float:
    """Meters as kilometers with one decimal, halves up: 12450 m is 12.5 km (the float 12.45 lies just below 12.45)."""
    return float(Decimal(repr(m / 1000)).quantize(Decimal("0.1"), ROUND_HALF_UP))


def to_u8(a: np.ndarray) -> np.ndarray:
    return (np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8)
