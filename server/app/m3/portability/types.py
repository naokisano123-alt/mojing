"""M3 Wave 3 可续玩迁移、历史来源墓碑与快照性能基准数据类型定义。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

VERSION = 1
SETTINGS_OPENINGS_VERSION = 2
SAVES_VERSION = 2
SAVES_OPENINGS_VERSION = 3
SAVES_INSTANCES_VERSION = 4

SETTINGS_FORMAT = "mojing-world-settings"
SAVES_FORMAT = "mojing-world-saves"
HISTORICAL_REF_FORMAT = "m3.historical_source_ref.v1"

TRANSFER_BYTE_LIMIT = 128 * 1024 * 1024
MAX_ZIP_ENTRIES = 2
WORLD_FILE = "world.json"
SAVE_FILE = "saves.json"

SnapshotStrategy = Literal["full", "compressed_zlib", "incremental_delta"]
SNAPSHOT_STRATEGIES: tuple[str, ...] = ("full", "compressed_zlib", "incremental_delta")


@dataclass(frozen=True)
class HistoricalSourceRef:
    """已删除资料的可审计历史引用（墓碑记录）。

    - `selectable_as_canon` 与 `searchable` 恒为 `False`，严禁被当作当前活跃 Canon 或进入资料库搜索；
    - 保留原资料类型（`chunk` / `entry`）、标题/文件名、删除时间与摘要哈希，保证历史事件因果链可审计且可再次导出。
    """

    source_id: str               # 当前世界内的映射 ID，如 chunk:<uuid> 或 entry:<uuid>
    original_source_id: str      # 导出前的原始来源 ID
    kind: Literal["chunk", "entry"]
    title: str
    document_filename: str = ""
    entry_type: str = ""
    deleted_at: str = ""
    content_sha256: str = ""
    status: Literal["deleted_historical"] = "deleted_historical"
    selectable_as_canon: bool = False
    searchable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": HISTORICAL_REF_FORMAT,
            "source_id": self.source_id,
            "original_source_id": self.original_source_id,
            "kind": self.kind,
            "title": self.title,
            "document_filename": self.document_filename,
            "entry_type": self.entry_type,
            "deleted_at": self.deleted_at,
            "content_sha256": self.content_sha256,
            "status": "deleted_historical",
            "selectable_as_canon": False,
            "searchable": False,
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "HistoricalSourceRef":
        raw_sid = str(data.get("source_id") or "").strip()
        raw_orig = str(data.get("original_source_id") or raw_sid).strip()
        raw_kind = str(data.get("kind") or "").strip()
        if not raw_kind:
            if raw_sid.startswith("chunk:"):
                raw_kind = "chunk"
            elif raw_sid.startswith("entry:"):
                raw_kind = "entry"
        return cls(
            source_id=raw_sid,
            original_source_id=raw_orig,
            kind=raw_kind,  # type: ignore[arg-type]
            title=str(data.get("title") or "已删除历史来源"),
            document_filename=str(data.get("document_filename") or ""),
            entry_type=str(data.get("entry_type") or ""),
            deleted_at=str(data.get("deleted_at") or ""),
            content_sha256=str(data.get("content_sha256") or ""),
            status="deleted_historical",
            selectable_as_canon=False,
            searchable=False,
        )


@dataclass(frozen=True)
class SnapshotStrategyMetrics:
    """单种快照方案在可重现负载下的性能与体积实测指标。"""

    strategy: SnapshotStrategy
    checkpoint_Count: int
    total_serialized_bytes: int
    avg_checkpoint_bytes: int
    compression_ratio_vs_full: float
    checkpoint_build_ms: float
    export_ms: float
    import_ms: float
    restore_ms: float
    exact_same_minute_multi_revision_verified: bool
    human_readable_json_in_db: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "checkpoint_count": self.checkpoint_Count,
            "total_serialized_bytes": self.total_serialized_bytes,
            "avg_checkpoint_bytes": self.avg_checkpoint_bytes,
            "compression_ratio_vs_full": round(self.compression_ratio_vs_full, 4),
            "checkpoint_build_ms": round(self.checkpoint_build_ms, 3),
            "export_ms": round(self.export_ms, 3),
            "import_ms": round(self.import_ms, 3),
            "restore_ms": round(self.restore_ms, 3),
            "exact_same_minute_multi_revision_verified": self.exact_same_minute_multi_revision_verified,
            "human_readable_json_in_db": self.human_readable_json_in_db,
        }
