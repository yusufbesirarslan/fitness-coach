"""C1 Nutrition domain; internal services remain legal/release gated OFF.

Canonical read/pure targeting exports are unchanged. Durable preparation,
confirmation and undo live in .service; no public route or C2 tool is wired.
See docs/ADAPTIVE_COACH_NUTRITION_C1.md for consent and recovery boundaries.
"""

from .document import (
    Edit, EditError, MealTarget, PreparedEdit, Snapshot, finish_edit,
    parse_snapshot, target_edit,
)
from .read import read_current_plan

__all__ = [
    "Edit", "EditError", "MealTarget", "PreparedEdit", "Snapshot",
    "finish_edit", "parse_snapshot", "read_current_plan", "target_edit",
]
