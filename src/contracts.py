"""Shared wire validation, identity, and safe value handling; no admission policy."""
import copy
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = 'openteams/satisfier-binding [0.1-draft]'
IDENTITY = {'id': 'openteams/cog-openrouter', 'version': '0.1.0'}
CAPABILITY = 'model-endpoint/openai-compatible'
SCHEMA = json.loads((ROOT / 'contracts/satisfier-binding.schema.json').read_text())
DECLARATION = json.loads((ROOT / 'binding/provider.json').read_text())


class Fault(Exception):
    def __init__(self, code, detail, status=422):
        self.code, self.detail, self.status = code, detail, status
        super().__init__(detail)


def require(condition, code, detail, status=422):
    if not condition:
        raise Fault(code, detail, status)


def validate(value, definition=None, schema=None):
    target = schema or (dict(SCHEMA, **{'$ref': '#/$defs/' + definition}) if definition else SCHEMA)
    # A definition reference must not inherit the root message union.
    if definition and schema is None:
        target.pop('oneOf', None)
    errors = list(Draft202012Validator(target).iter_errors(value))
    if errors:
        path = '/'.join(str(x) for x in errors[0].absolute_path) or '$'
        raise Fault('invalid-configuration', f'Invalid {definition or "document"} at {path}; schema rule {errors[0].validator}.')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def credential(reference):
    # This reference host deliberately supports only its two dedicated names.
    allowed = {'env:OPENROUTER_API_KEY', 'env:OPENROUTER_COG_TOKEN'}
    require(reference in allowed, 'missing-credential', 'Unsupported credential reference.')
    value = os.environ.get(reference[4:], '')
    require(bool(value) and not any(c in value for c in '\r\n'), 'missing-credential', 'Required credential is unavailable.', 503)
    return value


def gateway_url(value):
    try:
        p = urlsplit(value)
        port = p.port
    except ValueError:
        raise Fault('invalid-configuration', 'Invalid gateway URL.') from None
    require(p.scheme == 'http' and p.hostname == '127.0.0.1' and port is not None
            and 1024 <= port <= 65535 and not p.username and not p.password
            and not p.query and not p.fragment and p.path == '',
            'invalid-configuration', 'Gateway must be http://127.0.0.1:PORT with no path or credentials.')
    return value


def address(request):
    ident = request['binding_id']
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', ident), 'invalid-configuration', 'Binding ID must be a short path-safe identifier.')
    base = gateway_url(request['configuration']['gateway_base_url'])
    return f'{base}/bindings/{ident}/{request["revision"]}/v1'


def validate_request(request):
    require(isinstance(request, dict) and request.get('contract') == CONTRACT,
            'unsupported-contract', 'Unsupported binding contract.')
    validate(request, 'bind_request')
    require(request['provider'] == IDENTITY, 'incompatible-requirement', 'Provider package identity does not match.')
    validate(request['configuration'], schema=DECLARATION['configuration_schema'])
    validate(request['credential_refs'], schema=DECLARATION['credential_schema'])
    config, req = request['configuration'], request['requirement']
    require(not config['model_id'].startswith('openrouter/') and 'latest' not in config['model_id'].lower(),
            'unsupported-model', 'Router and latest aliases are not supported; choose a concrete catalog model.')
    require(config['allow_provider_fallback'] or len(config['allowed_upstreams']) == 1,
            'invalid-configuration', 'Fallback disabled requires exactly one allowed upstream.')
    require(req['capability'] == CAPABILITY and 'model' in req['accepted_compositions'],
            'incompatible-requirement', 'This provider supplies Model inference only.')
    require(req['model_id'] in (None, config['model_id']), 'incompatible-requirement', 'Required and configured model differ.')
    require('cloud' in req['allowed_localities'], 'incompatible-requirement', 'OpenRouter inference has cloud locality.')
    require(not req['identity_verified'] and not req['revision_pinned'], 'qualification-failed',
            'This adapter cannot independently verify weights or immutable model revision.')
    address(request)


def envelope(task, payload=None, fault=None, binding=None):
    return {'envelope': 1, 'cog': copy.deepcopy(IDENTITY), 'task': task,
            'ok': fault is None, 'error': {'code': fault.code, 'detail': fault.detail} if fault else None,
            'payload': payload, 'raw': None,
            'problems': [{'check': fault.code, 'detail': fault.detail, 'severity': 'error'}] if fault else [],
            'binding': binding, 'timing': {'latency_s': None}}
