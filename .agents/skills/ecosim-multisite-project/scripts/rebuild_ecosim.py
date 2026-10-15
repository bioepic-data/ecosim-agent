#!/usr/bin/env python3
"""Rebuild EcoSIM at the start of a project, before any smoke run.

The build runs in the environment of VSCode's integrated terminal (repository
rule): the workspace ``.vscode/settings.json`` overrides the user settings, and
``terminal.integrated.defaultProfile.<os>``, ``profiles.<os>`` and ``env.<os>``
give the shell, its arguments, and the environment (``${env:VAR}`` expanded).
Without VSCode settings the default shell environment is used and reported.

The script reuses the configured ``build/<config>`` whose ``CMakeCache.txt``
compilers match that environment's ``CC``/``FC`` (never mixing toolchains),
runs ``cmake --build <dir> --target install --parallel <jobs>`` from the EcoSIM
root (the install step build_EcoSIM.sh uses, which refreshes ``bin/`` and
``local/bin/``, where smoke and full runs find the executable), and
passes only when the executable exists and no ``f90src/**/*.F90`` is newer
than it. With ``--configure`` and no matching build directory it runs
``bash build_EcoSIM.sh`` in the same environment; otherwise it stops.

The result (executable, build directory, compilers, shell, sources checked) is
printed as JSON and, with ``--record``, written to a file such as
``result/projects/<PROJECT>/ecosim_build.json``. Export the executable as
``ECOSIM_EXE`` so every smoke and full run of the project uses this build.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parents[3]  # ecosim-agent
ECOSIM_ROOT = PROJECT_ROOT.parent
EXE_NAME = "ecosim.f90.x"
VSCODE_OS = {"Darwin": "osx", "Linux": "linux", "Windows": "windows"}


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas outside strings (VSCode settings are JSONC)."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def user_settings_path() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Code" / "User" / "settings.json"
    if system == "Windows":
        return Path(os.environ.get("APPDATA", "")) / "Code" / "User" / "settings.json"
    return Path.home() / ".config" / "Code" / "User" / "settings.json"


def load_settings(paths: List[Path]) -> Tuple[Dict, List[str]]:
    """Merge settings files in order; later files (workspace) override earlier ones (user)."""
    merged, used = {}, []
    for path in paths:
        if path.is_file():
            merged.update(json.loads(strip_jsonc(path.read_text())))
            used.append(str(path))
    return merged, used


def expand_env(value: str, base: Dict[str, str]) -> str:
    return re.sub(r"\$\{env:([^}]+)\}", lambda m: base.get(m.group(1), ""), value)


def terminal_environment(settings: Dict, os_key: str, base: Dict[str, str]) -> Tuple[List[str], Dict[str, str]]:
    """Shell command prefix and environment of the VSCode default terminal profile."""
    env = dict(base)
    for key, value in (settings.get(f"terminal.integrated.env.{os_key}") or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = expand_env(str(value), base)
    profile_name = settings.get(f"terminal.integrated.defaultProfile.{os_key}")
    profile = (settings.get(f"terminal.integrated.profiles.{os_key}") or {}).get(profile_name or "", {})
    shell = [profile["path"], *profile.get("args", [])] if profile.get("path") else ["/bin/bash"]
    return shell, env


def resolve_compiler(name: Optional[str], env: Dict[str, str]) -> Optional[str]:
    if not name:
        return None
    found = shutil.which(name, path=env.get("PATH"))
    return os.path.realpath(found) if found else None


def cache_compilers(cache: Path) -> Dict[str, Optional[str]]:
    values = {}
    for line in cache.read_text(errors="replace").splitlines():
        for lang in ("C", "Fortran"):
            prefix = f"CMAKE_{lang}_COMPILER:"
            if line.startswith(prefix):
                values[lang] = os.path.realpath(line.split("=", 1)[1].strip())
    return values


def missing_cached_python(cache: Path) -> Dict[str, str]:
    """FILEPATH cache entries for Python interpreters that no longer exist (e.g. after a Homebrew upgrade)."""
    missing = {}
    for line in cache.read_text(errors="replace").splitlines():
        match = re.match(r"^([A-Za-z0-9_]*PYTHON[A-Za-z0-9_]*):FILEPATH=(.+)$", line)
        if match and not Path(match.group(2).strip()).exists():
            missing[match.group(1)] = match.group(2).strip()
    return missing


def matching_build_dirs(root: Path, cc: Optional[str], fc: Optional[str]) -> List[Path]:
    """Configured build directories whose cached compilers equal the environment's (newest first)."""
    matches = []
    for cache in root.glob("build/*/CMakeCache.txt"):
        cached = cache_compilers(cache)
        if (fc is None or cached.get("Fortran") == fc) and (cc is None or cached.get("C") == cc):
            matches.append(cache.parent)
    return sorted(matches, key=lambda p: (p / "CMakeCache.txt").stat().st_mtime, reverse=True)


def find_executable(build_dir: Path) -> Optional[Path]:
    candidates = [p for p in (build_dir / "local" / "bin" / EXE_NAME, build_dir / "bin" / EXE_NAME) if p.exists()]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


def stale_sources(root: Path, exe: Path) -> List[str]:
    built = exe.stat().st_mtime
    return sorted(str(p.relative_to(root)) for p in (root / "f90src").rglob("*.F90") if p.stat().st_mtime > built)


def run_in_shell(shell: List[str], command: str, env: Dict[str, str], log: Path) -> int:
    with log.open("w") as handle:
        return subprocess.run([*shell, "-c", command], cwd=ECOSIM_ROOT, env=env,
                              stdout=handle, stderr=subprocess.STDOUT).returncode


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build-dir", type=Path, help="Configured build directory to use (default: match compilers)")
    parser.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    parser.add_argument("--configure", action="store_true",
                        help="Run bash build_EcoSIM.sh when no build directory matches the toolchain")
    parser.add_argument("--record", type=Path, help="Write the build record JSON here")
    parser.add_argument("--log", type=Path, help="Build log (default: <record dir>/ecosim_build.log or the build dir)")
    args = parser.parse_args(argv)

    os_key = VSCODE_OS.get(platform.system(), "linux")
    settings, used = load_settings([user_settings_path(), ECOSIM_ROOT / ".vscode" / "settings.json"])
    shell, env = terminal_environment(settings, os_key, dict(os.environ))
    cc, fc = resolve_compiler(env.get("CC"), env), resolve_compiler(env.get("FC"), env)
    record = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "environment_source": used or "default shell (no VSCode settings found)",
              "shell": shell, "CC": cc, "FC": fc, "status": "failed"}

    def finish(code: int) -> int:
        text = json.dumps(record, indent=2)
        print(text)
        if args.record:
            args.record.parent.mkdir(parents=True, exist_ok=True)
            args.record.write_text(text + "\n")
        return code

    if args.build_dir:
        build_dir = args.build_dir.resolve()
        cached = cache_compilers(build_dir / "CMakeCache.txt") if (build_dir / "CMakeCache.txt").exists() else {}
        if (fc and cached.get("Fortran") != fc) or (cc and cached.get("C") != cc):
            record["error"] = f"{build_dir} was configured with {cached}, not the terminal toolchain CC={cc} FC={fc}"
            return finish(2)
    else:
        matches = matching_build_dirs(ECOSIM_ROOT, cc, fc)
        build_dir = matches[0] if matches else None
    log = args.log or ((args.record.parent / "ecosim_build.log") if args.record else None)

    start = time.time()
    if build_dir is None:
        if not args.configure:
            record["error"] = (f"no build/*/CMakeCache.txt matches CC={cc} FC={fc}; "
                               "rerun with --configure to run build_EcoSIM.sh in this environment")
            return finish(2)
        log = log or ECOSIM_ROOT / "build" / "ecosim_build.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        record["command"] = "bash build_EcoSIM.sh"
        record["returncode"] = run_in_shell(shell, record["command"], env, log)
        matches = matching_build_dirs(ECOSIM_ROOT, cc, fc)
        build_dir = matches[0] if matches else None
        if record["returncode"] != 0 or build_dir is None:
            record["log"] = str(log)
            record["error"] = "configure/build failed or produced no build directory with this toolchain"
            return finish(1)
    else:
        log = log or build_dir / "ecosim_build.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        command = f"cmake --build {build_dir} --target install --parallel {args.jobs}"
        missing = missing_cached_python(build_dir / "CMakeCache.txt")
        if missing:
            python = shutil.which("python3", path=env.get("PATH"))
            if python is None:
                record["error"] = f"cached interpreter(s) {missing} missing and no python3 on the terminal PATH"
                return finish(2)
            defines = " ".join(f"-D{name}={python}" for name in missing)
            command = f"cmake -S {ECOSIM_ROOT} -B {build_dir} {defines} && {command}"
            record["repointed_cache_entries"] = {name: {"old": old, "new": python} for name, old in missing.items()}
        record["command"] = command
        record["returncode"] = run_in_shell(shell, command, env, log)

    record.update({"build_dir": str(build_dir), "log": str(log), "wall_seconds": round(time.time() - start, 1)})
    if record["returncode"] != 0:
        record["error"] = "build failed; see log"
        return finish(1)
    exe = find_executable(build_dir)
    if exe is None:
        record["error"] = f"no {EXE_NAME} under {build_dir}"
        return finish(1)
    stale = stale_sources(ECOSIM_ROOT, exe)
    record.update({"executable": str(exe), "stale_sources": stale[:20]})
    if stale:
        record["error"] = f"{len(stale)} Fortran source(s) still newer than the executable"
        return finish(1)
    record["status"] = "passed"
    return finish(0)


if __name__ == "__main__":
    sys.exit(main())
