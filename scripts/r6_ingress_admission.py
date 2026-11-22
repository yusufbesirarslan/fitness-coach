#!/usr/bin/python3 -I
"""R6 read-only ingress/generation proof under the existing production lock.
Uses the reviewed generation-barrier checks and canonical nginx parser.
Never switches, mutates configuration or opens application admission.
The explicit recovery-only reload uses the reviewed unchanged-config barrier.
"""
import glob
import hashlib
import importlib.util
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("r6_route_parser", Path(__file__).with_name("axisai_switch_web_slot.py"))
sw = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sw
spec.loader.exec_module(sw)
parse_nginx = sw.parse_nginx
BACKENDS = {"legacy": "127.0.0.1:5000", "blue": "127.0.0.1:5001", "green": "127.0.0.1:5002"}

class Hold(Exception):
    pass

def require(ok, code):
    if not ok:
        raise Hold(code)

def run(argv, seconds=10):
    p = subprocess.run(argv, capture_output=True, timeout=seconds, check=False,
                       env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"})
    require(p.returncode == 0 and len(p.stdout) <= 4*1024*1024, "INSPECTION_FAILED")
    return p.stdout

def walk(tree, parents=()):
    for d in tree:
        yield d, parents
        if d.block is not None:
            yield from walk(d.block, parents+(d,))

def args(d):
    return [a.value for a in d.args]

def trusted_file(path, limit=4*1024*1024):
    # nginx's reviewed, root-owned enabled-site/certbot symlinks are permitted;
    # their exact link/target identity and content are included in the snapshot.
    p = Path(path)
    require(p.is_absolute(), "ABSOLUTE_CONFIG_PATH_REQUIRED")
    links = []
    for q in [Path("/"), *reversed(p.parents[:-1]), p]:
        s = q.lstat()
        require(s.st_uid == 0, "UNTRUSTED_CONFIG_OWNER")
        if stat.S_ISLNK(s.st_mode):
            links.append((str(q), os.readlink(q), s.st_dev, s.st_ino))
        else:
            require(not s.st_mode & 0o022, "WRITABLE_CONFIG_PATH")
    real = p.resolve(strict=True)
    for q in [Path("/"), *reversed(real.parents[:-1])]:
        s = q.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and
                not s.st_mode & 0o022, "UNTRUSTED_CONFIG_TARGET_CHAIN")
    fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        s = os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and not s.st_mode & 0o022 and s.st_size <= limit,
                "UNTRUSTED_CONFIG_FILE")
        b = os.read(fd, limit+1)
        t = os.fstat(fd)
        require((s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns) ==
                (t.st_dev,t.st_ino,t.st_size,t.st_mtime_ns,t.st_ctime_ns),
                "CONFIG_CHANGED_DURING_READ")
    finally:
        os.close(fd)
    require(len(b) <= limit, "CONFIG_SIZE_BOUND")
    return b, (str(real), s.st_dev, s.st_ino, s.st_mode, s.st_uid,
               s.st_size, s.st_mtime_ns, s.st_ctime_ns, links,
               hashlib.sha256(b).hexdigest())

def configuration(state):
    # -T is an in-memory disk/configuration read, never a loaded-state receipt.
    raw = run(["/usr/sbin/nginx", "-T", "-q"], 10)
    markers = list(re.finditer(rb"(?m)^# configuration file (/[^\n]+):\n", raw))
    require(markers and len(markers) <= 128, "CONFIG_DUMP_AMBIGUOUS")
    require(markers[0].group(1)==b"/etc/nginx/nginx.conf", "NONCANONICAL_CONFIG_ROOT")
    files, snapshots = {}, {}
    for i, m in enumerate(markers):
        name = os.path.normpath(m.group(1).decode("utf-8"))
        require(name not in files, "DUPLICATE_CONFIG_DUMP_PATH")
        b, snap = trusted_file(name)
        dumped = raw[m.end():markers[i+1].start() if i+1<len(markers) else len(raw)]
        require(dumped in (b, b+b"\n"), "DUMP_FILE_BYTE_MISMATCH")
        files[name], snapshots[name] = b, snap
    require("/etc/nginx/nginx.conf" in files, "NONCANONICAL_NGINX_CONFIG")
    seen = set()
    def expand(name, chain=()):
        require(name in files and name not in chain and len(chain)<16,
                "INCLUDE_TREE_AMBIGUOUS")
        seen.add(name)
        def nodes(tree):
            out = []
            for d in tree:
                if d.name.value == "include":
                    require(d.block is None and len(d.args)==1, "INCLUDE_AMBIGUOUS")
                    pattern = args(d)[0]
                    require("$" not in pattern, "DYNAMIC_INCLUDE")
                    pattern = os.path.normpath(os.path.join("/etc/nginx", pattern))
                    matches = sorted(glob.glob(pattern))
                    require(matches or glob.has_magic(pattern), "MISSING_INCLUDE")
                    for x in matches:
                        out.extend(expand(os.path.normpath(x), chain+(name,)))
                else:
                    if d.block is not None:
                        d.block = nodes(d.block)
                    out.append(d)
            return out
        return nodes(parse_nginx(files[name]))
    tree = expand("/etc/nginx/nginx.conf")
    require(seen == set(files), "UNEXPLAINED_CONFIG_DUMP_FILE")
    allnodes = list(walk(tree))
    require(not any(d.name.value in ("stream","mail") for d,p in allnodes),
            "ALTERNATE_INGRESS_CONTEXT")
    require([args(d) for d,p in allnodes if d.name.value == "pid"] in
            ([], [["/run/nginx.pid"]]), "NONCANONICAL_PID_FILE")
    http = [d for d in tree if d.name.value=="http"]
    require(len(http)==1, "HTTP_CONTEXT_AMBIGUOUS")
    h = http[0]
    ups = [d for d in h.block if d.name.value=="upstream"]
    expected = {"axisai_web": BACKENDS[state], "fatsecret_proxy":"127.0.0.1:3000"}
    require(set(args(u)[0] for u in ups if len(args(u))==1)==set(expected)
            and len(ups)==2, "UNEXPECTED_UPSTREAM")
    for u in ups:
        require(len(args(u))==1 and len(u.block or [])==1
                and u.block[0].name.value=="server"
                and args(u.block[0])==[expected[args(u)[0]]], "NONCANONICAL_UPSTREAM")
    require(files.get("/etc/nginx/axisai/active-web-upstream.conf") ==
            f"server {BACKENDS[state]};\n".encode(), "EXPECTED_CANONICAL_ROUTE_ABSENT")
    require(not os.path.lexists("/run/axisai-web-route.pending"), "PENDING_ROUTE")
    servers = [d for d in h.block if d.name.value=="server"]
    require(servers and len(servers)<=32, "SERVER_COVERAGE_AMBIGUOUS")
    app_passes, listener_ports = 0, set()
    for d,p in allnodes:
        name = d.name.value
        require(not (name.endswith("_pass") and name!="proxy_pass") and
                "lua" not in name and not name.startswith(("js_","perl")),
                "UNREVIEWED_CONTENT_HANDLER")
        require(not any(re.search(r":(?:" + "|".join(str(x) for x in (5000,5001,5002) if str(x) != BACKENDS[state].rsplit(":",1)[1]) + r")(?:\D|$)",a) for a in args(d)),
                "ALTERNATE_APPLICATION_BACKEND")
        if name=="proxy_pass":
            containing = [v for v in p if v in servers]
            require(len(containing)==1 and len(args(d))==1, "PROXY_CONTEXT_AMBIGUOUS")
    for s in servers:
        listens = [args(d) for d in (s.block or []) if d.name.value=="listen"]
        require(listens, "IMPLICIT_LISTENER_AMBIGUOUS")
        fs = all(x[0]=="127.0.0.1:3000" for x in listens)
        for x in listens:
            require(not any(option in ("quic", "udp") for option in x[1:]),
                    "UNREVIEWED_UDP_INGRESS")
            token = x[0]
            require(re.fullmatch(r"(?:(?:\*|0\.0\.0\.0|127\.0\.0\.1|\[::\]|\[::1\]):)?(?:80|443|3000)",token),
                    "UNEXPECTED_LISTENER")
            port = int(token.rsplit(":",1)[-1])
            require(port!=3000 or fs, "PUBLIC_PROVIDER_PROXY")
            listener_ports.add(port)
        for d,p in walk(s.block or []):
            if d.name.value=="proxy_pass":
                value = args(d)[0]
                if fs:
                    require(value=="https://platform.fatsecret.com", "PROVIDER_ROUTE_DRIFT")
                else:
                    require(value in ("http://axisai_web", "http://fatsecret_proxy/rest/server.api"),
                            "NONCANONICAL_APP_PROXY")
                    if value=="http://axisai_web":
                        app_passes += 1
                    else:
                        require(any(v.name.value=="location" and args(v)==["=","/fatsecret/rest/server.api"]
                                    for v in p), "PROVIDER_PATH_NOT_EXACT")
            if d.name.value=="return":
                a = args(d)
                require(a in (["404"],["444"]) or (len(a)==2 and a[0] in
                    ("301","302","307","308") and a[1]=="https://$host$request_uri"),
                    "UNREVIEWED_REDIRECT")
    require(app_passes>=1 and {80,443}.issubset(listener_ports), "APP_INGRESS_MISSING")
    # Only the canonical upstream include may change in the route helper.
    # Bind bytes/link identities for every other config file across cutover.
    snapshots.pop("/etc/nginx/axisai/active-web-upstream.conf", None)
    digest = hashlib.sha256(json.dumps(snapshots, sort_keys=True).encode()).hexdigest()
    return digest, listener_ports

def nginx_processes():
    rows=[]
    for p in Path("/proc").iterdir():
        if not p.name.isdecimal():
            continue
        classified_nginx = False
        try:
            if p.joinpath("comm").read_text().strip()!="nginx":
                continue
            classified_nginx = True
            a=p.joinpath("stat").read_text().split(") ",1)[1].split()
            title=p.joinpath("cmdline").read_bytes().replace(b"\0",b" ").strip().decode()
            kind="master" if title.startswith("nginx: master process ") else (
                "retiring" if title=="nginx: worker process is shutting down" else (
                "worker" if title=="nginx: worker process" else "unknown"))
            require(a[0] not in ("Z","X") and kind!="unknown", "AMBIGUOUS_NGINX_PROCESS")
            rows.append({"pid":int(p.name),"ppid":int(a[1]),"start_ticks":int(a[19]),"kind":kind})
        except FileNotFoundError:
            # Unrelated processes can exit during /proc enumeration. An nginx
            # process that passed comm classification is an ambiguous lineage.
            if classified_nginx:
                raise Hold("NGINX_IDENTITY_RACE")
            continue
    masters=[x for x in rows if x["kind"]=="master"]
    require(len(masters)==1 and 1<=len(rows)-1<=64,"MASTER_WORKER_SET_AMBIGUOUS")
    master=masters[0]
    require(all(x["ppid"]==master["pid"] for x in rows if x is not master),"FOREIGN_NGINX_WORKER")
    require(any(x["kind"]=="worker" for x in rows), "NO_ACTIVE_NGINX_WORKER")
    return master, sorted([x for x in rows if x is not master],key=lambda x:x["pid"])

def identity(x):
    return x["pid"],x["start_ticks"]

def socket_coverage(master,workers,ports,application_ports=None):
    owned=set()
    for x in [master,*workers]:
        for f in Path('/proc',str(x['pid']),'fd').iterdir():
            try:
                target=os.readlink(f)
            except FileNotFoundError:
                # Accepted client sockets close normally during enumeration.
                # Missing listening ownership still fails the coverage below;
                # process lineage / directory / permission errors remain fatal.
                continue
            m=re.fullmatch(r"socket:\[([0-9]+)\]",target)
            if m: owned.add(int(m[1]))
    actual=set()
    for file,v6 in (("/proc/net/tcp",False),("/proc/net/tcp6",True)):
        for line in Path(file).read_text().splitlines()[1:]:
            a=line.split()
            if a[3]!="0A": continue
            host,port=a[1].split(':'); port=int(port,16); inode=int(a[9])
            b=bytes.fromhex(host)
            b=b''.join(b[i:i+4][::-1] for i in range(0,len(b),4))
            address=ipaddress.ip_address(b)
            if application_ports is not None:
                require(port not in {5000,5001,5002} - application_ports,
                        "OBSOLETE_APPLICATION_LISTENER")
            if inode in owned:
                require(port in ports,"UNEXPLAINED_NGINX_LISTENER")
                actual.add(port)
            elif not address.is_loopback:
                require(port==22,"ALTERNATE_PUBLIC_TCP_INGRESS")
    require(actual==ports,"LOADED_LISTENER_COVERAGE_MISMATCH")

def snapshot(state, candidate="any"):
    require(state in BACKENDS, "UNKNOWN_ROUTE")
    digest, ports = configuration(state)
    master, workers = nginx_processes()
    accepted = {"any": None, "empty": set(), "blue": {5001}, "green": {5002}}
    require(candidate in accepted, "UNEXPECTED_CANDIDATE")
    socket_coverage(master, workers, ports, accepted[candidate])
    return {"configuration": digest, "master": identity(master),
            "workers": [identity(w) for w in workers]}


def main():
    try:
        require(os.geteuid() == 0, "ROOT_INSPECTION_REQUIRED")
        require(len(sys.argv) in (3,4), "EXACT_STATE_REQUIRED")
        if len(sys.argv) == 4:
            require(sys.argv[3] == "reload", "UNKNOWN_OPERATION")
            cfg = sw.Config()
            lock = sw._flock(cfg.lock_path, cfg)
            try:
                require(sw.do_status(cfg).fields.get("state") == sys.argv[1], "ROUTE_CHANGED")
                before = snapshot(sys.argv[1], sys.argv[2])
                run(["/usr/sbin/nginx", "-t", "-q"])
                run(["/usr/bin/systemctl", "reload", "nginx"], 30)
                after = snapshot(sys.argv[1], sys.argv[2])
                require(before["configuration"] == after["configuration"] and
                        before["master"] == after["master"], "RELOAD_DRIFT")
            finally:
                os.close(lock)
            print(json.dumps(after, sort_keys=True))
            return 0
        print(json.dumps(snapshot(sys.argv[1],sys.argv[2]), sort_keys=True))
        return 0
    except Exception:
        # Never emit configuration, paths, secret-bearing argv or raw stderr.
        print(json.dumps({"error": "INGRESS_OR_GENERATION_UNPROVEN"}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
