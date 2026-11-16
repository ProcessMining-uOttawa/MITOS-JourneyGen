"""The logo files are what assets/make_logo.py writes, and the app uses them."""
import importlib.util
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")


def make_logo():
    spec = importlib.util.spec_from_file_location("make_logo",
                                                  os.path.join(ASSETS, "make_logo.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read(name):
    with open(os.path.join(ASSETS, name), encoding="utf-8") as f:
        return f.read()


def test_the_svg_files_match_their_script():
    m = make_logo()
    assert read("icon.svg") == m.icon_svg()
    assert read("logo_light.svg") == m.logo_svg("light")
    assert read("logo_dark.svg") == m.logo_svg("dark")
    # the wordmark must read on the background it is shown on
    assert m.WORDMARK["light"] in read("logo_light.svg")
    assert m.WORDMARK["dark"] in read("logo_dark.svg")


def test_the_browser_icon_is_a_square_png():
    with open(os.path.join(ASSETS, "icon.png"), "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    assert width == height == 256


def test_the_app_shows_the_logo_and_offers_both_themes():
    with open(os.path.join(ROOT, "app.py"), encoding="utf-8") as f:
        app = f.read()
    assert "st.logo(" in app and 'page_icon=os.path.join(HERE, "assets", "icon.png")' in app
    with open(os.path.join(ROOT, ".streamlit", "config.toml"), encoding="utf-8") as f:
        config = f.read()
    assert "[theme.light]" in config and "[theme.dark]" in config
