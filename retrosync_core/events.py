from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Protocol


class EventType(str, Enum):
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"
    RUN_CANCELLED = "run_cancelled"
    RUN_FAILED = "run_failed"
    OVERALL_UPDATED = "overall_updated"
    SYSTEM_STARTED = "system_started"
    SYSTEM_FINISHED = "system_finished"
    JOB_STARTED = "job_started"
    JOB_FINISHED = "job_finished"
    STEP_STARTED = "step_started"
    STEP_FINISHED = "step_finished"
    TRANSFER_STARTED = "transfer_started"
    TRANSFER_ADVANCED = "transfer_advanced"
    TRANSFER_FINISHED = "transfer_finished"
    SUMMARY_EMITTED = "summary_emitted"


@dataclass(frozen=True)
class SyncEvent:
    event_type: EventType
    run_id: str
    ts: float = field(default_factory=time.time)
    message: str | None = None
    system: str | None = None
    job: str | None = None
    step: str | None = None
    bytes_estimated: int | None = None
    total: int | None = None
    advance: int | None = None
    error: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


class EventSink(Protocol):
    def emit(self, event: SyncEvent) -> None: ...


class NullEventSink:
    def emit(self, event: SyncEvent) -> None:
        _ = event


class MemoryEventSink:
    def __init__(self):
        self.events: list[SyncEvent] = []

    def emit(self, event: SyncEvent) -> None:
        self.events.append(event)
