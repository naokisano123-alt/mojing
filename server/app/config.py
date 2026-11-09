"""运行期配置。所有可调项都能用环境变量覆盖，默认值即契约里的验收值。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = SERVER_DIR / "data" / "mojing.db"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_origins(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if not raw:
        return default
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return tuple(parts) or default


@dataclass(frozen=True)
class Settings:
    db_path: Path = DEFAULT_DB_PATH
    max_upload_bytes: int = 2 * 1024 * 1024
    max_doc_bytes: int = 8 * 1024 * 1024
    max_pdf_pages: int = 200
    max_text_units: int = 2_000_000
    max_docx_unzipped_bytes: int = 100 * 1024 * 1024
    max_documents_per_world: int = 20
    allow_gb18030: bool = True
    allow_big5: bool = False
    public_origin: str = ""
    allowed_model_base_urls: tuple[str, ...] = ()
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: (
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        )
    )


def load_settings(db_path: Path | str | None = None) -> Settings:
    default_db = Path(db_path) if db_path is not None else Path(
        os.getenv("MOJING_DB_PATH", str(DEFAULT_DB_PATH))
    )
    return Settings(
        db_path=default_db,
        max_upload_bytes=_env_int("MOJING_MAX_UPLOAD_BYTES", 2 * 1024 * 1024),
        max_doc_bytes=_env_int("MOJING_MAX_DOC_BYTES", 8 * 1024 * 1024),
        max_pdf_pages=_env_int("MOJING_MAX_PDF_PAGES", 200),
        max_text_units=_env_int("MOJING_MAX_TEXT_UNITS", 2_000_000),
        max_docx_unzipped_bytes=_env_int("MOJING_MAX_DOCX_UNZIPPED", 100 * 1024 * 1024),
        max_documents_per_world=_env_int("MOJING_MAX_DOCUMENTS_PER_WORLD", 20),
        allow_gb18030=_env_bool("MOJING_ALLOW_GB18030", True),
        allow_big5=_env_bool("MOJING_ALLOW_BIG5", False),
        public_origin=os.getenv("MOJING_PUBLIC_ORIGIN", "").strip(),
        allowed_model_base_urls=_env_origins("MOJING_ALLOWED_MODEL_BASE_URLS", ()),
        cors_origins=_env_origins(
            "MOJING_CORS_ORIGINS",
            ("http://localhost:5173", "http://127.0.0.1:5173"),
        ),
    )
