#!/usr/bin/env python3
"""
Compose-file host-binding linter.

The May 2 outage was caused by `docker-compose.prod.yml` (Apollo9-only)
being run on Azure7. Apollo9-specific port bindings (`100.117.127.32:9201`
and `10.99.0.2:9201`) couldn't bind on Azure7 → ES restart-loop → 15h
mirror outage.

Convention enforced here:
  docker-compose.<host>.yml may only bind IPs that BELONG to <host>.

Host → allowed IP table is hardcoded below. If you add a new host's
compose file, add the host's IPs here too.

Usage:
    python scripts/validate_compose_hosts.py           # lint all compose files
    python scripts/validate_compose_hosts.py -f X.yml  # lint one file

Exit code 0 = clean, 1 = lint failure.

Wire into git pre-commit (see .git/hooks/pre-commit) so wrong-host bindings
can never reach a remote.
"""
import argparse
import re
import sys
from pathlib import Path

# Hard-coded host → allowed IP prefixes. IPs that may legitimately appear:
#   - 127.0.0.1 / localhost (any host)
#   - 0.0.0.0 (any host)
#   - host.docker.internal (any host)
#   - that host's LAN / Tailscale / WireGuard IPs
HOST_IPS = {
    "apollo9": [
        "100.117.127.32",   # Tailscale
        "10.99.0.2",        # WireGuard
        "192.168.1.99",     # LAN
    ],
    "azure7": [
        "100.71.230.11",    # Tailscale
        "10.99.0.3",        # WireGuard (if assigned)
        "192.168.1.42",     # LAN
        "192.168.20.1",     # alternate LAN seen in older configs
    ],
    "tark1": [
        "100.85.177.13",    # Tailscale
        "192.168.1.200",    # LAN
    ],
    "dev": [
        # local dev — only loopback / 0.0.0.0 / host.docker.internal allowed
    ],
}

# Always-OK literal addresses
GLOBAL_OK = {"127.0.0.1", "0.0.0.0", "localhost", "host.docker.internal"}

# Regex for IPv4 in a port binding "<ip>:<port>:<port>" inside a compose file.
PORT_BINDING_RE = re.compile(
    r'-\s*["\']?(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s*:\s*\d+\s*:\s*\d+',
)


def host_from_filename(path):
    """docker-compose.<host>.yml → <host>; otherwise None."""
    name = Path(path).name
    m = re.match(r"^docker-compose\.([a-z0-9_-]+)\.yml$", name)
    return m.group(1) if m else None


def lint(path):
    host = host_from_filename(path)
    if host is None:
        return []  # Not a host-specific compose file; skip
    if host not in HOST_IPS:
        return [f"{path}: filename targets unknown host '{host}' — add to HOST_IPS table in scripts/validate_compose_hosts.py"]

    allowed = set(HOST_IPS[host]) | GLOBAL_OK
    errors = []
    text = Path(path).read_text()
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in PORT_BINDING_RE.finditer(line):
            ip = m.group(1)
            if ip not in allowed:
                errors.append(
                    f"{path}:{lineno}: IP {ip} not allowed for host '{host}' "
                    f"(allowed: {', '.join(sorted(allowed))})"
                )
    return errors


def main():
    p = argparse.ArgumentParser()
    p.add_argument("-f", "--files", nargs="*",
                   help="explicit file list; default = all docker-compose.*.yml in repo root")
    args = p.parse_args()

    if args.files:
        files = [Path(f) for f in args.files]
    else:
        repo = Path(__file__).resolve().parents[1]
        files = sorted(repo.glob("docker-compose.*.yml"))

    all_errors = []
    for f in files:
        if not f.exists():
            print(f"warning: {f} not found", file=sys.stderr)
            continue
        all_errors.extend(lint(f))

    if all_errors:
        print("Compose host-binding lint FAILED:", file=sys.stderr)
        for e in all_errors:
            print(f"  {e}", file=sys.stderr)
        print(
            "\nIf the IP is legitimate, add it to HOST_IPS in "
            "scripts/validate_compose_hosts.py.",
            file=sys.stderr,
        )
        return 1
    print(f"Compose host-binding lint OK ({len(files)} file(s) checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
