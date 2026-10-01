"""
tests/test_markup.py — the pages close their tags.

Written after a duplicate `</div>` reached production and took the settings page
apart. One stray closer in `_usage.html` ended the Analytics panel early, so
every section after it — Security, Appearance, Keyboard — nested inside a
container they did not belong to, and the browser rendered the navigation on top
of the content.

It came from an edit that replaced a block by slicing between two string
offsets. The new text carried its own closing tag and the slice had already kept
the old one. A diff shows two `</div>` lines four columns apart in a file full
of them; nothing about it looks wrong.

Three things made this expensive, and all three are about feedback:

  * HTML has no compiler. Browsers recover from bad nesting silently and
    differently, so the first report is a screenshot.
  * The templates are fragments. Each partial is balanced on its own or not at
    all, and none of them is a document anybody validates.
  * Every existing test asserted that a STRING appeared in the output. A page
    can be structurally ruined and still contain every string it should.

So this checks the one thing those cannot: that what comes back from the server
is a tree.
"""

import re

import pytest

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}

# Every page a signed-in visitor can reach without an id in the URL.
PAGES = ["/", "/dashboard", "/search", "/chat", "/collections", "/goals",
         "/progress", "/notes", "/settings", "/welcome", "/help", "/about",
         "/pricing", "/login"]


def imbalances(html: str) -> list[str]:
    """
    Every place the tree does not close the way it opened.

    Scripts, styles and comments are removed first: all three legitimately
    contain `<` and `>` in ways that are not markup — `a < b` in a comparison,
    a selector in a stylesheet, a tag named inside an explanatory comment.
    """
    html = re.sub(r"(?s)<script.*?</script>|<style.*?</style>|<!--.*?-->", "", html)

    stack: list[str] = []
    problems: list[str] = []

    for match in re.finditer(r"<(/?)([a-zA-Z][\w-]*)([^>]*?)(/?)>", html):
        closing, tag, self_closing = match.group(1), match.group(2).lower(), match.group(4)
        if tag in VOID or self_closing:
            continue

        if not closing:
            stack.append(tag)
            continue

        if not stack:
            problems.append(f"</{tag}> closes nothing")
            continue

        if stack[-1] != tag:
            problems.append(f"expected </{stack[-1]}> but found </{tag}>")
            while stack and stack[-1] != tag:
                stack.pop()
        if stack:
            stack.pop()

    if stack:
        problems.append("never closed: " + ", ".join(stack))
    return problems


@pytest.mark.parametrize("path", PAGES)
def test_the_page_is_a_tree(client, db, path):
    response = client.get(path, follow_redirects=True)
    if response.status_code != 200:
        pytest.skip(f"{path} is not reachable for this fixture ({response.status_code})")

    problems = imbalances(response.get_data(as_text=True))
    assert not problems, f"{path}: " + "; ".join(problems[:4])


def test_the_check_would_notice(client, db):
    """
    A guard that cannot fail is worse than none, and this one is a hand-written
    parser. Given markup that is actually broken, it has to say so.
    """
    assert imbalances("<div><p>text</div>")
    assert imbalances("<div><span>text</span></div></div>")
    assert imbalances("<section><div>text</section>")

    # And must not cry wolf over markup that is merely terse.
    assert not imbalances("<div><img src='x'><br><p>text</p></div>")
    assert not imbalances("<div><input value='a > b'></div>")
