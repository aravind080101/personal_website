"""CLI entry: personal website updater (same manager as Streamlit app)."""

from pathlib import Path

from dotenv import load_dotenv

from agents import Runner
from agents_config import personal_website_updater_agent
from tools import prefetch_editable_snapshot_section

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent


def main() -> None:
    print("Personal Website Updater (CLI)")
    print("Loads personal_identity.txt when present.\n")

    identity_path = BASE_DIR / "personal_identity.txt"
    try:
        personal_identity = identity_path.read_text(encoding="utf-8").strip()
    except OSError:
        personal_identity = ""

    url = input("Website URL: ").strip()
    if not url:
        print("URL required.")
        return

    try:
        snapshot_block, _n = prefetch_editable_snapshot_section(url)
    except Exception as exc:
        snapshot_block = (
            f"(Prefetch for copy snapshot failed: {exc}. "
            "The agent still runs build_updated_webpage which fetches the URL again.)\n"
        )

    print("Describe style + messaging intent (empty = sensible default):\n")
    user_request = input("> ").strip()
    if not user_request:
        user_request = (
            "Apply my saved personal identity—CSS polish plus voice/tone copy via JSON edits. "
            "Keep template structure and navigation."
        )

    prompt = f"""
Website URL:
{url}

User personal identity/details (name, brand context, audience, preferences):
{personal_identity or "No saved personal identity yet."}

Static page copy cues (verified selectors from a live HTML fetch):
{snapshot_block}

User request:
{user_request}

Mandatory preservation rules:
- Keep template, DOM order, and navigation recognizable
- Use content_edits_json for in-place wording; no new sections
""".strip()
    print("\nRunning updater...\n")
    result = Runner.run_sync(personal_website_updater_agent, prompt)
    print(result.final_output)


if __name__ == "__main__":
    main()
