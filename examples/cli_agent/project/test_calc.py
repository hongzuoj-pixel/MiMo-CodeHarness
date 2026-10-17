from calc import add


def test_add():
    assert add(3, 2) == 5


def test_zero():
    assert add(0, 0) == 0
