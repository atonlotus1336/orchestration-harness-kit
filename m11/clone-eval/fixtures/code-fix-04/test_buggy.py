from buggy import load_skill, reassemble_skill


SAMPLE_SKILL = """---
name: test-skill
description: A skill for testing things
version: 1.0.0
metadata:
  hermes:
    tags: [testing]
---

# Test Skill — Testing Things

## When to Use
Use this when you need to test things.

## Procedure
1. First, do the thing
2. Then, verify it worked
3. Report results

## Pitfalls
- Don't forget to check edge cases
"""


def test_parses_frontmatter(tmp_path):
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(SAMPLE_SKILL)
    skill = load_skill(skill_file)
    assert skill["name"] == "test-skill"
    assert skill["description"] == "A skill for testing things"
    assert "version: 1.0.0" in skill["frontmatter"]


def test_parses_body(tmp_path):
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(SAMPLE_SKILL)
    skill = load_skill(skill_file)
    assert "# Test Skill" in skill["body"]
    assert "## Procedure" in skill["body"]
    assert "Don't forget" in skill["body"]


def test_raw_contains_everything(tmp_path):
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(SAMPLE_SKILL)
    skill = load_skill(skill_file)
    assert skill["raw"] == SAMPLE_SKILL


def test_path_is_stored(tmp_path):
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(SAMPLE_SKILL)
    skill = load_skill(skill_file)
    assert skill["path"] == skill_file


def test_reassemble_roundtrip():
    frontmatter = "name: my-skill\ndescription: Does stuff"
    body = "# My Skill\nDo the thing."
    result = reassemble_skill(frontmatter, body)
    assert result.startswith("---\n")
    assert "name: my-skill" in result
    assert "# My Skill" in result
