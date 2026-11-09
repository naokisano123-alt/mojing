"""M2-A draft boundary. Sources are data; references prove identity, not truth."""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, asdict
from typing import Literal, Protocol
from ..adventures.state import CharacterState, StateError, project, character as validate_character

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
class HistoryTurn:
    action: str
    narrative: str


@dataclass(frozen=True)
class NarrationRequest:
    action: str
    sources: tuple[ContextSource, ...] = ()
    history: tuple[HistoryTurn, ...] = ()
    character: CharacterState | None = None
    world_intro: str = ""
    suggest_actions: bool = False
    item_descriptions: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class NarrationDraft:
    narrative: str
    references: tuple[str, ...]
    proposed_changes: dict | None = None
    suggestions: tuple[str, ...] = ()


def validate_request(request: NarrationRequest) -> None:
    if request.character is not None:
        try:
            validate_character(asdict(request.character))
        except (StateError, TypeError):
            raise DraftError('CONTEXT_INVALID', '当前角色状态无效。') from None
    if not isinstance(request.action, str) or not request.action.strip():
        raise DraftError('ACTION_INVALID', '请先输入行动。')
    if _units(request.action) > MAX_ACTION_UNITS:
        raise DraftError('ACTION_INVALID', '行动超过 2000 个 UTF-16 单位。')
    if len(request.history) > 6 or sum(_units(h.action) + _units(h.narrative) for h in request.history) > 12000:
        raise DraftError('CONTEXT_INVALID', '历史剧情超过上下文预算。')
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
    allowed = {'narrative', 'references'}
    if request.character is not None:
        allowed.add('proposed_changes')
    if request.suggest_actions:
        allowed.add('suggestions')
    if not isinstance(value, Mapping) or not {'narrative','references'} <= set(value) or set(value) - allowed:
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
    changes = value.get('proposed_changes')
    if changes is not None or 'proposed_changes' in value:
        try:
            project(request.character, changes)
        except StateError as exc:
            raise DraftError('MODEL_STATE_INVALID', str(exc)) from None
    suggestions = []
    raw_suggestions = value.get('suggestions')
    if request.suggest_actions and isinstance(raw_suggestions, list):
        for item in raw_suggestions[:3]:
            if not isinstance(item, str):
                continue
            item = item.strip()
            try:
                if item and item not in suggestions and _units(item) <= 120:
                    suggestions.append(item)
            except DraftError:
                continue
    return NarrationDraft(narrative.strip(), tuple(references), changes, tuple(suggestions))


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
        result = {
            'narrative': '【测试模型 · 未调用真实 AI】你尝试：' + request.action.strip(),
            'references': [request.sources[0].source_id] if request.sources else [],
        }
        if request.character:
            state = request.character
            changes = {'minutes': min(10, 1000000000-state.elapsed_minutes), 'stamina_delta': -1 if state.stamina else 0}
            items = {item.name: item.quantity for item in state.inventory}
            if (len(items) < 40 or '测试纪念签' in items) and items.get('测试纪念签', 0) < 9999:
                changes['inventory_changes'] = [{'name': '测试纪念签', 'delta': 1}]
                result['narrative'] += '【固定背包演示：提议增加一枚测试纪念签，不代表对行动的规则判定。】'
            result['proposed_changes'] = changes
        return result
