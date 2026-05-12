"""Agentic personal website updater orchestration for Streamlit UI."""

from datetime import datetime
import hashlib
import json
from pathlib import Path

from dotenv import load_dotenv

from agents import Runner
from agents_config import personal_website_updater_agent
from tools import prefetch_editable_snapshot_section, UPDATED_HTML_OUTPUT

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

SERVER_LOG = BASE_DIR / "web_ui_server.log"
UPDATED_WEBPAGE = UPDATED_HTML_OUTPUT
PERSONAL_PROFILE_FILE = BASE_DIR / "personal_identity.txt"
RUN_CACHE_FILE = BASE_DIR / ".last_update_cache.json"


def server_log(message: str) -> None:
    line = f"{datetime.now().isoformat()} {message}\n"
    try:
        SERVER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with SERVER_LOG.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass


def _log(log_lines: list[str], msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    log_lines.append(f"[{ts}] {msg}")


def load_personal_identity() -> str:
    """Load persisted personal identity/details used for style grounding."""
    try:
        if PERSONAL_PROFILE_FILE.exists():
            return PERSONAL_PROFILE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return ""


def save_personal_identity(identity_text: str) -> None:
    """Persist personal identity/details for future updater runs."""
    PERSONAL_PROFILE_FILE.write_text((identity_text or "").strip(), encoding="utf-8")


def _compute_run_fingerprint(website_url: str, personal_identity: str, user_request: str) -> str:
    payload = "\n".join(
        [
            website_url.strip(),
            (personal_identity or "").strip(),
            (user_request or "").strip(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_cached_fingerprint() -> str:
    try:
        if not RUN_CACHE_FILE.exists():
            return ""
        data = json.loads(RUN_CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            fp = data.get("fingerprint", "")
            if isinstance(fp, str):
                return fp
    except (OSError, json.JSONDecodeError):
        pass
    return ""


def _save_cached_fingerprint(fingerprint: str) -> None:
    try:
        RUN_CACHE_FILE.write_text(
            json.dumps({"fingerprint": fingerprint}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def run_personal_website_updater(
    website_url: str,
    user_request: str,
    log_lines: list[str] | None = None,
) -> str:
    """Apply personal style to a live URL while preserving original template."""
    logs = log_lines if log_lines is not None else []
    website_url = website_url.strip()
    if not website_url:
        raise ValueError("Website URL is required.")

    personal_identity = load_personal_identity()
    fingerprint = _compute_run_fingerprint(website_url, personal_identity, user_request)
    cached_fp = _load_cached_fingerprint()

    if cached_fp == fingerprint and UPDATED_WEBPAGE.exists():
        _log(logs, "Inputs unchanged; reusing existing updated webpage for consistency")
        return f"Updated webpage saved.\n\nHTML_PATH:\n{UPDATED_WEBPAGE}"

    try:
        if UPDATED_WEBPAGE.exists():
            UPDATED_WEBPAGE.unlink()
    except OSError:
        pass

    _log(logs, "Running Personal Website Updater Agent")

    snapshot_block = ""
    try:
        snapshot_block, _n = prefetch_editable_snapshot_section(website_url)
    except Exception as exc:
        snapshot_block = (
            f"(Prefetch for copy snapshot failed: {exc}. "
            "Derive safe selectors from the fetched page inside build_updated_webpage, or rely on CSS.)\n"
        )

    prompt = f"""
Website URL:
{website_url}

User personal identity/details (name, brand context, audience, preferences):
{personal_identity or "No saved personal identity yet."}

Static page copy cues (verified selectors from a live HTML fetch):
{snapshot_block}

User request (style + messaging for this run):
{user_request.strip() or (
    "Apply my personal identity: CSS polish plus voice/tone-aligned copy via content_edits_json. "
    "Keep template, section order, and navigation structure."
)}

Mandatory preservation rules:
- Keep template, DOM order, and navigation recognizable
- Use content_edits_json for in-place wording; no new sections
""".strip()

    result = Runner.run_sync(personal_website_updater_agent, prompt)
    output = (result.final_output or "").strip()
    if not UPDATED_WEBPAGE.exists():
        raise ValueError(
            "Updated webpage was not generated.\n\n"
            f"Agent output:\n{output}"
        )
    _save_cached_fingerprint(fingerprint)
    _log(logs, "Updater run completed")
    return output


def run_agentic_website_review(
    website_url: str,
    user_request: str,
    log_lines: list[str] | None = None,
) -> str:
    """Backwards-compatible alias."""
    return run_personal_website_updater(website_url, user_request, log_lines)
