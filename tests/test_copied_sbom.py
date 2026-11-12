"""The SBOM of the files the of-server image copies from Debian packages (deploy/copied_sbom.py): written from dpkg's
own records, never by hand, refused when a file differs from its package's file list, and in the form Trivy reads. No
dpkg, docker or Trivy runs here: dpkg's records are files in a temporary folder."""

import hashlib
import json
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "deploy"))
import copied_sbom  # noqa: E402

STATUS = """Package: openssh-client
Status: install ok installed
Architecture: amd64
Source: openssh
Version: 1:10.0p1-7+deb13u4
Description: secure shell (SSH) client
 a continuation line: with a colon

Package: libfido2-1
Status: install ok installed
Architecture: amd64
Source: libfido2 (1.15.0-1)
Version: 1.15.0-1+b1

Package: openssh-server
Status: deinstall ok config-files
Architecture: amd64
Version: 1:10.0p1-7+deb13u4
"""


def md5(path):
    return hashlib.md5(open(path, "rb").read()).hexdigest()


class SbomTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        self.keygen = self.put("usr/bin/ssh-keygen", b"keygen")
        self.fido = self.put("usr/lib/x86_64-linux-gnu/libfido2.so.1.15.0", b"fido")
        info = os.path.join(self.root, "info")
        os.makedirs(info)
        for pkg, path in (("openssh-client", self.keygen), ("libfido2-1:amd64", self.fido)):
            with open(os.path.join(info, f"{pkg}.md5sums"), "w") as fh:
                fh.write(f"{md5(path)}  {path.lstrip('/')}\n0123  usr/share/doc/{pkg}/copyright\n")
        status = os.path.join(self.root, "status")
        with open(status, "w") as fh:
            fh.write(STATUS)
        self.lists, self.packages = copied_sbom.file_lists(info), copied_sbom.installed(status)
        self.items = [(self.keygen, "/usr/bin/ssh-keygen"), (self.fido, "/usr/lib/x86_64-linux-gnu/libfido2.so.1")]

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, rel, data):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)
        return path

    def test_dpkg_status_keeps_installed_packages_only(self):
        self.assertEqual(sorted(self.packages), ["libfido2-1", "openssh-client"])
        self.assertEqual(self.packages["openssh-client"]["Version"], "1:10.0p1-7+deb13u4")

    def test_each_package_is_a_component_trivy_reads(self):
        doc = copied_sbom.sbom(self.items, self.lists, self.packages, "13.7")
        comps = {c["name"]: c for c in doc["components"]}
        self.assertEqual(comps["debian"], {"bom-ref": "os", "type": "operating-system", "name": "debian",
                                           "version": "13.7"})
        ssh = comps["openssh-client"]
        self.assertEqual(ssh["version"], "1:10.0p1-7+deb13u4")
        self.assertEqual(ssh["purl"], "pkg:deb/debian/openssh-client@10.0p1-7%2Bdeb13u4?arch=amd64&distro=debian-13.7"
                                      "&epoch=1")
        props = {p["name"]: p["value"] for p in ssh["properties"]}
        self.assertEqual((props["aquasecurity:trivy:SrcName"], props["aquasecurity:trivy:SrcVersion"],
                          props["aquasecurity:trivy:SrcRelease"], props["aquasecurity:trivy:SrcEpoch"]),
                         ("openssh", "10.0p1", "7+deb13u4", "1"))
        sha = hashlib.sha256(b"keygen").hexdigest()
        self.assertEqual(props["of:file"], f"/usr/bin/ssh-keygen from={self.keygen} md5={md5(self.keygen)} sha256={sha}")
        fido = {p["name"]: p["value"] for p in comps["libfido2-1"]["properties"]}
        self.assertEqual((fido["aquasecurity:trivy:SrcName"], fido["aquasecurity:trivy:SrcRelease"]), ("libfido2", "1"))
        self.assertNotIn("aquasecurity:trivy:SrcEpoch", fido)
        deps = {d["ref"]: d["dependsOn"] for d in doc["dependencies"]}
        self.assertEqual(sorted(deps["os"]), sorted([ssh["bom-ref"], comps["libfido2-1"]["bom-ref"]]))
        self.assertEqual(deps[copied_sbom.ROOT_REF], ["os"])
        self.assertEqual([f[0] for f in copied_sbom.parse_files(doc)], ["libfido2-1", "openssh-client"])

    def test_a_file_that_differs_from_its_dpkg_file_list_is_refused(self):
        with open(self.keygen, "ab") as fh:
            fh.write(b"patched")
        with self.assertRaises(SystemExit) as e:
            copied_sbom.sbom(self.items, self.lists, self.packages, "13.7")
        self.assertIn("differs from the dpkg file list of openssh-client", str(e.exception))

    def test_a_file_no_package_lists_is_refused(self):
        stray = self.put("usr/lib/libstray.so.1", b"x")
        with self.assertRaises(SystemExit):
            copied_sbom.sbom([(stray, "/usr/lib/libstray.so.1")], self.lists, self.packages, "13.7")

    def test_merged_usr_a_package_may_list_a_library_under_lib(self):
        self.assertEqual(copied_sbom.listed_as("/usr/lib/x/libz.so.1"), ["/usr/lib/x/libz.so.1", "/lib/x/libz.so.1"])
        self.assertEqual(copied_sbom.owner("/usr/lib/x/libz.so.1", {"/lib/x/libz.so.1": ("zlib1g", "ab")}),
                         ("zlib1g", "ab"))

    def test_plan_copies_missing_sonames_into_real_directories(self):
        real = self.put("usr/lib/libfoo.so.1.2", b"foo")
        os.symlink("libfoo.so.1.2", os.path.join(self.root, "usr/lib/libfoo.so.1"))
        os.symlink("usr/lib", os.path.join(self.root, "lib"))     # Debian 13: /lib is a link to /usr/lib
        libs = [("libfoo.so.1", os.path.join(self.root, "lib/libfoo.so.1")), ("libc.so.6", "/lib/libc.so.6")]
        items = copied_sbom.plan(self.keygen, libs, {"libc.so.6"})
        self.assertEqual(items, [(self.keygen, self.keygen),
                                 (real, os.path.join(self.root, "usr/lib/libfoo.so.1"))])

    def sbom_file(self, items):
        path = os.path.join(self.root, "sbom.cdx.json")
        with open(path, "w") as fh:
            json.dump(copied_sbom.sbom(items, self.lists, self.packages, "13.7"), fh)
        return path

    def test_the_runtime_image_check(self):
        path = self.sbom_file([(self.keygen, self.keygen)])
        not_installed = mock.patch.object(copied_sbom, "dpkg_query", return_value=types.SimpleNamespace(stdout=""))
        no_programs = mock.patch.object(copied_sbom, "other_openssh_programs", return_value=[])
        with not_installed, no_programs:
            copied_sbom.verify_final(path)
            with open(self.keygen, "ab") as fh:
                fh.write(b"swapped")
            with self.assertRaises(SystemExit):
                copied_sbom.verify_final(path)
        path = self.sbom_file([(self.fido, self.fido)])
        installed = mock.patch.object(copied_sbom, "dpkg_query",
                                      return_value=types.SimpleNamespace(stdout="install ok installed"))
        with installed, no_programs, self.assertRaises(SystemExit) as e:
            copied_sbom.verify_final(path)
        self.assertIn("openssh-client is installed", str(e.exception))
        with not_installed, mock.patch.object(copied_sbom, "other_openssh_programs", return_value=["ssh"]):
            with self.assertRaises(SystemExit):
                copied_sbom.verify_final(path)

    def test_trivy_must_list_every_package(self):
        path = self.sbom_file(self.items)
        report = os.path.join(self.root, "trivy.cdx.json")

        def trivy(components, of_os=None):
            os_comp = {"bom-ref": "os-1", "type": "operating-system", "name": "debian", "version": "13.7"}
            refs = [c["bom-ref"] for c in components] if of_os is None else of_os
            with open(report, "w") as fh:
                json.dump({"bomFormat": "CycloneDX", "components": [os_comp] + components,
                           "dependencies": [{"ref": "os-1", "dependsOn": refs}]}, fh)

        ssh = {"bom-ref": "r-ssh", "name": "openssh-client", "version": "10.0p1-7+deb13u4",
               "purl": "pkg:deb/debian/openssh-client@10.0p1-7%2Bdeb13u4?arch=amd64&distro=debian-13.7&epoch=1"}
        fido = {"bom-ref": "r-fido", "name": "libfido2-1", "version": "1.15.0-1+b1",
                "purl": "pkg:deb/debian/libfido2-1@1.15.0-1%2Bb1?arch=amd64&distro=debian-13.7"}
        trivy([ssh, fido])
        copied_sbom.seen(path, report)
        trivy([ssh, fido], of_os=["r-fido"])   # listed, but not under the operating system: no advisory matched
        with self.assertRaises(SystemExit) as e:
            copied_sbom.seen(path, report)
        self.assertIn("not as packages of the image's operating system", str(e.exception))
        trivy([fido, dict(ssh, purl=ssh["purl"].replace("&epoch=1", ""))])   # the version without its epoch
        with self.assertRaises(SystemExit) as e:
            copied_sbom.seen(path, report)
        self.assertIn("openssh-client 1:10.0p1-7+deb13u4", str(e.exception))
        trivy([fido])
        with self.assertRaises(SystemExit):
            copied_sbom.seen(path, report)


if __name__ == "__main__":
    unittest.main()
