"""Documented OpenRouter API only. No redirect forwarding or subscription access."""
import json
import urllib.error
import urllib.parse
import urllib.request

from contracts import Fault, require

API = 'https://openrouter.ai/api/v1'
LIMIT = 8 * 1024 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenRouterHTTP:
    def __init__(self):
        self.opener = urllib.request.build_opener(NoRedirect)

    def request(self, path, key=None, body=None):
        require(path.startswith('/') and not path.startswith('//'), 'invalid-configuration', 'Invalid API path.')
        headers = {'Accept': 'application/json', 'Content-Type': 'application/json'}
        if key:
            headers['Authorization'] = 'Bearer ' + key
        req = urllib.request.Request(API + path, headers=headers,
                data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.opener.open(req, timeout=120 if body is not None else 25) as response:
                raw = response.read(LIMIT + 1)
            require(len(raw) <= LIMIT, 'provider-unavailable', 'Provider response exceeded size limit.', 502)
            value = json.loads(raw)
            require(isinstance(value, dict), 'provider-unavailable', 'Provider returned a non-object response.', 502)
            if 'error' in value:
                code = value['error'].get('code') if isinstance(value['error'], dict) else None
                raise self.http_fault(code)
            return value
        except urllib.error.HTTPError as exc:
            # Upstream bodies and URLs may reflect credentials/prompts. Never echo them.
            fault = self.http_fault(exc.code)
            exc.close()
            raise fault from None
        except (urllib.error.URLError, OSError, ValueError):
            raise Fault('provider-unavailable', 'OpenRouter transport or JSON response failed.', 503) from None

    @staticmethod
    def http_fault(status):
        if status in (401, 403): return Fault('unauthorized', 'OpenRouter rejected authentication or access.', 503)
        if status in (402, 429): return Fault('quota-exceeded', 'OpenRouter credits or rate limit unavailable.', 429)
        if status == 404: return Fault('unsupported-model', 'OpenRouter model or endpoint was not found.', 422)
        return Fault('provider-unavailable', 'OpenRouter request failed.', 502)

    def catalog(self, model):
        return self.request('/models/' + urllib.parse.quote(model, safe='/') + '/endpoints')

    def key_health(self, key):
        result = self.request('/key', key)
        require(isinstance(result.get('data'), dict), 'provider-unavailable', 'Malformed credential health response.', 502)
        return result

    def completion(self, body, key):
        return self.request('/chat/completions', key, body)

    def generation(self, generation_id, key):
        return self.request('/generation?id=' + urllib.parse.quote(generation_id, safe=''), key)
