"""`of secret`: a position's credentials through its organization's broker (docs/agent-secrets.md §5.3).

  of secret run <credential> [--env NAME | --file NAME] [--org ORG] -- <command …>
      Asks the broker for the value as the calling session (CLAUDE_CODE_SESSION_ID), which must be the registered
      holder of a position that lists or keeps the credential, and runs the command with the value in the
      environment variable NAME (default: the credential id in capitals), or with NAME holding the path of a
      temporary file (mode 600) that is removed when the command ends. The value is never printed. The exit code is
      the command's.
  of secret plan [--org ORG]
      For each position that lists or keeps a credential with a secret reference, the line that creates its
      service account with exactly the vaults its credentials need and writes the token into the broker's folder.
      The founder runs these lines in a plain terminal (his 1Password); nothing here runs op.
"""
import argparse
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import of_credentials  # noqa: E402
import of_server_secrets  # noqa: E402


class SecretError(Exception):
    pass


def _env_name(credential):
    return re.sub(r"[^A-Z0-9]+", "_", credential.upper()).strip("_") or "SECRET"


def _registry_docs(orgs_file=None):
    import yaml
    out = {}
    for org, path in of_credentials._registries(orgs_file):
        try:
            with open(path) as fh:
                out[org] = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            continue
    return out


def caller(org=None, orgs_file=None, session=None):
    """(org, position, session) of the calling session: the position whose registered holder it is."""
    session = session or os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session:
        raise SecretError("no session: run it from the agent session that holds the position")
    found = [(o, p["name"]) for o, doc in _registry_docs(orgs_file).items() if org in (None, o)
             for p in doc.get("positions") or [] if isinstance(p, dict) and p.get("name")
             and isinstance(p.get("holder"), dict) and p["holder"].get("session") == session]
    if not found:
        raise SecretError(f"session {session[:8]}… holds no position in the registries (holder.session)")
    if len(found) > 1:
        raise SecretError("this session holds positions in several organizations: give --org")
    return found[0][0], found[0][1], session


def _used(org, position, session, credential, vault, decision, reason):
    """of.secret_used (docs/events.md, of_platform 0337f12): the envelope carries org, position, session and the
    machine; the payload never a value nor an item reference. A local event: it ships with the rest."""
    try:
        import of_events
        of_events.emit("secret_used", {"credential": credential, "vault": vault, "decision": decision,
                                       "reason": reason, "person": None}, position=position, org=org, session=session)
    except Exception:  # noqa: BLE001 - a record that cannot be written never blocks or reveals anything
        pass


def run(credential, command, env_name=None, file_name=None, org=None, client=None, orgs_file=None, session=None):
    if not command:
        raise SecretError("give the command after --")
    org, position, session = caller(org, orgs_file, session)
    if client is None:
        import of_client
        import of_ship
        cfg = of_ship.config()
        client = of_client.Client(of_ship.org_servers(cfg).get(org) or cfg["server_url"])
    try:
        ans = client.use_secret(org, position, session, credential)
        value = ans["value"]
    except Exception as e:  # noqa: BLE001 - the broker's reason, never a value
        _used(org, position, session, credential, None, "deny", str(e)[:300])
        raise SecretError(f"the broker refused {credential} for {org}:{position}: {e}")
    _used(org, position, session, credential, ans.get("ref_vault"), "allow", "cached" if ans.get("cached") else "read")
    env = dict(os.environ)
    if file_name:
        d = tempfile.mkdtemp(prefix="of-secret-")
        path = os.path.join(d, "value")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(value)
        env[file_name] = path
        try:
            return subprocess.run(command, env=env).returncode
        finally:
            for p in (path, d):
                try:
                    os.remove(p) if p == path else os.rmdir(p)
                except OSError:
                    pass
    env[env_name or _env_name(credential)] = value
    return subprocess.run(command, env=env).returncode


def plan(org=None, orgs_file=None, tokens_dir=of_server_secrets.TOKENS_DIR):
    """[(org, position, the creation line)] and [notes] for the founder's terminal."""
    lines, notes = [], []
    for o, entry in sorted(of_credentials.load(orgs_file).items()):
        if org not in (None, o):
            continue
        creds, uses = entry.get("credentials") or {}, {}
        for pos, ids in (entry.get("positions") or {}).items():
            for cid in ids:
                uses.setdefault(pos, {}).setdefault(cid, "read")
        for cid, c in creds.items():
            if c.get("keeper"):
                uses.setdefault(c["keeper"], {})[cid] = "write"
        for pos in sorted(uses):
            vaults = {}
            for cid, how in uses[pos].items():
                m = of_server_secrets.REF_RE.match((creds.get(cid) or {}).get("ref") or "")
                if not m:
                    notes.append(f"{o}:{pos}: {cid} has no ref yet (still a file), so it needs no vault")
                    continue
                v = m.group(1)
                vaults[v] = "write" if "write" in (vaults.get(v), how) else "read"
            if not vaults:
                continue
            flags = " ".join(f"--vault {v}:read_items{',write_items' if how == 'write' else ''}"
                             for v, how in sorted(vaults.items()))
            target = f"{tokens_dir}/{o}/{pos}.token"
            lines.append((o, pos, f"umask 077; mkdir -p {tokens_dir}/{o} && op service-account create of-{o}-{pos} "
                                  f"{flags} --raw > {target}"))
    return lines, notes


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command = []
    if "--" in argv:
        i = argv.index("--")
        argv, command = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser(prog="of secret", description="A position's credentials through its broker.")
    ap.add_argument("action", choices=["run", "plan"])
    ap.add_argument("credential", nargs="?")
    ap.add_argument("--org")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--env", metavar="NAME", help="the variable that holds the value (default: the id in capitals)")
    g.add_argument("--file", metavar="NAME", help="the variable that holds the path of a temporary file with it")
    a = ap.parse_args(argv)
    try:
        if a.action == "run":
            if not a.credential:
                raise SecretError("give the credential id")
            return run(a.credential, command, a.env, a.file, a.org)
        lines, notes = plan(a.org)
        for o, pos, line in lines:
            print(f"# {o}:{pos}\n{line}")
        for n in notes:
            print(f"# {n}")
        if not lines:
            print("# no position uses a credential with a secret reference yet")
        return 0
    except SecretError as e:
        print(f"of secret: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
