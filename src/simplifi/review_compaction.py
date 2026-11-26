"""Offline compaction of verified relationship evidence in an encrypted review."""

import hashlib
import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

from simplifi.errors import SimplifiError
from simplifi.journal_store import JournalStore
from simplifi.review_models import Review
from simplifi.reviews import execution_outcomes, load_review, replace_encrypted, review_summary
from simplifi.transaction_rules import digest, load_encrypted, save_encrypted


def _reference(confirmation):
    if not isinstance(confirmation, str) or not re.fullmatch(r"[0-9a-f]{64}", confirmation):
        raise SimplifiError("Invalid relationship plan digest")
    return f"plans/{confirmation[:32]}.dpapi"


def archive_plan(directory, plan, confirmation, review):
    """Called under the review lock. Verify the stored copy before publishing its reference."""
    reference = _reference(confirmation)
    if (plan.get("review_id"), plan.get("dataset_id"), plan.get("kind")) != (
        review.id,
        review.current.dataset_id,
        "transaction-relationship-plan",
    ) or digest(plan) != confirmation:
        raise SimplifiError("Embedded relationship plan identity or digest differs")
    path = Path(directory) / reference
    if not path.exists():
        path.parent.mkdir(exist_ok=True)
        replace_encrypted(path, plan)
    if digest(load_encrypted(path)) != confirmation:
        raise SimplifiError("Archived relationship plan digest differs")
    return reference


def _file_digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.digest()


def compact_review(directory):
    directory = Path(directory)
    source = directory / "review.dpapi"
    with JournalStore(directory / "execution").locked(), JournalStore(directory).locked():
        review = load_review(directory)
        before = source.stat().st_size
        embedded = sum("relationship_plan" in entry for entry in review.history)
        backup = None
        if embedded:
            backup = directory / f"precompact-{uuid4().hex[:16]}.dpapi"
            with source.open("rb") as incoming, backup.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            if _file_digest(source) != _file_digest(backup):
                raise SimplifiError("Encrypted review backup differs")

        history = []
        verified = set()
        for entry in review.history:
            if "relationship_plan" in entry or "relationship_plan_ref" in entry:
                confirmation = entry.get("plan_digest")
                reference = _reference(confirmation)
                if entry.get("relationship_plan_ref", reference) != reference:
                    raise SimplifiError("Unexpected relationship plan reference")
                entry = entry.copy()
                if "relationship_plan" in entry:
                    archive_plan(directory, entry.pop("relationship_plan"), confirmation, review)
                    entry["relationship_plan_ref"] = reference
                elif confirmation not in verified:
                    archived = load_encrypted(directory / reference)
                    if (archived.get("review_id"), archived.get("dataset_id")) != (
                        review.id,
                        review.current.dataset_id,
                    ) or digest(archived) != confirmation:
                        raise SimplifiError(
                            "Referenced relationship plan identity or digest differs"
                        )
                verified.add(confirmation)
            history.append(entry)

        if embedded:
            expected = review.model_copy(update={"history": history})
            staged = directory / f".compact-{uuid4().hex}.dpapi"
            try:
                save_encrypted(staged, expected.to_wire())
                actual = Review.model_validate(load_encrypted(staged))
                if (
                    actual != expected
                    or actual.findings != review.findings
                    or execution_outcomes(actual) != execution_outcomes(review)
                    or review_summary(actual) != review_summary(review)
                ):
                    raise SimplifiError("Compacted review verification failed")
                os.replace(staged, source)
            finally:
                staged.unlink(missing_ok=True)
        return {
            "before_bytes": before,
            "after_bytes": source.stat().st_size,
            "backup": str(backup) if backup else None,
            "compacted": embedded,
            "verified_plans": len(verified),
            "summary": review_summary(review),
        }
