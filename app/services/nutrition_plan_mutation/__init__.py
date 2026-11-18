"""C1 preparation only: canonical reads and pure, deterministic meal edits.

No provider call, persistence, public route or Coach tool is exposed here.
The caller supplies a server-resolved owner, never a model-supplied plan id.
Consent and the atomic journal/confirmation boundary must precede any future
provider-facing or write consumer. See docs/ADAPTIVE_COACH_NUTRITION_C1.md.
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
