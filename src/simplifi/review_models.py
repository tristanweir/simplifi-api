"""Versioned local review formats. Financial evidence is stored encrypted."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field, StrictBool, model_serializer, model_validator

from simplifi.category_update import transaction_edit_payload
from simplifi.rule_plan_models import StrictModel

Record = dict[str, Any]


class Snapshot(StrictModel):
    version: Literal[1] = 1
    dataset_id: str = Field(min_length=1)
    downloaded_at: str
    start: date
    end: date
    transactions: list[Record]
    categories: list[Record]

    @model_validator(mode="after")
    def valid_range(self):
        if self.start > self.end:
            raise ValueError("Invalid date range")
        ids = [str(t["id"]) for t in self.transactions]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate transaction identifiers")
        return self


class Finding(StrictModel):
    id: str = Field(min_length=1)
    number: int = Field(gt=0)
    title: str
    rationale: str
    proposed: str = "Review individually"
    transaction_ids: list[str] = Field(min_length=1)
    evidence: Record = Field(default_factory=dict)


class Decision(StrictModel):
    id: str = Field(min_length=1)
    finding_id: str
    transaction_ids: list[str] = Field(min_length=1)
    action: Literal["category", "transfer", "exclusions", "confirm", "dismiss", "unresolved"]
    category_id: str | None = None
    incoming: StrictBool | None = None
    exclusions: dict[Literal["isExcludedFromReports", "isExcludedFromF2S"], StrictBool] = Field(
        default_factory=dict
    )
    note: str = ""
    # Per-transaction exceptions are resolved to explicit IDs, never payee patterns.
    exceptions: dict[str, str] = Field(default_factory=dict)
    # Explicitly accepted reimbursement evidence; never an account-transfer link.
    offsets: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def explicit_targets(self):
        if len(self.transaction_ids) != len(set(self.transaction_ids)):
            raise ValueError("Duplicate decision selection")
        if not self.exceptions.keys() <= set(self.transaction_ids):
            raise ValueError("Exception did not match selection")
        if not self.offsets.keys() <= set(self.transaction_ids):
            raise ValueError("Offset credit did not match selection")
        if self.action == "category" and not self.category_id:
            raise ValueError("Category decision requires a category ID")
        if self.action != "category" and (self.category_id or self.exceptions or self.offsets):
            raise ValueError("Only category decisions accept targets")
        if (self.action == "transfer") != (self.incoming is not None):
            raise ValueError("Transfer requires an explicit direction")
        if self.action == "exclusions" and not self.exclusions:
            raise ValueError("Exclusions action requires explicit flags")
        if self.action in {"confirm", "dismiss", "unresolved"} and self.exclusions:
            raise ValueError("Local dispositions cannot edit exclusions")
        return self

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        wire = handler(self)
        # Retain canonical v1 bytes/digests for previously saved plans and journals.
        if self.incoming is None:
            wire.pop("incoming")
        if not self.exclusions:
            wire.pop("exclusions")
        return wire


class Review(StrictModel):
    version: Literal[1] = 1
    kind: Literal["transaction-review"] = "transaction-review"
    id: str = Field(min_length=1)
    original: Snapshot
    current: Snapshot
    findings: list[Finding] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    history: list[Record] = Field(default_factory=list)
    context: Record = Field(default_factory=dict)
    replacements: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consistent(self):
        if self.original.dataset_id != self.current.dataset_id:
            raise ValueError("Snapshot datasets differ")
        for values in (
            [f.id for f in self.findings],
            [f.number for f in self.findings],
            [d.id for d in self.decisions],
        ):
            if len(values) != len(set(values)):
                raise ValueError("Duplicate review identifiers")
        findings = {f.id: f for f in self.findings}
        originals = {str(t["id"]) for t in self.original.transactions}
        current = {str(t["id"]) for t in self.current.transactions}
        if any(a not in originals or b not in current for a, b in self.replacements.items()):
            raise ValueError("Replacement requires original and current evidence")
        for f in self.findings:
            if not set(f.transaction_ids) <= originals | current:
                raise ValueError("Finding references missing evidence")
        for d in self.decisions:
            if d.finding_id not in findings or not set(d.transaction_ids) <= set(
                findings[d.finding_id].transaction_ids
            ):
                raise ValueError("Decision exceeds its finding")
        return self


class TransactionOperation(StrictModel):
    action: Literal["category", "transfer", "exclusions", "confirm"]
    transaction_id: str
    original: Record
    category: Record | None = None
    payload: Record | None = None
    decision_ids: list[str]
    incoming: StrictBool | None = None
    exclusions: dict[Literal["isExcludedFromReports", "isExcludedFromF2S"], StrictBool] = Field(
        default_factory=dict
    )

    @model_validator(mode="after")
    def bound_payload(self):
        if str(self.original.get("id")) != self.transaction_id:
            raise ValueError("Original ID differs")
        if self.action == "confirm":
            if (
                self.category is not None
                or self.payload is not None
                or self.incoming is not None
                or self.exclusions
            ):
                raise ValueError("Confirmation cannot mutate a transaction")
            return self
        if (self.action == "category") != (self.category is not None):
            raise ValueError("Category action requires exactly one category")
        if (self.action == "transfer") != (self.incoming is not None):
            raise ValueError("Transfer action requires a direction")
        if (
            transaction_edit_payload(
                self.original,
                category=self.category,
                incoming=self.incoming,
                exclusions=self.exclusions,
            )
            != self.payload
        ):
            raise ValueError("Payload differs from selected changes")
        return self

    @model_serializer(mode="wrap")
    def serialize(self, handler):
        wire = handler(self)
        if self.incoming is None:
            wire.pop("incoming")
        if not self.exclusions:
            wire.pop("exclusions")
        return wire


class TransactionPlan(StrictModel):
    version: Literal[1] = 1
    kind: Literal["transaction-change-plan"] = "transaction-change-plan"
    review_id: str
    dataset_id: str
    decisions: list[Decision]
    operations: list[TransactionOperation] = Field(min_length=1)

    @model_validator(mode="after")
    def unique(self):
        ids = [o.transaction_id for o in self.operations]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate planned transaction")
        return self


class TransactionReceipt(StrictModel):
    """Explicit receipt format for a separately scoped, verified operation."""

    version: Literal[1] = 1
    kind: Literal["transaction-receipt"] = "transaction-receipt"
    dataset_id: str = Field(min_length=1)
    review_id: str = Field(min_length=1)
    verified_at: str = Field(min_length=1)
    operation: TransactionOperation
    result: Record
