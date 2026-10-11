#!/usr/bin/env python3
"""Deterministic test-coverage signals for a Python repo.

Reads a coverage.json produced by coverage.py (for example through
`pytest --cov --cov-report=json`) and the repo's source and test files, and
reports what the line percentage alone doesn't show:

- per-file line and branch coverage, with deltas against a saved baseline
- HTTP endpoints (FastAPI, Starlette, Flask, Django, Litestar) and whether
  each one is exercised and referenced by a test
- ghost tests: tests that assert nothing, are trivially true, are empty,
  are skipped, or are shadowed by a later test with the same name
- test files pytest won't collect
- source modules no test reaches, even transitively through imports
- quality gates with a pass, fail, skipped, or info status

Standard library only. Python 3.10 or later.

Usage:
    python3 coverage_signals.py --repo . --coverage coverage.json \
        [--baseline .ai/coverage/baseline.json] [--pytest-exit-code N] \
        [--json signals.json] [--write-baseline .ai/coverage/baseline.json]

Prints a Markdown report to stdout. Exit code is 0 when every gate passes
or is skipped, 1 when any gate fails.
"""

from __future__ import annotations

import argparse
import ast
import datetime as _dt
import json
import re
import sys
from pathlib import Path
from typing import Any

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
ROUTE_ATTRS = HTTP_METHODS | {"route", "api_route", "websocket"}

DEFAULTS: dict[str, Any] = {
    "source": None,            # list of source dirs; None means read coverage.json
    "tests": None,             # list of test dirs; None means pytest testpaths or tests/
    "floor": None,             # overall coverage floor in percent; None skips the gate
    "new_file_floor": 50.0,    # minimum coverage for a new or never-executed file
    "max_overall_regression": 0.5,
    "max_file_regression": 1.0,
    "baseline": ".ai/coverage/baseline.json",
    "report": ".ai/latest-coverage-report.md",
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _parse_toml_subset(text: str) -> dict[str, dict[str, Any]]:
    """Parse the flat subset of TOML that project configs use.

    Handles [section] and [a.b.c] headers, and key = value lines where the
    value is a string, number, boolean, or a one-line array of those. Enough
    for coverage, pytest, and this skill's own section without a TOML
    library on Python 3.10.
    """
    sections: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None

    def scalar(raw: str) -> Any:
        raw = raw.strip()
        if raw.startswith(('"', "'")) and raw.endswith(raw[0]) and len(raw) >= 2:
            return raw[1:-1]
        if raw in ("true", "false"):
            return raw == "true"
        try:
            return int(raw)
        except ValueError:
            pass
        try:
            return float(raw)
        except ValueError:
            return raw

    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip() if not line.strip().startswith(("\"", "'")) else line.strip()
        if not stripped:
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            name = stripped[1:-1].strip().strip('"')
            current = sections.setdefault(name, {})
            continue
        if current is None or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        key = key.strip().strip('"')
        raw = raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            inner = raw[1:-1].strip()
            items = [scalar(x) for x in re.split(r",\s*", inner) if x.strip()] if inner else []
            current[key] = items
        else:
            current[key] = scalar(raw)
    return sections


def _read_pyproject(repo: Path) -> dict[str, dict[str, Any]]:
    path = repo / "pyproject.toml"
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        import tomllib  # type: ignore[import-not-found]  # Python 3.11+

        data = tomllib.loads(text)
        flat: dict[str, dict[str, Any]] = {}

        def walk(prefix: str, node: dict[str, Any]) -> None:
            leaves = {k: v for k, v in node.items() if not isinstance(v, dict)}
            if leaves:
                flat[prefix] = leaves
            for k, v in node.items():
                if isinstance(v, dict):
                    walk(f"{prefix}.{k}" if prefix else k, v)

        walk("", data)
        return flat
    except ImportError:
        return _parse_toml_subset(text)


def load_config(repo: Path) -> dict[str, Any]:
    """Config = defaults, then pyproject sections, then [tool.pre-pr-test-coverage]."""
    cfg = dict(DEFAULTS)
    sections = _read_pyproject(repo)
    cov_run = sections.get("tool.coverage.run", {})
    if isinstance(cov_run.get("source"), list):
        cfg["source"] = [str(s) for s in cov_run["source"]]
    pytest_opts = sections.get("tool.pytest.ini_options", {})
    if isinstance(pytest_opts.get("testpaths"), list):
        cfg["tests"] = [str(s) for s in pytest_opts["testpaths"]]
    own = sections.get("tool.pre-pr-test-coverage", {})
    for key, value in own.items():
        cfg[key] = value
    codecov = repo / "codecov.yml"
    if cfg.get("floor") is None and codecov.exists():
        m = re.search(r"^\s*target:\s*([0-9.]+)%?", codecov.read_text(encoding="utf-8"), re.M)
        if m:
            cfg["floor"] = float(m.group(1))
    return cfg


# ---------------------------------------------------------------------------
# Files and coverage
# ---------------------------------------------------------------------------

def _rel(repo: Path, raw: str) -> str:
    p = Path(raw)
    if p.is_absolute():
        try:
            p = p.relative_to(repo)
        except ValueError:
            pass
    return p.as_posix()


def load_coverage(repo: Path, path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    files = {}
    for raw, entry in data.get("files", {}).items():
        files[_rel(repo, raw)] = entry
    data["files"] = files
    return data


def _source_dirs(repo: Path, cfg: dict[str, Any], coverage: dict[str, Any]) -> list[Path]:
    if cfg.get("source"):
        dirs = [repo / s for s in cfg["source"]]
    else:
        tops = {Path(f).parts[0] for f in coverage["files"] if "/" in f}
        dirs = [repo / t for t in sorted(tops)]
    return [d for d in dirs if d.exists()]


def _test_dirs(repo: Path, cfg: dict[str, Any]) -> list[Path]:
    if cfg.get("tests"):
        dirs = [repo / t for t in cfg["tests"]]
    elif (repo / "tests").exists():
        dirs = [repo / "tests"]
    elif (repo / "test").exists():
        dirs = [repo / "test"]
    else:
        dirs = [repo]
    return [d for d in dirs if d.exists()]


def _py_files(root: Path) -> list[Path]:
    skip = {".venv", "venv", "node_modules", ".git", "__pycache__", ".tox", ".eggs", "build", "dist", "site-packages"}
    out = []
    for p in sorted(root.rglob("*.py")):
        if any(part in skip for part in p.parts):
            continue
        out.append(p)
    return out


def _is_collected_name(name: str) -> bool:
    return (name.startswith("test_") and name.endswith(".py")) or name.endswith("_test.py")


def _test_files(repo: Path, test_dirs: list[Path]) -> tuple[list[Path], list[str]]:
    """Return (collected test files, uncollected files that still define tests)."""
    collected: list[Path] = []
    uncollected: list[str] = []
    for d in test_dirs:
        for p in _py_files(d):
            if p.name in ("conftest.py", "__init__.py"):
                continue
            if _is_collected_name(p.name):
                collected.append(p)
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")
                   for n in ast.walk(tree)):
                uncollected.append(_rel(repo, str(p)))
    return collected, uncollected


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _str_arg(call: ast.Call, index: int = 0) -> str | None:
    if len(call.args) > index and isinstance(call.args[index], ast.Constant) and isinstance(call.args[index].value, str):
        return call.args[index].value
    return None


def _path_arg(call: ast.Call) -> str | None:
    """The route path: first positional string, else path=. An empty string is a valid path."""
    positional = _str_arg(call)
    if positional is not None:
        return positional
    return _kw_str(call, "path")


def _kw_str(call: ast.Call, name: str) -> str | None:
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def _kw_list(call: ast.Call, name: str) -> list[str] | None:
    for kw in call.keywords:
        if kw.arg == name and isinstance(kw.value, (ast.List, ast.Tuple)):
            return [e.value for e in kw.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return None


def _join_path(prefix: str, path: str) -> str:
    full = (prefix or "") + (path or "")
    if not full.startswith("/"):
        full = "/" + full
    return re.sub(r"/{2,}", "/", full) or "/"


def _router_prefixes(tree: ast.Module) -> dict[str, str]:
    """Map variable name -> prefix for APIRouter(prefix=...) and Blueprint(url_prefix=...)."""
    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            fname = call.func.attr if isinstance(call.func, ast.Attribute) else getattr(call.func, "id", "")
            if fname in ("APIRouter", "Router"):
                prefix = _kw_str(call, "prefix") or ""
            elif fname == "Blueprint":
                prefix = _kw_str(call, "url_prefix") or ""
            else:
                continue
            for target in node.targets:
                if isinstance(target, ast.Name):
                    prefixes[target.id] = prefix
    return prefixes


def _include_prefixes(trees: dict[str, ast.Module]) -> dict[str, str]:
    """Map router module basename -> extra prefix from include_router(x.router, prefix=...)."""
    extra: dict[str, str] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("include_router", "register_blueprint"):
                prefix = _kw_str(node, "prefix") or _kw_str(node, "url_prefix")
                if not prefix or not node.args:
                    continue
                arg = node.args[0]
                if isinstance(arg, ast.Attribute) and isinstance(arg.value, ast.Name):
                    extra[arg.value.id] = prefix
    return extra


def _test_scopes(tree: ast.Module) -> list[tuple[str, list[ast.stmt]]]:
    scopes: list[tuple[str, list[ast.stmt]]] = [("", tree.body)]
    scopes += [(n.name, n.body) for n in tree.body if isinstance(n, ast.ClassDef) and n.name.startswith("Test")]
    return scopes


def _test_ids(rel: str, scope: str, body: list[ast.stmt]) -> list[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str]]:
    """Assign an id to each test function in a scope. The last definition of a
    name gets the plain id (that's the one pytest runs); earlier duplicates
    get a line suffix so they stay distinct."""
    funcs = [n for n in body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")]
    last: dict[str, int] = {f.name: i for i, f in enumerate(funcs)}
    prefix = f"{rel}::{scope + '::' if scope else ''}"
    out = []
    for i, f in enumerate(funcs):
        suffix = "" if last[f.name] == i else f"@{f.lineno}"
        out.append((f, f"{prefix}{f.name}{suffix}"))
    return out


def _test_units(repo: Path, test_files: list[Path]) -> list[tuple[str, str]]:
    """Split test files into (test id, source) units, plus one ("", text) unit per
    file for module-level code such as fixtures and helpers."""
    units: list[tuple[str, str]] = []
    for p in test_files:
        rel = _rel(repo, str(p))
        text = p.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            units.append(("", text))
            continue
        lines = text.splitlines()
        taken: set[int] = set()
        for scope, body in _test_scopes(tree):
            for func, uid in _test_ids(rel, scope, body):
                end = func.end_lineno or func.lineno
                units.append((uid, "\n".join(lines[func.lineno - 1:end])))
                taken.update(range(func.lineno, end + 1))
        units.append(("", "\n".join(l for i, l in enumerate(lines, 1) if i not in taken)))
    return units


def _endpoint_status(ran: bool, referencing: list[str], ghost_ids: set[str]) -> str:
    """untested: handler never ran. indirect: ran, no test names the path.
    weak: ran, but every test naming the path is a ghost. tested: otherwise."""
    if not ran:
        return "untested"
    if not referencing:
        return "indirect"
    named = [u for u in referencing if u]
    if named and all(u in ghost_ids for u in named):
        return "weak"
    return "tested"


def find_endpoints(repo: Path, source_dirs: list[Path], coverage: dict[str, Any], units: list[tuple[str, str]], ghost_ids: set[str]) -> list[dict[str, Any]]:
    trees: dict[str, ast.Module] = {}
    for d in source_dirs:
        for p in _py_files(d):
            try:
                trees[_rel(repo, str(p))] = ast.parse(p.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
    include_extra = _include_prefixes(trees)
    endpoints: list[dict[str, Any]] = []

    for rel, tree in trees.items():
        module_base = Path(rel).stem
        outer = include_extra.get(module_base, "")
        local = _router_prefixes(tree)
        executed = set(coverage["files"].get(rel, {}).get("executed_lines", []))

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for deco in node.decorator_list:
                    if not isinstance(deco, ast.Call):
                        continue
                    methods: list[str] = []
                    path: str | None = None
                    prefix = ""
                    func = deco.func
                    if isinstance(func, ast.Attribute) and func.attr in ROUTE_ATTRS and isinstance(func.value, ast.Name):
                        path = _path_arg(deco)
                        prefix = local.get(func.value.id, "")
                        if func.attr in HTTP_METHODS:
                            methods = [func.attr.upper()]
                        elif func.attr == "websocket":
                            methods = ["WS"]
                        else:
                            methods = [m.upper() for m in (_kw_list(deco, "methods") or ["GET"])]
                    elif isinstance(func, ast.Name) and func.id in HTTP_METHODS:
                        path = _path_arg(deco)
                        methods = [func.id.upper()]
                    elif isinstance(func, ast.Name) and func.id == "route":
                        path = _path_arg(deco)
                        methods = [m.upper() for m in (_kw_list(deco, "http_method") or ["GET"])]
                    if path is None:
                        continue
                    full = _join_path(outer + prefix, path)
                    body_lines = set(range(node.lineno + 1, (node.end_lineno or node.lineno) + 1))
                    ran = bool(body_lines & executed)
                    referencing = [uid for uid, seg in units if _referenced(full, seg)]
                    for method in methods:
                        endpoints.append({
                            "framework": "decorator",
                            "method": method,
                            "path": full,
                            "handler": node.name,
                            "file": rel,
                            "line": node.lineno,
                            "executed": ran,
                            "referenced_by": [u for u in referencing if u],
                            "status": _endpoint_status(ran, referencing, ghost_ids),
                        })
            elif isinstance(node, ast.Call) and rel.endswith("urls.py"):
                fname = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if fname in ("path", "re_path", "url") and node.args:
                    path = _str_arg(node)
                    if path is None:
                        continue
                    handler = ast.unparse(node.args[1]) if len(node.args) > 1 else "?"
                    full = _join_path("", path)
                    referencing = [uid for uid, seg in units if _referenced(full, seg)]
                    # urls.py has no handler body to check, so a reference stands in for execution.
                    endpoints.append({
                        "framework": "django",
                        "method": "ANY",
                        "path": full,
                        "handler": handler,
                        "file": rel,
                        "line": node.lineno,
                        "executed": bool(referencing),
                        "referenced_by": [u for u in referencing if u],
                        "status": _endpoint_status(bool(referencing), referencing, ghost_ids),
                    })
    endpoints.sort(key=lambda e: (e["file"], e["line"], e["method"]))
    return endpoints


def _referenced(path: str, test_text: str) -> bool:
    """True when a test file contains a string literal for this path."""
    pattern = re.escape(path)
    pattern = re.sub(r"\\\{[^}]*\\\}", r"[^/\"']+", pattern)       # {param}
    pattern = re.sub(r"<[^>]*>", r"[^/\"']+", pattern)              # <int:pk>
    pattern = pattern.rstrip("/") + "/?"
    return re.search(r"[\"']" + pattern + r"[\"'?]", test_text) is not None


# ---------------------------------------------------------------------------
# Ghost tests
# ---------------------------------------------------------------------------

_SKIP_ATTRS = {"skip", "skipif", "skipUnless", "skipIf"}


def _decorator_reason(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    for deco in func.decorator_list:
        target = deco.func if isinstance(deco, ast.Call) else deco
        name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
        if name in _SKIP_ATTRS:
            return "skipped"
        if name == "xfail":
            return "xfail"
    return None


def _is_docstring(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str)


def _is_pass_or_ellipsis(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Pass):
        return True
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis


def _body_reason(func: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    body = [s for s in func.body if not _is_docstring(s)]
    if all(_is_pass_or_ellipsis(s) for s in body):
        return "empty body"
    asserts = [s for s in body if isinstance(s, ast.Assert)]
    if asserts and len(asserts) == len(body) and all(
        isinstance(a.test, ast.Constant) and bool(a.test.value) for a in asserts
    ):
        return "trivial assertion"
    has_assertion = False
    for node in ast.walk(func):
        if isinstance(node, (ast.Assert, ast.Raise)):
            has_assertion = True
            break
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name.startswith("assert") or name in ("raises", "warns", "fail", "approx", "deprecated_call"):
                has_assertion = True
                break
    if not has_assertion:
        return "no assertion"
    return None


def find_ghost_tests(repo: Path, test_files: list[Path]) -> tuple[list[dict[str, Any]], int]:
    ghosts: list[dict[str, Any]] = []
    total = 0
    for p in test_files:
        rel = _rel(repo, str(p))
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for scope, body in _test_scopes(tree):
            ids = _test_ids(rel, scope, body)
            total += len(ids)
            last_line: dict[str, int] = {}
            for func, _ in reversed(ids):
                last_line.setdefault(func.name, func.lineno)
            for func, uid in ids:
                if uid.endswith(f"@{func.lineno}"):
                    ghosts.append({"id": uid, "file": rel, "line": func.lineno, "name": func.name,
                                   "reason": f"duplicate name (shadowed by line {last_line[func.name]})"})
                    continue
                reason = _decorator_reason(func) or _body_reason(func)
                if reason:
                    ghosts.append({"id": uid, "file": rel, "line": func.lineno, "name": func.name, "reason": reason})
    ghosts.sort(key=lambda g: (g["file"], g["line"]))
    return ghosts, total


# ---------------------------------------------------------------------------
# Modules without tests
# ---------------------------------------------------------------------------

def _module_name(repo: Path, path: Path) -> str:
    rel = path.relative_to(repo).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports_of(tree: ast.Module, own_module: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = own_module.split(".")
                anchor = anchor[: len(anchor) - node.level] if node.level <= len(anchor) else []
                base = ".".join([*anchor, base] if base else anchor)
            if base:
                names.add(base)
            for alias in node.names:
                names.add(f"{base}.{alias.name}" if base else alias.name)
    return names


def find_untested_modules(repo: Path, source_dirs: list[Path], test_files: list[Path], coverage: dict[str, Any]) -> list[dict[str, Any]]:
    modules: dict[str, Path] = {}
    for d in source_dirs:
        for p in _py_files(d):
            modules[_module_name(repo, p)] = p
    graph: dict[str, set[str]] = {}
    for name, p in modules.items():
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            graph[name] = set()
            continue
        graph[name] = {i for i in _imports_of(tree, name) if i in modules}
    reached: set[str] = set()
    stack: list[str] = []
    for p in test_files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for imp in _imports_of(tree, "tests"):
            if imp in modules:
                stack.append(imp)
        stem = p.stem.removeprefix("test_").removesuffix("_test")
        for name in modules:
            if name.rsplit(".", 1)[-1] == stem:
                stack.append(name)
    while stack:
        name = stack.pop()
        if name in reached:
            continue
        reached.add(name)
        stack.extend(graph.get(name, ()))
        parent = name.rsplit(".", 1)[0] if "." in name else ""
        if parent in modules:
            stack.append(parent)
    out = []
    for name, p in sorted(modules.items()):
        if p.name == "__init__.py" or name in reached:
            continue
        rel = _rel(repo, str(p))
        entry = coverage["files"].get(rel, {}).get("summary", {})
        if entry.get("num_statements", 1) == 0:
            continue
        out.append({"module": name, "file": rel, "percent_covered": round(entry.get("percent_covered", 0.0), 1)})
    return out


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def _pct(entry: dict[str, Any]) -> float:
    return round(float(entry.get("summary", {}).get("percent_covered", 0.0)), 1)


def _branch_pct(entry: dict[str, Any]) -> float | None:
    s = entry.get("summary", {})
    n = s.get("num_branches", 0)
    if not n:
        return None
    return round(100.0 * s.get("covered_branches", 0) / n, 1)


def analyze(
    repo: Path,
    coverage_path: Path,
    baseline_path: Path | None = None,
    pytest_exit_code: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repo = Path(repo).resolve()
    cfg = load_config(repo)
    if config:
        cfg.update(config)
    coverage = load_coverage(repo, Path(coverage_path))
    baseline: dict[str, Any] | None = None
    if baseline_path and Path(baseline_path).exists():
        baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))

    source_dirs = _source_dirs(repo, cfg, coverage)
    test_dirs = _test_dirs(repo, cfg)
    test_files, uncollected = _test_files(repo, test_dirs)
    ghosts, total_tests = find_ghost_tests(repo, test_files)
    ghost_ids = {g["id"] for g in ghosts}
    units = _test_units(repo, test_files)

    # Files
    base_files: dict[str, float] = (baseline or {}).get("files", {})
    files = []
    for rel, entry in sorted(coverage["files"].items()):
        pct = _pct(entry)
        prev = base_files.get(rel)
        if baseline is None:
            status, delta = "", None
        elif prev is None:
            status, delta = "new", None
        else:
            delta = round(pct - prev, 1)
            status = "regressed" if delta < -0.05 else ("improved" if delta > 0.05 else "steady")
        files.append({
            "file": rel,
            "percent_covered": pct,
            "branch_percent": _branch_pct(entry),
            "statements": entry.get("summary", {}).get("num_statements", 0),
            "executed": len(entry.get("executed_lines", [])),
            "missing_lines": entry.get("missing_lines", []),
            "previous": prev,
            "delta": delta,
            "status": status,
        })

    endpoints = find_endpoints(repo, source_dirs, coverage, units, ghost_ids)
    untested_modules = find_untested_modules(repo, source_dirs, test_files, coverage)

    overall = round(float(coverage.get("totals", {}).get("percent_covered", 0.0)), 1)
    base_overall = (baseline or {}).get("overall", {}).get("percent_covered")
    overall_delta = round(overall - base_overall, 2) if base_overall is not None else None

    untested_ids = [f"{e['method']} {e['path']}" for e in endpoints if e["status"] == "untested"]
    base_untested = set((baseline or {}).get("untested_endpoints", []))
    new_untested = [u for u in untested_ids if u not in base_untested] if baseline is not None else []
    ghost_ids = [g["id"] for g in ghosts]
    base_ghosts = set((baseline or {}).get("ghost_tests", []))
    new_ghosts = [g for g in ghost_ids if g not in base_ghosts] if baseline is not None else []

    gates = _gates(cfg, baseline, pytest_exit_code, overall, base_overall, files, untested_ids, new_untested, ghost_ids, new_ghosts, uncollected)
    verdict = "fail" if any(g["status"] == "fail" for g in gates) else "pass"

    return {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "repo": str(repo),
        "config": {k: v for k, v in cfg.items()},
        "summary": {
            "overall": overall,
            "baseline_overall": base_overall,
            "overall_delta": overall_delta,
            "branch_overall": _branch_pct({"summary": coverage.get("totals", {})}),
            "files": len(files),
            "tests": total_tests,
            "endpoints": len(endpoints),
            "untested_endpoints": untested_ids,
            "weak_endpoints": [f"{e['method']} {e['path']}" for e in endpoints if e["status"] == "weak"],
            "new_untested_endpoints": new_untested,
            "ghost_tests": len(ghosts),
            "new_ghost_tests": new_ghosts,
            "untested_modules": len(untested_modules),
            "verdict": verdict,
        },
        "gates": gates,
        "files": files,
        "endpoints": endpoints,
        "ghost_tests": ghosts,
        "uncollected_test_files": uncollected,
        "untested_modules": untested_modules,
    }


def _gates(cfg, baseline, pytest_exit_code, overall, base_overall, files, untested_ids, new_untested, ghost_ids, new_ghosts, uncollected):
    gates: list[dict[str, Any]] = []

    def add(name: str, status: str, detail: str) -> None:
        gates.append({"name": name, "status": status, "detail": detail})

    if pytest_exit_code is None:
        add("tests-green", "skipped", "no pytest exit code supplied")
    elif pytest_exit_code == 0:
        add("tests-green", "pass", "pytest exit code 0")
    else:
        add("tests-green", "fail", f"pytest exit code {pytest_exit_code}")

    floor = cfg.get("floor")
    if floor is None:
        add("overall-floor", "skipped", "no floor configured")
    else:
        add("overall-floor", "pass" if overall >= float(floor) else "fail", f"{overall}% vs floor {floor}%")

    if base_overall is None:
        add("overall-regression", "skipped", "no baseline")
    else:
        allowed = float(cfg.get("max_overall_regression", 0.5))
        ok = overall >= base_overall - allowed
        add("overall-regression", "pass" if ok else "fail", f"{overall}% vs baseline {base_overall}% (allowed drop {allowed})")

    if baseline is None:
        add("file-regression", "skipped", "no baseline")
    else:
        allowed = float(cfg.get("max_file_regression", 1.0))
        bad = [f"{f['file']} ({f['delta']:+.1f})" for f in files if f["delta"] is not None and f["delta"] < -allowed]
        add("file-regression", "fail" if bad else "pass", ", ".join(bad) if bad else f"no file dropped more than {allowed} points")

    new_floor = float(cfg.get("new_file_floor", 50.0))
    if baseline is None:
        candidates = [f for f in files if f["executed"] == 0 and f["statements"] > 0]
        label = "never-executed files"
    else:
        candidates = [f for f in files if f["status"] == "new" and f["statements"] > 0]
        label = "new files"
    low = [f"{f['file']} ({f['percent_covered']}%)" for f in candidates if f["percent_covered"] < new_floor]
    add("new-file-floor", "fail" if low else "pass", ", ".join(low) if low else f"no {label} below {new_floor}%")

    if baseline is None:
        add("untested-endpoints", "info", f"{len(untested_ids)} untested endpoint(s); no baseline to compare")
    else:
        add("untested-endpoints", "fail" if new_untested else "pass",
            ", ".join(new_untested) if new_untested else f"no new untested endpoints ({len(untested_ids)} carried over)")

    if baseline is None:
        add("ghost-tests", "info", f"{len(ghost_ids)} ghost test(s); no baseline to compare")
    else:
        add("ghost-tests", "fail" if new_ghosts else "pass",
            ", ".join(new_ghosts) if new_ghosts else f"no new ghost tests ({len(ghost_ids)} carried over)")

    add("uncollected-test-files", "fail" if uncollected else "pass", ", ".join(uncollected) if uncollected else "every test file is collected")
    return gates


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_baseline(result: dict[str, Any], path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "generated_at": result["generated_at"],
        "overall": {"percent_covered": result["summary"]["overall"]},
        "files": {f["file"]: f["percent_covered"] for f in result["files"]},
        "untested_endpoints": result["summary"]["untested_endpoints"],
        "ghost_tests": [g["id"] for g in result["ghost_tests"]],
        "tests": result["summary"]["tests"],
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _fmt(value: Any, suffix: str = "") -> str:
    if value is None:
        return "–"
    return f"{value}{suffix}"


def render_markdown(result: dict[str, Any]) -> str:
    s = result["summary"]
    lines = ["# Coverage signals", ""]
    delta = f" ({s['overall_delta']:+.1f} vs baseline)" if s["overall_delta"] is not None else " (no baseline)"
    branch = f", branches {s['branch_overall']}%" if s["branch_overall"] is not None else ""
    lines.append(f"Overall line coverage {s['overall']}%{delta}{branch}. "
                 f"{s['tests']} tests, {s['endpoints']} endpoints, {len(s['untested_endpoints'])} untested, "
                 f"{s['ghost_tests']} ghost tests, {s['untested_modules']} modules without tests. "
                 f"Verdict: **{s['verdict'].upper()}**.")
    lines += ["", "## Gates", "", "| Gate | Status | Detail |", "|---|---|---|"]
    for g in result["gates"]:
        lines.append(f"| {g['name']} | {g['status']} | {g['detail']} |")

    lines += ["", "## Coverage by file", "", "| File | Lines | Branches | Prev | Delta | Status | Missing |", "|---|---|---|---|---|---|---|"]
    for f in sorted(result["files"], key=lambda f: f["percent_covered"]):
        missing = ", ".join(str(n) for n in f["missing_lines"][:12]) + (" …" if len(f["missing_lines"]) > 12 else "")
        delta_txt = f"{f['delta']:+.1f}" if f["delta"] is not None else "–"
        lines.append(f"| {f['file']} | {f['percent_covered']}% | {_fmt(f['branch_percent'], '%')} | {_fmt(f['previous'], '%')} | {delta_txt} | {f['status'] or '–'} | {missing or '–'} |")

    lines += ["", "## Endpoints", ""]
    if result["endpoints"]:
        lines += ["| Method | Path | Handler | Where | Status |", "|---|---|---|---|---|"]
        order = {"untested": 0, "weak": 1, "indirect": 2, "tested": 3}
        for e in sorted(result["endpoints"], key=lambda e: (order[e["status"]], e["path"])):
            lines.append(f"| {e['method']} | `{e['path']}` | {e['handler']} | {e['file']}:{e['line']} | {e['status']} |")
        untested = [f"{e['method']} {e['path']}" for e in result["endpoints"] if e["status"] == "untested"]
        weak = [f"{e['method']} {e['path']}" for e in result["endpoints"] if e["status"] == "weak"]
        lines += ["", "Untested: " + (", ".join(untested) if untested else "none") + ".",
                  "Weak: " + (", ".join(weak) if weak else "none") + "."]
        lines += ["", "`untested`: the handler body never ran. `weak`: it ran, but every test that names the path is a ghost test. "
                      "`indirect`: it ran, but no test names the path. `tested`: it ran and a real test names the path."]
    else:
        lines.append("No HTTP endpoints found (FastAPI, Starlette, Flask, Django urls.py, Litestar).")

    lines += ["", "## Ghost tests", ""]
    if result["ghost_tests"]:
        lines += ["| Test | Reason |", "|---|---|"]
        for g in result["ghost_tests"]:
            lines.append(f"| {g['file']}:{g['line']} `{g['name']}` | {g['reason']} |")
    else:
        lines.append("None.")

    lines += ["", "## Modules without tests", ""]
    if result["untested_modules"]:
        lines += ["| Module | Coverage |", "|---|---|"]
        for m in result["untested_modules"]:
            lines.append(f"| {m['file']} | {m['percent_covered']}% |")
        lines.append("")
        lines.append("No test imports these modules, directly or through the modules it imports.")
    else:
        lines.append("Every source module is reachable from a test.")

    lines += ["", "## Uncollected test files", ""]
    if result["uncollected_test_files"]:
        for u in result["uncollected_test_files"]:
            lines.append(f"- `{u}` defines tests but its name doesn't match `test_*.py` or `*_test.py`, so pytest skips it.")
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".", help="repo root (default: current directory)")
    ap.add_argument("--coverage", default="coverage.json", help="coverage.py JSON report")
    ap.add_argument("--baseline", help="baseline JSON from an earlier --write-baseline (default: config baseline path if it exists)")
    ap.add_argument("--no-baseline", action="store_true", help="ignore any baseline")
    ap.add_argument("--pytest-exit-code", type=int, help="exit code of the pytest run that produced the coverage")
    ap.add_argument("--floor", type=float, help="overall coverage floor in percent")
    ap.add_argument("--json", dest="json_out", help="write the full signals JSON here")
    ap.add_argument("--write-baseline", help="after analysis, save this run as the new baseline at this path")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    cfg_over: dict[str, Any] = {}
    if args.floor is not None:
        cfg_over["floor"] = args.floor
    baseline_path: Path | None = None
    if not args.no_baseline:
        if args.baseline:
            baseline_path = Path(args.baseline)
        else:
            candidate = repo / load_config(repo)["baseline"]
            baseline_path = candidate if candidate.exists() else None

    result = analyze(repo, Path(args.coverage), baseline_path, args.pytest_exit_code, cfg_over)
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    if args.write_baseline:
        write_baseline(result, Path(args.write_baseline))
    sys.stdout.write(render_markdown(result))
    return 0 if result["summary"]["verdict"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
