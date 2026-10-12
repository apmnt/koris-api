"""Shared acquisition pipeline with pure planning and conversion."""

from .games import acquire_game, fetch_game, run_batch
from .planning import missing_components, plan_requests, resolve_reference
from .types import BatchReport, Component, GameOutcome, GameRef, Provider

__all__ = [
    "BatchReport",
    "Component",
    "GameOutcome",
    "GameRef",
    "Provider",
    "acquire_game",
    "fetch_game",
    "missing_components",
    "plan_requests",
    "resolve_reference",
    "run_batch",
]
