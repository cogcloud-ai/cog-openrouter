# OpenRouter reference implementation findings

Status: implementation feedback on the unadopted binding draft.
Date: 2026-09-07. Reference: local `cog-openrouter` repository, version 0.1.0.

## 1. Credential discovery was missing — draft corrected

The binding request had `credential_refs`, but the provider declaration did not
say which names or reference formats it required. A generic host could render
configuration and still not know how to complete authentication.

Provider declarations now require `credential_schema`, a self-contained JSON
Schema for the request's credential_refs object. Schemas constrain references,
not secret values. The OpenRouter reference uses api_key and gateway_token with
two dedicated env references. Reference resolution remains a host capability;
the general spec does not standardize a universal secret manager.

The profile schema, all provider examples and the implementation's vendored
schema were revised together. This is an explicit in-place correction of an
unadopted draft, not a compatible change to a released contract.

## 2. Who owns the invocation address? — reference convention, still extensible

The exchange did not specify how a provider learns the host's serving address.
The reference advertises gateway_base_url in its configuration schema; its
host checks this against a separately supplied host-owned loopback address.
Each ID/revision receives a distinct endpoint. This solves the local reference
without prescribing gateway allocation for every host. A remote/deployment
profile should define address allocation and client authentication explicitly.

The upstream API key and the local client's gateway token are separate
credential references. Publishing an invocation address does not grant access.

## 3. A matching model name is not independent identity verification

OpenRouter can report a model identifier and serving-provider name. Neither
proves an immutable weight revision. The reference therefore rejects requirements
for identity_verified or revision_pinned, even when a returned name matches.
Functional probes prove bounded observed behavior, not weights identity.

Legacy Smith records use model_identity: verified for an echoed-name match.
That is weaker than the new draft's identity_verified. An explicit migration
must preserve this distinction; copying the legacy flag would overclaim evidence.
No legacy client or Smith machinery was changed in this implementation.

## 4. Provider names and endpoint slugs differ

Routing selects slugs; responses may name a provider using its display name.
Base slugs may cover multiple endpoint variants. The reference maps names using
fresh catalog evidence, admits only base slugs with cloud locality, and does
not claim to identify a region or variant. Every matching catalog endpoint must
advertise the required parameters; probes sample an actual route per allowed
base provider rather than proving every physical endpoint's behavior.

Unknown provider evidence causes a run failure. Generation metadata may arrive
late; a failed evidence lookup can reject an already-billed completion. The
reference never retries a paid inference automatically. More precise endpoint
identity and evidence-retrieval/recovery policy need future host work.

## 5. Inference protocol and Cog envelope are distinct

Wrapping an OpenAI-compatible completion in envelope v1 breaks existing model
clients. The reference keeps lifecycle operations in envelope v1 and inference
in the advertised chat-completions format. It adds cog_binding metadata to
inference responses and stores a durable, versioned host run record.

Legacy clients ignore that additive metadata. Transport compatibility therefore
does not mean full draft-binding provenance propagation. Future clients need
an adapter that retains both the admitted binding reference and observed
identity facts. The reference-host run format is not a universal envelope change.

## 6. Provider implementation and host authority must stay separate

The provider's bind task cannot admit or write host bindings. A separate
reference_host module accepts the candidate, independently fetches provider
evidence, validates the complete requirement and commits immutable revisions.
The host shares pure API/validation utilities, not the provider's verdict.

Filesystem ownership is the authority boundary in this local reference.
Integrity hashes detect corruption, not a malicious same-user host administrator.
Probe admission invokes separate provider and host checks and therefore has
explicit cost. No fixture/replay flag can create a production admission.

## Implemented and deferred

Implemented: model-provider discovery, credential/configuration validation,
candidate preparation, independent local admission, explicit routing, bounded
text/JSON inference, revision isolation, revocation, durable identity evidence,
and deterministic/HTTP conformance tests.

Deferred: a provider-neutral production resolver, distributed authority,
subscription adapters, streaming, region-specific qualification, immutable model
attestation, service-manager installation and legacy envelope migration.
The reference runtime is custom; Smith's template-runtime checker correctly
does not validate it. Its dedicated runtime tests are required separately.
