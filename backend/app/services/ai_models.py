"""Task → model tiering registry.

A deliberately tiny seam: every AI *task* resolves to a model name, and today
**every task defaults to `settings.AI_MODEL`** (GLM-5.3-Flash). This is a seam,
not a behaviour change — a stronger model can be plugged per task later (e.g. a
reasoning-heavy `COMPARE` / `RECOMMEND` / `PROPUESTA`) without touching any call
site: they already route through :func:`model_for`.

The optional override lives in ``settings.AI_MODEL_TIERS`` as a comma-separated
``TASK=model`` list, e.g. ``"COMPARE=zai-org/GLM-4.6,PROPUESTA=zai-org/GLM-4.6"``.
Unknown tasks and blank values are ignored, so a typo can never silently drop a
task to an empty model. The override is read on every call so a test (or a live
config reload) sees the current value without re-importing.
"""
from __future__ import annotations

from enum import Enum

from app.core.config import settings

__all__ = ["AITask", "model_for"]


class AITask(str, Enum):
    """The AI tasks the platform performs, each independently tierable."""

    EXTRACT = "EXTRACT"
    COMPARE = "COMPARE"
    RECOMMEND = "RECOMMEND"
    PROPUESTA = "PROPUESTA"
    ANTECEDENTES = "ANTECEDENTES"
    SUMMARY = "SUMMARY"


_TASKS = {task.value for task in AITask}


def _parse_overrides(raw: str) -> dict[str, str]:
    """Parse ``"TASK=model,TASK2=model2"`` into a mapping, dropping garbage."""
    overrides: dict[str, str] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip().upper()
        value = value.strip()
        if key in _TASKS and value:
            overrides[key] = value
    return overrides


def model_for(task: "AITask | str") -> str:
    """The model name to use for ``task`` — an override, else ``settings.AI_MODEL``.

    Accepts an :class:`AITask` or its string name (case-insensitive). Any task
    that is not overridden falls back to the single configured default, so this
    is safe to route every call through today.
    """
    key = task.value if isinstance(task, AITask) else str(task).strip().upper()
    overrides = _parse_overrides(settings.AI_MODEL_TIERS)
    return overrides.get(key, settings.AI_MODEL)
