"""Train or validate the checked-in synthetic LightGBM priority artifact."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from packages.priority_model.training import train, validate


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifacts",
        type=Path,
        default=Path("packages/priority_model/artifacts"),
    )
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    result = validate(args.artifacts) if args.check else train(args.artifacts)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
