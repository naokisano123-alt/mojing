import asyncio
import json
import sqlite3

import httpx
import pytest
from app.errors import ApiError
from app.m3 import world_analysis as analysis
from app.model_gateway.structured import structured_output_options


@pytest.mark.parametrize('reason,content,code', [
    ('length', '{', 'MODEL_OUTPUT_TRUNCATED'),
    ('stop', '   ', 'MODEL_EMPTY_RESPONSE'),
    ('stop', '{invalid', 'MODEL_OUTPUT_PARSE_ERROR'),
    ('content_filter', '', 'MODEL_OUTPUT_INCOMPLETE'),
])
def test_opening_distinguishes_output_failures_without_automatic_paid_retry(reason, content, code):
    captured = []
    def provider(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            'choices': [{'finish_reason': reason, 'message': {'content': content}}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 200, 'total_tokens': 300},
        })
    with sqlite3.connect(':memory:') as conn:
        conn.row_factory = sqlite3.Row
        analysis.ensure_base_world_tables(conn)
        analysis.seed_skyforge_academy_world(conn)
        batch = analysis.extract_and_batch_world_sources(conn, analysis.WORLD_B_ID, '天工外院')['batches'][0]
        model = analysis.OpenAIWorldAnalysisModelClient(base_url='https://api.deepseek.com', model='deepseek-flash',
            api_key='sk-mock-only', transport=httpx.MockTransport(provider))
        with pytest.raises(ApiError) as failure:
            asyncio.run(model.analyze_batch(batch, instance_id='inst-sky'))
        assert failure.value.code == code
        assert len(captured) == 1
        assert captured[0]['response_format'] == {'type': 'json_object'}
        assert captured[0]['thinking'] == {'type': 'disabled'}
        assert model.last_usage['total_tokens'] == 300
        assert 'sk-mock-only' not in failure.value.message


def test_provider_options_do_not_leak_to_unrelated_compatible_endpoints():
    assert structured_output_options('https://proxy.example/v1', 'deepseek-flash') == {}
    assert structured_output_options('https://api.deepseek.com/v1', 'deepseek-chat') == {'response_format': {'type': 'json_object'}}
