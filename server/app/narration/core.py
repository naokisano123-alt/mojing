"""M2-A draft boundary. Sources are data; references prove identity, not truth."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

MAX_ACTION_UNITS = 2000
MAX_SOURCES = 8
MAX_SOURCE_UNITS = 4000
MAX_CONTEXT_UNITS = 16000
MAX_NARRATIVE_UNITS = 12000


class DraftError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _units(text: str) -> int:
    try:
        return len(text.encode('utf-16-le')) // 2
    except UnicodeEncodeError as exc:
        raise DraftError('INVALID_TEXT', '文字包含不完整的 Unicode 字符。') from exc


@dataclass(frozen=True)
class ContextSource:
    source_id: str  # chunk:<id> or entry:<id>
    kind: Literal['chunk', 'entry']
    text: str


@dataclass(frozen=True)
class NarrationRequest:
    action: str
    sources: tuple[ContextSource, ...] = ()


@dataclass(frozen=True)
class NarrationDraft:
    narrative: str
    references: tuple[str, ...]


def validate_request(request: NarrationRequest) -> None:
    if not isinstance(request.action, str) or not request.action.strip():
        raise DraftError('ACTION_INVALID', '请先输入行动。')
    if _units(request.action) > MAX_ACTION_UNITS:
        raise DraftError('ACTION_INVALID', '行动超过 2000 个 UTF-16 单位。')
    if len(request.sources) > MAX_SOURCES:
        raise DraftError('CONTEXT_INVALID', '引用资料过多。')
    ids: set[str] = set()
    total = 0
    for source in request.sources:
        if (source.kind not in ('chunk', 'entry') or
                not isinstance(source.source_id, str) or
                not source.source_id.startswith(source.kind + ':') or
                not source.source_id[len(source.kind) + 1:].strip() or
                source.source_id in ids or not isinstance(source.text, str) or
                not source.text.strip()):
            raise DraftError('CONTEXT_INVALID', '引用资料无效或标识重复。')
        units = _units(source.text)
        if units > MAX_SOURCE_UNITS:
            raise DraftError('CONTEXT_INVALID', '单份引用超过文本预算。')
        total += units
        ids.add(source.source_id)
    if total > MAX_CONTEXT_UNITS:
        raise DraftError('CONTEXT_INVALID', '引用资料超过总文本预算。')


def validate_draft(value: object, request: NarrationRequest) -> NarrationDraft:
    validate_request(request)
    if not isinstance(value, Mapping) or set(value) != {'narrative', 'references'}:
        raise DraftError('MODEL_OUTPUT_INVALID', '模型输出必须仅包含叙事与引用。')
    narrative, references = value['narrative'], value['references']
    if (not isinstance(narrative, str) or not narrative.strip() or
            _units(narrative) > MAX_NARRATIVE_UNITS):
        raise DraftError('MODEL_OUTPUT_INVALID', '模型叙事为空或超出长度限制。')
    if not isinstance(references, list) or any(not isinstance(x, str) for x in references):
        raise DraftError('MODEL_OUTPUT_INVALID', '模型引用格式错误。')
    allowed = {source.source_id for source in request.sources}
    if len(references) != len(set(references)) or any(x not in allowed for x in references):
        raise DraftError('MODEL_REFERENCE_INVALID', '模型引用了未提供或重复的资料。')
    return NarrationDraft(narrative.strip(), tuple(references))


class ModelClient(Protocol):
    async def generate(self, request: NarrationRequest) -> object: ...


async def generate_draft(request: NarrationRequest, model: ModelClient) -> NarrationDraft:
    validate_request(request)
    # Cancellation and transport failures propagate to the orchestration layer.
    value = await model.generate(request)
    return validate_draft(value, request)


class FakeModelClient:
    """Explicit test mode, never a fallback for failed paid/model requests."""

    async def generate(self, request: NarrationRequest) -> object:
        validate_request(request)
        await asyncio.sleep(0)
        return {
            'narrative': '【测试模型 · 未调用真实 AI】你尝试：' + request.action.strip(),
            'references': [request.sources[0].source_id] if request.sources else [],
        }
