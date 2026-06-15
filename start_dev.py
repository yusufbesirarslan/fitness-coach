"""
Dev startup script — runs Flask app + MCP server concurrently.

Usage:
    python start_dev.py
"""

import subprocess
import sys
import os
import signal

def main():
    env = os.environ.copy()
    procs = []

    print("[FitX] Starting Flask app on :5000 ...")
    flask_proc = subprocess.Popen(
        [sys.executable, "starter.py"],
        env=env,
    )
    procs.append(flask_proc)

    print("[FitX] Starting MCP server (HTTP) on :8100 ...")
    # --http taşıması FITX_MCP_ALLOW_HTTP=1 olmadan açılmayı reddeder (loopback arkası
    # güvenlik kapısı). Dev launcher bu yüzden bayrağı yalnızca MCP süreci için verir.
    mcp_env = {**env, "FITX_MCP_ALLOW_HTTP": "1"}
    mcp_proc = subprocess.Popen(
        [sys.executable, "-m", "fitx_mcp", "--http"],
        env=mcp_env,
    )
    procs.append(mcp_proc)

    print("[FitX] Both services running. Press Ctrl+C to stop.\n")

    def shutdown(signum, frame):
        print("\n[FitX] Shutting down ...")
        for p in procs:
            p.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    for p in procs:
        p.wait()


if __name__ == "__main__":
    main()
