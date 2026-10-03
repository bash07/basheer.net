"""Numpy-native relative extrema detection.

Provides a 100% compatible drop-in replacement for `scipy.signal.argrelextrema`
without requiring the heavy SciPy package (~50 MB wheel) on 1 GB RAM servers.
"""
import numpy as np


def argrelextrema(data: np.ndarray, comparator, axis: int = 0, order: int = 1, mode: str = "clip"):
    """Calculate the relative extrema of `data`.

    Drop-in replacement for scipy.signal.argrelextrema.
    """
    try:
        from scipy.signal import argrelextrema as _scipy_extrema
        return _scipy_extrema(data, comparator, axis=axis, order=order, mode=mode)
    except ImportError:
        pass

    datalen = data.shape[axis]
    locs = np.arange(0, datalen)

    results = np.ones(data.shape, dtype=bool)
    main = data.take(locs, axis=axis, mode=mode)
    for shift in range(1, order + 1):
        plus = data.take(locs + shift, axis=axis, mode=mode)
        minus = data.take(locs - shift, axis=axis, mode=mode)
        results &= comparator(main, plus)
        results &= comparator(main, minus)
        if not results.any():
            return (np.array([], dtype=int),)
    return np.nonzero(results)
