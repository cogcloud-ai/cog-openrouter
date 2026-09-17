# OpenRouter Cog

A configurable **Model Cog** providing direct inference through OpenRouter.
One package can prepare many concrete model bindings. It contains no agent
harness, does not add instructions or execute tools, and cannot admit itself.

This is the reference implementation of the proposed
`openteams/satisfier-binding [0.1-draft]` contract. A separate reference host in
this repository demonstrates independent qualification, immutable binding
revisions, a persistent local HTTP gateway, revocation, and run provenance.
It is a bounded reference, not a universal resolver or production multi-user host.

## Install and inspect

```sh
pixi install
pixi run check
pixi run test
pixi run card
pixi run models -- --model openai/gpt-4.1-mini
```

`models` reads OpenRouter's public endpoint catalog without an API key or an
inference call. The example model is illustrative, not a quality recommendation.
`card` returns the provider declaration, including configuration and credential
schemas. `check` validates the package; it does not claim provider availability.

## Connect once, bind explicitly

Set `OPENROUTER_API_KEY` in your local environment using your existing secret
management. Also set `OPENROUTER_COG_TOKEN` to a separate random local gateway
token; clients use this token rather than receiving the upstream API key.
Keep both out of source control and request files. Child services need the
variables in their launch environment. Only these two env references are
supported by this reference host.

Review `examples/bind-request.json`: it selects the model, allowed upstream,
fallback policy, local gateway address, output-token ceiling and requirement.
Set `evidence_level` to `probe` only when you want bounded paid functional
probes. The provider and independent host each probe the allowed routes.
Default `declaration` performs authentication/catalog checks without inference.

```sh
mkdir -p runs
pixi run bind -- --request examples/bind-request.json > runs/candidate.json
pixi run admit -- --request examples/bind-request.json \
  --candidate runs/candidate.json --state-dir state > runs/admitted.json
pixi run serve -- --state-dir state --port 8113
```

`bind` only returns a candidate (or a structured failure). `admit` is the
**host-side** operation, not a provider entry point. It re-fetches evidence,
checks correlation, capabilities, composition, locality, credentials and its
own gateway address, then commits the next unused revision. Failure preserves
the previous revision. State is owner-only and contains references, not keys.
The package's manifest exposes only provider operations; host lifecycle commands
are supplied for reference use.

The admitted example endpoint is:

```text
http://127.0.0.1:8113/bindings/binding-author-model/1/v1
```

From another terminal, a host-side CLI invocation is also available:

```sh
pixi run infer -- --binding-id binding-author-model --revision 1 \
  --request examples/completion.json --state-dir state
```

The HTTP interface requires `Authorization: Bearer <local gateway token>`.
It implements bound `/models` and non-streaming `/chat/completions`. Its
unbound `/health` reports process liveness only. Bound `/models` checks key
availability and current catalog compatibility. Every inference rechecks routes.

`serve` stays running until stopped. Run it under a host service manager if you
want restart-at-login behavior; installing this package does not install a
background daemon, and an asleep laptop is not an always-on host.

## Plug into existing Smith-created cogs

In `cog-author` or `cog-build-evaluator`, with the same local token available:

```sh
pixi run use -- --endpoint http://127.0.0.1:8113/bindings/binding-author-model/1/v1 \
  --model openai/gpt-4.1-mini --api-key-env OPENROUTER_COG_TOKEN \
  --response-format json_object --locality cloud
pixi run check -- --deep
pixi run ask -- --bundle examples/sample-bundle.json
```

This selects an already admitted endpoint. It does **not** teach legacy Smith
resolution how to perform draft-contract discovery or host admission. Also,
legacy Smith's `model_identity: verified` means an echoed name matched; it is
not the draft's independent weights verification. The gateway's `cog_binding`
metadata and host run records explicitly retain `identity_verified: false` and
`revision_pinned: false`. Existing clients do not yet propagate that additive
metadata into their own envelopes. Do not present legacy name matching as a
provenance-qualified model benchmark. See [spec findings](docs/spec-findings.md).

## Routing, changes and evidence

- Every request fixes `model` and sends `provider.only`, `provider.order`,
  `allow_fallbacks`, and `require_parameters: true` from the binding.
- A disabled fallback policy requires one base upstream. Enabled fallback is
  limited to the listed upstreams of the same model; no model fallback exists.
- All matching catalog endpoints must advertise required features. Provider
  names returned by completions or generation metadata are mapped using the
  catalog; names are never guessed from slugs.
- Region/variant selectors are unsupported because returned display names do
  not establish exact endpoint identity. Unknown or mismatched observed model
  or upstream makes the run fail. A transient metadata failure may therefore
  reject a completion that already incurred cost; the gateway never retries the
  paid inference automatically.
- Binding revisions are immutable. Update the request to revision 2, prepare
  and admit it, then explicitly point consumers to that endpoint. Revision 1
  remains available until revoked; changing a request file does not change it.

```sh
pixi run revoke -- --binding-id binding-author-model --revision 1 --state-dir state
```

Each run stores exact binding references, requested/observed identities,
verification limits, hashes of the request/response and deviations. Prompt and
completion text are not persisted by this host. Run filenames appear in additive
`cog_binding` response metadata. These records use
`openteams/openrouter-run [0.1]`, a reference-host format, not a new universal
envelope. State integrity hashes detect corruption; OS ownership is the trust
boundary, not cryptographic protection against a malicious host administrator.

## Supported scope

Features: `text-generation`, `json-output`, and `json-schema`. JSON output is
validated after inference; schema-format outputs are also checked locally.
Text messages may have system/user/assistant roles. Temperature, top_p, seed,
bounded token limits and the two JSON formats are supported. Unknown parameters
are refused, including provider overrides, tools, plugins, presets and streaming.
No region claims, weight digests, subscription integration, tool execution,
multi-user authorization or automatic billing fallback are implemented.

The provider uses a custom runtime, not Smith's context machinery. Smith checks
core/profile declarations and honestly reports that its template-runtime checks
do not apply. `pixi run test` is this package's actual runtime verification.

See [verification](docs/verification.md) and [API sources](docs/api-sources.md).

## Workbench integration

The optional `extensions.workbench_host` declaration names the separate host's
`admit`, `inspect-binding` and `revoke` tasks for the workbench suite. These do not
become provider operations or allow `bind` to self-admit. Workbench stores the
OpenRouter host state separately, rechecks it before dependent turns and can
start the existing authenticated loopback gateway. `inspect-binding` validates
record integrity and revocation without performing model inference.

The vendored unadopted schema now includes combined-harness turn references and
command transport used by sibling reference Cogs; OpenRouter remains model-only
and keeps its original inference protocol.
