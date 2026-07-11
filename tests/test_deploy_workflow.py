from pathlib import Path


WORKFLOW = Path(".github/workflows/deploy.yml")


def _deploy_yaml():
    return WORKFLOW.read_text(encoding="utf-8")


def test_deploy_fails_on_live_nginx_csp_header_instead_of_sed_mutation():
    body = _deploy_yaml()

    assert "add_header Content-Security-Policy" in body
    assert "sed -i" not in body
    assert "exit 1" in body


def test_deploy_health_gate_and_rollback_are_required():
    body = _deploy_yaml()

    assert "http://127.0.0.1:5000/health" in body
    assert "%{http_code}" in body
    assert "PREV_COMMIT" in body
    assert "ROLLBACK" in body


def test_deploy_gate_uses_deep_health():
    # I2: birincil gate derin sağlığa bakmalı — Redis-down'da login fail-closed
    # iken deploy "yeşil" geçmesin. (Rollback probe'u sığ kalır: kod geri
    # dönüşünü ölçer, Redis'i değil.)
    body = _deploy_yaml()
    assert "http://127.0.0.1:5000/health?deep=1" in body
