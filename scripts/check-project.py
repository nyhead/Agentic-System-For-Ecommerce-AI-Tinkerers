"""Fail clearly when the assumed Python application structure is absent."""

import sys
import tomllib
from pathlib import Path


def main() -> int:
    errors: list[str] = []
    for filename in ("pyproject.toml", "uv.lock", "app/main.py", "Dockerfile"):
        if not Path(filename).is_file():
            errors.append(f"Required file missing: {filename}")
    if not Path("tests").is_dir():
        errors.append("Add real offline tests under tests/.")
    project = Path("pyproject.toml")
    if project.is_file():
        try:
            config = tomllib.loads(project.read_text(encoding="utf-8"))
            if not config.get("dependency-groups", {}).get("dev"):
                errors.append("Define dependency-groups.dev with ruff, mypy and pytest.")
        except (OSError, ValueError) as error:
            errors.append(f"Invalid pyproject.toml: {error}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    print("Python project structure is present; run the real checks next.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
