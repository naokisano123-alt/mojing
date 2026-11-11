"""Options for short, structured world jobs; ordinary narration keeps its own policy."""
from urllib.parse import urlsplit


def structured_output_options(base_url: str, model: str) -> dict:
    if urlsplit(base_url).hostname != 'api.deepseek.com':
        return {}
    options = {'response_format': {'type': 'json_object'}}
    # DeepSeek's current default is thinking=enabled/high. Reserve the output
    # budget for JSON in these background jobs, without sending provider-specific
    # options to unrelated compatible endpoints.
    if (urlsplit(base_url).hostname == 'api.deepseek.com'
            and model in {'deepseek-flash', 'deepseek-v4-pro'}):
        options['thinking'] = {'type': 'disabled'}
    return options
