---
type: cog [0.1]
name: cog-openrouter
description: Configurable model-access provider using OpenRouter, with explicit model bindings and no agent harness.
version: "0.1.0"
license: Apache-2.0
publisher: OpenTeams
manifest: cog.yaml
manifest_schema: openteams/cog-manifest [0.1]
---

# OpenRouter Model Access

An installable model-access provider. It advertises potential satisfaction of
model-endpoint/openai-compatible; an independently admitted model binding is
the concrete satisfier. It carries transport code, not weights or an agent loop.
The profile's kind: model is a remote-access declaration, not a claim to carry
weights or to be a complete standalone model. See binding/provider.json.

Supports exact catalog models, explicit base-provider routing, text and JSON
inference, candidate preparation, and health. The separately supplied reference
host demonstrates admission, immutable revisions, revocation and serving.
No chat history is retained by the proxy. The upstream provider's data policies
still apply. All inference is cloud-locality even through a loopback gateway.

Out of scope: subscription access, agent harnesses, tools, streaming, multimodal
input, arbitrary provider parameters, region-specific routing, immutable weights
verification and public multi-user hosting. Missing credentials, incompatible
requirements, routing deviations and unknown serving-provider evidence fail
explicitly. No automatic fallback to a different model or billing account.

Lifecycle entry points return envelope v1. Inference uses the declared
OpenAI-compatible protocol, with additive cog_binding provenance and a durable
host-side run record. Binding and invocation interfaces have distinct contracts.
Provider candidates never authorize themselves. See README.md for the full flow.
