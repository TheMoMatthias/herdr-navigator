"""Compact widgets: one row high, no borders, so toolbars and forms fit a popup pane.

Every Navigator control is built through these, which keeps the look in one place.
"""
from __future__ import annotations

from textual.widgets import Button, Checkbox, Input, Select


def Btn(label, id: str | None = None, **kw) -> Button:
    kw.setdefault("compact", True)
    return Button(label, id=id, **kw)


def Field(value: str = "", **kw) -> Input:
    kw.setdefault("compact", True)
    return Input(value, **kw)


def Choice(options, **kw) -> Select:
    kw.setdefault("compact", True)
    return Select(options, **kw)


class _Tick(Checkbox):
    """A checkbox that reads as one: ✔ when on (the stock "X" reads as "off" or "cancel")."""
    BUTTON_INNER = "✔"


def Tick(label: str, value: bool = False, **kw) -> Checkbox:
    kw.setdefault("compact", True)
    return _Tick(label, value, **kw)


# Shared look of a one-row toolbar and of a "label  control" form row.
CSS = """
.bar { height: 1; margin: 0 0 1 0; padding: 0 1; }
.bar Button { margin: 0 1 0 0; min-width: 3; }
.bar Static.grow { width: 1fr; }
.bar .note { width: 1fr; color: $text-muted; padding: 0 0 0 1; }
.form-row { height: 1; margin: 0 0 1 0; }
.form-row .lbl { width: 20; color: $text-muted; }
.form-row Input { width: 1fr; margin: 0 1 0 0; }
.form-row Select { width: 1fr; }
.form-row Input.num { width: 8; }
.form-row Button { margin: 0 1 0 0; min-width: 3; }
.form-row Input.short { width: 20; }
.form-row Static.unit { width: auto; color: $text-muted; padding: 0 1; }
.hint { color: $text-muted; margin: 0 0 1 0; }
.section-title { text-style: bold; color: $accent; margin: 0 0 1 0; }
Checkbox { margin: 0 0 1 0; }
Input { background: $panel-lighten-1; }
Input:focus { background: $primary 30%; }
Select > SelectCurrent { background: $panel-lighten-1; }
"""
