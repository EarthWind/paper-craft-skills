"""api-image backend: generate the figure with Gemini "Nano Banana".

Reads image_prompt.txt and run_input.json from a prepared run directory,
sends one generateContent request (plus reference_image.* for edit runs),
and registers the returned image through register_generated_image.register.

  GEMINI_API_KEY       required (GOOGLE_API_KEY is accepted as a fallback)
  GEMINI_IMAGE_MODEL   optional, overrides the default model id
  GEMINI_API_BASE      optional, default https://generativelanguage.googleapis.com/v1

The key is only read from the environment and is never written to any file.
request.json keeps the request with the key and image bytes removed;
response_summary.json keeps finish reason, safety feedback, usage and text,
never the image base64. Failures write blocker.json; no image is faked.
"""

import argparse
import base64
import json
import os
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from build_figure_manifest import build_manifest
from host_image_common import DEFAULT_GEMINI_IMAGE_MODEL, file_digest, infer_mime, now_iso, read_json, write_json
from register_generated_image import block, register

DEFAULT_API_BASE = "https://generativelanguage.googleapis.com/v1"
SUFFIX_BY_MIME = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
TEXT_LIMIT = 2000


def resolve_api_key():
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            return value, name
    return "", None


def find_reference(run_dir):
    found = sorted(Path(run_dir).glob("reference_image.*"))
    return found[0] if found else None


def build_body(prompt, aspect_ratio, image_size, reference):
    parts = [{"text": prompt}]
    if reference:
        parts.append(
            {
                "inline_data": {
                    "mime_type": infer_mime(reference),
                    "data": base64.b64encode(reference.read_bytes()).decode("ascii"),
                }
            }
        )
    image_config = {"aspectRatio": aspect_ratio}
    if image_size:
        image_config["imageSize"] = str(image_size).upper()
    return {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"], "imageConfig": image_config},
    }


def redact_body(body, reference):
    redacted = json.loads(json.dumps(body))
    for part in redacted["contents"][0]["parts"]:
        if "inline_data" in part:
            part["inline_data"]["data"] = f"<omitted: {reference.name}, sha256={file_digest(reference)}>"
    return redacted


def post_json(url, api_key, body, timeout):
    request = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
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
        return status, json.loads(text)
    except json.JSONDecodeError:
        return status, {"raw_text": text[:TEXT_LIMIT]}


def inline_of(part):
    return part.get("inlineData") or part.get("inline_data")


def pick_image(data):
    """Return (mime, base64) of the final image part; skip thought/draft parts."""
    chosen = None
    for candidate in data.get("candidates") or []:
        for part in (candidate.get("content") or {}).get("parts") or []:
            inline = inline_of(part)
            if not inline or part.get("thought"):
                continue
            mime = inline.get("mimeType") or inline.get("mime_type") or ""
            if mime.startswith("image/") and inline.get("data"):
                chosen = (mime, inline["data"])
    return chosen


def summarize_response(status, data):
    candidates = data.get("candidates") or []
    texts = []
    for candidate in candidates:
        for part in (candidate.get("content") or {}).get("parts") or []:
            if part.get("text") and not part.get("thought"):
                texts.append(part["text"])
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


def record_model(run_dir, model, summary):
    metadata = read_json(run_dir / "metadata.json") or {}
    metadata.update(
        {
            "image_provider": "gemini",
            "model": model,
            "model_version": summary.get("model_version") if summary else None,
            "usage": summary.get("usage") if summary else None,
        }
    )
    write_json(run_dir / "metadata.json", metadata)
    write_json(run_dir / "figure_manifest.json", build_manifest(run_dir))


def main():
    parser = argparse.ArgumentParser(description="Generate a prepared figure run with Gemini Nano Banana (api-image backend).")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--model", help=f"Model id; default $GEMINI_IMAGE_MODEL or {DEFAULT_GEMINI_IMAGE_MODEL}.")
    parser.add_argument("--no-reference", action="store_true", help="Do not send reference_image.* even if the run has one.")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    run_input = read_json(run_dir / "run_input.json")
    prompt_path = run_dir / "image_prompt.txt"
    if not isinstance(run_input, dict) or not prompt_path.exists():
        raise SystemExit(f"not a prepared run directory: {run_dir}")
    model = args.model or os.environ.get("GEMINI_IMAGE_MODEL", "").strip() or DEFAULT_GEMINI_IMAGE_MODEL
    tool = f"gemini:{model}"

    api_key, key_env = resolve_api_key()
    if not api_key:
        code = block(run_dir, "missing GEMINI_API_KEY", {"hint": "export GEMINI_API_KEY=... (Google AI Studio API key)"}, "api-image", tool)
        record_model(run_dir, model, None)
        return code

    reference = None if args.no_reference else find_reference(run_dir)
    body = build_body(prompt_path.read_text(encoding="utf-8"), run_input.get("aspect_ratio") or "1:1", run_input.get("image_size"), reference)
    base = os.environ.get("GEMINI_API_BASE", "").strip().rstrip("/") or DEFAULT_API_BASE
    url = f"{base}/models/{model}:generateContent"
    write_json(
        run_dir / "request.json",
        {
            "created_at": now_iso(),
            "method": "POST",
            "url": url,
            "headers": {"Content-Type": "application/json", "x-goog-api-key": f"<redacted {key_env}>"},
            "body": redact_body(body, reference),
            "reference_image": str(reference) if reference else None,
        },
    )

    status, data = post_json(url, api_key, body, args.timeout)
    summary = summarize_response(status, data)
    write_json(run_dir / "response_summary.json", summary)

    image = pick_image(data)
    if status is None or status >= 400:
        message = ((data.get("error") or {}).get("message") or "")[:300]
        code = block(run_dir, f"Gemini request failed (HTTP {status})", {"status_code": status, "message": message}, "api-image", tool)
    elif (data.get("promptFeedback") or {}).get("blockReason"):
        reason = data["promptFeedback"]["blockReason"]
        code = block(run_dir, f"Gemini blocked the prompt: {reason}", {"prompt_feedback": data["promptFeedback"]}, "api-image", tool)
    elif not image:
        code = block(
            run_dir,
            "Gemini response contained no image",
            {"finish_reasons": summary["finish_reasons"], "text": (summary["text"] or "")[:300]},
            "api-image",
            tool,
        )
    else:
        mime, encoded = image
        with tempfile.NamedTemporaryFile(suffix=SUFFIX_BY_MIME.get(mime, ".png"), delete=False) as handle:
            handle.write(base64.b64decode(encoded))
            temp_path = Path(handle.name)
        try:
            code = register(run_dir, temp_path, backend="api-image", tool=tool)
        finally:
            temp_path.unlink(missing_ok=True)
    record_model(run_dir, model, summary)
    if summary.get("text"):
        print("Model note: " + summary["text"][:200].replace("\n", " "))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
