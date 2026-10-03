"""
Stage timing breakdown tracker for payment scenarios and MTProto interactions.
Records execution duration across granular operational phases.
"""

import time
from typing import Any


class StageTimer:
    """
    High-precision stopwatch that records incremental durations
    across bot interaction checkpoints.
    """

    def __init__(self) -> None:
        self._start_time: float = time.perf_counter()
        self._last_checkpoint: float = self._start_time
        self.stages: list[dict[str, Any]] = []

    def record_stage(self, stage: str, description: str) -> float:
        """
        Record elapsed seconds since the preceding checkpoint or initialization.
        """
        now = time.perf_counter()
        duration = round(now - self._last_checkpoint, 2)
        self._last_checkpoint = now
        self.stages.append(
            {
                "stage": stage,
                "description": description,
                "duration_sec": duration,
            }
        )
        return duration

    @property
    def total_duration_sec(self) -> float:
        """Total elapsed duration in seconds since timer inception."""
        return round(time.perf_counter() - self._start_time, 2)

    def to_dict(self) -> dict[str, Any]:
        """Return structured timing dictionary."""
        return {
            "total_duration_sec": self.total_duration_sec,
            "stages": self.stages,
        }
