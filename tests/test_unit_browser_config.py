"""
Static config validation for the C3 browser-auth viewport/resolution settings.

Parses the three configuration sources (cookie_extractor.py, entrypoint.sh,
docker-compose.yml) and asserts they are correct and mutually consistent.
No Docker, Playwright, or network required.
"""
import ast
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COOKIE_EXTRACTOR = ROOT / "browser_auth" / "cookie_extractor.py"
ENTRYPOINT_SH = ROOT / "browser_auth" / "entrypoint.sh"
DOCKER_COMPOSE = ROOT / "docker-compose.yml"

MIN_HEIGHT = 900


# ── helpers ───────────────────────────────────────────────────────────────────

def _parse_cookie_extractor():
    """Extract viewport dict, Chrome args, and ignore_default_args from launch_persistent_context."""
    source = COOKIE_EXTRACTOR.read_text()
    tree = ast.parse(source)

    viewport = None
    args_list = None
    ignore_default_args = None

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "launch_persistent_context":
            for kw in node.keywords:
                if kw.arg == "viewport":
                    viewport = ast.literal_eval(kw.value)
                elif kw.arg == "args":
                    args_list = ast.literal_eval(kw.value)
                elif kw.arg == "ignore_default_args":
                    ignore_default_args = ast.literal_eval(kw.value)
            break

    assert viewport is not None, "Could not find viewport= in launch_persistent_context"
    assert args_list is not None, "Could not find args= in launch_persistent_context"
    return viewport, args_list, ignore_default_args


def _parse_entrypoint_resolution() -> str:
    """Return the default SCREEN_RES value from entrypoint.sh (e.g. '1280x900x24')."""
    text = ENTRYPOINT_SH.read_text()
    m = re.search(r'VNC_RESOLUTION:-([^}]+)\}', text)
    assert m, "Could not find VNC_RESOLUTION default in entrypoint.sh"
    return m.group(1).strip()


def _parse_compose_vnc_resolution() -> str:
    """Return VNC_RESOLUTION from the browser-auth service in docker-compose.yml."""
    data = yaml.safe_load(DOCKER_COMPOSE.read_text())
    env_list = data["services"]["browser-auth"]["environment"]
    for entry in env_list:
        if entry.startswith("VNC_RESOLUTION="):
            return entry.split("=", 1)[1]
    raise AssertionError("VNC_RESOLUTION not found in docker-compose.yml browser-auth env")


def _split_resolution(res: str):
    """'1280x900x24' -> (1280, 900)  (ignoring depth)."""
    parts = res.split("x")
    return int(parts[0]), int(parts[1])


# ── tests ─────────────────────────────────────────────────────────────────────

def test_cookie_extractor_viewport_and_args():
    """Playwright viewport height >= MIN_HEIGHT, automation banner suppressed, --window-size matches."""
    viewport, args, ignore_defaults = _parse_cookie_extractor()

    assert viewport["height"] >= MIN_HEIGHT, (
        f"viewport height {viewport['height']} is below minimum {MIN_HEIGHT}"
    )

    assert ignore_defaults is not None, "ignore_default_args must be set on launch_persistent_context"
    assert "--enable-automation" in ignore_defaults, (
        "ignore_default_args must include '--enable-automation' to suppress the automation banner"
    )
    assert "--disable-infobars" in ignore_defaults, (
        "ignore_default_args must include '--disable-infobars' to suppress the unsupported-flag warning"
    )

    assert "--test-type" in args, (
        "--test-type must be in Chrome args to suppress 'unsupported flag' warning banners"
    )

    window_size_args = [a for a in args if a.startswith("--window-size=")]
    assert window_size_args, "--window-size not found in Chrome args"
    w, h = window_size_args[0].split("=")[1].split(",")
    assert int(h) >= MIN_HEIGHT, f"--window-size height {h} is below minimum {MIN_HEIGHT}"
    assert int(w) == viewport["width"], "window-size width != viewport width"
    assert int(h) == viewport["height"], "window-size height != viewport height"


def test_entrypoint_resolution():
    """Default SCREEN_RES in entrypoint.sh has height >= MIN_HEIGHT."""
    res = _parse_entrypoint_resolution()
    _, height = _split_resolution(res)
    assert height >= MIN_HEIGHT, (
        f"entrypoint.sh default SCREEN_RES height {height} is below minimum {MIN_HEIGHT}"
    )


def test_entrypoint_novnc_scaling():
    """entrypoint.sh must patch vnc_auto.html to enable auto-scaling by default."""
    text = ENTRYPOINT_SH.read_text()
    assert "getConfigVar('scale', true)" in text, (
        "entrypoint.sh must sed vnc_auto.html to set scaleViewport default to true"
    )


def test_compose_vnc_resolution():
    """VNC_RESOLUTION in docker-compose.yml has height >= MIN_HEIGHT."""
    res = _parse_compose_vnc_resolution()
    _, height = _split_resolution(res)
    assert height >= MIN_HEIGHT, (
        f"docker-compose.yml VNC_RESOLUTION height {height} is below minimum {MIN_HEIGHT}"
    )


def test_cross_source_consistency():
    """All three configuration sources must agree on width and height."""
    viewport, args, _ = _parse_cookie_extractor()
    py_w, py_h = viewport["width"], viewport["height"]

    sh_res = _parse_entrypoint_resolution()
    sh_w, sh_h = _split_resolution(sh_res)

    compose_res = _parse_compose_vnc_resolution()
    comp_w, comp_h = _split_resolution(compose_res)

    assert py_w == sh_w == comp_w, (
        f"Width mismatch: cookie_extractor={py_w}, entrypoint.sh={sh_w}, docker-compose={comp_w}"
    )
    assert py_h == sh_h == comp_h, (
        f"Height mismatch: cookie_extractor={py_h}, entrypoint.sh={sh_h}, docker-compose={comp_h}"
    )
