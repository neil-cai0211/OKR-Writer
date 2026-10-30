"""Check portable core Markdown references; this does not test AI behavior.

Run: python tests/core/check_package.py
Uses only the standard library and is not a skill runtime dependency.
"""
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "product-rd-okr-writer"
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")


def check():
    errors = []
    checked_links = 0
    core = list(SKILL.rglob("*.md"))
    public = [ROOT / "README.md", ROOT / "docs" / "01-安装技能.md"]
    edges = {}
    for path in core + public:
        content = path.read_text(encoding="utf-8")
        if content.startswith("\ufeff"):
            errors.append(f"{path.relative_to(ROOT)}: unexpected UTF-8 BOM")
        if "\ufffd" in content:
            errors.append(f"{path.relative_to(ROOT)}: replacement character")
        fences = [line for line in content.splitlines() if line.startswith("```")]
        if len(fences) % 2:
            errors.append(f"{path.relative_to(ROOT)}: unclosed code fence")
        edges[path.resolve()] = []
        for raw in LINK.findall(content):
            target = raw.strip().strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme in {"https", "http", "mailto"}:
                continue
            if parsed.scheme or target.startswith(("/", "\\")):
                errors.append(f"{path.relative_to(ROOT)}: nonportable reference {target}")
                continue
            if not parsed.path:
                continue
            destination = (path.parent / unquote(parsed.path)).resolve()
            checked_links += 1
            if not destination.exists():
                errors.append(f"{path.relative_to(ROOT)}: missing reference {target}")
            if path in core and not destination.is_relative_to(SKILL.resolve()):
                errors.append(f"{path.relative_to(ROOT)}: core reference escapes skill package: {target}")
            edges[path.resolve()].append(destination)
    reached = set()
    pending = [(SKILL / "SKILL.md").resolve()]
    while pending:
        current = pending.pop()
        if current in reached:
            continue
        reached.add(current)
        pending.extend(edges.get(current, []))
    for resource in (SKILL / "references").glob("*.md"):
        if resource.resolve() not in reached:
            errors.append(f"{resource.relative_to(ROOT)}: unreachable from SKILL.md")
    if errors:
        print("\n".join(errors))
        return 1
    print(f"PASS: {len(core)} core Markdown files, {len(public)} public files, "
          f"{checked_links} local links; all references exist and are reachable; "
          "core links remain inside the skill package.")
    return 0


if __name__ == "__main__":
    sys.exit(check())
