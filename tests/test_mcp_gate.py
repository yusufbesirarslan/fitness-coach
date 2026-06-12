"""Safety-net tests for the MCP server's HTTP transport gate (fitx_mcp/).

MCP araçları user_id'yi parametre alır ve KENDİ yetkilendirmesi YOKTUR
(CLAUDE.md uyarısı). Bu testler iki şeyi sabitler:
1. `python -m fitx_mcp --http`, FITX_MCP_ALLOW_HTTP=1 olmadan BAŞLAMAZ.
2. streamable-http çağrıları yalnızca loopback'e (127.0.0.1) bağlanır.

    python -m pytest tests/test_mcp_gate.py -v
"""
import os
import re
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("entrypoint", [
    ["-m", "fitx_mcp"],            # belgelenen yol
    ["fitx_mcp/server.py"],        # doğrudan script çalıştırma da kapıyı atlamamalı
])
def test_http_transport_refused_without_optin(entrypoint):
    env = {k: v for k, v in os.environ.items() if k != "FITX_MCP_ALLOW_HTTP"}
    result = subprocess.run(
        [sys.executable, *entrypoint, "--http"],
        cwd=_REPO_ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode != 0
    assert "FITX_MCP_ALLOW_HTTP" in result.stderr


def test_streamable_http_always_binds_loopback_only():
    # Kaynak-seviyesi koruma: herhangi bir streamable-http run() çağrısına
    # public bind (0.0.0.0 vb.) eklenirse bu test patlar.
    for rel_path in ("fitx_mcp/__main__.py", "fitx_mcp/server.py"):
        with open(os.path.join(_REPO_ROOT, rel_path)) as f:
            source = f.read()
        http_runs = re.findall(r'\.run\(transport="streamable-http"[^)]*\)', source)
        assert http_runs, f"{rel_path}: streamable-http çağrısı bulunamadı (yol değiştiyse testi güncelle)"
        for call in http_runs:
            assert 'host="127.0.0.1"' in call, f"{rel_path}: loopback dışı bind: {call}"
