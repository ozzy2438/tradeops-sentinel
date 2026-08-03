"""AST-only, fail-closed import graph for the TS-12 package boundary."""

from __future__ import annotations

import ast
import json
from collections import deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class IsolationReport:
    """Machine-readable result of the oracle/reconciliation boundary check."""

    schema_version: str
    oracle_prefix: str
    reconciliation_prefix: str
    modules: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]
    direct_forbidden_edges: tuple[tuple[str, str], ...]
    oracle_to_reconciliation_paths: tuple[tuple[str, ...], ...]
    reconciliation_to_oracle_paths: tuple[tuple[str, ...], ...]
    parse_errors: tuple[str, ...]
    unresolved_internal_imports: tuple[str, ...]
    dynamic_imports: tuple[str, ...]

    @property
    def isolated(self) -> bool:
        """Return true only when every failure surface is empty."""

        return not (
            self.direct_forbidden_edges
            or self.oracle_to_reconciliation_paths
            or self.reconciliation_to_oracle_paths
            or self.parse_errors
            or self.unresolved_internal_imports
            or self.dynamic_imports
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "oracle_prefix": self.oracle_prefix,
            "reconciliation_prefix": self.reconciliation_prefix,
            "isolated": self.isolated,
            "modules": list(self.modules),
            "edges": [list(edge) for edge in self.edges],
            "direct_forbidden_edges": [list(edge) for edge in self.direct_forbidden_edges],
            "oracle_to_reconciliation_paths": [
                list(path) for path in self.oracle_to_reconciliation_paths
            ],
            "reconciliation_to_oracle_paths": [
                list(path) for path in self.reconciliation_to_oracle_paths
            ],
            "parse_errors": list(self.parse_errors),
            "unresolved_internal_imports": list(self.unresolved_internal_imports),
            "dynamic_imports": list(self.dynamic_imports),
        }


class ImportIsolationError(RuntimeError):
    """Raised when the fail-closed isolation report is not isolated."""

    def __init__(self, report: IsolationReport) -> None:
        self.report = report
        super().__init__(json.dumps(report.as_dict(), sort_keys=True))


def scan_repository(
    root: Path,
    *,
    oracle_prefix: str = "packages.oracle",
    reconciliation_prefix: str = "packages.reconciliation",
) -> IsolationReport:
    """Scan Python packages without importing application modules."""

    package_root = root / "packages"
    module_paths = _module_paths(package_root)
    modules = set(module_paths)
    graph: dict[str, tuple[str, ...]] = {}
    parse_errors: list[str] = []
    unresolved: list[str] = []
    dynamic: list[str] = []
    for module, (path, is_package) in module_paths.items():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            parse_errors.append(f"{module}: {exc}")
            graph[module] = ()
            continue
        dynamic.extend(_dynamic_imports(tree, module))
        imports, missing = _imports(module, is_package, tree, modules)
        unresolved.extend(f"{module}: {name}" for name in missing)
        graph[module] = tuple(sorted(imports))

    edges = tuple(
        sorted((source, target) for source, targets in graph.items() for target in targets)
    )
    oracle_modules = tuple(sorted(module for module in modules if _under(module, oracle_prefix)))
    reconciliation_modules = tuple(
        sorted(module for module in modules if _under(module, reconciliation_prefix))
    )
    direct = tuple(
        edge
        for edge in edges
        if (_under(edge[0], oracle_prefix) and _under(edge[1], reconciliation_prefix))
        or (_under(edge[0], reconciliation_prefix) and _under(edge[1], oracle_prefix))
    )
    return IsolationReport(
        schema_version="1.0.0",
        oracle_prefix=oracle_prefix,
        reconciliation_prefix=reconciliation_prefix,
        modules=tuple(sorted(modules)),
        edges=edges,
        direct_forbidden_edges=direct,
        oracle_to_reconciliation_paths=_paths_between(graph, oracle_modules, reconciliation_prefix),
        reconciliation_to_oracle_paths=_paths_between(graph, reconciliation_modules, oracle_prefix),
        parse_errors=tuple(sorted(parse_errors)),
        unresolved_internal_imports=tuple(sorted(unresolved)),
        dynamic_imports=tuple(sorted(dynamic)),
    )


def enforce_isolation(report: IsolationReport) -> IsolationReport:
    """Return a passing report or raise with machine-readable failure data."""

    if not report.isolated:
        raise ImportIsolationError(report)
    return report


def _module_paths(package_root: Path) -> dict[str, tuple[Path, bool]]:
    if not package_root.exists():
        raise ValueError(f"package root does not exist: {package_root}")
    paths: dict[str, tuple[Path, bool]] = {}
    for path in sorted(package_root.rglob("*.py")):
        relative = path.relative_to(package_root.parent).with_suffix("")
        parts = list(relative.parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts.pop()
        if parts:
            paths[".".join(parts)] = (path, is_package)
    return paths


def _imports(
    current_module: str,
    current_is_package: bool,
    tree: ast.AST,
    modules: set[str],
) -> tuple[set[str], set[str]]:
    imports: set[str] = set()
    missing: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not alias.name.startswith("packages"):
                    continue
                resolved = _resolve_absolute(alias.name, modules)
                if resolved is None:
                    missing.add(alias.name)
                else:
                    imports.add(resolved)
        elif isinstance(node, ast.ImportFrom):
            candidates = _resolve_from(current_module, current_is_package, node)
            for candidate in candidates:
                if not candidate.startswith("packages"):
                    continue
                resolved = _resolve_absolute(candidate, modules)
                if resolved is None:
                    missing.add(candidate)
                else:
                    imports.add(resolved)
    return imports, missing


def _resolve_from(current_module: str, current_is_package: bool, node: ast.ImportFrom) -> set[str]:
    if node.level:
        source_parts = current_module.split(".")
        package_parts = source_parts if current_is_package else source_parts[:-1]
        trim = node.level - 1
        if trim > len(package_parts):
            return set()
        base_parts = package_parts[: len(package_parts) - trim]
    else:
        base_parts = []
    if node.module:
        base = ".".join((*base_parts, *node.module.split(".")))
        return {base, *[f"{base}.{alias.name}" for alias in node.names]}
    base = ".".join(base_parts)
    return {".".join((*base_parts, alias.name)) if base else alias.name for alias in node.names} | (
        {base} if base else set()
    )


def _resolve_absolute(candidate: str, modules: set[str]) -> str | None:
    value = candidate
    while value:
        if value in modules:
            return value
        value = value.rsplit(".", 1)[0] if "." in value else ""
    return None


def _dynamic_imports(tree: ast.AST, module: str) -> list[str]:
    findings: list[str] = []
    importlib_module_aliases = {"importlib"}
    # Reject the conventional callable name even when an import statement is
    # omitted or hidden from this module-level alias scan; false positives are
    # safer than allowing a dynamic-import bypass at this boundary.
    import_module_aliases = {"import_module"}
    import_builtin_aliases = {"__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_module_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "importlib":
                for alias in node.names:
                    if alias.name == "import_module":
                        import_module_aliases.add(alias.asname or alias.name)
            elif node.module == "builtins":
                for alias in node.names:
                    if alias.name == "__import__":
                        import_builtin_aliases.add(alias.asname or alias.name)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "__import__":
            findings.append(f"{module}: __import__")
        elif isinstance(node.func, ast.Name) and node.func.id in import_builtin_aliases:
            findings.append(f"{module}: __import__ (alias: {node.func.id})")
        elif isinstance(node.func, ast.Name) and node.func.id in import_module_aliases:
            findings.append(f"{module}: importlib.import_module (alias: {node.func.id})")
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in importlib_module_aliases
        ):
            if node.func.value.id == "importlib":
                findings.append(f"{module}: importlib.import_module")
            else:
                findings.append(f"{module}: importlib.import_module (alias: {node.func.value.id})")
    return findings


def _paths_between(
    graph: Mapping[str, Sequence[str]],
    starts: Iterable[str],
    target_prefix: str,
) -> tuple[tuple[str, ...], ...]:
    paths: list[tuple[str, ...]] = []
    for start in starts:
        queue: deque[tuple[str, ...]] = deque([(start,)])
        visited = {start}
        while queue:
            path = queue.popleft()
            node = path[-1]
            for target in graph.get(node, ()):
                next_path = (*path, target)
                if _under(target, target_prefix):
                    paths.append(next_path)
                    continue
                if target not in visited:
                    visited.add(target)
                    queue.append(next_path)
    return tuple(sorted(set(paths)))


def _under(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")
