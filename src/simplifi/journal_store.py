"""Encrypted journal persistence, independent of rule execution and API transport."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

from simplifi.errors import ReadOnlyViolation, SimplifiError
from simplifi.rule_plan_models import ChangePlan, JournalState, OperationState
from simplifi.transaction_rules import digest, load_encrypted, save_encrypted


class JsonStorage(Protocol):
    def read(self, path: Path): ...
    def write(self, path: Path, value): ...
    def exists(self, path: Path) -> bool: ...
    def files(self, directory: Path, pattern: str) -> list[Path]: ...


class EncryptedJsonStorage:
    def read(self, path):
        return load_encrypted(path)

    def write(self, path, value):
        save_encrypted(path, value)

    def exists(self, path):
        return path.exists()

    def files(self, directory, pattern):
        return sorted(directory.glob(pattern))


class JournalStore:
    def __init__(self, directory, *, storage: JsonStorage | None = None):
        self.directory = Path(directory)
        self.storage = storage if storage is not None else EncryptedJsonStorage()

    @contextmanager
    def locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / "executor.lock").open("a+b") as stream:
            stream.seek(0, 2)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise SimplifiError("Another executor holds this journal lock") from None
            try:
                yield
            finally:
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)

    def _path(self, sequence):
        return self.directory / f"checkpoint-{sequence:08d}.dpapi"

    def read(self):
        previous = None
        state = None
        for index, path in enumerate(self.storage.files(self.directory, "checkpoint-*.dpapi")):
            if path != self._path(index):
                raise SimplifiError("Journal checkpoint gap; manual recovery required")
            wire = self.storage.read(path)
            state = JournalState.from_wire(wire)
            if state.sequence != index or state.previous != (
                digest(previous) if previous else None
            ):
                raise SimplifiError("Journal checkpoint chain invalid; manual recovery required")
            previous = wire
        return state

    def start(self, plan: ChangePlan, confirmation: str):
        state = self.read()
        if state is None:
            if self.storage.files(self.directory, "backup-*.dpapi"):
                raise SimplifiError("Journal missing but backups exist; manual recovery required")
            state = JournalState.from_wire(
                {
                    "version": 1,
                    "sequence": 0,
                    "previous": None,
                    "plan_digest": confirmation,
                    "steps": [{"status": "pending", "result": None} for _ in plan.operations],
                }
            )
            self.storage.write(self._path(0), state.to_wire())
        if state.plan_digest != confirmation or len(state.steps) != len(plan.operations):
            raise ReadOnlyViolation("Journal belongs to a different plan")
        self.verify_backups(plan, state)
        return state

    def checkpoint(self, state: JournalState):
        previous = self.storage.read(self._path(state.sequence))
        # Reload the durable predecessor rather than hashing an in-memory update.
        durable = JournalState.from_wire(previous)
        if (
            durable.plan_digest != state.plan_digest
            or durable.sequence != state.sequence
            or len(durable.steps) != len(state.steps)
        ):
            raise SimplifiError("Checkpoint differs from its durable execution journal")
        updated = JournalState.from_wire(
            {**state.to_wire(), "sequence": state.sequence + 1, "previous": digest(previous)}
        )
        self.storage.write(self._path(updated.sequence), updated.to_wire())
        return updated

    def _backup(self, index):
        return self.directory / f"backup-{index:08d}.dpapi"

    def save_backup(self, index, operation, confirmation):
        path = self._backup(index)
        expected = {"plan_digest": confirmation, "operation": operation.to_wire()}
        if self.storage.exists(path):
            if self.storage.read(path) != expected:
                raise SimplifiError("Existing operation backup differs from this plan")
        else:
            self.storage.write(path, expected)

    def verify_backups(self, plan, state):
        for index, (operation, step) in enumerate(zip(plan.operations, state.steps)):
            if step.status == OperationState.PENDING:
                continue
            expected = {"plan_digest": state.plan_digest, "operation": operation.to_wire()}
            path = self._backup(index)
            if not self.storage.exists(path) or self.storage.read(path) != expected:
                raise SimplifiError("Execution backup missing or changed; manual recovery required")
            if step.status == OperationState.VERIFIED:
                if operation.action == "delete" and step.result is not None:
                    raise SimplifiError("Deleted rule has a journal result")
                if operation.action == "update" and (
                    step.result is None or step.result.get("id") != operation.rule_id
                ):
                    raise SimplifiError("Updated rule has no matching journal result")

    def status(self):
        state = self.read()
        if state is None:
            return {"started": False}
        return {
            "started": True,
            "confirmation": state.plan_digest,
            "states": [s.status.value for s in state.steps],
            "complete": all(s.status == OperationState.VERIFIED for s in state.steps),
        }
