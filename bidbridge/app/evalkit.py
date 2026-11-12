"""Extraction benchmark.  Usage:  python -m app.evalkit eval/corpus [--min-f1 0.85]

Corpus layout: ``name.txt`` (tender text; PDFs/DOCX also accepted) + ``name.json`` with the hand-labelled
requirements.  Matching is on (type, doc_kind, subject for experience, value, op, due); free text is ignored.
Run it on *real* tenders you have labelled to obtain honest precision/recall for your own documents.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.services.extractor import ExtractedRequirement, extract_rules, locate_quote, pick_deadline
from app.services.ingest import parse_document

Key = tuple[str, str, str, float | None, str, str]


def _key(type_: str, doc_kind: str | None, subject: str | None, value: float | None, op: str | None, due: str | None) -> Key:
    return (type_, doc_kind or "", (subject or "") if type_ == "experience" else "",
            round(float(value), 2) if value is not None else None, op or "", due or "")


def key_of(r: ExtractedRequirement) -> Key:
    return _key(r.type.value, r.doc_kind.value if r.doc_kind else None, r.subject, r.value, r.op,
                r.due.isoformat() if r.due else None)


def key_of_expected(e: dict) -> Key:
    return _key(e["type"], e.get("doc_kind"), e.get("subject"), e.get("value"), e.get("op"), e.get("due"))


@dataclass
class CaseResult:
    name: str
    missing: list[Key]
    spurious: list[Key]
    deadline_ok: bool
    quotes_ok: float


@dataclass
class EvalReport:
    per_type: dict[str, dict[str, float]] = field(default_factory=dict)
    overall: dict[str, float] = field(default_factory=dict)
    deadline_accuracy: float = 0.0
    quote_verification: float = 0.0
    cases: list[CaseResult] = field(default_factory=list)


def _prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    p = tp / (tp + fp) if tp + fp else 1.0
    r = tp / (tp + fn) if tp + fn else 1.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(2 * p * r / (p + r), 3) if p + r else 0.0,
            "tp": tp, "fp": fp, "fn": fn}


def run(corpus: Path) -> EvalReport:
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    rep = EvalReport()
    dl_hits = quotes = total_reqs = 0
    files = sorted(p for p in corpus.iterdir() if p.suffix in {".txt", ".pdf", ".docx"})
    for f in files:
        label = json.loads(f.with_suffix(".json").read_text(encoding="utf-8"))
        pages = parse_document(f.name, f.read_bytes())
        reqs = extract_rules(pages)
        got = {key_of(r) for r in reqs}
        want = {key_of_expected(e) for e in label["requirements"]}
        for k in got & want:
            counts[k[0]][0] += 1
        for k in got - want:
            counts[k[0]][1] += 1
        for k in want - got:
            counts[k[0]][2] += 1
        exp_dl = date.fromisoformat(label["deadline"]) if label.get("deadline") else None
        ok_q = sum(locate_quote(r.quote, pages) == r.page for r in reqs)
        quotes += ok_q
        total_reqs += len(reqs)
        dl_ok = pick_deadline(reqs) == exp_dl
        dl_hits += dl_ok
        rep.cases.append(CaseResult(f.name, sorted(want - got, key=str), sorted(got - want, key=str), dl_ok,
                                    ok_q / len(reqs) if reqs else 1.0))
    rep.per_type = {t: _prf(*c) for t, c in sorted(counts.items())}
    rep.overall = _prf(*(sum(c[i] for c in counts.values()) for i in range(3)))
    rep.deadline_accuracy = round(dl_hits / len(files), 3) if files else 0.0
    rep.quote_verification = round(quotes / total_reqs, 3) if total_reqs else 1.0
    return rep


def main(argv: list[str]) -> int:
    corpus = Path(argv[0] if argv else "eval/corpus")
    min_f1 = float(argv[argv.index("--min-f1") + 1]) if "--min-f1" in argv else 0.0
    rep = run(corpus)
    print(f"{'type':<12}{'prec':>7}{'recall':>8}{'f1':>7}{'tp':>5}{'fp':>5}{'fn':>5}")
    for t, m in {**rep.per_type, "OVERALL": rep.overall}.items():
        print(f"{t:<12}{m['precision']:>7}{m['recall']:>8}{m['f1']:>7}{m['tp']:>5}{m['fp']:>5}{m['fn']:>5}")
    print(f"\ndeadline accuracy: {rep.deadline_accuracy}   quote verification: {rep.quote_verification}")
    for c in rep.cases:
        if c.missing or c.spurious or not c.deadline_ok:
            print(f"\n[{c.name}] deadline_ok={c.deadline_ok}\n  missing : {c.missing}\n  spurious: {c.spurious}")
    return 0 if rep.overall["f1"] >= min_f1 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
