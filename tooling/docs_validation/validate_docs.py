"""Validate relative Markdown links and Mermaid block structure."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def validate_links(path: Path) -> list[str]:
    """Return broken relative Markdown links."""
    errors: list[str] = []
    for target in LINK.findall(path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        raw_path = target.split("#", 1)[0]
        if raw_path and not (path.parent / raw_path).resolve().exists():
            errors.append(f"{path.relative_to(ROOT)}: broken link {target}")
    return errors


def validate_mermaid(path: Path) -> list[str]:
    """Ensure Mermaid fences are paired and non-empty."""
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    in_mermaid = False
    body: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if line.strip() == "```mermaid":
            if in_mermaid:
                errors.append(f"{path.relative_to(ROOT)}:{line_number}: nested Mermaid fence")
            in_mermaid = True
            body = []
        elif line.strip() == "```" and in_mermaid:
            if not any(part.strip() for part in body):
                errors.append(f"{path.relative_to(ROOT)}:{line_number}: empty Mermaid block")
            in_mermaid = False
        elif in_mermaid:
            body.append(line)
    if in_mermaid:
        errors.append(f"{path.relative_to(ROOT)}: unclosed Mermaid fence")
    return errors


def main() -> int:
    """Run documentation validation."""
    errors: list[str] = []
    for path in sorted(ROOT.rglob("*.md")):
        if ".venv" in path.parts:
            continue
        errors.extend(validate_links(path))
        errors.extend(validate_mermaid(path))
    if errors:
        print("\n".join(errors))
        return 1
    print("documentation links and Mermaid structure: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
