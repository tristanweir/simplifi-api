"""Strict internal models; version-1 JSON keeps its original canonical shape."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from simplifi.errors import ReadOnlyViolation, SimplifiError
from simplifi.transaction_rules import EDITABLE, rule_payload

Record = dict[str, Any]
RuleId = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_-]+$")]
Digest = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    def to_wire(self):
        return self.model_dump(mode="json")


class UpdateRequest(StrictModel):
    action: Literal["update"]
    rule_id: RuleId
    changes: Record = Field(min_length=1)


class DeleteRequest(StrictModel):
    action: Literal["delete"]
    rule_id: RuleId


Request = Annotated[UpdateRequest | DeleteRequest, Field(discriminator="action")]
REQUESTS = TypeAdapter(list[Request])


def parse_requests(value):
    try:
        if not isinstance(value, list):
            raise TypeError("Operation list required")
        requests = REQUESTS.validate_python(value)
        if not requests or len({r.rule_id for r in requests}) != len(requests):
            raise ValueError("Empty or duplicate request")
        return requests
    except (ValueError, TypeError):
        raise ReadOnlyViolation("Invalid operations; use each valid rule ID once") from None


class Operation(StrictModel):
    rule_id: RuleId
    original: Record

    @model_validator(mode="after")
    def active_original(self):
        if self.original.get("id") != self.rule_id or self.original.get("isDeleted"):
            raise ValueError("An existing active rule is required")
        return self


class UpdateOperation(Operation):
    action: Literal["update"]
    payload: Record
    preview: Record

    @property
    def changes(self):
        return {
            k: self.payload.get(k) for k in EDITABLE if self.payload.get(k) != self.original.get(k)
        }

    @model_validator(mode="after")
    def approved_payload(self):
        if self.payload.get("applyToExistingTransactions") is not False:
            raise ValueError("Batch plans are future-only")
        if rule_payload(self.original, self.changes) != self.payload:
            raise ValueError("Unsupported payload")
        if not isinstance(self.preview.get("transactions"), list):
            raise TypeError("Missing preview transactions")
        return self


class DeleteOperation(Operation):
    action: Literal["delete"]
    payload: None
    preview: None


PlannedOperation = Annotated[UpdateOperation | DeleteOperation, Field(discriminator="action")]


class ChangePlan(StrictModel):
    version: Literal[1]
    kind: Literal["rule-change-plan"]
    dataset_id: Annotated[str, Field(strict=True, min_length=1)]
    baseline: dict[str, Record]
    operations: list[PlannedOperation] = Field(min_length=1)

    @model_validator(mode="after")
    def consistent_baseline(self):
        if len({op.rule_id for op in self.operations}) != len(self.operations):
            raise ValueError("Duplicate rule ID")
        if any(self.baseline.get(op.rule_id) != op.original for op in self.operations):
            raise ValueError("Original differs from baseline")
        return self

    @classmethod
    def from_wire(cls, value):
        try:
            return cls.model_validate(value)
        except (ValidationError, ValueError, TypeError, AttributeError):
            raise ReadOnlyViolation(
                "Invalid rule change plan; batch plans are future-only"
            ) from None


class OperationState(str, Enum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    VERIFIED = "verified"


class JournalStep(StrictModel):
    status: OperationState
    result: Record | None

    @model_validator(mode="after")
    def result_only_after_verification(self):
        if self.status != OperationState.VERIFIED and self.result is not None:
            raise ValueError("Unverified step has a result")
        return self


class JournalState(StrictModel):
    version: Literal[1]
    sequence: Annotated[StrictInt, Field(ge=0)]
    previous: Digest | None
    plan_digest: Digest
    steps: list[JournalStep] = Field(min_length=1)

    @model_validator(mode="after")
    def ordered_steps(self):
        pending = False
        dispatched = False
        for step in self.steps:
            if step.status == OperationState.PENDING:
                pending = True
            elif step.status == OperationState.DISPATCHED:
                if pending or dispatched:
                    raise ValueError("Out-of-order dispatch")
                dispatched = True
            elif pending or dispatched:
                raise ValueError("Out-of-order verification")
        return self

    def replace_step(self, index, status, result=None):
        allowed = {
            (OperationState.PENDING, OperationState.DISPATCHED),
            (OperationState.DISPATCHED, OperationState.VERIFIED),
        }
        if (self.steps[index].status, status) not in allowed:
            raise SimplifiError("Invalid journal state transition")
        steps = list(self.steps)
        steps[index] = JournalStep(status=status, result=result)
        return JournalState.from_wire({**self.to_wire(), "steps": [s.to_wire() for s in steps]})

    @classmethod
    def from_wire(cls, value):
        try:
            return cls.model_validate(value)
        except (ValidationError, ValueError, TypeError):
            raise SimplifiError("Invalid journal checkpoint; manual recovery required") from None
