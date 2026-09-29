"""
tests/test_theme_contrast.py — both themes stay readable.

The light theme moved from white to a warm cream. A palette swap goes wrong in
exactly one way: the neutrals move and the text sitting on them quietly stops
being legible — quietly, because it still looks fine to whoever chose the
colours on the screen they chose them on.

So the tokens are read back out of base.css and every pair that actually occurs
in the UI is scored. This also caught something that predates the change:
`--brand` was #12a56a, which scores **3.17** against the white text on every
primary button. That has been failing AA since the first light theme; the cream
did not cause it, it exposed it.

WCAG 2.1: 4.5:1 for body text, 3:1 for large text and for text that carries no
meaning on its own.
"""

import pathlib
import re

import pytest

CSS = (pathlib.Path(__file__).resolve().parents[1] / "dashboard" / "static"
       / "css" / "base.css").read_text(encoding="utf-8")


def _raw(block: str) -> dict[str, str]:
    """Every colour token declared in one `:root` block of the real stylesheet."""
    body = CSS.split(block, 1)[1].split("}", 1)[0]
    found = dict(re.findall(r"--([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})", body))
    found.update(re.findall(r"--([a-z0-9-]+):\s*(rgba\([^)]*\))", body))
    return found


def _flatten(value: str, over: str) -> str:
    """
    A colour as the screen shows it.

    The dark theme states its tints as `rgba(…)` — a translucent wash over
    whatever is behind. Read as-is they are meaningless to compare, and read as
    the light theme's opaque equivalent they are wrong: this composites them
    over the ground they actually sit on, which is what the browser does.
    """
    if value.startswith("#"):
        return value

    r, g, b, a = (float(p) for p in re.findall(r"[\d.]+", value))
    base = over.lstrip("#")
    mixed = []
    for i, channel in enumerate((r, g, b)):
        behind = int(base[i * 2:i * 2 + 2], 16)
        mixed.append(round(channel * a + behind * (1 - a)))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def _resolve(raw: dict[str, str]) -> dict[str, str]:
    ground = raw.get("surface", "#ffffff")
    return {name: _flatten(value, ground) for name, value in raw.items()}


LIGHT_RAW, DARK_RAW = _raw(":root {"), _raw(':root[data-theme="dark"] {')
LIGHT = _resolve(LIGHT_RAW)
DARK = _resolve({**LIGHT_RAW, **DARK_RAW})


def _luminance(colour: str) -> float:
    colour = colour.lstrip("#")
    channels = []
    for value in (int(colour[i:i + 2], 16) / 255 for i in (0, 2, 4)):
        channels.append(value / 12.92 if value <= 0.03928
                        else ((value + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


# (foreground, background, minimum, what it is)
PAIRS = [
    ("text", "surface", 4.5, "body text on a card"),
    ("text", "bg", 4.5, "body text on the page"),
    ("text", "surface-2", 4.5, "body text on a tinted row"),
    ("text-muted", "surface", 4.5, "secondary text"),
    ("text-muted", "bg", 4.5, "secondary text on the page"),
    # Dates, word counts, tag counts: never the only carrier of meaning.
    ("text-subtle", "surface", 3.0, "meta text"),
    ("text-subtle", "surface-2", 3.0, "meta text on a tinted row"),
    ("brand-tint-fg", "brand-tint", 4.5, "text on the brand tint"),
    ("brand-tint-fg", "surface", 4.5, "a link"),
    ("brand-fg", "brand", 4.5, "a primary button"),
    ("danger", "surface", 4.5, "a destructive action"),
    ("ok", "surface", 4.5, "a success message"),
    ("warn", "surface", 4.5, "a warning"),
    ("info", "surface", 4.5, "an informational message"),
]


@pytest.mark.parametrize("theme,tokens", [("light", LIGHT), ("dark", DARK)],
                         ids=["light", "dark"])
@pytest.mark.parametrize("fg,bg,floor,label",
                         PAIRS, ids=[p[3].replace(" ", "-") for p in PAIRS])
def test_the_pair_is_readable(theme, tokens, fg, bg, floor, label):
    # The dark block only redefines what changes; anything it leaves out is
    # inherited from light.
    resolved = {**LIGHT, **tokens}
    if fg not in resolved or bg not in resolved:
        pytest.skip(f"{theme} defines no {fg}/{bg}")

    score = contrast(resolved[fg], resolved[bg])
    assert score >= floor, (
        f"{theme}: {label} is {score:.2f}:1, needs {floor}:1 "
        f"({resolved[fg]} on {resolved[bg]})")


# ---------------------------------------------------------------------------
# The change itself
# ---------------------------------------------------------------------------

def test_the_light_theme_is_not_white():
    """The point of the change. Pure white is what it moved away from."""
    assert LIGHT["surface"].lower() != "#ffffff"
    assert LIGHT["bg"].lower() != "#ffffff"


def test_the_whole_neutral_ramp_is_warm():
    """
    Cream behind cool-grey cards and borders does not read as warm, it reads as
    dirty. Every neutral has to carry more red than blue.
    """
    for name in ("bg", "surface", "surface-2", "surface-3", "border", "border-strong"):
        colour = LIGHT[name].lstrip("#")
        red, blue = int(colour[0:2], 16), int(colour[4:6], 16)
        assert red > blue, f"{name} ({LIGHT[name]}) is not warm"


def test_the_shadows_are_warm_too():
    """A blue-black shadow on cream is a grey smudge."""
    block = CSS.split(":root {", 1)[1].split("}", 1)[0]
    shadows = re.findall(r"--shadow[a-z-]*:\s*([^;]+);", block)
    assert shadows
    for shadow in shadows:
        for r, g, b in re.findall(r"rgba\((\d+), *(\d+), *(\d+)", shadow):
            assert int(r) > int(b), f"cool shadow: {shadow.strip()}"


def test_the_dark_theme_still_has_its_own_ground():
    """Warming the light theme must not have leaked into the dark one."""
    assert _luminance(DARK["surface"]) < 0.1
    assert DARK["brand"] != LIGHT["brand"]


def test_both_themes_declare_the_same_tokens():
    """
    A token defined in one theme and not the other is a colour that silently
    falls back to the other theme's value — which is how a light-theme green
    ends up on a dark background.
    """
    # Compared against what each block DECLARES, not the resolved maps: the
    # resolved dark map inherits from light on purpose, which would make this
    # pass no matter what.
    colours = [name for name in LIGHT_RAW if name.startswith(
        ("bg", "surface", "border", "text", "brand", "info", "ok", "warn", "danger"))]
    missing = [name for name in colours if name not in DARK_RAW]

    assert missing == [], f"the dark theme inherits light colours: {missing}"


# ---------------------------------------------------------------------------
# Nothing hard-codes a colour the theme is supposed to choose
# ---------------------------------------------------------------------------

def test_nothing_paints_hard_coded_white_on_the_brand():
    """
    The dark theme's brand is a BRIGHT green, so `color: #fff` on it scores
    2.0:1 while `var(--brand-fg)` — near-black there — scores 9.48. Two rules
    were doing exactly that, and had been since before the cream.
    """
    sheets = (pathlib.Path(__file__).resolve().parents[1] / "dashboard"
              / "static" / "css").glob("*.css")

    offenders = []
    for sheet in sheets:
        text = sheet.read_text(encoding="utf-8")
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        for rule in re.findall(r"\{[^}]*\}", text):
            if "var(--brand)" in rule and re.search(r"color:\s*#(fff|ffffff)\b", rule):
                offenders.append(f"{sheet.name}: {' '.join(rule.split())[:80]}")

    assert offenders == [], offenders
