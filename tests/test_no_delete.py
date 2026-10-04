"""The IMAP layer must never contain a way to delete mail."""
import re
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "servers" / "mailcleaner"
FORBIDDEN = [r"expunge\(", r'"EXPUNGE"', r"\+FLAGS", r"-FLAGS", r"\(\\\\Deleted\)", r"\.delete\(", r"empty_trash",
             r"delete_permanently", r"move_to_trash", r'"STORE"', r'"COPY"', r"_imap\.close\(", r"conn\.close\(", r"_conn\(\)\.close\("]


def code_only(text: str) -> str:
    text = re.sub(r'""".*?"""', "", text, flags=re.S)
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_no_delete_paths():
    for path in SERVER.glob("*.py"):
        body = code_only(path.read_text())
        for pat in FORBIDDEN:
            assert not re.search(pat, body), f"{path.name} contains {pat}"


def test_move_refuses_without_move_capability():
    from errors import CleanerError
    from imap_client import ImapSession
    s = ImapSession("h", 993, "a@b.c", "x")
    s._capabilities = set()
    try:
        s.move(["1"], "T")
    except CleanerError as exc:
        assert "copy + delete" in str(exc)
    else:
        raise AssertionError("move() must stop without MOVE support")
