"""Repository hygiene: the things that break for someone cloning it fresh.

The exec bit is the interesting one. git stores it, so a script committed as
644 reaches everyone as `sudo: ./install.sh: command not found`, which reads
like a missing file rather than a missing permission.
"""

import os
import re
import subprocess
import unittest
from pathlib import Path

from harness import REPO

SCRIPTS = sorted(REPO.glob("*.sh")) + sorted(REPO.glob("*/*.sh"))
EXECUTABLES = SCRIPTS + [REPO / "aorusctl"]


class TestFileModes(unittest.TestCase):

    def test_every_script_is_executable(self):
        for f in EXECUTABLES:
            with self.subTest(file=f.relative_to(REPO)):
                self.assertTrue(os.access(f, os.X_OK),
                                f"{f.name} is not executable; sudo reports that as "
                                f"'command not found'. Fix with: "
                                f"git update-index --chmod=+x {f.relative_to(REPO)}")

    def test_git_records_the_executable_bit(self):
        try:
            out = subprocess.run(["git", "ls-files", "-s"], cwd=REPO,
                                 capture_output=True, text=True, timeout=20)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self.skipTest("git not available")
        if out.returncode != 0:
            self.skipTest("not a git checkout")
        modes = {}
        for line in out.stdout.splitlines():
            mode, _, rest = line.partition(" ")
            modes[rest.split("\t", 1)[-1]] = mode
        for f in EXECUTABLES:
            rel = str(f.relative_to(REPO))
            if rel not in modes:
                continue
            with self.subTest(file=rel):
                self.assertEqual(modes[rel], "100755",
                                 f"{rel} is committed as {modes[rel]}, not 100755")


class TestScripts(unittest.TestCase):

    def test_every_script_has_a_bash_shebang(self):
        for f in SCRIPTS:
            with self.subTest(file=f.relative_to(REPO)):
                first = f.read_bytes().split(b"\n", 1)[0]
                self.assertTrue(first.startswith(b"#!"), "missing shebang")
                self.assertIn(b"bash", first, "these use bash features")

    def test_no_file_has_windows_line_endings(self):
        for f in EXECUTABLES + [REPO / "config.toml"]:
            with self.subTest(file=f.relative_to(REPO)):
                self.assertNotIn(b"\r\n", f.read_bytes(),
                                 "CRLF breaks the shebang on Linux")

    def test_no_tracked_text_file_has_windows_line_endings(self):
        # Same check across everything git tracks. Binary files contain \r\n by
        # coincidence, so skip them the way git does: a NUL byte in the first
        # 8000 means binary.
        try:
            out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO,
                                 capture_output=True, timeout=20)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            self.skipTest("git not available")
        if out.returncode != 0:
            self.skipTest("not a git checkout")
        for name in filter(None, out.stdout.decode().split("\0")):
            path = REPO / name
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if b"\0" in data[:8000]:
                continue
            with self.subTest(file=name):
                self.assertNotIn(b"\r\n", data, "CRLF breaks the shebang on Linux")

    def test_every_script_parses(self):
        for f in SCRIPTS:
            with self.subTest(file=f.relative_to(REPO)):
                r = subprocess.run(["bash", "-n", str(f)], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)

    def test_version_is_semver(self):
        from harness import load_module
        v = load_module().VERSION
        self.assertRegex(v, r"^\d+\.\d+\.\d+$", f"VERSION is {v!r}")

    def test_the_changelog_documents_the_current_version(self):
        from harness import load_module
        v = load_module().VERSION
        changelog = REPO / "CHANGELOG.md"
        if not changelog.exists():
            self.skipTest("no changelog")
        headings = re.findall(r"^## \[([^\]]+)\]", changelog.read_text(), re.M)
        self.assertTrue(headings, "no version headings in the changelog")
        self.assertEqual(headings[0], v,
                         f"VERSION is {v} but the newest changelog entry is "
                         f"{headings[0]}; releasing would tag the wrong notes")

    def test_the_tool_is_valid_python(self):
        import ast
        ast.parse((REPO / "aorusctl").read_text())

    def test_the_tool_imports_nothing_outside_the_standard_library(self):
        import ast
        tree = ast.parse((REPO / "aorusctl").read_text())
        stdlib = set(getattr(__import__("sys"), "stdlib_module_names", ()))
        if not stdlib:
            self.skipTest("needs Python 3.10+")
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module.split(".")[0]]
            for n in names:
                with self.subTest(module=n):
                    self.assertIn(n, stdlib,
                                  f"{n} is a third-party dependency; the tool is "
                                  f"meant to run on a stock Python")


class TestSystemdUnits(unittest.TestCase):

    UNITS = sorted((REPO / "systemd").glob("*.service"))

    def test_units_exist(self):
        self.assertTrue(self.UNITS)

    def test_units_have_the_required_sections(self):
        for u in self.UNITS:
            with self.subTest(unit=u.name):
                text = u.read_text()
                for section in ("[Unit]", "[Service]", "[Install]"):
                    self.assertIn(section, text)

    def test_units_only_run_the_installed_binary(self):
        for u in self.UNITS:
            with self.subTest(unit=u.name):
                for line in u.read_text().splitlines():
                    if line.startswith(("ExecStart", "ExecStop")):
                        _, _, cmd = line.partition("=")
                        self.assertTrue(cmd.strip().startswith("/usr/local/bin/aorusctl"),
                                        f"{line} does not point at the installed tool")

    def test_every_unit_subcommand_exists(self):
        from harness import load_module
        ac = load_module()
        parser = ac.build_parser()
        known = set()
        for action in parser._actions:
            if hasattr(action, "choices") and action.choices:
                known |= set(action.choices)
        for u in self.UNITS:
            for line in u.read_text().splitlines():
                if not line.startswith(("ExecStart", "ExecStop")):
                    continue
                parts = line.partition("=")[2].split()
                if len(parts) < 2:
                    continue
                with self.subTest(unit=u.name, cmd=parts[1]):
                    self.assertIn(parts[1], known,
                                  f"{u.name} runs `aorusctl {parts[1]}`, which is not "
                                  f"a subcommand")

    def test_the_web_unit_binds_to_loopback(self):
        web = REPO / "systemd/aorusctl-web.service"
        self.assertIn("--bind 127.0.0.1", web.read_text(),
                      "the dashboard must not be exposed to the network by default")


if __name__ == "__main__":
    unittest.main()
