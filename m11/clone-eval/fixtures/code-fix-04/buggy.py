"""Skill frontmatter loader.

Derived from evolution.skills.skill_module.load_skill.
Self-contained so the eval runner needs no repo install.
"""

from pathlib import Path


def load_skill(skill_path: Path) -> dict:
    raw = skill_path.read_text()

    frontmatter = ""
    body = raw
    if raw.strip().startswith("---"):
        parts = raw.split("---", 1)
        if len(parts) >= 2:
            frontmatter = parts[1].strip()
            body = ""

    name = ""
    description = ""
    for line in frontmatter.split("\n"):
        if line.strip().startswith("name:"):
            name = line.split(":", 1)[1].strip().strip("'\"")
        elif line.strip().startswith("description:"):
            description = line.split(":", 1)[1].strip().strip("'\"")

    return {
        "path": skill_path,
        "raw": raw,
        "frontmatter": frontmatter,
        "body": body,
        "name": name,
        "description": description,
    }


def reassemble_skill(frontmatter: str, evolved_body: str) -> str:
    return f"---\n{frontmatter}\n---\n\n{evolved_body}\n"
