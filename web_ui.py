"""
Personal Agentic Website Review — Streamlit UI.

Run from the project folder:
  streamlit run web_ui.py

Or:
  .venv/bin/streamlit run web_ui.py
"""

from __future__ import annotations

import os
from pathlib import Path

# Streamlit stores config under ``Path.home() / ".streamlit"``. Pointing HOME at this
# project avoids PermissionError when ``~/.streamlit`` is not writable (e.g. sandbox).
# Set STREAMLIT_PROJECT_HOME=0 to use your real home directory instead.
_project_root = Path(__file__).resolve().parent
if os.environ.get("STREAMLIT_PROJECT_HOME", "1").lower() not in ("0", "false"):
    os.environ["HOME"] = str(_project_root)

import streamlit as st
import streamlit.components.v1 as components

import pipeline as P


def _apply_streamlit_secrets_to_environ() -> None:
    """Streamlit Community Cloud exposes app secrets via ``st.secrets``; the Agents SDK reads ``OPENAI_API_KEY`` etc. from the environment."""
    try:
        sec = getattr(st, "secrets", None)
        if sec is None:
            return
        for key in ("OPENAI_API_KEY", "OPENAI_API_BASE", "OPENAI_ORG_ID"):
            if key in sec and not os.environ.get(key):
                os.environ[key] = str(sec[key])
    except (AttributeError, TypeError, RuntimeError):
        pass


def _init_session() -> None:
    if "agent_result" not in st.session_state:
        st.session_state.agent_result = ""
    if "agent_logs" not in st.session_state:
        st.session_state.agent_logs = []
    if "last_run_done" not in st.session_state:
        st.session_state.last_run_done = False
    if "personal_identity" not in st.session_state:
        st.session_state.personal_identity = P.load_personal_identity()


def main() -> None:
    st.set_page_config(
        page_title="Personal Website Updater",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _apply_streamlit_secrets_to_environ()
    _init_session()

    st.title("Pradeep.io")
    st.caption(
        "Personal Agent Editor"
    )

    website_url = st.text_input(
        "Website URL",
        placeholder="https://example.com",
    )
    user_request = st.text_area(
        "Style and messaging intent for this run",
        height=140,
        placeholder=(
            "Examples:\n"
            "- Premium spacing/typography and sharper hero + primary CTA wording per my identity file.\n"
            "- Keep Bootstrap; calm marketing tone; improve feature section headlines for scanability."
        ),
    )
    with st.expander("Personal identity / own details (applies every run)", expanded=False):
        identity = st.text_area(
            "My details",
            height=140,
            key="personal_identity",
            placeholder=(
                "Example: I am Aravind, building premium SaaS experiences for modern product teams. "
                "Audience: founders and PMs. Prioritize clarity, trust, and conversion."
            ),
        )
        if st.button("Save personal details"):
            try:
                P.save_personal_identity(identity)
                st.success("Personal details saved.")
            except Exception as e:
                st.error(f"Could not save personal details: {e}")
    if st.button("Update website", type="primary"):
        if not website_url.strip():
            st.warning("Please provide a website URL.")
        else:
            with st.spinner("Applying visual polish and voice/tone copy edits..."):
                try:
                    logs: list[str] = []
                    result = P.run_personal_website_updater(
                        website_url.strip(),
                        (user_request or "").strip(),
                        logs,
                    )
                    st.session_state.agent_result = result
                    st.session_state.agent_logs = logs
                    st.session_state.last_run_done = True
                    P.server_log(f"Personal website update completed for {website_url.strip()}")
                    if P.UPDATED_WEBPAGE.exists():
                        st.success("Updated webpage is ready.")
                    else:
                        st.error("Update finished but webpage file was not generated.")
                except Exception as e:
                    st.session_state.last_run_done = True
                    P.server_log(f"Personal website update failed: {e}")
                    st.error(str(e))

    if P.UPDATED_WEBPAGE.exists():
        st.divider()
        st.subheader("Updated Webpage")
        if st.session_state.agent_result:
            with st.expander("Changes made", expanded=True):
                st.markdown(st.session_state.agent_result)
        html_text = P.UPDATED_WEBPAGE.read_text(encoding="utf-8")
        st.download_button(
            "Download page",
            data=html_text,
            file_name="updated_page.html",
            mime="text/html",
        )
        st.caption("Inline preview")
        components.html(html_text, height=900, scrolling=True)
    elif st.session_state.last_run_done:
        st.warning("No updated webpage to open for the last run.")
        if st.session_state.agent_result:
            with st.expander("Last run details", expanded=False):
                st.code(st.session_state.agent_result)


main()
