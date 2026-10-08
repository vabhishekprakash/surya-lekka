"""Paths and review order for the local redaction tool."""

from dataclasses import dataclass
from pathlib import Path

DPI = 200
MAX_PDF_BYTES = 20 * 1024 * 1024

# Held-out vendors first, then the rest. Q12 and Q16 are already redacted.
ORDER = [
    "Q10", "Q11", "Q13", "Q14",
    "Q01", "Q05",
    "Q02", "Q03", "Q04", "Q06", "Q07", "Q08", "Q09",
    "Q15", "Q17", "Q18", "Q20",
]
HELD_OUT = {"Q10", "Q11", "Q13", "Q14"}
SKIP = {"Q12", "Q16"}

SOURCE_EXTS = (".pdf", ".jpg", ".jpeg", ".png")
REPO = Path(__file__).resolve().parents[2]


class ConfigError(Exception):
    pass


def _inside(child, parent):
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class Paths:
    originals: Path
    redacted: Path
    review: Path
    pii_terms: Path

    @classmethod
    def from_root(cls, root, repo=REPO):
        root = Path(root).resolve()
        p = cls(root / "originals", root / "redacted", root / "review", root / "pii_terms")
        p.validate(repo)
        return p

    def validate(self, repo=REPO):
        repo = Path(repo).resolve()
        for d in (self.originals, self.redacted, self.review, self.pii_terms):
            if _inside(d.resolve(), repo):
                raise ConfigError("data folders must be outside the repository")
        orig = self.originals.resolve()
        for d in (self.redacted, self.review):
            d = d.resolve()
            if d == orig or _inside(d, orig) or _inside(orig, d):
                raise ConfigError("output and review folders must be separate from originals")

    def original(self, doc_id):
        """First existing source file for doc_id, checked by exact name (no listing)."""
        for ext in SOURCE_EXTS:
            p = self.originals / f"{doc_id}{ext}"
            if p.is_file():
                return p
        return None

    def output(self, doc_id):
        return self.redacted / f"{doc_id}.pdf"

    def state(self, doc_id):
        return self.review / f"{doc_id}.json"

    def terms(self, doc_id):
        return self.pii_terms / f"{doc_id}.txt"

    @property
    def manifest(self):
        return self.redacted / "manifest.json"


def check_doc_id(doc_id):
    if doc_id not in ORDER:
        raise KeyError("unknown or skipped doc id")
    return doc_id
