"""Offline, conservative import of legacy encrypted review evidence."""

from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path

from simplifi.errors import SimplifiError
from simplifi.review_models import Decision, Finding, Review, Snapshot
from simplifi.reviews import create_review, review_summary
from simplifi.transaction_rules import digest, load_encrypted
from simplifi.views.classify import transaction_date


def _transactions(value):
    if isinstance(value, dict):
        if all(k in value for k in ("id", "type", "amount", "accountId")):
            yield value
        else:
            for nested in value.values():
                yield from _transactions(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _transactions(nested)


def _same_result(a, b):
    # Ignore modification metadata, never differences in financial/link fields.
    ignored = {"modifiedAt", "dbVersion", "userModifiedAt"}
    return {k: v for k, v in a.items() if k not in ignored} == {
        k: v for k, v in b.items() if k not in ignored
    }


def build_legacy_import(source, *, start=None, end=None):
    """Build and preview in memory; does not create files or an API client."""
    source = Path(source)
    original_wire = load_encrypted(source / "snapshot.dpapi")
    current_wire = load_encrypted(source / "snapshot-current.dpapi")
    inventory = load_encrypted(source / "findings.dpapi")
    dataset = original_wire["dataset_id"]
    if not dataset or current_wire["dataset_id"] != dataset:
        raise SimplifiError("Legacy snapshot datasets differ")
    dates = [date.fromisoformat(transaction_date(t)) for t in original_wire["transactions"]]
    start, end = start or min(dates), end or max(dates)
    history, issues, receipts, records, replacements = [], [], {}, {}, {}
    manifest = {}
    notes = {}
    finding_notes = {}
    for path in sorted(source.rglob("*.dpapi")):
        if path.is_symlink():
            raise SimplifiError("Legacy evidence symlinks are not supported")
        name = path.relative_to(source).as_posix()
        manifest[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        if name in {"snapshot.dpapi", "snapshot-current.dpapi", "findings.dpapi"}:
            continue
        try:
            evidence = load_encrypted(path)
        except SimplifiError:
            issues.append({"artifact": name, "reason": "Unreadable encrypted evidence"})
            continue
        if isinstance(evidence, dict) and evidence.get("dataset_id", dataset) != dataset:
            raise SimplifiError("Legacy evidence belongs to another dataset")
        history.append(
            {"kind": "legacy-evidence", "artifact": name, "evidence_digest": digest(evidence)}
        )
        if isinstance(evidence, dict):
            note = {
                k: v for k, v in evidence.items() if k in {"note", "notes", "method", "statuses"}
            }
            if note:
                notes[name] = note
            if isinstance(evidence.get("notes"), dict):
                for number, text in evidence["notes"].items():
                    if str(number).isdigit() and isinstance(text, str):
                        finding_notes.setdefault(int(number), []).append(text)
            if isinstance(evidence.get("finding"), int) and isinstance(evidence.get("note"), str):
                finding_notes.setdefault(evidence["finding"], []).append(evidence["note"])
        verified_artifact = (
            "verified" in path.stem
            or ("final" in path.stem and "plan" not in path.stem)
            or path.stem.endswith("verification")
        )
        for t in _transactions(evidence):
            records.setdefault(str(t["id"]), t)
            if verified_artifact:
                receipts.setdefault(str(t["id"]), []).append(t)
        if (
            isinstance(evidence, dict)
            and evidence.get("original_id")
            and evidence.get("transaction")
        ):
            new = str(evidence["transaction"]["id"])
            old = str(evidence["original_id"])
            if old in replacements and replacements[old] != new:
                raise SimplifiError("Ambiguous legacy replacement IDs")
            replacements[old] = new
    original = {str(t["id"]): t for t in original_wire["transactions"]}
    current = {str(t["id"]): t for t in current_wire["transactions"]}
    if len(original) != len(original_wire["transactions"]) or len(current) != len(
        current_wire["transactions"]
    ):
        raise SimplifiError("Duplicate transaction identifiers in legacy snapshots")
    findings, decisions, outcomes = [], [], {}
    for f in inventory["findings"]:
        ids = [str(t) for t in f["transaction_ids"]]
        for tid in ids:
            if tid not in original:
                evidence = records.get(tid) or current.get(tid)
                if evidence is None:
                    raise SimplifiError("Finding has no recoverable transaction evidence")
                original[tid] = evidence
                issues.append({"transaction_id": tid, "reason": "Original snapshot missing ID"})
        fid = digest([dataset, "legacy-finding", f["number"]])
        findings.append(
            Finding(
                id=fid,
                number=f["number"],
                title=f["title"],
                rationale=f.get("rationale", ""),
                proposed=f.get("proposed", ""),
                transaction_ids=ids,
                evidence={"legacy": f},
            )
        )
        for tid in ids:
            mapped = replacements.get(tid, tid)
            t = current.get(mapped)
            verified = t is not None and any(_same_result(r, t) for r in receipts.get(mapped, []))
            if verified:
                decisions.append(
                    Decision(
                        id=digest([fid, tid]),
                        finding_id=fid,
                        transaction_ids=[tid],
                        action="confirm",
                        note="Imported verified legacy result; not a new instruction to write. "
                        + " ".join(finding_notes.get(f["number"], [])),
                    )
                )
                before = original[tid]
                outcomes[mapped] = "already_correct" if _same_result(before, t) else "changed"
            else:
                issues.append(
                    {
                        "finding": f["number"],
                        "transaction_id": tid,
                        "reason": "No matching verified receipt; needs review",
                    }
                )
    replacements = {a: b for a, b in replacements.items() if a in original and b in current}

    def snapshot(wire, transactions):
        return Snapshot(
            dataset_id=dataset,
            downloaded_at=wire["downloaded_at"],
            start=start,
            end=end,
            transactions=transactions,
            categories=wire["categories"],
        )

    html_path = source / "category-review.html"
    context = {
        "import_issues": issues,
        "private_account_context": current_wire.get("private_account_context", {}),
        "legacy_manifest": manifest,
        "legacy_notes": notes,
    }
    if html_path.exists():
        context["legacy_html_annotations"] = html_path.read_text(encoding="utf-8")
    history.append(
        {"kind": "execution", "source": "legacy-verified-receipts", "outcomes": outcomes}
    )
    review = Review(
        id=digest([dataset, "legacy", digest(inventory)]),
        original=snapshot(original_wire, list(original.values())),
        current=snapshot(current_wire, list(current.values())),
        findings=findings,
        decisions=decisions,
        history=history,
        context=context,
        replacements=replacements,
    )
    preview = {
        **review_summary(review),
        "evidence_artifacts": len(history) - 1,
        "replacement_ids": len(replacements),
        "import_digest": digest(review.to_wire()),
        "live_writes": 0,
    }
    return review, preview


def import_legacy_review(source, destination, *, confirmation, start=None, end=None):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise SimplifiError("Import destination must be separate from legacy evidence")
    review, preview = build_legacy_import(source, start=start, end=end)
    if confirmation != preview["import_digest"]:
        raise SimplifiError("Import evidence changed or confirmation differs")
    # Keep bulky historical snapshots outside the active projection, retaining exact
    # encrypted originals with an approved manifest. Resume an interrupted copy only
    # when its bytes still match that manifest.
    for name, expected in review.context["legacy_manifest"].items():
        payload = (source / name).read_bytes()
        if hashlib.sha256(payload).hexdigest() != expected:
            raise SimplifiError("Legacy evidence changed during import")
        target = destination / "legacy-evidence" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != payload:
                raise SimplifiError("Imported evidence differs from approved source")
        else:
            with target.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
    create_review(destination, review)
    return preview
