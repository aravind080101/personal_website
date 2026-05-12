from __future__ import annotations

import json
from pathlib import Path
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from agents import function_tool
from bs4 import BeautifulSoup
from bs4.element import NavigableString

_PROJECT_ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = _PROJECT_ROOT / "outputs"
UPDATED_HTML_OUTPUT = OUTPUT_DIR / "updated_page.html"
_MAX_CSS_CHARS = 80_000
_MAX_CONTENT_EDITS_JSON_CHARS = 200_000
_MAX_CONTENT_EDIT_OPS = 40
_FETCH_TIMEOUT_SECS = 20

_CONTENT_EDIT_ALLOWED_TAGS = frozenset(
    {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "p",
        "a",
        "button",
        "label",
        "span",
        "li",
        "td",
        "th",
        "figcaption",
        "blockquote",
        "strong",
        "em",
        "small",
    }
)

_FORBIDDEN_CONTEXT_TAGS = frozenset({"script", "style", "noscript", "svg"})
_SNAPSHOT_MAX_NODES = 40
_SNAPSHOT_TEXT_PREVIEW = 180


def _within_footer(tag: object) -> bool:
    el = tag
    while el is not None and getattr(el, "name", None):
        nm = el.name.lower()
        if nm == "footer":
            return True
        attrs = getattr(el, "attrs", None) or {}
        role_val = attrs.get("role") or attrs.get("ROLE")
        if isinstance(role_val, str) and role_val.lower().strip() == "contentinfo":
            return True
        el = el.parent
    return False


def _nth_of_type_segment(tag: object) -> str:
    parent = getattr(tag, "parent", None)
    if not parent:
        name = getattr(tag, "name", "") or "*"
        return name
    name = getattr(tag, "name", "") or "*"
    sames = [c for c in parent.children if getattr(c, "name", None) == name]
    try:
        idx = sames.index(tag) + 1
    except ValueError:
        idx = 1
    return f"{name}:nth-of-type({idx})"


def _css_attr_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _selector_from_body_unique(soup: BeautifulSoup, el: object) -> str | None:
    get = getattr(el, "get", None)
    if callable(get):
        pid = get("id")
        if isinstance(pid, str) and pid.strip():
            raw = pid.strip()
            if re.fullmatch(r"[A-Za-z][\w\-]*", raw):
                trial_hash = f"#{raw}"
                try:
                    hits = soup.select(trial_hash)
                except Exception:
                    hits = []
                if len(hits) == 1 and hits[0] is el:
                    return trial_hash
            trial_attr = f'[id="{_css_attr_escape(raw)}"]'
            try:
                hits = soup.select(trial_attr)
            except Exception:
                hits = []
            if len(hits) == 1 and hits[0] is el:
                return trial_attr

    segments: list[str] = []
    cur: object | None = el
    depth_cap = 0
    while cur is not None and getattr(cur, "name", None) and depth_cap < 42:
        depth_cap += 1
        nm = cur.name.lower()
        if nm == "html":
            break
        if nm == "body":
            segments.append("body")
            break
        segments.append(_nth_of_type_segment(cur))
        cur = cur.parent

    segments.reverse()
    core = " > ".join(segments)
    trials = [core]
    if core.startswith("body"):
        trials.append(f"html > {core}")
    for trial_sel in trials:
        try:
            hits = soup.select(trial_sel)
        except Exception:
            continue
        if len(hits) == 1 and hits[0] is el:
            return trial_sel
    return None


def build_editable_snapshot_for_prompt(html: str) -> tuple[str, int]:
    soup = BeautifulSoup(html, "html.parser")
    roots = soup.select("main")
    search_roots = roots if roots else ([soup.body] if soup.body else [])
    if not search_roots:
        return ("(No <main> or <body> — no static copy snapshots.)", 0)

    rows: list[str] = []
    seen_el: set[int] = set()

    allowed_list = sorted(_CONTENT_EDIT_ALLOWED_TAGS)

    for root in search_roots:
        for el in root.find_all(allowed_list):
            if id(el) in seen_el:
                continue
            if not getattr(el, "name", None):
                continue
            if (
                _blocked_context(el)
                or _within_navigation_or_skip_region(el)
                or _within_footer(el)
            ):
                continue
            text = el.get_text(separator=" ", strip=True)
            if len(text) < 12:
                continue
            sel = _selector_from_body_unique(soup, el)
            if not sel:
                continue
            seen_el.add(id(el))
            preview = text[:_SNAPSHOT_TEXT_PREVIEW]
            if len(text) > _SNAPSHOT_TEXT_PREVIEW:
                preview += " …"
            safe_sel = sel.replace("`", "\\`")
            rows.append(
                f"- TAG `{el.name}` | selector: `{safe_sel}` | index: `0` | current: {preview!r}"
            )
            if len(rows) >= _SNAPSHOT_MAX_NODES:
                break
        if len(rows) >= _SNAPSHOT_MAX_NODES:
            break

    if not rows:
        return ("(No qualifying static text nodes found for voice/tone edits — CSS only.)", 0)

    nrows = len(rows)
    min_edits = min(4, nrows)
    stretch = min(8, nrows)
    block = (
        "EDITABLE COPY TARGETS (pre-extracted from the fetched HTML; selectors are unique in this page):\n"
        + "\n".join(rows)
        + f"\n\nYou MUST emit at least **{min_edits}** `element_text` operations (prefer **{stretch}**) "
        "pulled from the rows above. Copy `selector` and `index` byte-for-byte from a row; "
        "rewrite `text` to match Voice & Tone. If you claim copy changes in ## Changes Made, "
        "those bullets must correspond to real `element_text` entries—never fabricate.\n"
    )
    return block, nrows


def _within_navigation_or_skip_region(tag: object) -> bool:
    el = tag
    while el is not None and getattr(el, "name", None):
        if el.name.lower() == "nav":
            return True
        attrs = getattr(el, "attrs", None) or {}
        role_val = attrs.get("role") or attrs.get("ROLE")
        if isinstance(role_val, str) and role_val.lower().strip() == "navigation":
            return True
        el = el.parent
    return False


def _blocked_context(tag: object) -> bool:
    if not getattr(tag, "name", None):
        return True
    if tag.name.lower() in _FORBIDDEN_CONTEXT_TAGS:
        return True
    for anc in getattr(tag, "parents", []):
        if getattr(anc, "name", None) and anc.name.lower() in _FORBIDDEN_CONTEXT_TAGS:
            return True
    return False


def apply_content_edits_to_html(html: str, content_edits_json: str) -> tuple[str, int, int]:
    raw = (content_edits_json or "").strip()
    if raw in {"", "[]", "null"}:
        return html, 0, 0
    if len(raw) > _MAX_CONTENT_EDITS_JSON_CHARS:
        raise ValueError("content_edits_json is too large.")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"content_edits_json is not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise ValueError("content_edits_json must be a JSON array.")
    soup = BeautifulSoup(html, "html.parser")
    attempted = 0
    applied = 0
    for op in parsed[:_MAX_CONTENT_EDIT_OPS]:
        if not isinstance(op, dict):
            continue
        if op.get("type") != "element_text":
            continue
        attempted += 1
        sel = (op.get("selector") or "").strip()
        if not sel:
            continue
        idx = op.get("index", 0)
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            continue
        text = op.get("text")
        if text is None:
            continue
        if not isinstance(text, str):
            text = str(text)
        if len(text) > 20_000:
            text = text[:20_000]
        try:
            matches = soup.select(sel)
        except Exception:
            continue
        if idx < 0 or idx >= len(matches):
            continue
        el = matches[idx]
        nm = getattr(el, "name", None)
        if not nm or nm.lower() not in _CONTENT_EDIT_ALLOWED_TAGS:
            continue
        if _blocked_context(el) or _within_navigation_or_skip_region(el):
            continue
        el.clear()
        el.append(NavigableString(text))
        applied += 1
    return str(soup), attempted, applied


def prefetch_editable_snapshot_section(url: str) -> tuple[str, int]:
    html = fetch_website_html(url)
    return build_editable_snapshot_for_prompt(html)


def fetch_website_html(url: str) -> str:
    req = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; PersonalWebsiteUpdater/1.0)"},
    )
    try:
        with urlopen(req, timeout=_FETCH_TIMEOUT_SECS) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "text/html" not in content_type:
                raise ValueError(f"Unsupported content type: {content_type}")
            raw = resp.read(1_000_000)
            return raw.decode("utf-8", errors="ignore")
    except URLError as exc:
        reason = str(getattr(exc, "reason", exc))
        ssl_verify_error = (
            "CERTIFICATE_VERIFY_FAILED" in reason
            or "certificate verify failed" in reason.lower()
        )
        if not ssl_verify_error:
            raise
        insecure_ctx = ssl._create_unverified_context()
        with urlopen(req, timeout=_FETCH_TIMEOUT_SECS, context=insecure_ctx) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "text/html" not in content_type:
                raise ValueError(f"Unsupported content type: {content_type}")
            raw = resp.read(1_000_000)
            return raw.decode("utf-8", errors="ignore")


def _absolutize_asset_urls(html: str, source_url: str) -> str:
    def repl(match: re.Match[str]) -> str:
        attr = match.group(1)
        quote = match.group(2)
        value = match.group(3).strip()
        lower = value.lower()
        if (
            lower.startswith(("http://", "https://", "data:", "mailto:", "tel:", "javascript:", "#"))
            or value.startswith("//")
        ):
            return match.group(0)
        absolute = urljoin(source_url, value)
        return f'{attr}={quote}{absolute}{quote}'

    pattern = re.compile(r'(href|src)\s*=\s*(["\'])(.*?)\2', re.IGNORECASE)
    return pattern.sub(repl, html)


def inject_css_into_html(html: str, css_overrides: str, source_url: str) -> str:
    css = (css_overrides or "").strip()
    if not css:
        css = "/* personal-style layer: messaging-only pass (no authored CSS tweaks) */\n"
    if len(css) > _MAX_CSS_CHARS:
        css = css[:_MAX_CSS_CHARS]

    html = _absolutize_asset_urls(html, source_url)
    patch = (
        f'\n<base href="{source_url}">\n'
        f'<style id="personal-style-patch">\n{css}\n</style>\n'
    )
    lower = html.lower()

    head_close_idx = lower.rfind("</head>")
    if head_close_idx != -1:
        return html[:head_close_idx] + patch + html[head_close_idx:]

    body_open_idx = lower.find("<body")
    if body_open_idx != -1:
        body_tag_end = html.find(">", body_open_idx)
        if body_tag_end != -1:
            insert_idx = body_tag_end + 1
            return html[:insert_idx] + patch + html[insert_idx:]

    return patch + html


@function_tool
def build_updated_webpage(
    url: str,
    css_overrides: str,
    content_edits_json: str = "[]",
) -> str:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return "Website update failed: URL must start with http:// or https://"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        html = fetch_website_html(url)
        edited, attempted, applied = apply_content_edits_to_html(html, content_edits_json)
        patched = inject_css_into_html(edited, css_overrides, url)
        UPDATED_HTML_OUTPUT.write_text(patched, encoding="utf-8")
        vt = (
            f"Voice/tone JSON ops: {attempted} parsed / {applied} successfully applied in DOM. "
            "If applied is 0 but you described copy changes, revise selectors next run.\n\n"
        )
        return f"{vt}Updated webpage saved.\n\nHTML_PATH:\n{UPDATED_HTML_OUTPUT}"
    except HTTPError as exc:
        return f"Website update failed: HTTP error {exc.code}"
    except URLError as exc:
        reason = getattr(exc, "reason", exc)
        detail = str(reason) if reason is not None else str(exc)
        low = detail.lower()
        if "errno 8" in low or "nodename nor servname" in low or "name or service not known" in low:
            return (
                "Website update failed: DNS could not resolve this host—check URL and network "
                "(or paste HTML / use a reachable staging URL)."
            )
        return f"Website update failed: URL fetch failed: {detail}"
    except Exception as exc:
        return f"Website update failed: {exc}"
