"""Install the PostgreSQL activity calorie calculation trigger."""

from alembic import op


revision = "bb22cc33dd44"
down_revision = "aa11bb22cc33"
branch_labels = None
depends_on = None


CREATE_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION calc_activity_calories()
RETURNS TRIGGER AS $$
DECLARE
    w FLOAT; h FLOAT; stride FLOAT; dist FLOAT; dur FLOAT;
    met_val FLOAT; spd FLOAT;
BEGIN
    SELECT weight, height INTO w, h FROM "user" WHERE id = NEW.user_id;
    w := COALESCE(w, 70); h := COALESCE(h, 170);
    met_val := CASE NEW.intensity
        WHEN 'light' THEN 2.0 WHEN 'moderate' THEN 3.5
        WHEN 'brisk' THEN 4.3 WHEN 'fast' THEN 5.0 ELSE 3.5 END;
    spd := CASE NEW.intensity
        WHEN 'light' THEN 3.0 WHEN 'moderate' THEN 4.5
        WHEN 'brisk' THEN 5.5 WHEN 'fast' THEN 6.5 ELSE 4.5 END;
    stride := h * 0.414;
    dist := NEW.steps * stride / 100000.0;
    dur := dist / spd;
    NEW.calories_burned := ROUND((met_val * w * dur)::numeric, 1);
    NEW.distance_km := ROUND(dist::numeric, 2);
    NEW.duration_min := ROUND((dur * 60)::numeric, 1);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(CREATE_FUNCTION_SQL)
    op.execute("DROP TRIGGER IF EXISTS trg_calc_activity ON daily_activity")
    op.execute("""CREATE TRIGGER trg_calc_activity
        BEFORE INSERT OR UPDATE ON daily_activity
        FOR EACH ROW EXECUTE FUNCTION calc_activity_calories()""")


def downgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS trg_calc_activity ON daily_activity")
    op.execute("DROP FUNCTION IF EXISTS calc_activity_calories()")
