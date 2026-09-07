"""Capability-specific evidence checks shared by provider and independent host.

Host calls these against freshly fetched evidence, never trusts provider verdicts.
"""
import json
import secrets

from contracts import Fault, require

FEATURES = {'text-generation', 'json-output', 'json-schema'}


def inspect_catalog(catalog, config, required_features):
    require(set(required_features) <= FEATURES, 'incompatible-requirement', 'Unknown required model feature.')
    data = catalog.get('data')
    require(isinstance(data, dict) and data.get('id') == config['model_id'], 'unsupported-model', 'Catalog does not identify the selected model.')
    architecture = data.get('architecture')
    require(isinstance(architecture, dict) and isinstance(architecture.get('output_modalities'), list)
            and 'text' in architecture['output_modalities'], 'incompatible-requirement', 'Model does not advertise text output.')
    endpoints = data.get('endpoints')
    require(isinstance(endpoints, list), 'provider-unavailable', 'Catalog has no endpoint list.')
    selected = []
    for upstream in config['allowed_upstreams']:
        # OpenRouter base slugs include endpoint variants; qualify ALL matching routes.
        matches = [e for e in endpoints if isinstance(e, dict) and isinstance(e.get('tag'), str)
                   and (e['tag'] == upstream or ('/' not in upstream and e['tag'].startswith(upstream + '/')))]
        require(bool(matches), 'unsupported-model', 'An allowed upstream is absent from the model catalog.')
        selected.extend(matches)
    common = set(FEATURES)
    for e in selected:
        parameters = e.get('supported_parameters')
        require(isinstance(parameters, list) and all(isinstance(p, str) for p in parameters),
                'provider-unavailable', 'Malformed endpoint parameter declaration.')
        supported = set(parameters)
        features = {'text-generation'}
        if 'response_format' in supported: features.add('json-output')
        if 'structured_outputs' in supported: features.add('json-schema')
        common &= features
    require(set(required_features) <= common, 'incompatible-requirement', 'Not every permitted endpoint advertises the required features.')
    return sorted(common), selected


def routed(config, body, upstreams=None):
    allowed = upstreams or config['allowed_upstreams']
    return {**body, 'model': config['model_id'], 'provider': {
        'only': allowed, 'order': allowed, 'allow_fallbacks': config['allow_provider_fallback'] if upstreams is None else False,
        'require_parameters': True}, 'stream': False}


def observe(response, config, endpoints, api, key, trace=None):
    require(isinstance(response.get('choices'), list) and response['choices'], 'provider-unavailable', 'Completion has no choices.', 502)
    model = response.get('model')
    if trace is not None: trace['observed_model'] = model if isinstance(model, str) else None
    require(isinstance(model, str) and model == config['model_id'], 'identity-mismatch', 'Observed model differs from the bound model or is unknown.', 502)
    name = response.get('provider')
    generation_id = response.get('id')
    if not isinstance(name, str) and isinstance(generation_id, str):
        try:
            info = api.generation(generation_id, key).get('data') or {}
            require(isinstance(info, dict), 'provider-unavailable', 'Malformed generation metadata.', 502)
            require(info.get('model') in (None, model), 'identity-mismatch', 'Generation metadata names a different model.', 502)
            name = info.get('provider_name')
        except Fault as exc:
            if exc.code == 'identity-mismatch': raise
            name = None
    if trace is not None: trace['observed_provider'] = name if isinstance(name, str) else None
    require(isinstance(name, str) and bool(name), 'qualification-failed', 'Serving provider is unknown; cannot confirm allowed routing.', 502)
    # Display names are not slugs. Map through the fresh catalog, never lowercase-guess.
    matches = [e for e in endpoints if e.get('provider_name') == name or e.get('tag') == name]
    require(bool(matches), 'identity-mismatch', 'Observed provider is outside the permitted route set.', 502)
    # A display name cannot establish a region/variant slug. Explicit suffix routes
    # are rejected at configuration time in this reference version.
    return {'observed_model': model, 'observed_provider': name,
            'identity_verified': False, 'revision_pinned': False,
            'verification': 'provider-reported-model-and-upstream; weights-unverified'}


def probe(api, config, endpoints, features, key):
    evidence = []
    # Every selectable base provider is probed; no evidence for one route stands for all.
    for upstream in config['allowed_upstreams']:
        nonce = secrets.token_hex(8)
        body = {'messages': [{'role': 'user', 'content': 'Return exactly the JSON object {"check":"' + nonce + '"} and no other text.'}], 'max_tokens': 128}
        if 'json-schema' in features:
            body['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'binding_probe', 'strict': True,
                'schema': {'type': 'object', 'additionalProperties': False, 'required': ['check'], 'properties': {'check': {'type': 'string', 'const': nonce}}}}}
        elif 'json-output' in features:
            body['response_format'] = {'type': 'json_object'}
        response = api.completion(routed(config, body, [upstream]), key)
        allowed_endpoints = [e for e in endpoints if e['tag'] == upstream or e['tag'].startswith(upstream + '/')]
        observe(response, config, allowed_endpoints, api, key)
        try:
            content = response['choices'][0]['message']['content']
            if 'json-output' in features or 'json-schema' in features:
                require(json.loads(content) == {'check': nonce}, 'qualification-failed', 'Structured-output probe failed.')
            else:
                require(isinstance(content, str) and nonce in content, 'qualification-failed', 'Text-generation probe failed.')
        except (KeyError, TypeError, ValueError):
            raise Fault('qualification-failed', 'Probe returned unusable output.') from None
        evidence.append({'check': 'functional-probe', 'observation': 'Passed requested feature probe via ' + upstream + '; not weights verification.'})
    return evidence
