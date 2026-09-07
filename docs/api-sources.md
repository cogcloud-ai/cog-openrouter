# API sources and interpretation

Implementation checked against official OpenRouter documentation and the public
model endpoint catalog on 2026-09-07:

- [Provider routing](https://openrouter.ai/docs/guides/routing/provider-selection)
  documents `only`, `order`, `allow_fallbacks`, `require_parameters`, and base
  slug matching across endpoint variants. The reference sends all four routing
  controls explicitly, and supports base slugs only.
- [Model endpoints](https://openrouter.ai/docs/api/api-reference/endpoints/list-endpoints)
  describes `/models/{author}/{slug}/endpoints`. The live public response for
  `openai/gpt-4.1-mini` exposed model identity, architecture, endpoint tags,
  provider display names and supported parameters. No completion was requested.
- [Chat completions](https://openrouter.ai/docs/api/api-reference/chat/create-a-chat-completion)
  supplies the OpenAI-compatible completion interface. This reference supports
  only a documented non-streaming text/JSON subset and refuses other fields.
- [Generation metadata](https://openrouter.ai/docs/api/api-reference/generations/get-request-&-usage-metadata-for-a-generation)
  supplies `provider_name` when a completion does not include a provider.

Observed model/provider labels are routing evidence, not immutable model-weight
attestation. The reference makes no independent verification or pinning claim.
