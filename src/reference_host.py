"""Host-owned admission and storage. This module is not the provider's bind task."""
import copy
import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from contracts import IDENTITY, Fault, address, credential, digest, require, validate, validate_request
from qualification import inspect_catalog, probe


def correlate(request, binding, state):
    validate_request(request)
    validate(binding, 'binding')
    req, config = request['requirement'], request['configuration']
    expected = {'provider': IDENTITY, 'binding_id': request['binding_id'], 'revision': request['revision'],
        'requirement_id': req['id'], 'configuration': config, 'credential_refs': request['credential_refs'],
        'capability': req['capability'], 'composition': 'model', 'features': req['features'],
        'model': {'id': config['model_id'], 'revision': None, 'digest': None}, 'harness': None,
        'model_binding': None, 'locality': 'cloud', 'state': state,
        'invocation': {'protocol': 'openai-chat-completions-v1', 'address': address(request)}}
    require(all(binding.get(k) == v for k, v in expected.items()), 'qualification-failed', 'Candidate differs from requested identity, configuration or supported model semantics.')
    q = binding['qualification']
    require(q['level'] == req['evidence_level'] and not q['identity_verified'] and not q['revision_pinned'],
            'qualification-failed', 'Candidate qualification claims unsupported verification or pinning.')


def admit(request, candidate_envelope, api, store, gateway_base_url):
    require(isinstance(candidate_envelope, dict) and candidate_envelope.get('envelope') == 1
            and candidate_envelope.get('ok') is True and not candidate_envelope.get('error')
            and not candidate_envelope.get('problems') and candidate_envelope.get('cog') == IDENTITY,
            'qualification-failed', 'Admission requires a clean provider envelope.')
    result = candidate_envelope.get('payload')
    validate(result, 'bind_result')
    require(result['request_id'] == request.get('request_id') and result['status'] == 'candidate',
            'qualification-failed', 'Provider did not return the requested candidate.')
    binding = copy.deepcopy(result['binding'])
    correlate(request, binding, 'candidate')
    require(request['configuration']['gateway_base_url'] == gateway_base_url,
            'unauthorized', 'Requested gateway is not the host-owned address.')
    key = credential(request['credential_refs']['api_key'])
    credential(request['credential_refs']['gateway_token'])
    api.key_health(key)
    # Independent fetch and functional checks, even if provider supplied matching evidence.
    _, endpoints = inspect_catalog(api.catalog(request['configuration']['model_id']), request['configuration'], request['requirement']['features'])
    evidence = [{'check': 'host-catalog', 'observation': 'Host independently fetched and checked all allowed routes and features.'}]
    if request['requirement']['evidence_level'] == 'probe':
        evidence += probe(api, request['configuration'], endpoints, request['requirement']['features'], key)
    binding['qualification']['evidence'] = evidence
    binding['state'] = 'admitted'
    binding['admission'] = {'resolver_id': 'openteams/openrouter-reference-host/0.1.0', 'checks': [
        {'check': name, 'passed': True, 'detail': detail} for name, detail in [
            ('request-correlation', 'Exact ID/revision, package, requirement, configuration and references match.'),
            ('capability-and-composition', 'Required Model capability/features checked against fresh catalog.'),
            ('identity-and-locality', 'Exact selected model; cloud admitted; no claim of verified weights or revision pin.'),
            ('authority-and-protocol', 'Host owns loopback gateway; dedicated credentials available; supported invocation protocol.'),
            ('qualification', 'Host independently performed required declaration or functional checks.')]]}
    validate(binding, 'binding')
    store.commit(request, binding, digest(candidate_envelope))
    return binding


class Store:
    """Trusted owner-only host state. Hashes detect corruption, not malicious host edits."""
    def __init__(self, root):
        self.root = Path(root).absolute()
        require(not self.root.is_symlink(), 'unauthorized', 'State directory must not be a symlink.')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        require(self.root.stat().st_uid == os.getuid() and self.root.stat().st_mode & 0o077 == 0,
                'unauthorized', 'State directory must be owner-only (mode 700).')

    @contextmanager
    def lock(self):
        fd = os.open(self.root / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'r+') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            yield

    def path(self, binding_id, revision):
        require(isinstance(binding_id, str) and type(revision) is int and revision >= 1,
                'invalid-configuration', 'Invalid binding reference.')
        return self.root / f'{digest(binding_id)}-{revision}.json'

    def commit(self, request, binding, candidate_digest):
        with self.lock():
            path = self.path(binding['binding_id'], binding['revision'])
            revisions = list(self.root.glob(digest(binding['binding_id']) + '-*.json'))
            previous = max((int(p.stem.rsplit('-', 1)[1]) for p in revisions), default=0)
            require(binding['revision'] == previous + 1 and not path.exists(), 'qualification-failed', 'Binding revision must be the next unused revision.')
            entry = {'request': request, 'binding': binding, 'candidate_sha256': candidate_digest}
            entry['sha256'] = digest(entry)
            self._atomic(path, entry)

    def _atomic(self, path, value):
        fd, temporary = tempfile.mkstemp(prefix='.pending-', dir=self.root)
        try:
            with os.fdopen(fd, 'w') as handle:
                json.dump(value, handle, indent=2)
                handle.flush(); os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary): os.unlink(temporary)

    def load(self, binding_id, revision):
        path = self.path(binding_id, revision)
        require(not path.is_symlink() and path.is_file(), 'provider-unavailable', 'Binding revision is not admitted.', 503)
        require(not path.with_suffix('.revoked').exists(), 'unauthorized', 'Binding revision was revoked.', 503)
        try:
            entry = json.loads(path.read_text())
            stored_digest = entry.pop('sha256')
            require(stored_digest == digest(entry), 'qualification-failed', 'Host binding record integrity check failed.', 503)
            correlate(entry['request'], entry['binding'], 'admitted')
            require(entry['binding']['binding_id'] == binding_id and entry['binding']['revision'] == revision,
                    'qualification-failed', 'Stored revision reference differs.', 503)
            return entry
        except (ValueError, KeyError, TypeError):
            raise Fault('qualification-failed', 'Malformed host binding state.', 503) from None

    def revoke(self, binding_id, revision):
        with self.lock():
            self.load(binding_id, revision)
            self._atomic(self.path(binding_id, revision).with_suffix('.revoked'), {'revoked': True})

    def record_run(self, record):
        # Fail closed if provenance cannot be retained. Does not retain prompt/output text.
        name = f'run-{time.time_ns()}-{os.urandom(4).hex()}.json'
        self._atomic(self.root / name, record)
        return name
