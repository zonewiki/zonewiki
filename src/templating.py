"""Leaf's HTML templates (src/templates): Jinja2, auto-escaped. A variable
holding HTML that Leaf built itself ends in _html and is the only kind
marked |safe there. Not Flask's render_template: templates render outside
a request too (tests, and Python callers that embed the result)."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

import config
from pages import url

_env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                   autoescape=True, trim_blocks=True, lstrip_blocks=True)
_env.globals.update(url=url, config=config)


def render(name: str, **context) -> str:
    """A template rendered to HTML."""
    return _env.get_template(name).render(**context)


def macro(template: str, name: str):
    """A macro of a template, to call from Python (returns HTML)."""
    return getattr(_env.get_template(template).module, name)
