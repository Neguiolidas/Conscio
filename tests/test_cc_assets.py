import pathlib
import conscio.integrations.claude_code as cc

ASSETS = pathlib.Path(cc.__file__).parent / "assets"
EXPECTED = {"recall", "remember", "state", "society", "relay", "reflect",
            "propose", "handoff", "awake", "sleep"}


def test_all_ten_commands_present():
    got = {p.stem for p in (ASSETS / "commands").glob("*.md")}
    assert got == EXPECTED


def test_commands_have_frontmatter_description():
    for p in (ASSETS / "commands").glob("*.md"):
        text = p.read_text()
        assert text.startswith("---"), p.name
        assert "description:" in text, p.name


def test_skill_present():
    sk = ASSETS / "skills" / "conscio" / "SKILL.md"
    assert sk.is_file()
    assert "description:" in sk.read_text()
