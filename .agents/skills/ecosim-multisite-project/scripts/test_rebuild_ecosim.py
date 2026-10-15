#!/usr/bin/env python3
"""Tests for the project-start EcoSIM rebuild helper (no build is run)."""

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rebuild_ecosim as rb


class SettingsTests(unittest.TestCase):
    def test_jsonc_comments_and_trailing_commas(self):
        text = '{\n // note\n "a": "http://x", /* block */ "b": [1, 2,],\n}'
        self.assertEqual(json.loads(rb.strip_jsonc(text)), {"a": "http://x", "b": [1, 2]})

    def test_workspace_overrides_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            user, ws = Path(tmp, "user.json"), Path(tmp, "ws.json")
            user.write_text('{"x": 1, "y": 1}')
            ws.write_text('{"y": 2}')
            merged, used = rb.load_settings([user, ws, Path(tmp, "missing.json")])
            self.assertEqual(merged, {"x": 1, "y": 2})
            self.assertEqual(len(used), 2)

    def test_terminal_profile_and_env_expansion(self):
        settings = {
            "terminal.integrated.defaultProfile.osx": "bash",
            "terminal.integrated.profiles.osx": {"bash": {"path": "/bin/bash", "args": ["--noprofile", "--norc"]}},
            "terminal.integrated.env.osx": {"PATH": "/opt/homebrew/bin:${env:PATH}", "FC": "agf13"},
        }
        shell, env = rb.terminal_environment(settings, "osx", {"PATH": "/usr/bin"})
        self.assertEqual(shell, ["/bin/bash", "--noprofile", "--norc"])
        self.assertEqual(env["PATH"], "/opt/homebrew/bin:/usr/bin")
        self.assertEqual(env["FC"], "agf13")


class BuildDirTests(unittest.TestCase):
    def test_only_matching_toolchain_is_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, fc in (("a", "/usr/bin/gfortran-a"), ("b", "/usr/bin/gfortran-b")):
                (root / "build" / name).mkdir(parents=True)
                (root / "build" / name / "CMakeCache.txt").write_text(
                    f"CMAKE_C_COMPILER:STRING=/usr/bin/cc\nCMAKE_Fortran_COMPILER:STRING={fc}\n")
            found = rb.matching_build_dirs(root, os.path.realpath("/usr/bin/cc"), os.path.realpath("/usr/bin/gfortran-b"))
            self.assertEqual([p.name for p in found], ["b"])

    def test_stale_sources_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "f90src").mkdir()
            exe = root / rb.EXE_NAME
            exe.write_text("")
            old = time.time() - 100
            os.utime(exe, (old, old))
            (root / "f90src" / "Mod.F90").write_text("")
            self.assertEqual(rb.stale_sources(root, exe), ["f90src/Mod.F90"])

    def test_missing_cached_python_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp, "CMakeCache.txt")
            cache.write_text(f"ECOSIM_TIMING_PYTHON:FILEPATH=/nonexistent/python3\nOTHER_PYTHON:FILEPATH={sys.executable}\n")
            self.assertEqual(rb.missing_cached_python(cache), {"ECOSIM_TIMING_PYTHON": "/nonexistent/python3"})


if __name__ == "__main__":
    unittest.main()
