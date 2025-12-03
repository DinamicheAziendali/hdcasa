#!/usr/bin/env python3

import ast
import logging
import re
from pathlib import Path

# Markers in README.md
START_MARKER = "[//]: # (addons)"
END_MARKER = "[//]: # (end addons)"

_logger = logging.getLogger(__name__)


def find_addon_dirs(root: Path):
    """Return a list of subdirectories containing a __manifest__.py file."""
    return [
        p for p in root.iterdir() if p.is_dir() and (p / "__manifest__.py").exists()
    ]


def parse_manifest(manifest_path: Path):
    """Parse the __manifest__.py file and extract metadata."""
    content = manifest_path.read_text(encoding="utf-8")
    # Extract the dict literal
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        return None
    data = ast.literal_eval(match.group(0))

    maintainers = data.get("maintainers", [])
    if isinstance(maintainers, list | tuple):
        maintainers = ", ".join(str(m) for m in maintainers)
    else:
        maintainers = str(maintainers)

    return {
        "dir": manifest_path.parent.name,
        "version": data.get("version", ""),
        "maintainers": maintainers,
        "summary": data.get("summary", "").strip().replace("\n", " "),
    }


def generate_table(addons: list):
    """Generate a Markdown table for the list of addons."""
    header = ["addon", "version", "maintainers", "summary"]
    lines = []
    lines.append("Available addons")
    lines.append("----------------")
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")

    for addon in sorted(addons, key=lambda x: x["dir"]):
        link = f"[{addon['dir']}]({addon['dir']}/)"
        lines.append(
            f"| {link} | {addon['version']} | {addon['maintainers']} | {addon['summary']} |"  # noqa: E501
        )

    return "\n".join(lines)


def update_readme(readme_path: Path, table_md: str):
    """Replace the section between markers in README.md with the generated table."""
    text = readme_path.read_text(encoding="utf-8")
    pattern = re.compile(re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER), re.S)
    new_section = f"{START_MARKER}\n\n{table_md}\n\n{END_MARKER}"
    new_text = pattern.sub(new_section, text)
    readme_path.write_text(new_text, encoding="utf-8")


def main():
    root = Path(__file__).parent
    readme = root / "README.md"
    addon_dirs = find_addon_dirs(root)
    addons = []
    for d in addon_dirs:
        manifest = d / "__manifest__.py"
        meta = parse_manifest(manifest)
        if meta:
            addons.append(meta)

    table_md = generate_table(addons)
    update_readme(readme, table_md)
    _logger.info("README.md updated with the list of addons.")


if __name__ == "__main__":
    main()
