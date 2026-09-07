"""Executable reference of discovery -> preparation -> independent admission -> use."""
import copy
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from contracts import DECLARATION, SCHEMA, Fault, validate
from provider import bind
from reference_host import Store, admit
from gateway import infer, make_server
from openrouter_http import OpenRouterHTTP
from qualification import inspect_catalog


class FakeOpenRouter:
    """Only injected by tests; production CLI has no replay/fixture admission switch."""
    def __init__(self):
        self.calls = []
        self.response_model = 'openai/gpt-4.1-mini'
        self.response_provider = 'OpenAI'
        self.quota = False
        self.features = ['response_format', 'structured_outputs', 'max_tokens', 'temperature']
        self.metadata = True
        self.content = None

    def catalog(self, model):
        self.calls.append(('catalog', model))
        return {'data': {'id': 'openai/gpt-4.1-mini', 'architecture': {'output_modalities': ['text']},
                         'endpoints': [{'tag': 'openai', 'provider_name': 'OpenAI', 'supported_parameters': self.features},
                                       {'tag': 'azure', 'provider_name': 'Azure', 'supported_parameters': self.features}]}}

    def key_health(self, key):
        self.calls.append(('key', None))
        return {'data': {'label': 'fixture'}}

    def completion(self, body, key):
        self.calls.append(('completion', copy.deepcopy(body)))
        if self.quota: raise Fault('quota-exceeded', 'Fixture quota exhausted.', 429)
        nonce = re.search(r'\{"check":"([a-f0-9]+)"\}', body['messages'][0]['content'])
        content = self.content or (json.dumps({'check': nonce.group(1)}) if nonce else '{"greeting":"hello"}')
        return {'id': 'gen-fixture', 'model': self.response_model, 'provider': self.response_provider,
                'object': 'chat.completion', 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': content}, 'finish_reason': 'stop'}]}

    def generation(self, generation_id, key):
        if not self.metadata: raise Fault('provider-unavailable', 'Metadata unavailable.')
        return {'data': {'model': self.response_model, 'provider_name': 'OpenAI'}}


class ReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {'OPENROUTER_API_KEY': 'fixture-upstream-secret', 'OPENROUTER_COG_TOKEN': 'fixture-gateway-secret'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.store = Store(Path(self.tmp.name) / 'state')
        self.api = FakeOpenRouter()
        self.request = json.loads((ROOT / 'examples/bind-request.json').read_text())
        self.body = json.loads((ROOT / 'examples/completion.json').read_text())
        self.base = self.request['configuration']['gateway_base_url']

    def prepare_and_admit(self):
        candidate = bind(self.request, self.api)
        self.assertEqual(candidate['payload']['status'], 'candidate')
        binding = admit(self.request, candidate, self.api, self.store, self.base)
        return binding

    def invoke(self, body=None):
        return infer(self.store, self.api, self.request['binding_id'], self.request['revision'], body or self.body, self.base)

    def test_provider_contract_and_package(self):
        validate(DECLARATION, 'provider')
        from jsonschema import Draft202012Validator
        Draft202012Validator.check_schema(SCHEMA)
        result = subprocess.run([sys.executable, str(ROOT / 'src/cli.py'), 'check'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(json.loads(result.stdout)['ok'])

    def test_candidate_never_admits_itself(self):
        candidate = bind(self.request, self.api)
        self.assertEqual(candidate['payload']['binding']['state'], 'candidate')
        self.assertIsNone(candidate['payload']['binding']['admission'])
        with self.assertRaises(Fault): self.invoke()

    def test_admission_independently_fetches_evidence(self):
        candidate = bind(self.request, self.api)
        self.api.calls.clear()
        admit(self.request, candidate, self.api, self.store, self.base)
        self.assertIn(('catalog', self.body['model']), self.api.calls)
        self.assertIn(('key', None), self.api.calls)

    def test_inference_preserves_messages_and_routes(self):
        self.prepare_and_admit()
        response = self.invoke()
        sent = [v for k, v in self.api.calls if k == 'completion'][-1]
        self.assertEqual(sent['messages'], self.body['messages'])
        self.assertEqual(sent['model'], self.body['model'])
        self.assertEqual(sent['provider'], {'only': ['openai'], 'order': ['openai'], 'allow_fallbacks': False, 'require_parameters': True})
        provenance = response['cog_binding']
        self.assertFalse(provenance['identity_verified'])
        self.assertFalse(provenance['revision_pinned'])
        self.assertEqual(provenance['observed_provider'], 'OpenAI')
        self.assertTrue((self.store.root / provenance['run_record']).exists())

    def test_no_credentials_or_prompt_text_in_state(self):
        self.prepare_and_admit(); self.invoke()
        text = ''.join(p.read_text() for p in self.store.root.glob('*.json'))
        self.assertNotIn('fixture-upstream-secret', text)
        self.assertNotIn('fixture-gateway-secret', text)
        self.assertNotIn(self.body['messages'][0]['content'], text)

    def test_model_and_routing_overrides_rejected_before_inference(self):
        self.prepare_and_admit()
        for key, value in [('model', 'other/model'), ('models', ['other/model']), ('provider', {'only': ['azure']}), ('plugins', []), ('tools', []), ('stream', True), ('route', 'fallback')]:
            body = {**self.body, key: value}
            before = len([c for c in self.api.calls if c[0] == 'completion'])
            with self.assertRaises(Fault, msg=key): self.invoke(body)
            self.assertEqual(before, len([c for c in self.api.calls if c[0] == 'completion']))

    def test_binding_configuration_failures(self):
        for update in [dict(model_id='openrouter/auto'), dict(model_id='@preset'), dict(model_id='vendor/latest'),
                       dict(allowed_upstreams=['openai', 'azure']), dict(allowed_upstreams=['azure/region']),
                       dict(gateway_base_url='http://example.com:8113'), dict(gateway_base_url='http://127.0.0.1:99999')]:
            request = copy.deepcopy(self.request); request['configuration'].update(update)
            self.assertEqual(bind(request, self.api)['payload']['status'], 'failed', update)

    def test_unsupported_requirement_never_probes(self):
        for update in [dict(accepted_compositions=['harness']), dict(features=['magic']), dict(allowed_localities=['local']),
                       dict(identity_verified=True), dict(revision_pinned=True), dict(model_id='other/model')]:
            request = copy.deepcopy(self.request); request['requirement'].update(update)
            self.assertEqual(bind(request, self.api)['payload']['status'], 'failed', update)
        self.assertFalse(any(k == 'completion' for k, _ in self.api.calls))

    def test_missing_credential_and_literal_secret_rejected(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': ''}):
            self.assertEqual(bind(self.request, self.api)['payload']['status'], 'failed')
        self.request['credential_refs']['api_key'] = 'fixture-upstream-secret'
        result = bind(self.request, self.api)
        self.assertEqual(result['payload']['status'], 'failed')
        self.assertNotIn('fixture-upstream-secret', json.dumps(result))

    def test_tampered_candidate_cannot_be_admitted(self):
        for field, value in [('state', 'admitted'), ('composition', 'model+harness'), ('locality', 'local'), ('features', ['magic'])]:
            candidate = bind(self.request, self.api); candidate['payload']['binding'][field] = value
            with self.assertRaises(Fault, msg=field): admit(self.request, candidate, self.api, self.store, self.base)
        candidate = bind(self.request, self.api); candidate['payload']['binding']['qualification']['revision_pinned'] = True
        with self.assertRaises(Fault): admit(self.request, candidate, self.api, self.store, self.base)

    def test_wrong_request_correlation_and_gateway(self):
        candidate = bind(self.request, self.api); candidate['payload']['request_id'] = 'other'
        with self.assertRaises(Fault): admit(self.request, candidate, self.api, self.store, self.base)
        candidate = bind(self.request, self.api)
        with self.assertRaises(Fault): admit(self.request, candidate, self.api, self.store, 'http://127.0.0.1:8114')

    def test_atomic_revisions_and_failed_rebind_preserve_old(self):
        self.prepare_and_admit()
        with self.assertRaises(Fault): self.prepare_and_admit()
        self.request['revision'] = 2
        candidate = bind(self.request, self.api); self.api.features = []
        with self.assertRaises(Fault): admit(self.request, candidate, self.api, self.store, self.base)
        self.assertEqual(self.store.load(self.request['binding_id'], 1)['binding']['revision'], 1)
        with self.assertRaises(Fault): self.store.load(self.request['binding_id'], 2)

    def test_probe_runs_separately_in_provider_and_host(self):
        self.request['requirement']['evidence_level'] = 'probe'
        self.prepare_and_admit()
        self.assertEqual(sum(k == 'completion' for k, _ in self.api.calls), 2)

    def test_failed_probe_does_not_admit(self):
        self.request['requirement']['evidence_level'] = 'probe'; self.api.content = 'not json'
        self.assertEqual(bind(self.request, self.api)['payload']['status'], 'failed')
        self.assertEqual(list(self.store.root.glob('*.json')), [])

    def test_catalog_drift_is_checked_before_each_invocation(self):
        self.prepare_and_admit(); self.api.features = []
        with self.assertRaises(Fault): self.invoke()

    def test_all_fallback_routes_must_qualify(self):
        config = copy.deepcopy(self.request['configuration']); config.update(allowed_upstreams=['openai', 'azure'], allow_provider_fallback=True)
        catalog = self.api.catalog(config['model_id']); catalog['data']['endpoints'][1]['supported_parameters'] = []
        with self.assertRaises(Fault): inspect_catalog(catalog, config, ['json-output'])

    def test_model_mismatch_retains_failure_provenance(self):
        self.prepare_and_admit(); self.api.response_model = 'other/model'
        with self.assertRaises(Fault): self.invoke()
        record = json.loads(next(self.store.root.glob('run-*.json')).read_text())
        self.assertEqual(record['observed_model'], 'other/model')
        self.assertEqual(record['outcome'], 'failed')

    def test_provider_mismatch_and_unknown_provider_fail(self):
        self.prepare_and_admit(); self.api.response_provider = 'Azure'
        with self.assertRaises(Fault): self.invoke()
        self.api.response_provider = None; self.api.metadata = False
        with self.assertRaises(Fault): self.invoke()

    def test_generation_metadata_resolves_display_name(self):
        self.prepare_and_admit(); self.api.response_provider = None
        self.assertEqual(self.invoke()['cog_binding']['observed_provider'], 'OpenAI')

    def test_quota_and_revocation_do_not_fallback(self):
        self.prepare_and_admit(); self.api.quota = True
        with self.assertRaises(Fault): self.invoke()
        self.store.revoke(self.request['binding_id'], 1)
        with self.assertRaises(Fault): self.invoke()

    def test_record_corruption_fails_closed(self):
        self.prepare_and_admit()
        path = self.store.path(self.request['binding_id'], 1)
        value = json.loads(path.read_text()); value['binding']['configuration']['model_id'] = 'other/model'; path.write_text(json.dumps(value))
        with self.assertRaises(Fault): self.invoke()

    def test_output_json_is_actually_checked(self):
        self.prepare_and_admit(); self.api.content = 'not json'
        with self.assertRaises(Fault): self.invoke()

    def test_token_ceiling_and_remote_schema_references(self):
        self.prepare_and_admit()
        for change in [dict(max_tokens=999999), dict(temperature=float('nan')),
                       dict(response_format={'type': 'json_schema', 'json_schema': {'name':'x', 'strict':True, 'schema':{'$ref':'https://example.invalid/schema'}}})]:
            with self.assertRaises(Fault): self.invoke({**self.body, **change})

    def test_real_http_gateway_auth_routing_and_health(self):
        server = make_server(self.store, self.api, 0)
        self.addCleanup(server.server_close)
        port = server.server_port
        self.base = f'http://127.0.0.1:{port}'
        self.request['configuration']['gateway_base_url'] = self.base
        binding = self.prepare_and_admit()
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.shutdown)
        endpoint = binding['invocation']['address']
        with self.assertRaises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(endpoint + '/models')
        self.assertEqual(failure.exception.code, 401)
        failure.exception.close()
        headers = {'Authorization': 'Bearer fixture-gateway-secret', 'Content-Type': 'application/json'}
        with urllib.request.urlopen(urllib.request.Request(endpoint+'/models', headers=headers)) as response:
            self.assertEqual(json.load(response)['data'][0]['id'], self.body['model'])
        with urllib.request.urlopen(urllib.request.Request(endpoint+'/chat/completions', data=json.dumps(self.body).encode(), headers=headers)) as response:
            result = json.load(response)
        self.assertEqual(result['cog_binding']['outcome'], 'completed')
        with self.assertRaises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(urllib.request.Request(endpoint+'/models', headers={**headers, 'Origin':'https://example.invalid'}))
        failure.exception.close()

    def test_bind_malformed_input_is_structured_failure(self):
        for value in (None, [], {}, {'contract': 'unknown'}, {**self.request, 'revision': True}):
            result = bind(value, self.api)
            validate(result['payload'], 'bind_result')
            self.assertEqual(result['payload']['status'], 'failed')

    def test_remote_redirects_are_not_forwarded(self):
        from openrouter_http import NoRedirect
        request = urllib.request.Request('https://openrouter.ai/api/v1/key', headers={'Authorization': 'Bearer secret'})
        self.assertIsNone(NoRedirect().redirect_request(request, None, 302, 'redirect', {}, 'https://example.invalid'))

    def test_candidate_cannot_supply_new_credential_reference(self):
        candidate = bind(self.request, self.api)
        candidate['payload']['binding']['credential_refs']['api_key'] = 'env:OTHER_SECRET'
        with self.assertRaises(Fault): admit(self.request, candidate, self.api, self.store, self.base)

    def test_model_catalog_absence_fails(self):
        self.request['configuration']['model_id'] = 'unknown/model'
        self.request['requirement']['model_id'] = 'unknown/model'
        self.assertEqual(bind(self.request, self.api)['payload']['status'], 'failed')

    def test_structured_output_violation_is_upstream_failure(self):
        self.prepare_and_admit()
        body = {**self.body, 'response_format': {'type': 'json_schema', 'json_schema': {
            'name': 'expected', 'strict': True, 'schema': {'type': 'object', 'required': ['missing']}}}}
        with self.assertRaises(Fault) as result: self.invoke(body)
        self.assertEqual(result.exception.status, 502)
        self.assertEqual(result.exception.code, 'qualification-failed')

    def test_http_errors_never_echo_upstream_body_or_key(self):
        api = OpenRouterHTTP()
        error = urllib.error.HTTPError('https://openrouter.ai', 401, 'bad', {}, io.BytesIO(b'fixture-upstream-secret'))
        with patch.object(api.opener, 'open', side_effect=error):
            with self.assertRaises(Fault) as result: api.key_health('fixture-upstream-secret')
        self.assertNotIn('fixture-upstream-secret', str(result.exception))


if __name__ == '__main__':
    unittest.main()
