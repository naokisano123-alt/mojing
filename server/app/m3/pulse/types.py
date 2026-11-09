"""M3.2 世界脉动核心数据类型与契约 DTO。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

JobKind = Literal["seed", "advance"]
JobStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "waiting_for_model",
    "cancelled",
]
SessionMode = Literal["unbound", "fake", "independent", "reuse_narration"]
ComplexityMode = Literal["light", "standard", "deep"]
UsageSource = Literal["actual", "estimated"]

VALID_JOB_KINDS: frozenset[str] = frozenset({"seed", "advance"})
VALID_JOB_STATUSES: frozenset[str] = frozenset({
    "pending",
    "running",
    "completed",
    "failed",
    "waiting_for_model",
    "cancelled",
})
VALID_COMPLEXITY_MODES: frozenset[str] = frozenset({"light", "standard", "deep"})
VALID_SESSION_MODES: frozenset[str] = frozenset({
    "unbound",
    "fake",
    "independent",
    "reuse_narration",
})


@dataclass(frozen=True)
class PulseTrigger:
    """入队触发器契约。"""

    instance_id: str
    kind: JobKind
    trigger_adventure_id: str
    trigger_revision: int
    base_world_revision: int
    target_world_minute: int
    idempotency_key: str | None = None
    coalesce: bool = True
    max_attempts: int = 3
    payload: dict[str, Any] = field(default_factory=dict)
    now: float | None = None


@dataclass(frozen=True)
class PulseBudgetConfig:
    """实例级世界模拟预算与冷却配置（默认标准复杂度）。"""

    complexity: ComplexityMode = "standard"
    max_calls_per_round: int = 2
    max_tokens_per_round: int = 4096
    daily_call_budget: int = 12
    daily_token_budget: int = 24000
    cooldown_seconds: float = 5.0
    min_game_minute_interval: int = 0
    defer_seconds_on_exhausted: float = 60.0

    @classmethod
    def for_complexity(cls, complexity: ComplexityMode = "standard") -> "PulseBudgetConfig":
        if complexity == "light":
            return cls(
                complexity="light",
                max_calls_per_round=1,
                max_tokens_per_round=2048,
                daily_call_budget=6,
                daily_token_budget=12000,
                cooldown_seconds=10.0,
            )
        if complexity == "deep":
            return cls(
                complexity="deep",
                max_calls_per_round=4,
                max_tokens_per_round=8192,
                daily_call_budget=24,
                daily_token_budget=48000,
                cooldown_seconds=3.0,
            )
        return cls(complexity="standard")


@dataclass(frozen=True)
class PulseUsageEvidence:
    """单次模型调用或失败超时的用量证据。"""

    purpose: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    usage_source: UsageSource  # "actual" | "estimated"
    potentially_billed: bool = True
    outcome: str = "succeeded"  # "succeeded" | "timeout_maybe_billed" | "upstream_error_maybe_billed" | "aborted_unbilled"


@dataclass(frozen=True)
class PulseModelRequest:
    """发送给世界脉动模型的结构化请求（在 SQLite 事务外构造与使用）。"""

    instance_id: str
    kind: JobKind
    base_world_revision: int
    target_world_minute: int
    snapshot: dict[str, Any] = field(default_factory=dict)
    max_tokens: int = 2048


@dataclass(frozen=True)
class PulseModelResponse:
    """世界脉动模型返回的结构化提议与用量证据。"""

    proposal: dict[str, Any]
    usage: PulseUsageEvidence
