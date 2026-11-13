"""The task app's parts find what they use of each other.

The task app (public/tasks.js) is one object, `app`, that its parts
(public/js/task*.js) are handed: each `initTask…(app)` uses the others'
functions as `app.name(…)` and returns its own, which tasks.js adds to
`app`. Nothing checks those names when the page loads: a part that calls
`app.rendr()` fails only when that line runs, in a browser, perhaps in a
handler no test clicks. So this reads the sources: every `app.name` a part
uses is one tasks.js puts there or a part returns.
"""

import re
from pathlib import Path

PUBLIC = Path(__file__).resolve().parent.parent / "public"
PARTS = sorted((PUBLIC / "js").glob("task*.js"))


def _without_comments(text: str) -> str:
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


def _keys(obj: str) -> set[str]:
    """The names an object literal's body gives: shorthand names, name: value, get name()."""
    obj = _without_comments(obj)
    names = set(re.findall(r"\bget\s+([A-Za-z_$][\w$]*)\s*\(", obj))
    obj = re.sub(r"\bget\s+[A-Za-z_$][\w$]*\s*\(\)\s*\{[^}]*\}", "", obj)
    for item in obj.split(","):
        m = re.match(r"\s*([A-Za-z_$][\w$]*)\s*(?::|$)", item.strip())
        if m:
            names.add(m.group(1))
    return names


def _sources() -> list[tuple[str, set[str]]]:
    """(where, the names it puts on app): tasks.js's literal first, then each part's return."""
    tasks = (PUBLIC / "tasks.js").read_text(encoding="utf-8")
    literal = re.search(r"^const app = \{\n(.*?)^\};", tasks, re.S | re.M)
    assert literal, "tasks.js no longer makes `const app = {…}`"
    assigned = set(re.findall(r"^\s*app\.([A-Za-z_$][\w$]*)\s*=[^=]", tasks, re.M))
    sources = [("tasks.js", _keys(literal.group(1)) | assigned)]
    for part in PARTS:
        text = part.read_text(encoding="utf-8")
        # What the part returns from its init function: its last `return {…};`.
        returns = re.findall(r"^return \{(.*?)\};", text, re.S | re.M)
        names = _keys(returns[-1]) if returns else set()
        names |= set(re.findall(r"^\s*app\.([A-Za-z_$][\w$]*)\s*=[^=]", text, re.M))
        sources.append((part.name, names))
    return sources


def _provided() -> dict[str, str]:
    """name → where it comes from."""
    provided: dict[str, str] = {}
    for where, names in _sources():
        for name in names:
            provided.setdefault(name, where)
    return provided


def _used() -> dict[str, set[str]]:
    """name → the files that use app.name."""
    used: dict[str, set[str]] = {}
    for path in [PUBLIC / "tasks.js", *PARTS]:
        text = _without_comments(path.read_text(encoding="utf-8"))
        for name in re.findall(r"\bapp\.([A-Za-z_$][\w$]*)", text):
            used.setdefault(name, set()).add(path.name)
    return used


def test_every_name_a_part_uses_is_provided():
    provided, used = _provided(), _used()
    missing = {name: sorted(files) for name, files in used.items() if name not in provided}
    assert not missing, f"used as app.<name> but never put on app: {missing}"


def test_no_name_is_put_on_app_twice():
    """Object.assign(app, part) onto a name app has would replace it for the
    parts that come later, or throw where tasks.js made it a getter."""
    seen: dict[str, str] = {}
    twice = {}
    for where, names in _sources():
        for name in names:
            if name in seen:
                twice[name] = (seen[name], where)
            seen[name] = where
    assert not twice, f"put on app by two files: {twice}"


def test_the_parts_are_found():
    """The check reads something: the parts, what they return, what they use."""
    provided, used = _provided(), _used()
    assert len(PARTS) >= 7 and {"render", "openEditModal", "st", "listEl"} <= set(provided)
    assert {"render", "updateTask"} <= set(used)
    assert provided["render"] == "taskrender.js"
