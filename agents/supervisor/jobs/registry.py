"""JobRegistry：name → ScheduledJob 的小注册表。"""

from __future__ import annotations

from agents.supervisor.jobs.base import ScheduledJob


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, ScheduledJob] = {}

    def register(self, job: ScheduledJob) -> None:
        if job.name in self._jobs:
            raise ValueError(f"重复注册的 Job: {job.name}")
        self._jobs[job.name] = job

    def get(self, kind: str) -> ScheduledJob | None:
        return self._jobs.get(kind)

    def names(self) -> list[str]:
        return list(self._jobs.keys())
