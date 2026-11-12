#!/usr/bin/env python3
"""The OpenSSH files the of-server image copies from its build stage, and the SBOM that names their packages.

The server runs only `ssh-keygen -Y verify` (deploy/Dockerfile). The image holds Debian's ssh-keygen and the libraries
it loads that the runtime image lacks, copied from a build stage where openssh-client is installed, and no OpenSSH
package. So that scanners still see what was copied, the build stage writes a CycloneDX SBOM from its own dpkg records,
never by hand: each copied file's package, exact version and source package, and the file's MD5 (equal to the line in
the package's dpkg file list, checked here) and SHA-256. CI proves the copies in the runtime image are those files and
that Trivy takes its packages as the image's own (.github/workflows/of-server-image.yml). Basis: 2026-11-12, a user
organization's security keeper accepted leaving the package out only on these two conditions (msg 01a1cf70), and
accepted the MD5 in dpkg's file list as the anchor, with SHA-256 from the build stage to the image (msg 01a1cf80).

  copied_sbom.py build --before SONAMES --out DIR BINARY   build stage: copy BINARY and each library it loads whose
                                                            soname is not in the file SONAMES under DIR, and write the
                                                            SBOM to DIR/usr/share/of-server/sbom/ssh-keygen.cdx.json
  copied_sbom.py verify --stage                            build stage image (CI): dpkg's own tools name each source
                                                            file's package, file list line and version, and they equal
                                                            the SBOM's
  copied_sbom.py verify --final                            runtime image (CI): each copied file's hashes equal the
                                                            SBOM's, and no OpenSSH package or other OpenSSH program
  copied_sbom.py seen --sbom FILE --report FILE            CI: Trivy's CycloneDX report of the image lists every
                                                            package the SBOM names, at its version

Both verify modes print the SBOM's SHA-256 last, so CI can check the image holds the build stage's SBOM unchanged.
One component per package; each file it gave is a property `of:file` whose value is
"<path in the image> from=<path in the build stage> md5=<hex> sha256=<hex>".
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import quote, unquote

DPKG_INFO = "/var/lib/dpkg/info"
DPKG_STATUS = "/var/lib/dpkg/status"
DEBIAN_VERSION = "/etc/debian_version"
SBOM = "usr/share/of-server/sbom/ssh-keygen.cdx.json"
ROOT_REF = "of-server-copied-openssh"
TRIVY = "aquasecurity:trivy:"
OPENSSH_PROGRAMS = ("ssh", "sshd", "scp", "sftp", "ssh-agent", "ssh-add", "ssh-keyscan")
OPENSSH_PACKAGES = ("openssh-client", "openssh-server", "openssh-sftp-server")


def fail(msg):
    raise SystemExit(f"copied_sbom: {msg}")


def read(path):
    with open(path, encoding="utf-8", errors="surrogateescape") as fh:
        return fh.read()


def digest(path, alg):
    h = hashlib.new(alg)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def file_lists(info=DPKG_INFO):
    """dpkg's file lists: absolute path as the package lists it -> (package, md5)."""
    out = {}
    for name in sorted(os.listdir(info)):
        if name.endswith(".md5sums"):
            pkg = name[:-len(".md5sums")].split(":")[0]
            with open(os.path.join(info, name), encoding="utf-8", errors="surrogateescape") as fh:
                for line in fh:
                    md5, _, rel = line.rstrip("\n").partition("  ")
                    if rel:
                        out["/" + rel] = (pkg, md5)
    return out


def listed_as(real):
    """The paths a package may list a file under: on merged /usr, /usr/lib/x is also /lib/x."""
    return [real] + ([real[4:]] if real.startswith("/usr/") else [])


def owner(real, lists):
    for path in listed_as(real):
        if path in lists:
            return lists[path]
    fail(f"no dpkg file list holds {real}")


def installed(status=DPKG_STATUS):
    """Installed packages from dpkg's status file: name -> its fields."""
    out = {}
    with open(status, encoding="utf-8") as fh:
        for stanza in fh.read().split("\n\n"):
            fields = {}
            for line in stanza.splitlines():
                if line[:1] not in (" ", "\t") and ":" in line:
                    key, _, value = line.partition(":")
                    fields[key] = value.strip()
            if fields.get("Status") == "install ok installed" and "Package" in fields:
                out[fields["Package"]] = fields
    return out


def split_version(version):
    """Debian version -> (epoch, upstream, revision)."""
    epoch, _, rest = version.partition(":") if ":" in version else ("", "", version)
    upstream, _, revision = rest.rpartition("-") if "-" in rest else (rest, "", "")
    return epoch, upstream, revision


def component(pkg, fields, distro, files):
    """The CycloneDX component of one Debian package, in the form Trivy writes and reads."""
    version, arch = fields["Version"], fields["Architecture"]
    m = re.match(r"(\S+)(?: \((\S+)\))?$", fields.get("Source", ""))
    src, src_version = (m.group(1), m.group(2) or version) if m else (pkg, version)
    epoch, upstream, revision = split_version(version)
    s_epoch, s_upstream, s_revision = split_version(src_version)
    purl_version = upstream + (f"-{revision}" if revision else "")
    qualifiers = f"arch={arch}&distro=debian-{distro}" + (f"&epoch={epoch}" if epoch else "")
    purl = f"pkg:deb/debian/{pkg}@{quote(purl_version, safe='.~')}?{qualifiers}"
    props = [{"name": TRIVY + "SrcName", "value": src}, {"name": TRIVY + "SrcVersion", "value": s_upstream}]
    if s_revision:
        props.append({"name": TRIVY + "SrcRelease", "value": s_revision})
    if s_epoch:
        props.append({"name": TRIVY + "SrcEpoch", "value": s_epoch})
    props += [{"name": "of:file", "value": f} for f in files]
    return {"bom-ref": purl, "type": "library", "name": pkg, "version": version, "purl": purl, "properties": props}


def sbom(items, lists, packages, distro):
    """ITEMS: (path in the build stage, path in the image). Each source must match its dpkg file list line."""
    files = {}
    for src, dest in items:
        pkg, md5 = owner(src, lists)
        if digest(src, "md5") != md5:
            fail(f"{src} differs from the dpkg file list of {pkg}")
        files.setdefault(pkg, []).append(f"{dest} from={src} md5={md5} sha256={digest(src, 'sha256')}")
    comps = [component(pkg, packages[pkg], distro, files[pkg]) for pkg in sorted(files)]
    return {
        "bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
        "metadata": {"component": {"bom-ref": ROOT_REF, "type": "application",
                                   "name": "of-server: files copied from Debian packages"}},
        "components": [{"bom-ref": "os", "type": "operating-system", "name": "debian", "version": distro}] + comps,
        "dependencies": [{"ref": ROOT_REF, "dependsOn": ["os"]},
                         {"ref": "os", "dependsOn": [c["bom-ref"] for c in comps]}]
                        + [{"ref": c["bom-ref"], "dependsOn": []} for c in comps],
    }


def ldd(binary):
    """(soname, path) of each library BINARY loads."""
    out = subprocess.run(["ldd", binary], capture_output=True, text=True, check=True).stdout
    libs = []
    for line in out.splitlines():
        if "not found" in line:
            fail(f"{binary}: {line.strip()}")
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "=>" and parts[2].startswith("/"):
            libs.append((parts[0], parts[2]))
    return libs


def plan(binary, libs, before):
    """(source, destination) for BINARY and each library whose soname the runtime image lacks. A destination is in its
    directory's real path: on Debian 13 /lib links to /usr/lib, and a COPY must never put a directory in its place."""
    real_dir = lambda path: os.path.realpath(os.path.dirname(path))
    items = [(os.path.realpath(binary), os.path.join(real_dir(binary), os.path.basename(binary)))]
    items += [(os.path.realpath(path), os.path.join(real_dir(path), name)) for name, path in libs if name not in before]
    return items


def parse_files(doc):
    """(package, version, dest, src, md5, sha256) for every file the SBOM names."""
    out = []
    for c in doc.get("components", []):
        for p in c.get("properties", []):
            if p.get("name") == "of:file":
                dest, *kv = p["value"].split(" ")
                f = dict(x.split("=", 1) for x in kv)
                out.append((c["name"], c["version"], dest, f["from"], f["md5"], f["sha256"]))
    if not out:
        fail("the SBOM names no file")
    return out


def dpkg(*args):
    return subprocess.run(["dpkg", *args], capture_output=True, text=True)


def dpkg_query(*args):
    return subprocess.run(["dpkg-query", *args], capture_output=True, text=True)


def verify_stage(path):
    """dpkg's own tools, not this file's readers, name each source's package, file list line and version."""
    for pkg, version, _dest, src, md5, sha256 in parse_files(json.loads(read(path))):
        lines = [ln for p in listed_as(src) for ln in dpkg("-S", p).stdout.splitlines()]
        if not any(ln.split(":")[0] == pkg for ln in lines):
            fail(f"dpkg -S does not name {pkg} for {src}")
        got = dpkg_query("-W", "-f", "${Version}", pkg).stdout
        if got != version:
            fail(f"{pkg} is {got} in the build stage, the SBOM says {version}")
        control = dpkg_query("--control-path", pkg, "md5sums").stdout.strip()
        rows = set(read(control).splitlines()) if control else set()
        if not any(f"{md5}  {p.lstrip('/')}" in rows for p in listed_as(src)):
            fail(f"{src}: md5 {md5} is not the line in {pkg}'s dpkg file list")
        if digest(src, "md5") != md5 or digest(src, "sha256") != sha256:
            fail(f"{src}: its hashes differ from the SBOM's")
        print(f"ok {src}: {pkg} {version}, dpkg md5 {md5}, sha256 {sha256}")


def other_openssh_programs():
    return [p for p in OPENSSH_PROGRAMS if shutil.which(p) or os.path.exists(f"/usr/sbin/{p}")]


def verify_final(path):
    for pkg, version, dest, _src, md5, sha256 in parse_files(json.loads(read(path))):
        if digest(dest, "md5") != md5 or digest(dest, "sha256") != sha256:
            fail(f"{dest}: its hashes differ from the SBOM's ({pkg} {version})")
        print(f"ok {dest}: {pkg} {version}, sha256 {sha256}")
    for pkg in OPENSSH_PACKAGES:
        if dpkg_query("-W", "-f", "${Status}", pkg).stdout == "install ok installed":
            fail(f"{pkg} is installed")
    found = other_openssh_programs()
    if found:
        fail(f"OpenSSH programs in the image besides ssh-keygen: {', '.join(found)}")
    print("ok no OpenSSH package; ssh-keygen is the only OpenSSH program")


def seen(sbom_path, report_path):
    """Every package the SBOM names is an OS package in Trivy's report of the image: a deb component at its version
    that the report's operating-system component depends on. Trivy matches Debian's advisories only to the packages
    of that component, so a deb listed elsewhere would be seen and never checked (organic-framework PR 57 review)."""
    report = json.loads(read(report_path))
    os_refs = {c.get("bom-ref") for c in report.get("components", []) if c.get("type") == "operating-system"}
    of_os = {r for d in report.get("dependencies", []) if d.get("ref") in os_refs for r in d.get("dependsOn") or []}
    have, listed = set(), set()
    for c in report.get("components", []):
        m = re.match(r"pkg:deb/[^/]+/([^@]+)@([^?#]+)(?:\?([^#]*))?", str(c.get("purl", "")))
        if m:
            q = dict(kv.split("=", 1) for kv in (m.group(3) or "").split("&") if "=" in kv)
            v = unquote(m.group(2))
            keys = {(m.group(1), c.get("version")), (m.group(1), f"{q['epoch']}:{v}" if q.get("epoch") else v)}
            listed |= keys
            if c.get("bom-ref") in of_os:
                have |= keys
    wanted = {(pkg, version) for pkg, version, *_ in parse_files(json.loads(read(sbom_path)))}
    missing = sorted(wanted - listed)
    if missing:
        fail("Trivy does not list " + ", ".join(f"{p} {v}" for p, v in missing))
    loose = sorted(wanted - have)
    if loose:
        fail("Trivy lists, but not as packages of the image's operating system (so no Debian advisory is matched to "
             "them): " + ", ".join(f"{p} {v}" for p, v in loose))
    for pkg, version in sorted(wanted):
        print(f"ok Trivy lists {pkg} {version} as a package of the image's operating system")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--before", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("binary")
    v = sub.add_parser("verify")
    v.add_argument("--stage", action="store_true")
    v.add_argument("--final", action="store_true")
    s = sub.add_parser("seen")
    s.add_argument("--sbom", required=True)
    s.add_argument("--report", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "build":
        before = set(read(a.before).split())
        items = plan(a.binary, ldd(a.binary), before)
        distro = read(DEBIAN_VERSION).strip()
        doc = sbom(items, file_lists(), installed(), distro)
        for src, dest in items:
            os.makedirs(os.path.dirname(a.out + dest), exist_ok=True)
            shutil.copy2(src, a.out + dest)
        out = os.path.join(a.out, SBOM)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.write("\n")
        for src, dest in items:
            print(f"copied {src} -> {dest}")
    elif a.cmd == "verify":
        if a.stage == a.final:
            fail("verify takes --stage or --final")
        path = os.path.join("/out" if a.stage else "/", SBOM)
        (verify_stage if a.stage else verify_final)(path)
        print("sbom sha256", digest(path, "sha256"))
    else:
        seen(a.sbom, a.report)


if __name__ == "__main__":
    sys.exit(main())
