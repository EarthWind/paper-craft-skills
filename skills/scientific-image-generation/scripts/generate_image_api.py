"""api-image backend: generate a prepared figure run with an image model API.

Providers (all serve the Gemini "Nano Banana" image models):

  gemini      Gemini API (Google AI Studio key)
                GEMINI_API_KEY (GOOGLE_API_KEY fallback), GEMINI_API_BASE
  vertex      Vertex AI generateContent
                OAuth:   GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION (default global),
                         VERTEX_ACCESS_TOKEN or `gcloud auth print-access-token`
                Express: VERTEX_API_KEY (no project; not every model is offered)
                VERTEX_API_BASE overrides the host
  openrouter  OpenRouter Image API (POST /images)
                OPENROUTER_API_KEY, OPENROUTER_API_BASE

Model: --model, else GEMINI_IMAGE_MODEL / OPENROUTER_IMAGE_MODEL, else the
provider default. Provider: --provider, else run_input.json image_provider.

Keys and tokens are read from the environment (or gcloud) only and never
written. request.json keeps the request with credentials and image bytes
removed; response_summary.json never contains image base64. Every failure
writes blocker.json; no image is faked.
"""

import argparse
import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from build_figure_manifest import build_manifest
from host_image_common import DEFAULT_GEMINI_IMAGE_MODEL, IMAGE_PROVIDERS, file_digest, infer_mime, now_iso, read_json, write_json
from register_generated_image import block, register

DEFAULT_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1"
DEFAULT_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "google/" + DEFAULT_GEMINI_IMAGE_MODEL
SUFFIX_BY_MIME = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
TEXT_LIMIT = 2000


class Blocked(Exception):
    def __init__(self, reason, details=None):
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


def env(name):
    return os.environ.get(name, "").strip()


def find_reference(run_dir):
    found = sorted(Path(run_dir).glob("reference_image.*"))
    return found[0] if found else None


def omitted(reference):
    return f"<omitted: {reference.name}, sha256={file_digest(reference)}>"


def http_json(url, headers, body, timeout):
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=dict(headers, **{"Content-Type": "application/json"}),
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            status = response.status
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except (URLError, TimeoutError, OSError) as exc:
        return None, {"error": {"message": str(exc), "type": type(exc).__name__}}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"raw_text": text[:TEXT_LIMIT]}
    return status, data if isinstance(data, dict) else {"raw": data}


def error_message(data):
    error = data.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error)[:300]
    if error:
        return str(error)[:300]
    return str(data.get("raw_text") or "")[:300]


# ---------------------------------------------------------------- generateContent (gemini, vertex)


def content_body(prompt, aspect_ratio, image_size, reference, role):
    parts = [{"text": prompt}]
    if reference:
        parts.append({"inline_data": {"mime_type": infer_mime(reference), "data": base64.b64encode(reference.read_bytes()).decode("ascii")}})
    image_config = {"aspectRatio": aspect_ratio}
    if image_size:
        image_config["imageSize"] = str(image_size).upper()
    return {
        "contents": [{"role": role, "parts": parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "imageConfig": image_config},
    }


def redact_content_body(body, reference):
    redacted = json.loads(json.dumps(body))
    for part in redacted["contents"][0]["parts"]:
        if "inline_data" in part:
            part["inline_data"]["data"] = omitted(reference)
    return redacted


def pick_content_image(data):
    """(mime, base64) of the final image part; thought/draft parts are skipped."""
    chosen = None
    for candidate in data.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = part.get("inlineData") or part.get("inline_data")
            if not inline or part.get("thought"):
                continue
            mime = inline.get("mimeType") or inline.get("mime_type") or ""
            if mime.startswith("image/") and inline.get("data"):
                chosen = (mime, inline["data"])
    return chosen


def summarize_content(status, data):
    candidates = data.get("candidates") or []
    texts = [
        part["text"]
        for candidate in candidates
        for part in (candidate.get("content") or {}).get("parts") or []
        if part.get("text") and not part.get("thought")
    ]
    return {
        "status_code": status,
        "model_version": data.get("modelVersion"),
        "response_id": data.get("responseId"),
        "finish_reasons": [c.get("finishReason") for c in candidates],
        "safety_ratings": [c.get("safetyRatings") for c in candidates if c.get("safetyRatings")],
        "prompt_feedback": data.get("promptFeedback"),
        "usage": data.get("usageMetadata"),
        "text": "\n".join(texts)[:TEXT_LIMIT] or None,
        "error": data.get("error"),
    }


def interpret_content(status, data, label):
    if status is None or status >= 400:
        raise Blocked(f"{label} request failed (HTTP {status})", {"status_code": status, "message": error_message(data)})
    block_reason = (data.get("promptFeedback") or {}).get("blockReason")
    if block_reason:
        raise Blocked(f"{label} blocked the prompt: {block_reason}", {"prompt_feedback": data["promptFeedback"]})
    image = pick_content_image(data)
    if not image:
        summary = summarize_content(status, data)
        raise Blocked(f"{label} response contained no image", {"finish_reasons": summary["finish_reasons"], "text": (summary["text"] or "")[:300]})
    return image


def gemini_call(model, prompt, run_input, reference):
    key, key_env = "", None
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        if env(name):
            key, key_env = env(name), name
            break
    if not key:
        raise Blocked("missing GEMINI_API_KEY", {"hint": "export GEMINI_API_KEY=... (Google AI Studio API key)"})
    base = env("GEMINI_API_BASE").rstrip("/") or DEFAULT_GEMINI_BASE
    body = content_body(prompt, run_input.get("aspect_ratio") or "1:1", run_input.get("image_size"), reference, "user")
    return {
        "url": f"{base}/models/{model}:generateContent",
        "headers": {"x-goog-api-key": key},
        "redacted_headers": {"x-goog-api-key": f"<redacted {key_env}>"},
        "body": body,
        "redacted_body": redact_content_body(body, reference),
        "label": "Gemini",
        "kind": "content",
    }


def gcloud_access_token():
    gcloud = shutil.which("gcloud")
    if not gcloud:
        return ""
    try:
        proc = subprocess.run([gcloud, "auth", "print-access-token"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def vertex_call(model, prompt, run_input, reference):
    body = content_body(prompt, run_input.get("aspect_ratio") or "1:1", run_input.get("image_size"), reference, "USER")
    express_key = env("VERTEX_API_KEY")
    if express_key:
        base = env("VERTEX_API_BASE").rstrip("/") or "https://aiplatform.googleapis.com/v1"
        url = f"{base}/publishers/google/models/{model}:generateContent"
        headers = {"x-goog-api-key": express_key}
        redacted_headers = {"x-goog-api-key": "<redacted VERTEX_API_KEY>"}
        mode = "express"
    else:
        project = env("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise Blocked(
                "missing Vertex credentials",
                {"hint": "set GOOGLE_CLOUD_PROJECT (+ VERTEX_ACCESS_TOKEN or gcloud login), or VERTEX_API_KEY for express mode"},
            )
        location = env("GOOGLE_CLOUD_LOCATION") or "global"
        token_source = "VERTEX_ACCESS_TOKEN"
        token = env("VERTEX_ACCESS_TOKEN")
        if not token:
            token, token_source = gcloud_access_token(), "gcloud auth print-access-token"
        if not token:
            raise Blocked("missing Vertex access token", {"hint": "run `gcloud auth login` or set VERTEX_ACCESS_TOKEN"})
        host = "https://aiplatform.googleapis.com" if location == "global" else f"https://{location}-aiplatform.googleapis.com"
        base = env("VERTEX_API_BASE").rstrip("/") or f"{host}/v1"
        url = f"{base}/projects/{project}/locations/{location}/publishers/google/models/{model}:generateContent"
        headers = {"Authorization": "Bearer " + token}
        redacted_headers = {"Authorization": f"Bearer <redacted {token_source}>"}
        mode = "oauth"
    return {
        "url": url,
        "headers": headers,
        "redacted_headers": redacted_headers,
        "body": body,
        "redacted_body": redact_content_body(body, reference),
        "label": "Vertex AI",
        "kind": "content",
        "auth_mode": mode,
    }


# ---------------------------------------------------------------- OpenRouter Image API


def openrouter_call(model, prompt, run_input, reference):
    key = env("OPENROUTER_API_KEY")
    if not key:
        raise Blocked("missing OPENROUTER_API_KEY", {"hint": "export OPENROUTER_API_KEY=..."})
    if "/" not in model:
        model = "google/" + model
    body = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "aspect_ratio": run_input.get("aspect_ratio") or "1:1",
        "response_format": "b64_json",
    }
    if run_input.get("image_size"):
        body["resolution"] = str(run_input["image_size"]).upper()
    redacted = dict(body)
    if reference:
        data_url = f"data:{infer_mime(reference)};base64," + base64.b64encode(reference.read_bytes()).decode("ascii")
        body["input_references"] = [{"type": "image_url", "image_url": {"url": data_url}}]
        redacted["input_references"] = [{"type": "image_url", "image_url": {"url": omitted(reference)}}]
    base = env("OPENROUTER_API_BASE").rstrip("/") or DEFAULT_OPENROUTER_BASE
    return {
        "url": f"{base}/images",
        "headers": {"Authorization": "Bearer " + key},
        "redacted_headers": {"Authorization": "Bearer <redacted OPENROUTER_API_KEY>"},
        "body": body,
        "redacted_body": redacted,
        "label": "OpenRouter",
        "kind": "images",
        "model": model,
    }


def summarize_images(status, data):
    return {
        "status_code": status,
        "model_version": data.get("model"),
        "created": data.get("created"),
        "images_returned": len(data.get("data") or []),
        "usage": data.get("usage"),
        "text": None,
        "error": data.get("error"),
    }


def fetch_bytes(url, timeout):
    with urlopen(url, timeout=timeout) as response:
        return response.headers.get_content_type(), response.read()


def interpret_images(status, data, label, timeout):
    if status is None or status >= 400:
        raise Blocked(f"{label} request failed (HTTP {status})", {"status_code": status, "message": error_message(data)})
    items = data.get("data") or []
    for item in items:
        mime = item.get("media_type") or "image/png"
        if item.get("b64_json"):
            return mime, item["b64_json"]
        if item.get("url"):
            url = item["url"]
            if url.startswith("data:"):
                header, encoded = url.split(",", 1)
                return header[5:].split(";", 1)[0] or mime, encoded
            try:
                fetched_mime, raw = fetch_bytes(url, timeout)
            except (URLError, TimeoutError, OSError) as exc:
                raise Blocked(f"{label} image download failed", {"error": str(exc)})
            return (fetched_mime if fetched_mime.startswith("image/") else mime), base64.b64encode(raw).decode("ascii")
    raise Blocked(f"{label} response contained no image", {"images_returned": len(items)})


# ---------------------------------------------------------------- driver

PROVIDERS = {"gemini": gemini_call, "vertex": vertex_call, "openrouter": openrouter_call}


def default_model(provider):
    if provider == "openrouter":
        return env("OPENROUTER_IMAGE_MODEL") or DEFAULT_OPENROUTER_MODEL
    return env("GEMINI_IMAGE_MODEL") or DEFAULT_GEMINI_IMAGE_MODEL


def record_model(run_dir, provider, model, summary, auth_mode=None):
    metadata = read_json(run_dir / "metadata.json") or {}
    metadata.update(
        {
            "image_provider": provider,
            "model": model,
            "model_version": summary.get("model_version") if summary else None,
            "usage": summary.get("usage") if summary else None,
            "auth_mode": auth_mode,
        }
    )
    write_json(run_dir / "metadata.json", metadata)
    write_json(run_dir / "figure_manifest.json", build_manifest(run_dir))


def main():
    parser = argparse.ArgumentParser(description="Generate a prepared figure run with Nano Banana via Gemini API, Vertex AI, or OpenRouter.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--provider", choices=IMAGE_PROVIDERS, help="Default: image_provider from run_input.json, else gemini.")
    parser.add_argument("--model", help="Model id (OpenRouter slugs get a google/ prefix when none is given).")
    parser.add_argument("--no-reference", action="store_true", help="Do not send reference_image.* even if the run has one.")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    run_input = read_json(run_dir / "run_input.json")
    prompt_path = run_dir / "image_prompt.txt"
    if not isinstance(run_input, dict) or not prompt_path.exists():
        raise SystemExit(f"not a prepared run directory: {run_dir}")
    provider = args.provider or run_input.get("image_provider") or "gemini"
    model = args.model or default_model(provider)
    reference = None if args.no_reference else find_reference(run_dir)
    prompt = prompt_path.read_text(encoding="utf-8")

    summary, auth_mode = None, None
    try:
        call = PROVIDERS[provider](model, prompt, run_input, reference)
        model = call.get("model", model)
        auth_mode = call.get("auth_mode")
        write_json(
            run_dir / "request.json",
            {
                "created_at": now_iso(),
                "provider": provider,
                "auth_mode": auth_mode,
                "method": "POST",
                "url": call["url"],
                "headers": dict(call["redacted_headers"], **{"Content-Type": "application/json"}),
                "body": call["redacted_body"],
                "reference_image": str(reference) if reference else None,
            },
        )
        status, data = http_json(call["url"], call["headers"], call["body"], args.timeout)
        if call["kind"] == "content":
            summary = summarize_content(status, data)
            write_json(run_dir / "response_summary.json", summary)
            mime, encoded = interpret_content(status, data, call["label"])
        else:
            summary = summarize_images(status, data)
            write_json(run_dir / "response_summary.json", summary)
            mime, encoded = interpret_images(status, data, call["label"], args.timeout)
    except Blocked as exc:
        code = block(run_dir, exc.reason, exc.details, "api-image", f"{provider}:{model}")
        record_model(run_dir, provider, model, summary, auth_mode)
        return code

    with tempfile.NamedTemporaryFile(suffix=SUFFIX_BY_MIME.get(mime, ".png"), delete=False) as handle:
        handle.write(base64.b64decode(encoded))
        temp_path = Path(handle.name)
    try:
        code = register(run_dir, temp_path, backend="api-image", tool=f"{provider}:{model}")
    finally:
        temp_path.unlink(missing_ok=True)
    record_model(run_dir, provider, model, summary, auth_mode)
    if summary and summary.get("text"):
        print("Model note: " + summary["text"][:200].replace("\n", " "))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
