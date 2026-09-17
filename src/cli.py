#!/usr/bin/env python3
"""Declared Cog operations plus explicitly separate reference-host commands."""
import argparse
import json
import sys
from pathlib import Path

from contracts import ROOT, DECLARATION, IDENTITY, Fault, envelope, validate
from openrouter_http import OpenRouterHTTP
from provider import bind
from reference_host import Store, admit
from gateway import infer, make_server


def read(path):
    return json.loads(Path(path).read_text())


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='task', required=True)
    sub.add_parser('card')
    sub.add_parser('check')
    models = sub.add_parser('models'); models.add_argument('--model', required=True)
    b = sub.add_parser('bind'); b.add_argument('--request', required=True)
    a = sub.add_parser('admit'); a.add_argument('--request', required=True); a.add_argument('--candidate', required=True)
    a.add_argument('--gateway-url', default='http://127.0.0.1:8113')
    s = sub.add_parser('serve'); s.add_argument('--port', type=int, default=8113)
    i = sub.add_parser('infer'); i.add_argument('--request', required=True); i.add_argument('--binding-id', required=True); i.add_argument('--revision', type=int, required=True)
    i.add_argument('--gateway-url', default='http://127.0.0.1:8113')
    r = sub.add_parser('revoke'); r.add_argument('--binding-id', required=True); r.add_argument('--revision', type=int, required=True)
    inspect = sub.add_parser('inspect-binding'); inspect.add_argument('--binding-id', required=True); inspect.add_argument('--revision', type=int, required=True)
    for cmd in (a, s, i, r, inspect): cmd.add_argument('--state-dir', default='state')
    args = p.parse_args(argv)
    api = OpenRouterHTTP()
    try:
        if args.task == 'card': result = envelope('card', DECLARATION)
        elif args.task == 'check':
            validate(DECLARATION, 'provider')
            for field in ('configuration_schema', 'credential_schema'):
                from jsonschema import Draft202012Validator
                Draft202012Validator.check_schema(DECLARATION[field])
            import yaml
            manifest = yaml.safe_load((ROOT / 'cog.yaml').read_text())
            assert manifest['id'] == IDENTITY['id'] and manifest['version'] == IDENTITY['version']
            assert any(x['name'] == DECLARATION['binding_interface'] and x['audience'] == 'lifecycle' for x in manifest['interfaces'])
            result = envelope('check', {'package': 'valid', 'availability': 'not-probed'})
        elif args.task == 'models':
            # No credential or inference charge: read public endpoint catalog only.
            import re
            if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', args.model):
                raise Fault('unsupported-model', 'Supply an exact author/model identifier.')
            result = envelope('models', api.catalog(args.model))
        elif args.task == 'bind': result = bind(read(args.request), api)
        elif args.task == 'admit':
            binding = admit(read(args.request), read(args.candidate), api, Store(args.state_dir), args.gateway_url)
            result = envelope('reference-host/admit', binding)
        elif args.task == 'inspect-binding':
            result = envelope('reference-host/inspect-binding', Store(args.state_dir).load(args.binding_id, args.revision)['binding'])
        elif args.task == 'revoke':
            Store(args.state_dir).revoke(args.binding_id, args.revision)
            result = envelope('reference-host/revoke', {'revoked': True})
        elif args.task == 'infer':
            # CLI is a host-side authorized invocation, not a network-auth bypass.
            result = infer(Store(args.state_dir), api, args.binding_id, args.revision, read(args.request), args.gateway_url)
        else:
            from contracts import credential
            credential('env:OPENROUTER_COG_TOKEN')
            server = make_server(Store(args.state_dir), api, args.port)
            print(f'OpenRouter bound gateway listening on 127.0.0.1:{server.server_port}', file=sys.stderr)
            try: server.serve_forever()
            except KeyboardInterrupt: pass
            finally: server.server_close()
            return 0
        print(json.dumps(result, indent=2, allow_nan=False))
        return 1 if result.get('payload', {}).get('status') == 'failed' else 0
    except Fault as exc:
        print(json.dumps(envelope(args.task, fault=exc), indent=2))
        return 1
    except (OSError, ValueError, KeyError, TypeError, AssertionError):
        print(json.dumps(envelope(args.task, fault=Fault('invalid-configuration', 'Invalid input, package or inaccessible host state.')), indent=2))
        return 1


if __name__ == '__main__':
    sys.exit(main())
