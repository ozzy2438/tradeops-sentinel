"""Build and validate the deployable wheel from a clean virtual environment."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REQUIRED_IMPORTS = (
    "tradeops_sentinel",
    "packages.contracts",
    "packages.generator",
    "packages.persistence",
    "packages.reconciliation",
    "packages.oracle",
    "packages.remediation",
    "packages.priority_model",
)
REQUIRED_WHEEL_MEMBERS = (
    "packages/contracts/examples/valid/source-of-truth-policy.json",
    "packages/contracts/schemas/common.schema.json",
    "packages/persistence/ddl/0001_canonical_persistence.sql",
    "packages/persistence/ddl/0002_p1_production_boundaries.sql",
    "packages/persistence/ddl/0003_product_runtime.sql",
    "packages/persistence/ddl/0004_ai_remediation.sql",
    "packages/persistence/ddl/0005_ml_priority_assessment.sql",
    "packages/priority_model/artifacts/priority_model.txt",
    "packages/priority_model/artifacts/metadata.json",
    "packages/remediation/runbooks/RB-001-fx-economic-value-mismatch.md",
    "packages/remediation/runbooks/RB-002-maker-checker-approval-policy.md",
    "packages/remediation/runbooks/RB-003-automation-failure-recovery.md",
)


def _run(command: list[str], *, cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def _venv_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _build_wheel(repository: Path, output_directory: Path) -> Path:
    _run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(output_directory),
        ],
        cwd=repository,
    )
    wheels = sorted(output_directory.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel, found: {wheels}")
    return wheels[0]


def _validate_wheel_members(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        members = set(archive.namelist())
    missing = [member for member in REQUIRED_WHEEL_MEMBERS if member not in members]
    if missing:
        raise RuntimeError(f"wheel is missing required runtime artefacts: {missing}")


def _clean_install_and_import(wheel: Path, workspace: Path) -> None:
    environment = workspace / "clean-venv"
    _run([sys.executable, "-m", "venv", str(environment)], cwd=workspace)
    python = _venv_python(environment)
    _run([str(python), "-m", "pip", "install", str(wheel)], cwd=workspace)
    import_script = (
        "import importlib, json; "
        f"names={list(REQUIRED_IMPORTS)!r}; "
        "[importlib.import_module(name) for name in names]; "
        "from packages.persistence import load_mvp_source_of_truth_policy; "
        "policy=load_mvp_source_of_truth_policy(); "
        "assert policy.policy_version == '1.0.0'; "
        "print(json.dumps({'imports': names, 'policy_version': policy.policy_version, "
        "'status': 'ok'}, sort_keys=True))"
    )
    _run([str(python), "-I", "-c", import_script], cwd=workspace)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    repository = args.repository.resolve()
    temporary_parent = "/tmp" if os.name != "nt" else None
    with tempfile.TemporaryDirectory(prefix="tradeops-wheel-", dir=temporary_parent) as temporary:
        workspace = Path(temporary)
        wheel = _build_wheel(repository, workspace / "dist")
        _validate_wheel_members(wheel)
        _clean_install_and_import(wheel, workspace)
        print(
            json.dumps(
                {
                    "status": "ok",
                    "wheel": wheel.name,
                    "required_imports": REQUIRED_IMPORTS,
                    "required_members": REQUIRED_WHEEL_MEMBERS,
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
