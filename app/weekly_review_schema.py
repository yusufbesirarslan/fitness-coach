"""Storage invariants shared by the model and the PD6 expand migration."""

STATUS_CHECK = "ck_weekly_checkin_review_state"
STATUS_RULE = """
CASE
  WHEN review_status IS NULL THEN
    review_context IS NULL AND review_updated_at IS NULL AND review_result IS NULL
    AND review_evidence_digest IS NULL AND review_failure IS NULL
  WHEN review_status IN ('pending', 'processing', 'dispatching') THEN
    yogunluk IS NOT NULL AND review_context IS NOT NULL
    AND review_updated_at IS NOT NULL AND review_result IS NULL
    AND review_evidence_digest IS NULL AND review_failure IS NULL
  WHEN review_status = 'completed' THEN
    yogunluk IS NOT NULL AND review_context IS NULL AND review_updated_at IS NOT NULL
    AND review_result IS NOT NULL AND review_evidence_digest IS NOT NULL AND review_failure IS NULL
  WHEN review_status = 'unavailable' THEN
    yogunluk IS NOT NULL AND review_context IS NULL AND review_updated_at IS NOT NULL
    AND review_result IS NULL AND review_evidence_digest IS NULL AND review_failure IS NOT NULL
  ELSE FALSE
END
"""
WORK_INDEX = "ix_weekly_checkin_review_work"
WORK_PREDICATE = "review_status IN ('pending', 'processing', 'dispatching')"
