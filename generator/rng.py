"""Stage 1: the single seeded random number generator.

One ``numpy.random.Generator`` (PCG64) is created from ``--seed`` and passed through
every stage in a fixed order. Nothing else (wall clock, uuid4, hash order) is a
source of randomness.
"""

from __future__ import annotations

import numpy as np


def make_rng(seed: int) -> np.random.Generator:
    """Return the run's only random generator, seeded from ``seed``."""
    if isinstance(seed, bool) or not isinstance(seed, (int, np.integer)) or seed < 0:
        raise ValueError(f"seed must be a non-negative integer, got {seed!r}")
    return np.random.Generator(np.random.PCG64(int(seed)))


def pick(rng: np.random.Generator, options: list, size: int | None = None, p: list[float] | None = None):
    """Choose from a Python list deterministically (returns list items, not numpy scalars)."""
    idx = rng.choice(len(options), size=size, p=p)
    if size is None:
        return options[int(idx)]
    return [options[int(i)] for i in idx]
