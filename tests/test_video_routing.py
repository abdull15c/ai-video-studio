import pytest

from modules.video_generator import should_try_stock_first


@pytest.mark.parametrize(
    "pure_stock,confidence,expected",
    [
        (True, "low", True),
        (False, "high", True),
        (False, "medium", True),
        (False, "low", False),
        (False, None, True),
        (False, "", True),
    ],
)
def test_should_try_stock_first(pure_stock, confidence, expected):
    assert should_try_stock_first(pure_stock, confidence) is expected
