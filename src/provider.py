"""Provider half: creates candidates only. No write path to host admissions."""
from contracts import IDENTITY, CAPABILITY, CONTRACT, Fault, address, credential, envelope, validate, validate_request
from qualification import inspect_catalog, probe


def prepare(request, api):
    validate_request(request)
    config, req = request['configuration'], request['requirement']
    key = credential(request['credential_refs']['api_key'])
    credential(request['credential_refs']['gateway_token'])
    api.key_health(key)
    features, endpoints = inspect_catalog(api.catalog(config['model_id']), config, req['features'])
    evidence = [{'check': 'endpoint-catalog', 'observation': 'Every permitted route advertises the selected feature set; cloud locality.'}]
    if req['evidence_level'] == 'probe':
        evidence += probe(api, config, endpoints, req['features'], key)
    binding = {'document_kind': 'binding', 'contract': CONTRACT,
        'binding_id': request['binding_id'], 'revision': request['revision'], 'state': 'candidate',
        'provider': IDENTITY, 'requirement_id': req['id'], 'configuration': config,
        'credential_refs': request['credential_refs'], 'capability': CAPABILITY,
        'features': req['features'], 'composition': 'model',
        'model': {'id': config['model_id'], 'revision': None, 'digest': None},
        'harness': None, 'model_binding': None, 'locality': 'cloud',
        'invocation': {'protocol': 'openai-chat-completions-v1', 'address': address(request)},
        'qualification': {'level': req['evidence_level'], 'identity_verified': False,
                          'revision_pinned': False, 'evidence': evidence}, 'admission': None}
    result = {'document_kind': 'bind_result', 'contract': CONTRACT, 'request_id': request['request_id'],
              'status': 'candidate', 'binding': binding, 'problems': []}
    validate(result, 'bind_result')
    return envelope('bind', result)


def bind(request, api):
    try:
        return prepare(request, api)
    except Fault as exc:
        rid = request.get('request_id') if isinstance(request, dict) else None
        result = {'document_kind': 'bind_result', 'contract': CONTRACT,
                  'request_id': rid if isinstance(rid, str) and rid else 'invalid-request',
                  'status': 'failed', 'binding': None,
                  'problems': [{'code': exc.code, 'detail': exc.detail}]}
        # Binding failure is a successfully produced failure payload; it is never admitted.
        return envelope('bind', result)
