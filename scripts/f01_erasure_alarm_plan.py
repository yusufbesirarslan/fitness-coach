"""M4 alarm parameter plan only. No AWS client, application or apply command."""
import re

from app.services import health_erasure_monitoring as m


def alarm_plan(page_topic, critical_topic, *, actions_enabled=False):
    for topic in (page_topic, critical_topic):
        if not re.fullmatch(r"arn:aws:sns:[a-z0-9-]+:[0-9]{12}:[A-Za-z0-9_-]+", topic):
            raise ValueError("existing approved SNS topic ARN required")
    alarms = []
    for suffix, metric, threshold, missing, topic in (
        ("Retry-Older-Than-75m", m.PASS_AGE, 4500, "breaching", page_topic),
        ("Pending-After-Two-Retries", m.TWICE, 0, "notBreaching", page_topic),
        ("Critical-Past-30d", m.OVERDUE, 0, "notBreaching", critical_topic),
    ):
        alarms.append(dict(AlarmName="AxisAI-F01-M4-" + suffix,
            AlarmDescription="F01 activation gate M4. See docs/privacy/F01_D1_ACTIVATION_READINESS.md.",
            Namespace="FitX/Runtime", MetricName=metric,
            Dimensions=[dict(Name="Service", Value="worker")], Statistic="Maximum",
            Period=60, EvaluationPeriods=1, DatapointsToAlarm=1,
            Threshold=threshold, ComparisonOperator="GreaterThanThreshold",
            TreatMissingData=missing, ActionsEnabled=actions_enabled,
            AlarmActions=[topic], OKActions=[topic]))
    return alarms
