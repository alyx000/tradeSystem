"""验证 .cursor / .claude 下指向 .agents 真源的 symlink 拓扑完整。

真源在 .agents/skills/ 与 .agents/rules/*.md。
.cursor/skills 与 .claude/skills 是目录 symlink。
.cursor/rules/<name>.mdc 与 .claude/rules/<name>.md 是文件 symlink。

任何 symlink 断裂、目标缺失、文件名/扩展名错位都会让此测试失败。
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_RULES = REPO_ROOT / ".agents" / "rules"
AGENTS_SKILLS = REPO_ROOT / ".agents" / "skills"


def _truth_rule_names() -> list[str]:
    return sorted(p.stem for p in AGENTS_RULES.glob("*.md"))


def test_agents_truth_source_exists() -> None:
    assert AGENTS_SKILLS.is_dir(), ".agents/skills 真源目录缺失"
    assert (AGENTS_SKILLS / "INDEX.md").is_file(), ".agents/skills/INDEX.md 缺失"
    assert AGENTS_RULES.is_dir(), ".agents/rules 真源目录缺失"
    assert _truth_rule_names(), ".agents/rules/*.md 真源为空"


@pytest.mark.parametrize("shell_dir", [".cursor/skills", ".claude/skills"])
def test_skills_dir_symlink(shell_dir: str) -> None:
    p = REPO_ROOT / shell_dir
    assert p.is_symlink(), f"{shell_dir} 应为目录 symlink"
    assert p.resolve() == AGENTS_SKILLS.resolve(), (
        f"{shell_dir} symlink 未指向 .agents/skills 真源"
    )
    assert (p / "INDEX.md").is_file(), f"通过 {shell_dir} 无法访问 INDEX.md"


@pytest.mark.parametrize("ext_pair", [(".cursor/rules", ".mdc"), (".claude/rules", ".md")])
def test_rules_file_symlinks(ext_pair: tuple[str, str]) -> None:
    shell_dir, ext = ext_pair
    shell_path = REPO_ROOT / shell_dir
    assert shell_path.is_dir(), f"{shell_dir} 目录缺失"

    truth_names = set(_truth_rule_names())
    shell_files = {p.stem: p for p in shell_path.glob(f"*{ext}")}

    assert set(shell_files) == truth_names, (
        f"{shell_dir} 与 .agents/rules 的规则名集合不一致："
        f"shell={sorted(shell_files)} truth={sorted(truth_names)}"
    )
    for name, p in shell_files.items():
        assert p.is_symlink(), f"{p} 应为 symlink"
        assert p.resolve() == (AGENTS_RULES / f"{name}.md").resolve(), (
            f"{p} 未指向 .agents/rules/{name}.md"
        )
