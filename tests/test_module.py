import pkgname


def test_main_returns_zero(capsys):
    assert pkgname.main(["world"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Hello, world!\n"
