"""
Simple asyncio-based task queue.
Tracks running tasks so /cancel works and /status shows active jobs.
"""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Callable, Coroutine, Any


@dataclass
class TaskInfo:
    id: str
    name: str
    module: str
    started_at: float = field(default_factory=time.time)
    task: asyncio.Task | None = None
    model_info: str = ""   # LLM model name used by this task, if applicable


class TaskQueue:
    def __init__(self):
        self._tasks: dict[str, TaskInfo] = {}

    def submit(
        self,
        task_id: str,
        name: str,
        module: str,
        coro: Coroutine,
        model_info: str = "",
    ) -> asyncio.Task:
        info = TaskInfo(id=task_id, name=name, module=module, model_info=model_info)
        t = asyncio.create_task(coro)
        info.task = t
        self._tasks[task_id] = info

        def _cleanup(future):
            self._tasks.pop(task_id, None)

        t.add_done_callback(_cleanup)
        return t

    def cancel(self, task_id: str) -> bool:
        info = self._tasks.get(task_id)
        if info and info.task:
            return info.task.cancel()
        return False

    def cancel_all(self) -> int:
        count = sum(1 for i in self._tasks.values() if i.task and i.task.cancel())
        return count

    def running(self) -> list[TaskInfo]:
        return list(self._tasks.values())

    def is_busy(self) -> bool:
        return bool(self._tasks)


# Global queue instance
queue = TaskQueue()
