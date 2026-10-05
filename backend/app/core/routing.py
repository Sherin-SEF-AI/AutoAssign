"""Provider level routing answer, shared by the provider adapters and the ETA layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RawLeg:
    """A provider answer before quantile factors are applied."""

    duration_s: float
    distance_m: float
    source: str
    free_flow_s: float | None = None
    provider_ref: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
