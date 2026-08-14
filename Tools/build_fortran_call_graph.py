#!/usr/bin/env python3
"""Build a searchable static call graph for the EcoSIM Fortran executable."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


FORTRAN_SUFFIXES = {".f90", ".f", ".for", ".f95", ".f03", ".f08"}
DEF_RE = re.compile(r"\b(subroutine|function)\s+([a-z_]\w*)", re.IGNORECASE)
PROGRAM_RE = re.compile(r"^\s*program\s+([a-z_]\w*)", re.IGNORECASE)
MODULE_RE = re.compile(
    r"^\s*module\s+(?!procedure\b|subroutine\b|function\b)([a-z_]\w*)",
    re.IGNORECASE,
)
END_PROC_RE = re.compile(r"^\s*end\s*(subroutine|function|program)\b", re.IGNORECASE)
END_MODULE_RE = re.compile(r"^\s*end\s*module\b", re.IGNORECASE)
CALL_RE = re.compile(r"\bcall\s+([a-z_]\w*(?:\s*%\s*[a-z_]\w*)*)", re.IGNORECASE)
PAREN_NAME_RE = re.compile(r"\b([a-z_]\w*(?:\s*%\s*[a-z_]\w*)*)\s*\(", re.IGNORECASE)
USE_RE = re.compile(
    r"^\s*use(?:\s*,\s*(?:non_)?intrinsic\s*)?(?:\s*::\s*)?\s*([a-z_]\w*)(.*)$",
    re.IGNORECASE,
)
INTERFACE_RE = re.compile(r"^\s*interface\s+([a-z_]\w*)", re.IGNORECASE)
MODULE_PROCEDURE_RE = re.compile(r"^\s*module\s+procedure\s+(.+)$", re.IGNORECASE)
BINDING_RE = re.compile(
    r"^\s*procedure(?:\s*\([^)]*\))?(?:\s*,[^:]*)?\s*::\s*(.+)$",
    re.IGNORECASE,
)


@dataclass
class Statement:
    line: int
    text: str


@dataclass
class Procedure:
    id: str
    name: str
    kind: str
    module: str | None
    file: str
    absolute_file: str
    line: int
    end_line: int
    signature: str
    category: str
    statements: list[Statement] = field(default_factory=list, repr=False)
    use_aliases: dict[str, tuple[str, str]] = field(default_factory=dict, repr=False)
    use_modules: list[str] = field(default_factory=list, repr=False)

    @property
    def qualified_name(self) -> str:
        return f"{self.module}::{self.name}" if self.module else self.name


def strip_comment(line: str) -> str:
    quote = None
    output = []
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            output.append(char)
            if char == quote:
                if index + 1 < len(line) and line[index + 1] == quote:
                    output.append(line[index + 1])
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            quote = char
            output.append(char)
        elif char == "!":
            break
        else:
            output.append(char)
        index += 1
    return "".join(output)


def mask_strings(text: str) -> str:
    quote = None
    output = []
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            output.append(" ")
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    output.append(" ")
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            quote = char
            output.append(" ")
        else:
            output.append(char)
        index += 1
    return "".join(output)


def split_semicolons(text: str) -> list[str]:
    quote = None
    pieces = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                if index + 1 < len(text) and text[index + 1] == quote:
                    index += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == ";":
            pieces.append(text[start:index])
            start = index + 1
        index += 1
    pieces.append(text[start:])
    return [piece.strip() for piece in pieces if piece.strip()]


def logical_statements(path: Path) -> Iterable[Statement]:
    buffer = ""
    start_line = 0
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    for line_number, raw_line in enumerate(lines, start=1):
        if raw_line.lstrip().startswith("#"):
            continue
        clean = strip_comment(raw_line).rstrip()
        if not clean.strip():
            continue
        if not buffer:
            start_line = line_number
        continuation = clean.endswith("&")
        clean = clean[:-1] if continuation else clean
        if buffer and clean.lstrip().startswith("&"):
            clean = clean.lstrip()[1:]
        buffer = f"{buffer} {clean.strip()}".strip()
        if continuation:
            continue
        for piece in split_semicolons(buffer):
            yield Statement(start_line, piece)
        buffer = ""
    if buffer:
        for piece in split_semicolons(buffer):
            yield Statement(start_line, piece)


def source_files(source_root: Path, driver_root: Path) -> list[Path]:
    files = []
    for root in (source_root, driver_root):
        files.extend(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in FORTRAN_SUFFIXES
        )
    return sorted(set(files))


def relative_source_path(path: Path, source_root: Path, driver_root: Path) -> str:
    try:
        return f"f90src/{path.relative_to(source_root).as_posix()}"
    except ValueError:
        return f"drivers/ecosim/{path.relative_to(driver_root).as_posix()}"


def source_category(relative_file: str) -> str:
    parts = relative_file.split("/")
    return "Driver" if parts[0] == "drivers" else parts[1]


def parse_use(statement: str) -> tuple[str, dict[str, str]] | None:
    match = USE_RE.match(statement)
    if not match:
        return None
    module = match.group(1).lower()
    aliases: dict[str, str] = {}
    only_match = re.search(r"\bonly\s*:\s*(.*)$", match.group(2), re.IGNORECASE)
    if only_match:
        for item in only_match.group(1).split(","):
            item = item.strip()
            if not item:
                continue
            if "=>" in item:
                local, remote = [part.strip().lower() for part in item.split("=>", 1)]
            else:
                local = remote = item.lower()
            if re.fullmatch(r"[a-z_]\w*", local) and re.fullmatch(r"[a-z_]\w*", remote):
                aliases[local] = remote
    return module, aliases


def make_procedure_id(module: str | None, name: str, relative_file: str, line: int) -> str:
    return f"{module or 'external-scope'}::{name}@{relative_file}:{line}".lower()


def discover(paths: list[Path], source_root: Path, driver_root: Path):
    procedures: list[Procedure] = []
    module_aliases: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)
    module_wildcards: dict[str, list[str]] = defaultdict(list)
    generic_members: dict[str, list[str]] = defaultdict(list)
    bindings: dict[str, list[str]] = defaultdict(list)

    for path in paths:
        relative_file = relative_source_path(path, source_root, driver_root)
        current_module: str | None = None
        proc_stack: list[Procedure] = []
        interface_name: str | None = None
        statements = list(logical_statements(path))

        for statement in statements:
            text = statement.text
            lowered = mask_strings(text).lower().strip()
            if END_PROC_RE.match(lowered):
                if proc_stack:
                    proc_stack[-1].end_line = statement.line
                    proc_stack.pop()
                continue
            if END_MODULE_RE.match(lowered):
                current_module = None
                interface_name = None
                continue
            if re.match(r"^\s*end\s*interface\b", lowered):
                interface_name = None
                continue

            module_match = MODULE_RE.match(lowered)
            if module_match and not proc_stack:
                current_module = module_match.group(1).lower()
                continue

            interface_match = INTERFACE_RE.match(lowered)
            if interface_match and not proc_stack:
                interface_name = interface_match.group(1).lower()
                continue
            module_proc_match = MODULE_PROCEDURE_RE.match(lowered)
            if module_proc_match and interface_name and current_module:
                key = f"{current_module}::{interface_name}"
                generic_members[key].extend(
                    item.strip().lower()
                    for item in module_proc_match.group(1).split(",")
                    if re.fullmatch(r"[a-z_]\w*", item.strip(), re.IGNORECASE)
                )
                continue

            program_match = PROGRAM_RE.match(lowered)
            definition_match = None if lowered.startswith("end") else DEF_RE.search(lowered)
            if program_match or definition_match:
                if program_match:
                    kind, name = "program", program_match.group(1).lower()
                else:
                    kind, name = definition_match.group(1).lower(), definition_match.group(2).lower()
                procedure = Procedure(
                    id=make_procedure_id(current_module, name, relative_file, statement.line),
                    name=name,
                    kind=kind,
                    module=current_module,
                    file=relative_file,
                    absolute_file=str(path.resolve()),
                    line=statement.line,
                    end_line=statement.line,
                    signature=text.strip(),
                    category=source_category(relative_file),
                )
                procedures.append(procedure)
                proc_stack.append(procedure)
                continue

            use_info = parse_use(text)
            if use_info:
                used_module, aliases = use_info
                if proc_stack:
                    procedure = proc_stack[-1]
                    procedure.use_modules.append(used_module)
                    procedure.use_aliases.update(
                        {local: (used_module, remote) for local, remote in aliases.items()}
                    )
                elif current_module:
                    module_wildcards[current_module].append(used_module)
                    module_aliases[current_module].update(
                        {local: (used_module, remote) for local, remote in aliases.items()}
                    )

            binding_match = BINDING_RE.match(lowered)
            if binding_match and not proc_stack:
                for item in binding_match.group(1).split(","):
                    item = item.strip()
                    if not item:
                        continue
                    binding, implementation = (
                        [part.strip() for part in item.split("=>", 1)]
                        if "=>" in item
                        else (item, item)
                    )
                    if re.fullmatch(r"[a-z_]\w*", binding) and re.fullmatch(
                        r"[a-z_]\w*", implementation
                    ):
                        bindings[binding].append(implementation)

            if proc_stack:
                proc_stack[-1].statements.append(statement)

        last_line = statements[-1].line if statements else 1
        for procedure in proc_stack:
            procedure.end_line = last_line

    for procedure in procedures:
        if procedure.module:
            aliases = dict(module_aliases.get(procedure.module, {}))
            aliases.update(procedure.use_aliases)
            procedure.use_aliases = aliases
            procedure.use_modules = list(
                dict.fromkeys([*module_wildcards.get(procedure.module, []), *procedure.use_modules])
            )
        else:
            procedure.use_modules = list(dict.fromkeys(procedure.use_modules))
    return procedures, generic_members, bindings


def build_graph(procedures: list[Procedure], generic_members, bindings):
    by_name: dict[str, list[Procedure]] = defaultdict(list)
    by_module_name: dict[tuple[str, str], list[Procedure]] = defaultdict(list)
    function_names = set()
    for procedure in procedures:
        by_name[procedure.name].append(procedure)
        if procedure.module:
            by_module_name[(procedure.module, procedure.name)].append(procedure)
        if procedure.kind == "function":
            function_names.add(procedure.name)

    virtual_nodes: dict[str, dict] = {}

    def virtual(prefix: str, name: str, candidates=()):
        node_id = f"{prefix}::{name}".lower()
        node = virtual_nodes.setdefault(
            node_id,
            {
                "id": node_id,
                "name": name.lower(),
                "qualified_name": name.lower(),
                "kind": prefix,
                "module": None,
                "category": "External" if prefix == "external" else "Dispatch",
                "file": "",
                "absolute_file": "",
                "line": None,
                "end_line": None,
                "signature": "",
                "virtual": True,
                "candidates": [],
            },
        )
        node["candidates"] = list(
            dict.fromkeys([*node["candidates"], *(candidate.id for candidate in candidates)])
        )
        return node_id

    def resolve(procedure: Procedure, raw_target: str):
        target = "%".join(part.strip().lower() for part in raw_target.split("%"))
        if "%" in target:
            method = target.rsplit("%", 1)[1]
            candidates = [
                candidate
                for implementation in bindings.get(method, [])
                for candidate in by_name.get(implementation, [])
            ]
            candidates = list({candidate.id: candidate for candidate in candidates}.values())
            if len(candidates) == 1:
                return candidates[0].id, "type-bound"
            return virtual("dispatch", method, candidates), "type-bound-unresolved"

        alias = procedure.use_aliases.get(target)
        if alias:
            used_module, remote = alias
            candidates = by_module_name.get((used_module, remote), [])
            if len(candidates) == 1:
                return candidates[0].id, "use-associated"
            generic_key = f"{used_module}::{remote}"
            members = [
                candidate
                for member in generic_members.get(generic_key, [])
                for candidate in by_module_name.get((used_module, member), [])
            ]
            if members:
                return virtual("generic", generic_key, members), "generic"

        if procedure.module:
            local = by_module_name.get((procedure.module, target), [])
            if len(local) == 1:
                return local[0].id, "same-module"

        wildcard = [
            candidate
            for used_module in procedure.use_modules
            for candidate in by_module_name.get((used_module, target), [])
        ]
        wildcard = list({candidate.id: candidate for candidate in wildcard}.values())
        if len(wildcard) == 1:
            return wildcard[0].id, "use-associated"
        generic_candidates = [
            candidate
            for used_module in procedure.use_modules
            for member in generic_members.get(f"{used_module}::{target}", [])
            for candidate in by_module_name.get((used_module, member), [])
        ]
        generic_candidates = list(
            {candidate.id: candidate for candidate in generic_candidates}.values()
        )
        if generic_candidates:
            return virtual("generic", target, generic_candidates), "generic"

        candidates = by_name.get(target, [])
        if len(candidates) == 1:
            return candidates[0].id, "unique-name"
        if candidates:
            return virtual("ambiguous", target, candidates), "ambiguous"
        return virtual("external", target), "external"

    edges = []
    seen = set()
    for procedure in procedures:
        for statement in procedure.statements:
            code = mask_strings(statement.text)
            explicit_spans = []
            for match in CALL_RE.finditer(code):
                explicit_spans.append((match.start(1), match.end(1)))
                target_id, resolution = resolve(procedure, match.group(1))
                key = (procedure.id, target_id, procedure.file, statement.line, "subroutine")
                if key not in seen:
                    seen.add(key)
                    edges.append(
                        {
                            "source": procedure.id,
                            "target": target_id,
                            "kind": "subroutine",
                            "resolution": resolution,
                            "file": procedure.file,
                            "absolute_file": procedure.absolute_file,
                            "line": statement.line,
                            "statement": statement.text.strip(),
                        }
                    )

            for match in PAREN_NAME_RE.finditer(code):
                target = "%".join(part.strip().lower() for part in match.group(1).split("%"))
                method = target.rsplit("%", 1)[-1]
                if any(start <= match.start(1) < end for start, end in explicit_spans):
                    continue
                if "%" not in target and method not in function_names:
                    alias = procedure.use_aliases.get(method)
                    if not alias or not by_module_name.get(alias):
                        continue
                if "%" in target and method not in bindings:
                    continue
                target_id, resolution = resolve(procedure, match.group(1))
                key = (procedure.id, target_id, procedure.file, statement.line, "function")
                if key not in seen:
                    seen.add(key)
                    edges.append(
                        {
                            "source": procedure.id,
                            "target": target_id,
                            "kind": "function",
                            "resolution": resolution,
                            "file": procedure.file,
                            "absolute_file": procedure.absolute_file,
                            "line": statement.line,
                            "statement": statement.text.strip(),
                        }
                    )

    nodes = [
        {
            "id": procedure.id,
            "name": procedure.name,
            "qualified_name": procedure.qualified_name,
            "kind": procedure.kind,
            "module": procedure.module,
            "category": procedure.category,
            "file": procedure.file,
            "absolute_file": procedure.absolute_file,
            "line": procedure.line,
            "end_line": procedure.end_line,
            "signature": procedure.signature,
            "virtual": False,
            "candidates": [],
        }
        for procedure in procedures
    ]
    nodes.extend(virtual_nodes.values())
    return nodes, edges


def mark_reachable(nodes: list[dict], edges: list[dict]) -> list[str]:
    entrypoints = [node["id"] for node in nodes if node["kind"] == "program"]
    adjacency = defaultdict(list)
    for edge in edges:
        adjacency[edge["source"]].append(edge["target"])
    reachable = set(entrypoints)
    queue = deque(entrypoints)
    while queue:
        for target in adjacency.get(queue.popleft(), []):
            if target not in reachable:
                reachable.add(target)
                queue.append(target)
    for node in nodes:
        node["reachable_from_entry"] = node["id"] in reachable
    return entrypoints


def git_metadata(root: Path) -> dict:
    def run(*args: str):
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else None

    status = run("status", "--porcelain")
    return {
        "repository": str(root.resolve()),
        "commit": run("rev-parse", "HEAD"),
        "branch": run("branch", "--show-current"),
        "dirty": bool(status) if status is not None else None,
    }


def write_indexes(output_dir: Path, graph: dict) -> None:
    nodes, edges = graph["nodes"], graph["edges"]
    incoming, outgoing = defaultdict(set), defaultdict(set)
    for edge in edges:
        outgoing[edge["source"]].add(edge["target"])
        incoming[edge["target"]].add(edge["source"])

    fields = [
        "id", "name", "qualified_name", "kind", "module", "category", "file",
        "absolute_file", "line", "end_line", "signature", "virtual",
        "reachable_from_entry", "caller_count", "callee_count", "candidate_ids",
    ]
    with (output_dir / "procedures.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for node in nodes:
            row = {field: node.get(field) for field in fields}
            row["caller_count"] = len(incoming[node["id"]])
            row["callee_count"] = len(outgoing[node["id"]])
            row["candidate_ids"] = ";".join(node.get("candidates", []))
            writer.writerow(row)

    call_fields = [
        "source", "target", "kind", "resolution", "file", "absolute_file",
        "line", "statement",
    ]
    with (output_dir / "calls.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=call_fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(edges)


def write_dot(output_dir: Path, graph: dict) -> None:
    nodes, edges = graph["nodes"], graph["edges"]
    reachable = {node["id"] for node in nodes if node["reachable_from_entry"]}
    lines = [
        "digraph ecosim_call_graph {",
        "  rankdir=LR;",
        '  node [shape=box, style="rounded,filled", fillcolor="#e8f0f7"];',
    ]
    for node in nodes:
        if node["id"] not in reachable:
            continue
        label = node["qualified_name"].replace('"', '\\"')
        if node.get("line"):
            label += f"\\n{node['file']}:{node['line']}"
        shape = "ellipse" if node["virtual"] else "box"
        lines.append(f'  "{node["id"]}" [label="{label}", shape={shape}];')
    for edge in edges:
        if edge["source"] in reachable and edge["target"] in reachable:
            dashed = edge["resolution"] in {
                "ambiguous", "external", "generic", "type-bound-unresolved"
            }
            lines.append(
                f'  "{edge["source"]}" -> "{edge["target"]}" '
                f'[label="{edge["kind"]}", style={"dashed" if dashed else "solid"}];'
            )
    lines.append("}")
    (output_dir / "ecosim_call_graph.dot").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_readme(output_dir: Path, graph: dict) -> None:
    metadata = graph["metadata"]
    source_commit = metadata["git"].get("commit") or "unknown"
    source_branch = metadata["git"].get("branch") or "unknown"
    source_dirty = metadata["git"].get("dirty")
    if source_dirty is True:
        source_state = "dirty; the graph may include uncommitted source changes"
    elif source_dirty is False:
        source_state = "clean"
    else:
        source_state = "unknown"
    text = f"""# EcoSIM searchable calling sequence

Open `ecosim_call_graph.html` in a browser. Search by procedure, module, subsystem, or source path. Selecting a node shows its definition, callers, callees, call sites, and possible dispatch targets.

- tracked EcoSIM source commit: `{source_commit}`
- source branch: `{source_branch}`
- source worktree state: `{source_state}`
- internal procedures: `{metadata['internal_procedures']}`
- call edges: `{metadata['call_edges']}`
- source files: `{metadata['source_files']}`

## Tracked EcoSIM revision

This calling graph was generated from EcoSIM commit `{source_commit}` on branch `{source_branch}`. The source worktree was `{source_state}` when indexed. When the state is dirty, the commit identifies the baseline revision, but the graph can also reflect local source edits that are not contained in that commit.

## Top-level execution sequence

```mermaid
flowchart TD
    MAIN[main] --> SETUP[Namelist, mesh, module and input initialization]
    SETUP --> YEAR[AdvanceModelOneYear]
    YEAR --> YINIT[Annual forcing, plant traits, restart and state initialization]
    YINIT --> DAY[DAY: daily management and accumulators]
    DAY --> WEATHER[PrepHourlyWeather]
    WEATHER --> STEP[Run_EcoSIM_one_step]
    STEP --> HOUR1[HOUR1: surface energy and water]
    HOUR1 --> WATSUB[WATSUB: soil water and heat]
    WATSUB --> MIC[MicrobeModel, conditional]
    MIC --> PLANT[PlantModel, conditional]
    PLANT --> CHEM[soluteModel, conditional]
    CHEM --> TRANSPORT[TranspNoSalt and optional TranspSalt]
    TRANSPORT --> EROSION[EROSION]
    EROSION --> REDIST[REDIST: update soil states]
    REDIST --> BALANCE[Diagnostics and balance checks]
    BALANCE --> HISTORY[History buffers, restart and clock update]
    HISTORY --> DAY
    YEAR --> FINAL[Regression test and DestructEcoSIM]
```

The executable entry is `drivers/ecosim/ecosim.F90:1`. The annual loop is in `drivers/ecosim/EcoSIMAPI.F90:326`, and the ordered process step is in `drivers/ecosim/EcoSIMAPI.F90:36`.

Machine-readable indexes are `ecosim_call_graph.json`, `procedures.csv`, and `calls.csv`. `ecosim_call_graph.dot` contains the entry-point-reachable graph for Graphviz-compatible tools.

Rebuild or search from the repository root:

```bash
python3 Tools/build_fortran_call_graph.py \\
  --source-root /Users/jinyuntang/work/github/ecosim_workspace/main/f90src \\
  --driver-root /Users/jinyuntang/work/github/ecosim_workspace/main/drivers/ecosim \\
  --output-dir code_analysis/ecosim_call_graph

python3 Tools/build_fortran_call_graph.py \\
  --source-root /Users/jinyuntang/work/github/ecosim_workspace/main/f90src \\
  --driver-root /Users/jinyuntang/work/github/ecosim_workspace/main/drivers/ecosim \\
  --output-dir code_analysis/ecosim_call_graph --query PlantModel
```

This is static analysis. Runtime flags, preprocessing, generic interfaces, procedure pointers, and type-bound dispatch can change the executed sequence. Unresolved calls remain marked instead of being silently assigned.
"""
    (output_dir / "README.md").write_text(text, encoding="utf-8")


def write_html(output_dir: Path, graph: dict) -> None:
    template_path = Path(__file__).with_name("ecosim_call_graph_template.html")
    template = template_path.read_text(encoding="utf-8")
    payload = json.dumps(graph, separators=(",", ":")).replace("</", "<\\/")
    (output_dir / "ecosim_call_graph.html").write_text(
        template.replace("__GRAPH_JSON__", payload, 1), encoding="utf-8"
    )


def print_query(graph: dict, query: str) -> None:
    lowered = query.lower()
    node_map = {node["id"]: node for node in graph["nodes"]}
    outgoing = defaultdict(list)
    incoming = defaultdict(list)
    for edge in graph["edges"]:
        outgoing[edge["source"]].append(edge)
        incoming[edge["target"]].append(edge)
    matches = [
        node
        for node in graph["nodes"]
        if lowered
        in " ".join(
            str(node.get(key, ""))
            for key in ("name", "qualified_name", "module", "category", "file", "signature")
        ).lower()
    ]
    matches.sort(key=lambda node: (node["name"] != lowered, node["qualified_name"]))
    for node in matches[:30]:
        print(f"{node['qualified_name']} [{node['kind']}]")
        print(f"  definition: {node.get('absolute_file') or '<virtual>'}:{node.get('line') or ''}")
        print(f"  callers: {len(incoming[node['id']])}; callees: {len(outgoing[node['id']])}")
        for edge in outgoing[node["id"]][:12]:
            target = node_map[edge["target"]]
            print(
                f"    -> {target['qualified_name']} "
                f"({edge['kind']}, {edge['file']}:{edge['line']}, {edge['resolution']})"
            )
        print()
    if not matches:
        print(f"No call-graph entries match {query!r}.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--driver-root", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("code_analysis/ecosim_call_graph"),
        help="Knowledge-base output directory (default: code_analysis/ecosim_call_graph)",
    )
    parser.add_argument("--query", help="Print matching definitions and direct callees")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_root, driver_root = args.source_root.resolve(), args.driver_root.resolve()
    if not source_root.is_dir() or not driver_root.is_dir():
        raise SystemExit("Both source roots must be existing directories.")
    paths = source_files(source_root, driver_root)
    procedures, generic_members, bindings = discover(paths, source_root, driver_root)
    nodes, edges = build_graph(procedures, generic_members, bindings)
    entrypoints = mark_reachable(nodes, edges)
    common_root = Path(os.path.commonpath([source_root, driver_root]))
    metadata = {
        "source_root": str(source_root),
        "driver_root": str(driver_root),
        "source_files": len(paths),
        "internal_procedures": sum(not node["virtual"] for node in nodes),
        "virtual_nodes": sum(node["virtual"] for node in nodes),
        "call_edges": len(edges),
        "git": git_metadata(common_root),
    }
    graph = {"metadata": metadata, "entrypoints": entrypoints, "nodes": nodes, "edges": edges}
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "ecosim_call_graph.json").write_text(
        json.dumps(graph, indent=2) + "\n", encoding="utf-8"
    )
    write_indexes(output_dir, graph)
    write_dot(output_dir, graph)
    write_readme(output_dir, graph)
    write_html(output_dir, graph)
    if args.query:
        print_query(graph, args.query)
    print(
        f"Indexed {metadata['internal_procedures']} procedures and {len(edges)} calls "
        f"from {len(paths)} files into {output_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
