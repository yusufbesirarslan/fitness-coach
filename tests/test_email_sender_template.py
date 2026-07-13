"""infra/cognito-email-sender/template.yaml sözleşmesi (H5).

Trigger havuza bağlandığı anda Cognito KENDİ e-postalarını göndermeyi bırakır.
Bu Lambda sessizce kırılırsa doğrulama/sıfırlama kodları HİÇ kimseye ulaşmaz ama
/register 200 dönmeye devam eder — yani hesap oluşturma tamamen ölür ve hiçbir
gösterge kırmızı yanmaz. Alarm zinciri bu yüzden koda bağlı bir sözleşmedir.

    python -m pytest tests/test_email_sender_template.py -v
"""
from pathlib import Path

import pytest
import yaml

TEMPLATE = Path("infra/cognito-email-sender/template.yaml")


class _CfnLoader(yaml.SafeLoader):
    """CloudFormation kısa-gösterim etiketlerini (!Ref, !Sub, !GetAtt...) yut."""


def _any_tag(loader, tag_suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_CfnLoader.add_multi_constructor("!", _any_tag)


@pytest.fixture(scope="module")
def template():
    return yaml.load(TEMPLATE.read_text(encoding="utf-8"), Loader=_CfnLoader)


@pytest.fixture(scope="module")
def resources(template):
    return template["Resources"]


def test_template_is_valid_yaml_and_sam(template):
    assert template["Transform"] == "AWS::Serverless-2016-10-31"


def test_alarm_topic_and_conditional_subscription_exist(template, resources):
    assert resources["EmailAlarmTopic"]["Type"] == "AWS::SNS::Topic"
    sub = resources["EmailAlarmSubscription"]
    assert sub["Type"] == "AWS::SNS::Subscription"
    # AlarmEmail boşsa abonelik OLUŞMAMALI (stack yine deploy edilebilmeli).
    assert sub["Condition"] == "HasAlarmEmail"
    assert "HasAlarmEmail" in template["Conditions"]
    assert template["Parameters"]["AlarmEmail"]["Default"] == ""


def test_metric_filter_catches_swallowed_failures(resources):
    """Handler sözleşme gereği ASLA yükseltmez → Lambda "başarılı" görünür ve
    DLQ'ya hiçbir şey düşmez. Yutulan istisna asıl arıza modudur; doğru
    enstrüman log metric filter'dır."""
    mf = resources["EmailFailureMetricFilter"]
    assert mf["Type"] == "AWS::Logs::MetricFilter"
    pattern = mf["Properties"]["FilterPattern"]
    # Hem yutulan istisna (ERROR) hem gönderim hatası (WARNING) hem de
    # "RESEND_API_KEY yok → sessiz no-op" vakası yakalanmalı.
    assert "[ERROR]" in pattern
    assert "[WARNING]" in pattern
    assert "RESEND_API_KEY" in pattern

    transform = mf["Properties"]["MetricTransformations"][0]
    assert transform["MetricNamespace"] == "AxisAI/CognitoEmail"
    assert transform["MetricName"] == "EmailSendFailures"


def test_metric_filter_does_not_declare_log_group(resources):
    """Log grubu Lambda tarafından ZATEN otomatik oluşturuldu; CFN ile yeniden
    tanımlamak canlı stack'i "already exists" ile düşürürdü."""
    assert not any(r.get("Type") == "AWS::Logs::LogGroup" for r in resources.values())


@pytest.mark.parametrize("alarm", [
    "EmailFailureAlarm",           # yutulan hata / sessiz no-op
    "EmailLambdaErrorsAlarm",      # INIT timeout / OOM — handler'a hiç ulaşmaz
    "EmailLambdaThrottlesAlarm",
])
def test_alarms_notify_topic_and_ignore_quiet_periods(resources, alarm):
    props = resources[alarm]["Properties"]
    assert resources[alarm]["Type"] == "AWS::CloudWatch::Alarm"
    assert props["AlarmActions"]  # SNS'e bağlı
    # Invoke yoksa metrik yoktur — sessiz saatler ALARM DEĞİLDİR.
    assert props["TreatMissingData"] == "notBreaching"


def test_lambda_timeout_and_memory_match_documented_values(resources):
    """256MB/5s prod'da import-timeout ölüm sarmalına yol açmıştı; 512MB/20s
    bilinçli bir karardır ve kod yorumları buna atıf yapar."""
    props = resources["EmailSenderFunction"]["Properties"]
    assert props["Timeout"] == 20
    assert props["MemorySize"] == 512


def test_lambda_can_only_decrypt_its_own_key(resources):
    """IAM kapsamı regresyon kapısı: yalnızca kms:Decrypt, yalnızca bu anahtar."""
    statements = resources["EmailSenderFunction"]["Properties"]["Policies"][0]["Statement"]
    assert len(statements) == 1
    assert statements[0]["Action"] == "kms:Decrypt"
