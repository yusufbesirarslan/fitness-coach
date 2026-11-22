"""F1: the documented Coach acceptance CLI against the real slot-runtime path.

Each test runs ``python -m scripts.r6_coach_acceptance`` as a subprocess from a
synthetic git checkout holding this repository's script bytes. The reader,
HostOps and ``web_slot_runtime.py`` all run for real; only the host binaries
``docker`` and ``sudo`` are synthetic fixtures on PATH. Nothing is mocked inside
Python and nothing reaches Docker, nginx or AWS.
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import r6_coach_acceptance as acceptance
from scripts import r6_coach_attestation as coach

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts"
PROJECT = "fitness-coach"
IMAGE = "sha256:" + "3" * 64
HEADS = ["ad17de1e0001"]
STARTED = "2026-01-01T00:00:00.123456789Z"
CHALLENGE = "a" * 32
WEB_ID = "1" * 64
WORKER_ID = "2" * 64

FAKE_DOCKER = r'''
import json, os, sys
state = json.load(open(os.environ["FAKE_HOST_STATE"]))
with open(os.environ["FAKE_HOST_LOG"], "a") as log:
    log.write(json.dumps(["docker", *sys.argv[1:]]) + "\n")
args = sys.argv[1:]
actors = {a["id"]: a for a in state["actors"]}

def short(cid):
    found = [a for i, a in actors.items() if i.startswith(cid)]
    if len(found) != 1:
        sys.stderr.write("Error: No such object: " + cid + "\n"); sys.exit(1)
    return found[0]

def full(a):
    return {"Id": a["id"], "Name": "/" + a["name"], "Image": a["image"],
            "State": {"Status": a["status"], "Health": {"Status": a["health"]},
                      "StartedAt": a["started"]},
            "Config": {"Image": a["config_image"], "Labels": a["labels"],
                       "Env": ["APP_REVISION=" + a["revision"],
                               "FITX_STARTUP_MODE=" + a["mode"]]},
            "HostConfig": {"PortBindings": a["ports"]}}

if args[:2] == ["ps", "-a"]:
    wanted = dict(f.split("=", 1)[1].split("=", 1) for f in args[3:-2:2])
    print("\n".join(i[:12] for i, a in actors.items()
                    if all(a["labels"].get(k) == v for k, v in wanted.items())))
elif args[0] == "inspect" and args[1] == "--format":
    a = short(args[3])
    print(json.dumps([a["id"], a["image"], a["started"], a["status"], a["health"],
                      a["restarts"], False]))
elif args[0] == "inspect":
    print(json.dumps([full(short(cid)) for cid in args[1:]]))
elif args[0] == "exec" and args[2:5] == ["python3", "-I", "-c"]:
    a = short(args[1])
    if len(args) != 9 or args[7] != state["revision"]:
        sys.exit(1)
    sys.stdout.write(a["proof"] if isinstance(a["proof"], str) else json.dumps(a["proof"]))
else:
    sys.stderr.write("unexpected docker call\n"); sys.exit(97)
'''

FAKE_SUDO = r'''
import json, os, sys
state = json.load(open(os.environ["FAKE_HOST_STATE"]))
with open(os.environ["FAKE_HOST_LOG"], "a") as log:
    log.write(json.dumps(["sudo", *sys.argv[1:]]) + "\n")
if sys.argv[1:] != ["-n", "/usr/local/sbin/axisai-switch-web-slot", "status"]:
    sys.exit(97)
print("op=status result=ok state=%s backend=%s" % tuple(state["route"]))
'''


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def _checkout(parent, source=SOURCE):
    checkout = parent / PROJECT
    (checkout / "scripts").mkdir(parents=True)
    for path in sorted(source.glob("*.py")):
        shutil.copyfile(path, checkout / "scripts" / path.name)
    _git(checkout, "init", "-q")
    _git(checkout, "config", "user.email", "r6@example.invalid")
    _git(checkout, "config", "user.name", "r6 synthetic")
    _git(checkout, "config", "commit.gpgsign", "false")
    _git(checkout, "add", "-A")
    _git(checkout, "commit", "-q", "-m", "synthetic")
    return checkout, _git(checkout, "rev-parse", "HEAD"), _git(checkout, "rev-parse", "HEAD^{tree}")


def _proof(role, revision):
    return dict(version=coach.VERSION, role=role, revision=revision, heads=HEADS[:],
                boot=("b" if role == "web" else "c") * 64, pid=2 if role == "web" else 1,
                start_ticks=41, flag=False, guard=False)


def _actors(revision):
    web = dict(id=WEB_ID, name="axisai-web-blue-web-1", image=IMAGE, status="running",
               health="healthy", started=STARTED, restarts=0, revision=revision,
               config_image=f"axisai-web:{revision}", mode="read-only",
               labels={"com.docker.compose.project": "axisai-web-blue",
                       "com.docker.compose.service": "web"},
               ports={"5000/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5001"}]},
               proof=_proof("web", revision))
    worker = dict(id=WORKER_ID, name="fitness-coach-worker-1", image=IMAGE, status="running",
                  health="healthy", started=STARTED, restarts=0, revision=revision,
                  config_image=f"axisai-web:{revision}", mode="read-only",
                  labels={"com.docker.compose.project": PROJECT,
                          "com.docker.compose.service": "worker"},
                  ports={}, proof=_proof("worker", revision))
    return [web, worker]


def _expected_digest(approved, actors):
    web, worker = ({**a["proof"], "container": a["id"], "started": a["started"]} for a in actors)
    return coach.fingerprint(approved, web, worker)


@pytest.fixture
def host(tmp_path):
    base = tmp_path.resolve()
    checkout, revision, tree = _checkout(base / "host")
    tools = base / "bin"
    tools.mkdir()
    for name, body in (("docker", FAKE_DOCKER), ("sudo", FAKE_SUDO)):
        (tools / f"{name}.py").write_text(body, encoding="utf-8")
        wrapper = tools / name
        wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{tools / name}.py" "$@"\n',
                           encoding="ascii")
        wrapper.chmod(0o755)
    state = {"revision": revision, "route": ["blue", "127.0.0.1:5001"],
             "actors": _actors(revision)}
    approved = coach.binding(revision, tree, IMAGE, "blue", HEADS)

    class Host:
        pass

    h = Host()
    h.base, h.checkout, h.revision, h.tree, h.tools = base, checkout, revision, tree, tools
    h.state, h.approved = state, approved
    h.log = base / "host.log"
    h.digest = _expected_digest(approved, state["actors"])

    def run(*, deploy_dir=None, cwd=None, digest=None, challenge=CHALLENGE, revision=None,
            main_project=PROJECT, extra=()):
        (base / "state.json").write_text(json.dumps(h.state), encoding="utf-8")
        h.log.write_text("", encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "GIT_"))}
        env.update(PATH=f"{tools}{os.pathsep}{os.environ.get('PATH', '')}",
                   PYTHONDONTWRITEBYTECODE="1", FAKE_HOST_STATE=str(base / "state.json"),
                   FAKE_HOST_LOG=str(h.log))
        argv = [sys.executable, "-m", "scripts.r6_coach_acceptance",
                "--deploy-dir", str(checkout) if deploy_dir is None else deploy_dir,
                "--main-project", main_project, "--revision", revision or h.revision,
                "--tree", h.tree, "--image", IMAGE, "--slot", "blue", "--head", HEADS[0],
                "--digest", digest or h.digest, "--challenge", challenge, *extra]
        started = datetime.now(timezone.utc)
        done = subprocess.run(argv, cwd=str(cwd or checkout), env=env, capture_output=True,
                              text=True, timeout=120)
        ended = datetime.now(timezone.utc)
        calls = [json.loads(line) for line in h.log.read_text().splitlines()]
        return done, calls, started, ended

    h.run = run
    return h


def _slot_runtime_calls(calls):
    return [c for c in calls if c[:5] == ["docker", "ps", "-a", "--filter",
                                          "label=com.docker.compose.project=axisai-web-blue"]]


def test_documented_cli_reaches_real_slot_runtime_and_passes_outer_verifier(host):
    done, calls, started, ended = host.run()
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    # The real web_slot_runtime.py ran (its docker ps for the slot project).
    assert len(_slot_runtime_calls(calls)) == 2
    assert ["sudo", "-n", "/usr/local/sbin/axisai-switch-web-slot", "status"] in calls
    assert sum(c[:2] == ["docker", "exec"] for c in calls) == 3
    # Every host call is a read: no compose, run, rm, stop, kill, switch or write.
    assert {c[1] for c in calls if c[0] == "docker"} <= {"ps", "inspect", "exec"}
    acceptance.verify_observation(report, host.approved, host.digest, CHALLENGE, started, ended)
    with pytest.raises(ValueError, match="BINDING_INVALID"):
        acceptance.verify_observation(report, host.approved, host.digest, "b" * 32,
                                      started, ended)


def test_original_relative_context_is_refused_by_the_real_slot_runtime(host):
    """F1 regression: HostOps(context_dir=".") makes the slot runtime refuse."""
    script = (
        "import json,sys\n"
        "from scripts.r6_deploy_transaction import HostOps, OpError\n"
        f"ops = HostOps(deploy_dir={str(host.checkout)!r}, context_dir='.', work_dir='.',"
        f" main_project={PROJECT!r}, deploy_sha={host.revision!r}, previous_commit='')\n"
        "try:\n    ops.slot_inspect('blue', 30)\nexcept OpError as exc:\n"
        "    print(str(exc)); sys.exit(3)\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env["PATH"] = f"{host.tools}{os.pathsep}{os.environ.get('PATH', '')}"
    done = subprocess.run([sys.executable, "-c", script], cwd=str(host.checkout), env=env,
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 3
    assert "deploy dir must be absolute" in done.stdout


@pytest.mark.parametrize("variant", ["relative", "dot", "trailing", "dotdot", "double", "symlink",
                                     "other_name"])
def test_relative_or_noncanonical_deploy_dir_is_refused_before_any_host_read(host, variant):
    checkout = str(host.checkout)
    link = host.base / "link"
    link.mkdir()
    (link / PROJECT).symlink_to(host.checkout, target_is_directory=True)
    deploy_dir = {"relative": f"host/{PROJECT}", "dot": ".", "trailing": checkout + "/",
                  "dotdot": f"{host.checkout.parent}/../host/{PROJECT}",
                  "double": checkout.replace("/host/", "//host//"),
                  "symlink": str(link / PROJECT), "other_name": str(host.base / "host")}[variant]
    done, calls, *_ = host.run(deploy_dir=deploy_dir)
    assert done.returncode == 1
    assert done.stdout == ""
    assert done.stderr.strip() == "R6_COACH_LIVE_ACCEPTANCE_UNPROVEN"
    assert calls == []


def test_main_project_must_name_the_checkout(host):
    done, calls, *_ = host.run(main_project="axisai-web-blue")
    assert (done.returncode, done.stdout, calls) == (1, "", [])


def test_wrong_source_checkout_is_refused(host):
    other, *_ = _checkout(host.base / "other")
    # The reader runs from `other` while naming the approved checkout.
    done, calls, *_ = host.run(cwd=other)
    assert (done.returncode, done.stdout, calls) == (1, "", [])
    # The reader runs from the approved checkout while naming `other`.
    done, calls, *_ = host.run(deploy_dir=str(other))
    assert (done.returncode, done.stdout, calls) == (1, "", [])


def test_checkout_must_be_the_approved_commit_with_unmodified_scripts(host):
    done, calls, *_ = host.run(revision="9" * 40)
    assert (done.returncode, done.stdout, calls) == (1, "", [])
    runtime = host.checkout / "scripts" / "web_slot_runtime.py"
    runtime.write_text(runtime.read_text() + "\n# drift\n")
    done, calls, *_ = host.run()
    assert (done.returncode, done.stdout, calls) == (1, "", [])


@pytest.mark.parametrize("mutation", [
    ("web", "proof", "role", "worker"), ("worker", "proof", "revision", "9" * 40),
    ("worker", "proof", "flag", True), ("web", "proof", "heads", ["foreign"]),
    ("web", "image", None, "sha256:" + "4" * 64), ("worker", "health", None, "unhealthy"),
    ("web", "revision", None, "9" * 40), ("worker", "restarts", None, 1),
])
def test_actor_mismatch_is_unproven(host, mutation):
    role, key, field, value = mutation
    actor = host.state["actors"][0 if role == "web" else 1]
    if field:
        actor[key][field] = value
    else:
        actor[key] = value
    done, calls, *_ = host.run()
    assert (done.returncode, done.stdout) == (1, "")
    assert done.stderr.strip() == "R6_COACH_LIVE_ACCEPTANCE_UNPROVEN"
    assert _slot_runtime_calls(calls)


@pytest.mark.parametrize("proof", ["", "not json", "{}", json.dumps({"flag": False})])
def test_missing_or_malformed_process_proof_is_unproven(host, proof):
    host.state["actors"][1]["proof"] = proof
    done, calls, *_ = host.run()
    assert (done.returncode, done.stdout) == (1, "")
    assert _slot_runtime_calls(calls) and ["docker", "exec"] in [c[:2] for c in calls]


def test_missing_worker_or_extra_slot_container_is_unproven(host):
    worker = host.state["actors"].pop()
    done, calls, *_ = host.run()
    assert (done.returncode, done.stdout) == (1, "")
    assert _slot_runtime_calls(calls)
    host.state["actors"] += [worker, {**host.state["actors"][0], "id": "5" * 64}]
    done, calls, *_ = host.run()
    assert (done.returncode, done.stdout) == (1, "")
    assert _slot_runtime_calls(calls) and not [c for c in calls if c[:2] == ["docker", "exec"]]


def test_route_and_digest_mismatch_are_unproven(host):
    # The full pair proof runs, then the deployed digest differs.
    done, calls, *_ = host.run(digest="e" * 64)
    assert (done.returncode, done.stdout) == (1, "")
    assert sum(c[:2] == ["docker", "exec"] for c in calls) == 3
    host.state["route"] = ["green", "127.0.0.1:5002"]
    done, calls, *_ = host.run()
    assert (done.returncode, done.stdout) == (1, "")
    assert calls == [["sudo", "-n", "/usr/local/sbin/axisai-switch-web-slot", "status"]]


@pytest.mark.parametrize("challenge,digest", [("A" * 32, None), ("a" * 31, None),
                                              (CHALLENGE, "E" * 64)])
def test_invalid_binding_inputs_are_refused_before_any_host_read(host, challenge, digest):
    done, calls, *_ = host.run(challenge=challenge, digest=digest)
    assert (done.returncode, done.stdout, calls) == (1, "", [])
