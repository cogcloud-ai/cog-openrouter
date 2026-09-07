"""Bound, authenticated text-inference gateway; never an agent runtime."""
import copy
import hmac
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jsonschema import Draft202012Validator, SchemaError

from contracts import Fault, credential, digest, require, validate
from qualification import inspect_catalog, observe, routed

MESSAGE = {'type': 'object', 'additionalProperties': False, 'required': ['role', 'content'],
           'properties': {'role': {'enum': ['system', 'user', 'assistant']}, 'content': {'type': 'string'}}}
INPUT = {'type': 'object', 'additionalProperties': False, 'required': ['messages'], 'properties': {
    'model': {'type': 'string'}, 'messages': {'type': 'array', 'minItems': 1, 'maxItems': 1000, 'items': MESSAGE},
    'temperature': {'type': 'number', 'minimum': 0, 'maximum': 2},
    'top_p': {'type': 'number', 'exclusiveMinimum': 0, 'maximum': 1},
    'seed': {'type': 'integer'}, 'stream': {'const': False},
    'max_tokens': {'type': 'integer', 'minimum': 1},
    'max_completion_tokens': {'type': 'integer', 'minimum': 1},
    'response_format': {'oneOf': [
        {'type': 'object', 'additionalProperties': False, 'required': ['type'], 'properties': {'type': {'const': 'json_object'}}},
        {'type': 'object', 'additionalProperties': False, 'required': ['type', 'json_schema'], 'properties': {
            'type': {'const': 'json_schema'}, 'json_schema': {'type': 'object', 'additionalProperties': False,
            'required': ['name', 'strict', 'schema'], 'properties': {'name': {'type': 'string', 'minLength': 1},
            'strict': {'const': True}, 'schema': {'type': 'object'}}}}}]} }}


def check_request(body, config):
    validate(body, schema=INPUT)
    try:
        json.dumps(body, allow_nan=False)
    except (ValueError, TypeError):
        raise Fault('invalid-configuration', 'Request must contain finite JSON values.') from None
    require(body.get('model', config['model_id']) == config['model_id'], 'incompatible-requirement', 'Inference cannot override the bound model.')
    require(not ('max_tokens' in body and 'max_completion_tokens' in body), 'invalid-configuration', 'Supply only one token-limit parameter.')
    for name in ('max_tokens', 'max_completion_tokens'):
        require(body.get(name, 1) <= config['max_output_tokens'], 'invalid-configuration', 'Requested output exceeds the binding token ceiling.')
    fmt = body.get('response_format', {})
    if fmt.get('type') == 'json_schema':
        def walk(node):
            if isinstance(node, dict):
                for k, v in node.items():
                    if k in ('$ref', '$dynamicRef'):
                        require(isinstance(v, str) and v.startswith('#'), 'invalid-configuration', 'Output schema references must be local.')
                    walk(v)
            elif isinstance(node, list):
                for v in node: walk(v)
        walk(fmt['json_schema']['schema'])
        try:
            Draft202012Validator.check_schema(fmt['json_schema']['schema'])
        except SchemaError:
            raise Fault('invalid-configuration', 'Invalid requested output schema.') from None


def infer(store, api, binding_id, revision, body, gateway_base_url):
    entry = store.load(binding_id, revision)
    binding, request = entry['binding'], entry['request']
    config = binding['configuration']
    require(config['gateway_base_url'] == gateway_base_url, 'unauthorized', 'Binding belongs to a different gateway.')
    record = {'record': 'openteams/openrouter-run [0.1]', 'binding_id': binding_id, 'revision': revision,
        'requested_model': config['model_id'], 'observed_model': None, 'observed_provider': None,
        'composition': 'model', 'harness': None, 'identity_verified': False, 'revision_pinned': False,
        'request_sha256': digest(body), 'response_sha256': None, 'outcome': 'failed', 'deviations': []}
    try:
        check_request(body, config)
        key = credential(binding['credential_refs']['api_key'])
        required = list(request['requirement']['features'])
        fmt = body.get('response_format', {}).get('type')
        if fmt == 'json_object': required += ['json-output']
        if fmt == 'json_schema': required += ['json-schema']
        _, endpoints = inspect_catalog(api.catalog(config['model_id']), config, required)
        sent = copy.deepcopy(body)
        if 'max_tokens' not in sent and 'max_completion_tokens' not in sent:
            sent['max_tokens'] = config['max_output_tokens']
        response = api.completion(routed(config, sent), key)
        record['response_sha256'] = digest(response)
        facts = observe(response, config, endpoints, api, key, trace=record)
        try:
            message = response['choices'][0]['message']
            require(isinstance(message, dict) and isinstance(message.get('content'), str)
                    and not message.get('tool_calls') and not message.get('function_call'),
                    'provider-unavailable', 'Provider did not return plain text content.', 502)
            if fmt:
                parsed = json.loads(message['content'])
                require(isinstance(parsed, dict), 'qualification-failed', 'Provider returned a non-object JSON payload.', 502)
                if fmt == 'json_schema':
                    try:
                        validate(parsed, schema=body['response_format']['json_schema']['schema'])
                    except Fault:
                        raise Fault('qualification-failed', 'Provider output violated the requested schema.', 502) from None
        except (KeyError, TypeError, ValueError):
            raise Fault('provider-unavailable', 'Provider returned malformed completion content.', 502) from None
        # Check revocation again after a slow provider call; no automatic retry/double charge.
        store.load(binding_id, revision)
        record.update(facts, outcome='completed')
        run_file = store.record_run(record)
        result = dict(response)
        result['cog_binding'] = {**record, 'run_record': run_file}
        return result
    except Fault as exc:
        record['deviations'].append({'code': exc.code, 'detail': exc.detail})
        store.record_run(record)
        raise


def make_server(store, api, port):
    base = f'http://127.0.0.1:{port}'

    class Requests(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # No request URLs, bodies, keys or completion text in access logs.

        def respond(self, status, body):
            data = json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def binding(self):
            require(not self.headers.get('Origin'), 'unauthorized', 'Browser-origin requests are not supported.', 403)
            match = re.fullmatch(r'/bindings/([A-Za-z0-9][A-Za-z0-9_-]{0,99})/([1-9][0-9]*)/v1/(models|chat/completions)', self.path)
            require(match is not None, 'provider-unavailable', 'Unknown bound endpoint.', 404)
            bid, rev, route = match.group(1), int(match.group(2)), match.group(3)
            entry = store.load(bid, rev)
            require(entry['binding']['configuration']['gateway_base_url'] == base, 'unauthorized', 'Wrong gateway for this revision.', 403)
            token = credential(entry['binding']['credential_refs']['gateway_token'])
            require(hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token), 'unauthorized', 'Gateway authentication required.', 401)
            return bid, rev, route, entry

        def do_GET(self):
            try:
                if self.path == '/health':
                    return self.respond(200, {'status': 'running', 'note': 'Liveness only; query the authenticated bound models endpoint for readiness.'})
                _, _, route, entry = self.binding()
                require(route == 'models', 'provider-unavailable', 'Unknown endpoint.', 404)
                config = entry['binding']['configuration']
                api.key_health(credential(entry['binding']['credential_refs']['api_key']))
                inspect_catalog(api.catalog(config['model_id']), config, entry['request']['requirement']['features'])
                self.respond(200, {'object': 'list', 'data': [{'id': config['model_id'], 'object': 'model'}]})
            except Fault as exc:
                self.respond(exc.status, {'error': {'message': exc.detail, 'type': 'cog_error', 'code': exc.code}})
            except (ValueError, OSError, TypeError):
                self.respond(503, {'error': {'message': 'Host unavailable.', 'code': 'provider-unavailable'}})

        def do_POST(self):
            try:
                bid, rev, route, _ = self.binding()
                require(route == 'chat/completions', 'provider-unavailable', 'Unknown endpoint.', 404)
                require(not self.headers.get('Transfer-Encoding'), 'invalid-configuration', 'Chunked bodies are unsupported.', 400)
                length = int(self.headers.get('Content-Length', '0'))
                require(0 < length <= 2 * 1024 * 1024, 'invalid-configuration', 'Invalid request size.', 413)
                self.connection.settimeout(15)
                body = json.loads(self.rfile.read(length))
                result = infer(store, api, bid, rev, body, base)
                self.respond(200, result)
            except Fault as exc:
                self.respond(exc.status, {'error': {'message': exc.detail, 'type': 'cog_error', 'code': exc.code}})
            except (ValueError, TypeError):
                self.respond(400, {'error': {'message': 'Malformed request.', 'code': 'invalid-configuration'}})
            except OSError:
                self.respond(503, {'error': {'message': 'Host state or transport unavailable.', 'code': 'provider-unavailable'}})

    server = ThreadingHTTPServer(('127.0.0.1', port), Requests)
    base = f'http://127.0.0.1:{server.server_port}'
    return server
