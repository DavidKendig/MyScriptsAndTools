"""Chat backends: Ollama native API and any OpenAI-compatible server (LM Studio).

Only the standard library is used, so a bare Python install can run the app.
Both backends stream their responses, which keeps the Stop button responsive
and lets the GUI show progress while a page is being translated.
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request

THINK_TAG = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)
# An unterminated opening tag means the model ran out of tokens mid-thought.
OPEN_THINK_TAG = re.compile(r"<(think|thinking|reasoning)>.*$", re.DOTALL | re.IGNORECASE)


class BackendError(Exception):
    """Any failure talking to the model server."""


class Stopped(Exception):
    """Raised when the user asks to stop mid-generation."""


def strip_thinking(text):
    """Remove <think> blocks that a model emitted inline in its answer."""
    text = THINK_TAG.sub("", text)
    text = OPEN_THINK_TAG.sub("", text)
    return text.strip()


def _describe_http_error(exc):
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:
        body = ""
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            err = parsed.get("error", parsed.get("message", body))
            if isinstance(err, dict):
                err = err.get("message", json.dumps(err))
            body = str(err)
    except ValueError:
        pass
    return "HTTP {}: {}".format(exc.code, (body or exc.reason or "").strip()[:500])


class Backend(object):
    """Common HTTP plumbing shared by both backends."""

    name = "backend"

    def __init__(self, base_url, api_key="", timeout=600):
        self.base_url = self._normalise(base_url)
        self.api_key = (api_key or "").strip()
        self.timeout = timeout

    @staticmethod
    def _normalise(url):
        return (url or "").strip().rstrip("/")

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        return headers

    def _open(self, url, payload=None, timeout=None):
        data = None
        method = "GET"
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            method = "POST"
        req = urllib.request.Request(
            url, data=data, headers=self._headers(), method=method
        )
        try:
            return urllib.request.urlopen(req, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as exc:
            raise BackendError(_describe_http_error(exc))
        except urllib.error.URLError as exc:
            raise BackendError(
                "Cannot reach {} - {}. Is the server running?".format(
                    url, getattr(exc, "reason", exc)
                )
            )
        except Exception as exc:
            raise BackendError("{}: {}".format(type(exc).__name__, exc))

    def _get_json(self, url, timeout=20):
        with self._open(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    def _post_json(self, url, payload, timeout=None):
        with self._open(url, payload, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    # --- interface -------------------------------------------------------
    def list_models(self):
        raise NotImplementedError

    def capabilities(self, model):
        """Return a set like {"vision", "thinking"}; empty means unknown."""
        return set()

    def chat(self, model, system, text, images, think, options, on_delta=None,
             should_stop=None):
        raise NotImplementedError


class OllamaBackend(Backend):
    """Native Ollama API (http://localhost:11434)."""

    name = "Ollama"

    @staticmethod
    def _normalise(url):
        url = (url or "").strip().rstrip("/")
        if url.endswith("/api"):
            url = url[: -len("/api")]
        return url or "http://localhost:11434"

    def list_models(self):
        data = self._get_json(self.base_url + "/api/tags")
        names = []
        for entry in data.get("models", []) or []:
            name = entry.get("name") or entry.get("model")
            if name:
                names.append(name)
        return sorted(names, key=str.lower)

    def capabilities(self, model):
        if not model:
            return set()
        try:
            data = self._post_json(
                self.base_url + "/api/show", {"model": model}, timeout=30
            )
        except BackendError:
            return set()
        caps = data.get("capabilities") or []
        return set(str(c).lower() for c in caps)

    def chat(self, model, system, text, images, think, options, on_delta=None,
             should_stop=None):
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append(
            {"role": "user", "content": text, "images": [b64 for b64, _ in images]}
        )
        # Note: the returned translation is always cleaned of inline <think>
        # blocks. `think` decides whether the model reasons, not whether the
        # reasoning ends up in the saved .txt file.
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {
                "temperature": float(options.get("temperature", 0.2)),
                "num_ctx": int(options.get("num_ctx", 8192)),
            },
        }
        if think is not None:
            payload["think"] = bool(think)

        try:
            return self._stream(payload, on_delta, should_stop)
        except BackendError as exc:
            # Older builds and non-reasoning models reject the think flag.
            if think is not None and "think" in str(exc).lower():
                payload.pop("think", None)
                return self._stream(payload, on_delta, should_stop)
            raise

    def _stream(self, payload, on_delta, should_stop):
        content, thinking = [], []
        with self._open(self.base_url + "/api/chat", payload) as resp:
            for line in resp:
                if should_stop and should_stop():
                    raise Stopped()
                line = line.strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if chunk.get("error"):
                    raise BackendError(str(chunk["error"]))
                msg = chunk.get("message") or {}
                piece = msg.get("content") or ""
                thought = msg.get("thinking") or ""
                if thought:
                    thinking.append(thought)
                if piece:
                    content.append(piece)
                    if on_delta:
                        on_delta(piece)
                if chunk.get("done"):
                    break
        return strip_thinking("".join(content)), "".join(thinking).strip()


class OpenAICompatBackend(Backend):
    """LM Studio, llama.cpp server, vLLM, or anything else speaking /v1/chat."""

    name = "LM Studio"

    @staticmethod
    def _normalise(url):
        url = (url or "").strip().rstrip("/")
        if not url:
            return "http://localhost:1234/v1"
        if not url.endswith("/v1") and "/v1/" not in url + "/":
            url += "/v1"
        return url

    def list_models(self):
        data = self._get_json(self.base_url + "/models")
        names = []
        for entry in data.get("data", []) or []:
            name = entry.get("id")
            if name:
                names.append(name)
        return sorted(names, key=str.lower)

    def chat(self, model, system, text, images, think, options, on_delta=None,
             should_stop=None):
        parts = [{"type": "text", "text": text}]
        for b64, mime in images:
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": "data:{};base64,{}".format(mime, b64)},
                }
            )
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": parts})

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "temperature": float(options.get("temperature", 0.2)),
        }
        max_tokens = int(options.get("max_tokens", 0) or 0)
        if max_tokens > 0:
            payload["max_tokens"] = max_tokens
        if think is not None:
            # The Qwen3-style switch understood by LM Studio and llama.cpp.
            payload["chat_template_kwargs"] = {"enable_thinking": bool(think)}

        try:
            return self._stream(payload, on_delta, should_stop)
        except BackendError as exc:
            # Servers that do not understand the reasoning switch reject the body.
            if "chat_template_kwargs" in payload and _is_bad_request(str(exc)):
                payload.pop("chat_template_kwargs", None)
                return self._stream(payload, on_delta, should_stop)
            raise

    def _stream(self, payload, on_delta, should_stop):
        content, thinking = [], []
        with self._open(self.base_url + "/chat/completions", payload) as resp:
            for raw in resp:
                if should_stop and should_stop():
                    raise Stopped()
                line = raw.strip()
                if not line or not line.startswith(b"data:"):
                    continue
                body = line[5:].strip()
                if body == b"[DONE]":
                    break
                try:
                    chunk = json.loads(body.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if isinstance(chunk, dict) and chunk.get("error"):
                    err = chunk["error"]
                    if isinstance(err, dict):
                        err = err.get("message", err)
                    raise BackendError(str(err))
                for choice in chunk.get("choices", []) or []:
                    delta = choice.get("delta") or choice.get("message") or {}
                    thought = delta.get("reasoning_content") or delta.get("reasoning")
                    if thought:
                        thinking.append(thought)
                    piece = delta.get("content") or ""
                    if piece:
                        content.append(piece)
                        if on_delta:
                            on_delta(piece)
        return strip_thinking("".join(content)), "".join(thinking).strip()


def _is_bad_request(message):
    return "http 400" in message.lower() or "http 422" in message.lower()


BACKENDS = {
    "Ollama": OllamaBackend,
    "LM Studio": OpenAICompatBackend,
}


def build(name, base_url, api_key="", timeout=600):
    cls = BACKENDS.get(name, OllamaBackend)
    return cls(base_url, api_key=api_key, timeout=timeout)
