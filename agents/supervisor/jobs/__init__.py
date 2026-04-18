"""调度任务包：PatrolJob / SimpleGoalJob / PostJob + JobRegistry。"""

from agents.supervisor.jobs.base import JobContext, ScheduledJob
from agents.supervisor.jobs.patrol import PatrolJob
from agents.supervisor.jobs.post import PostJob
from agents.supervisor.jobs.registry import JobRegistry
from agents.supervisor.jobs.simple import SimpleGoalJob

__all__ = [
    "JobContext",
    "JobRegistry",
    "PatrolJob",
    "PostJob",
    "ScheduledJob",
    "SimpleGoalJob",
]
