"""Private loaded-process Coach proof and its strict wire format (stdlib only)."""
import hashlib
import json
import re
import socket
import stat
import struct
import sys
from pathlib import Path

VERSION = "r6-coach-containment-v1"
LIMIT = 4096
SHA = re.compile(r"[0-9a-f]{40}")
DIGEST = re.compile(r"[0-9a-f]{64}")
NONCE = re.compile(r"[0-9a-f]{32}")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
STARTED = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?Z")
HEAD = re.compile(r"[a-z0-9_]{1,64}")
ROUTES = {"blue": "127.0.0.1:5001", "green": "127.0.0.1:5002"}
FIELDS = {"version", "role", "revision", "boot", "pid", "start_ticks",
          "heads", "flag", "guard"}


def socket_path(pid):
    return Path(f"/tmp/axisai-coach-{pid}.sock")


def boot_digest(boot):
    return hashlib.sha256(boot.encode("ascii")).hexdigest()


def validate_process(proof, *, role, revision):
    if (not isinstance(proof, dict) or set(proof) != FIELDS
            or role not in ("web", "worker") or not SHA.fullmatch(revision)
            or proof["version"] != VERSION or proof["role"] != role
            or proof["revision"] != revision or proof["flag"] is not False
            or proof["guard"] is not False
            or not DIGEST.fullmatch(proof["boot"])
            or any(type(proof[k]) is not int or proof[k] < 1 for k in ("pid", "start_ticks"))):
        raise ValueError("R6_COACH_PROCESS_UNPROVEN")
    heads = proof["heads"]
    if (not isinstance(heads, list) or not heads
            or any(not HEAD.fullmatch(head) for head in heads)
            or heads != sorted(set(heads))):
        raise ValueError("R6_COACH_SCHEMA_UNPROVEN")
    return proof


def _process(pid):
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()
    if fields[0] in ("Z", "X"):
        raise ValueError("R6_COACH_PROCESS_DEAD")
    return int(fields[1]), int(fields[19])


def _actor(role):
    # The production slot uses a single Gunicorn child of master PID 1.
    # The production worker runs python worker.py as PID 1 and forks RQ jobs.
    argv = Path("/proc/1/cmdline").read_bytes().split(b"\0")
    master = _process(1)
    if role == "worker" and b"worker.py" in argv:
        return 1, master[1], master[1]
    if role != "web" or b"starter:app" not in argv or not any(
            arg.rsplit(b"/", 1)[-1] == b"gunicorn" for arg in argv):
        raise ValueError("R6_COACH_LINEAGE_UNPROVEN")
    children = Path("/proc/1/task/1/children").read_text().split()
    if len(children) != 1:
        raise ValueError("R6_COACH_WEB_INVENTORY_UNPROVEN")
    pid = int(children[0])
    parent, ticks = _process(pid)
    if parent != 1:
        raise ValueError("R6_COACH_WEB_PARENT_UNPROVEN")
    return pid, ticks, master[1]


def decode_response(line, challenge, role, revision):
    def unique_fields(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("R6_COACH_DUPLICATE_FIELDS")
        return result

    if len(line) >= LIMIT or not line.endswith(b"\n"):
        raise ValueError("R6_COACH_WIRE_INVALID")
    response = json.loads(line, object_pairs_hook=unique_fields)
    if not isinstance(response, dict) or response.pop("challenge", None) != challenge:
        raise ValueError("R6_COACH_CHALLENGE_UNPROVEN")
    return validate_process(response, role=role, revision=revision)


def read_process(role, revision, challenge):
    if not NONCE.fullmatch(challenge):
        raise ValueError("R6_COACH_CHALLENGE_INVALID")
    actor = _actor(role)
    path = socket_path(actor[0])
    if not stat.S_ISSOCK(path.lstat().st_mode) or stat.S_IMODE(path.lstat().st_mode) != 0o600:
        raise ValueError("R6_COACH_SOCKET_UNPROVEN")
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(5)
        client.connect(str(path))
        peer, _, _ = struct.unpack("3i", client.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
        if peer != actor[0]:
            raise ValueError("R6_COACH_PEER_UNPROVEN")
        client.sendall(challenge.encode("ascii") + b"\n")
        with client.makefile("rb") as source:
            proof = decode_response(source.readline(LIMIT), challenge, role, revision)
    if _actor(role) != actor or (proof["pid"], proof["start_ticks"]) != actor[:2]:
        raise ValueError("R6_COACH_PROCESS_CHANGED")
    return proof


def binding(revision, tree, image, slot, heads):
    if (not SHA.fullmatch(revision) or not SHA.fullmatch(tree) or not IMAGE.fullmatch(image)
            or slot not in ROUTES or not isinstance(heads, list) or not heads
            or any(not HEAD.fullmatch(head) for head in heads)
            or heads != sorted(set(heads))):
        raise ValueError("R6_COACH_RELEASE_BINDING_INVALID")
    return dict(revision=revision, tree=tree, image=image, slot=slot,
                backend=ROUTES[slot], heads=heads)


def bind_actor(proof, before, after, approved, role):
    validate_process(proof, role=role, revision=approved["revision"])
    if (before != after or not isinstance(before, list) or len(before) != 7
            or not DIGEST.fullmatch(before[0]) or not STARTED.fullmatch(before[2])
            or before[1] != approved["image"] or before[3:5] != ["running", "healthy"]
            or type(before[5]) is not int or before[5] != 0 or before[6] is not False
            or proof["heads"] != approved["heads"]):
        raise ValueError("R6_COACH_ACTOR_BINDING_INVALID")
    return {**proof, "container": before[0], "started": before[2]}


def fingerprint(approved, web, worker):
    if web["container"] == worker["container"] or web["boot"] == worker["boot"]:
        raise ValueError("R6_COACH_PAIR_IDENTITY_INVALID")
    data = json.dumps({"release": approved, "web": web, "worker": worker},
                      sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(data).hexdigest()


if __name__ == "__main__":
    try:
        print(json.dumps(read_process(*sys.argv[1:])))
    except Exception:
        sys.exit("R6_COACH_PROCESS_UNPROVEN")
