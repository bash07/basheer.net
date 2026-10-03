import numpy as np
import pytest
from unittest.mock import patch
import builtins

from modules.finance._shared.extrema import argrelextrema


def test_extrema_minima_and_maxima():
    # Simple peak and valley
    # [1, 5, 2, 8, 3] -> peak at 1 (val 5), trough at 2 (val 2), peak at 3 (val 8)
    arr = np.array([1, 5, 2, 8, 3, 7, 1])
    maxima = argrelextrema(arr, np.greater, order=1)[0]
    minima = argrelextrema(arr, np.less, order=1)[0]

    assert list(maxima) == [1, 3, 5]
    assert list(minima) == [2, 4]


def test_extrema_fallback_when_scipy_missing():
    # Force ImportError on scipy
    orig_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name.startswith("scipy"):
            raise ImportError("No module named 'scipy'")
        return orig_import(name, *args, **kwargs)

    arr = np.array([10, 20, 15, 30, 25, 40, 20])
    with patch("builtins.__import__", side_effect=mock_import):
        maxima = argrelextrema(arr, np.greater, order=1)[0]
        minima = argrelextrema(arr, np.less, order=1)[0]

    assert list(maxima) == [1, 3, 5]
    assert list(minima) == [2, 4]
