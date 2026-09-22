"""v4.6.8: the README's "Latest release" block stays a block, not a ledger.

The owner rule: the README keeps ONLY the current release, in one short
paragraph. Previous releases live in CHANGELOG.md — piling patch notes into
the README makes the front page unreadable. This test pins the contract so
a future release doesn't quietly grow a second block.
"""
import re
from pathlib import Path

README = Path(__file__).resolve().parents[1] / "README.md"


def _release_blocks(text: str) -> list[str]:
    # Bold markers: **Latest release — `vX.Y.Z` ... ** or **Previous — ...**
    return re.findall(r"\*\*(?:Latest release|Previous)[^*]*`[^`]*`", text)


class TestReadmeLatestRelease:
    def test_exactly_one_release_block(self):
        text = README.read_text(encoding="utf-8")
        blocks = _release_blocks(text)
        assert len(blocks) == 1, (
            f"README must carry exactly ONE release block (the latest); "
            f"found {len(blocks)}: {[b[:60] for b in blocks]}")

    def test_the_block_is_the_latest(self):
        text = README.read_text(encoding="utf-8")
        m = re.search(r"\*\*Latest release — `v([0-9.]+)`", text)
        assert m, "no 'Latest release — `vX.Y.Z`' block found"
        # the README's version must equal the package's version
        init = (Path(__file__).resolve().parents[1]
                / "conscio" / "__init__.py").read_text(encoding="utf-8")
        pkg = re.search(r'__version__ = "([^"]+)"', init).group(1)
        assert m.group(1) == pkg, (
            f"README says v{m.group(1)} but the package is v{pkg}")

    def test_no_previous_paragraph_left_behind(self):
        text = README.read_text(encoding="utf-8")
        assert "Previous —" not in text, (
            "previous-release prose belongs in CHANGELOG.md, not the README")

    def test_the_block_is_short(self):
        text = README.read_text(encoding="utf-8")
        m = re.search(r"\*\*Latest release[^*]*\*\*(.+?)\n\n", text, re.DOTALL)
        assert m, "block not found"
        words = len(m.group(1).split())
        assert words <= 120, (
            f"latest-release paragraph is {words} words — keep it short "
            f"(CHANGELOG carries the detail)")
