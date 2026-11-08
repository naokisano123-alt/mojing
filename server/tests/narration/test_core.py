import asyncio
from dataclasses import FrozenInstanceError

import pytest

from app.narration import (ContextSource, DraftError, FakeModelClient,
                          NarrationRequest, generate_draft, validate_draft, validate_request)


def test_fake_draft_is_explicit_and_does_not_modify_request():
    request = NarrationRequest('探索港口', (ContextSource('chunk:a', 'chunk', '港口有一座灯塔。'),))
    draft = asyncio.run(generate_draft(request, FakeModelClient()))
    assert '未调用真实 AI' in draft.narrative
    assert draft.references == ('chunk:a',)
    assert request.action == '探索港口'
    with pytest.raises(FrozenInstanceError):
        request.action = 'changed'


@pytest.mark.parametrize('output', [None, {}, {'narrative': '', 'references': []},
    {'narrative': 'ok', 'references': 'chunk:a'},
    {'narrative': 'ok', 'references': [], 'state_changes': {'money': 99}},
    {'narrative': '😀' * 6001, 'references': []}])
def test_invalid_output_rejected(output):
    with pytest.raises(DraftError):
        validate_draft(output, NarrationRequest('行动'))


@pytest.mark.parametrize('references', [['chunk:unknown'], ['chunk:a', 'chunk:a'], [True]])
def test_unprovided_duplicate_or_invalid_reference_rejected(references):
    request = NarrationRequest('行动', (ContextSource('chunk:a', 'chunk', '资料'),))
    with pytest.raises(DraftError):
        validate_draft({'narrative': '正文', 'references': references}, request)


def test_no_reference_is_valid_and_does_not_claim_a_source():
    assert validate_draft({'narrative': ' 未引用正文 ', 'references': []},
                          NarrationRequest('行动')).references == ()


@pytest.mark.parametrize('case', [NarrationRequest('  '), NarrationRequest('😀' * 1001),
    NarrationRequest('行动', (ContextSource('chunk:a', 'chunk', '😀' * 2001),)),
    NarrationRequest('行动', tuple(ContextSource(f'chunk:{i}', 'chunk', '字' * 4000) for i in range(5))),
    NarrationRequest('行动', (ContextSource('chunk:a', 'entry', '资料'),)),
    NarrationRequest('行动', (ContextSource('chunk:a', 'chunk', '资料'),) * 2)])
def test_request_budgets_and_source_identity(case):
    with pytest.raises(DraftError):
        validate_request(case)


def test_cancellation_and_transport_errors_are_not_fake_success():
    class Cancelled:
        async def generate(self, request):
            raise asyncio.CancelledError()

    class Broken:
        async def generate(self, request):
            raise TimeoutError('upstream')

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(generate_draft(NarrationRequest('行动'), Cancelled()))
    with pytest.raises(TimeoutError):
        asyncio.run(generate_draft(NarrationRequest('行动'), Broken()))
