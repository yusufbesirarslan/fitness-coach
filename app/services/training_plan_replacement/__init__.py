"""Backend transaction foundation; no transport or provider orchestration."""
from .service import (BaseBinding, ConfirmationResult, ReplacementRefusal,
                      ConfirmationConflict, ProposalUnavailable,
                      stage_proposal, confirm_replacement)
