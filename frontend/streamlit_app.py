"""
Streamlit Chatbot UI for BookScout AI — AI-Powered Book Discovery.
Client-facing demo — no technical/sensitive information exposed.
"""

import os
import re
import json
import requests
import streamlit as st
from pathlib import Path
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Environment & Configuration (hidden from UI)
# ---------------------------------------------------------------------------

_env_path = Path(__file__).resolve().parent.parent / ".env"
if _env_path.exists():
    load_dotenv(_env_path)

_API_URL = os.getenv("API_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="BookScout AI — AI-Powered Book Discovery",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

/* ── Hide Sidebar & Toggle Button Completely ────────────────────── */
[data-testid="stSidebar"],
[data-testid="collapsedControl"] {
    display: none !important;
}

.status-dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 6px;
    vertical-align: middle;
}
.status-online  { background: #22c55e; box-shadow: 0 0 6px #22c55e88; }
.status-offline { background: #ef4444; box-shadow: 0 0 6px #ef444488; }



/* Welcome screen */
.welcome-heading {
    text-align: center;
    padding: 32px 0 8px 0;
    font-size: 22px;
    font-weight: 600;
    color: #f1f5f9;
}
.welcome-sub {
    text-align: center;
    font-size: 14px;
    color: #64748b;
    margin-bottom: 28px;
}

/* Starter suggestion & action buttons */
div[data-testid="stButton"] > button[kind="secondary"] {
    background: rgba(30,41,59,0.7) !important;
    border: 1px solid rgba(255,255,255,0.08) !important;
    border-radius: 12px !important;
    color: #cbd5e1 !important;
    font-size: 13px !important;
    text-align: left !important;
    padding: 12px 16px !important;
    transition: all 0.2s ease !important;
    white-space: normal !important;
    height: auto !important;
}
div[data-testid="stButton"] > button[kind="secondary"]:hover {
    background: rgba(99,102,241,0.15) !important;
    border-color: rgba(99,102,241,0.4) !important;
    color: #e2e8f0 !important;
    transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(0,0,0,0.2) !important;
}

/* Hide only disabled stButtons (e.g. suggestions during execution), NEVER stChatInput */
div[data-testid="stButton"] > button:disabled,
div[data-testid="stButton"] > button[disabled],
div[data-testid="stButton"] > button[aria-disabled="true"] {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
    min-height: 0 !important;
    max-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    border: none !important;
}

div[data-testid="stElementContainer"]:has(div[data-testid="stButton"] > button:disabled):not(:has([data-testid="stChatInput"])) {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
    min-height: 0 !important;
    max-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    border: none !important;
}

/* Primary buttons */
div[data-testid="stButton"] > button[kind="primary"] {
    background: linear-gradient(135deg, #6366f1, #8b5cf6) !important;
    border: none !important;
    border-radius: 10px !important;
    font-weight: 600 !important;
    letter-spacing: 0.2px !important;
    transition: all 0.2s ease !important;
}
div[data-testid="stButton"] > button[kind="primary"]:hover {
    filter: brightness(1.12);
    transform: translateY(-1px);
    box-shadow: 0 4px 14px rgba(99,102,241,0.35) !important;
}

/* Chat input bar - always visible and floating at bottom */
[data-testid="stBottom"],
div[data-testid="stElementContainer"]:has([data-testid="stChatInput"]),
[data-testid="stChatInput"] {
    display: block !important;
    visibility: visible !important;
    opacity: 1 !important;
}

[data-testid="stChatInput"] textarea {
    border-radius: 14px !important;
    border: 1px solid rgba(99,102,241,0.3) !important;
    background: rgba(15,15,26,0.9) !important;
    color: #f1f5f9 !important;
    font-size: 14px !important;
    padding: 12px 16px !important;
    transition: all 0.2s ease;
}
[data-testid="stChatInput"] textarea:focus {
    border-color: rgba(99,102,241,0.7) !important;
    box-shadow: 0 0 0 2px rgba(99,102,241,0.15) !important;
}
[data-testid="stChatInput"] textarea:disabled {
    opacity: 0.5 !important;
    cursor: not-allowed !important;
    background: rgba(15,15,26,0.5) !important;
}

/* Chat input send button states */
[data-testid="stChatInput"] button {
    transition: all 0.2s ease !important;
}
[data-testid="stChatInput"] button:disabled,
[data-testid="stChatInput"] button[disabled],
[data-testid="stChatInput"] button[aria-disabled="true"] {
    opacity: 0.35 !important;
    cursor: not-allowed !important;
    pointer-events: none !important;
}
[data-testid="stChatInput"] button:not(:disabled):not([aria-disabled="true"]) {
    opacity: 1 !important;
    cursor: pointer !important;
    color: #818cf8 !important;
}


/* Chat messages */
[data-testid="stChatMessage"] {
    border-radius: 14px !important;
    margin-bottom: 6px !important;
}



/* AI Step Status Indicator */
.ai-status-pill {
    display: inline-flex;
    align-items: center;
    gap: 9px;
    padding: 7px 16px;
    border-radius: 9999px;
    background: rgba(99, 102, 241, 0.12);
    border: 1px solid rgba(99, 102, 241, 0.35);
    color: #c7d2fe;
    font-size: 13px;
    font-weight: 500;
    margin-bottom: 8px;
    animation: fadeIn 0.2s ease-in-out;
}

@keyframes fadeIn {
    from { opacity: 0; transform: translateY(3px); }
    to { opacity: 1; transform: translateY(0); }
}

.ai-spinner {
    display: inline-block;
    width: 14px;
    height: 14px;
    border: 2px solid rgba(165, 180, 252, 0.25);
    border-radius: 50%;
    border-top-color: #a5b4fc;
    animation: spin 0.8s linear infinite;
}

@keyframes spin {
    to { transform: rotate(360deg); }
}

/* Hide Streamlit default elements */
#MainMenu, footer, header { visibility: hidden; }
.stDeployButton { display: none; }

</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

if "messages" not in st.session_state:
    st.session_state.messages = []

if "pending_query" not in st.session_state:
    st.session_state.pending_query = None


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------

def _health() -> dict | None:
    try:
        r = requests.get(f"{_API_URL}/health", timeout=4)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _chat(user_text: str, history: list) -> tuple[dict | None, str | None]:
    try:
        payload = {
            "query": user_text,
            "history": history[-6:],
        }
        r = requests.post(f"{_API_URL}/chat", json=payload, timeout=120)
        if r.status_code == 200:
            return r.json(), None
        return None, f"Server error {r.status_code}"
    except Exception as e:
        return None, f"Connection error: {e}"


def _chat_stream(user_text: str, history: list):
    """Stream NDJSON events from backend /chat/stream."""
    try:
        payload = {
            "query": user_text,
            "history": history[-6:],
        }
        r = requests.post(f"{_API_URL}/chat/stream", json=payload, stream=True, timeout=120)
        if r.status_code != 200:
            yield {"type": "error", "error": f"Server error {r.status_code}"}
            return
        for line in r.iter_lines(decode_unicode=True):
            if line:
                try:
                    event = json.loads(line)
                    yield event
                except Exception:
                    pass
    except Exception as e:
        yield {"type": "error", "error": f"Connection error: {e}"}




# ---------------------------------------------------------------------------
# Answer sanitiser — strips URLs / markdown links before display
# ---------------------------------------------------------------------------

_MD_LINK_RE = re.compile(r'\[([^\]]+)\]\([^)]*\)')   # [text](url) → text
_BARE_URL_RE = re.compile(
    r'https?://[^\s)>"]+',                             # bare https://... URLs
    re.IGNORECASE,
)


def _sanitize_answer(text: str) -> str:
    """Remove all hyperlinks and bare URLs from the LLM response."""
    text = _MD_LINK_RE.sub(r'\1', text)   # keep link label, drop URL
    text = _BARE_URL_RE.sub('', text)      # remove any remaining bare URLs
    return text.strip()


def _format_timing_badge(msg: dict) -> str:
    """Returns HTML caption for total execution time and latency breakdown."""
    exec_time = msg.get("execution_time_ms")
    breakdown = msg.get("latency_breakdown")
    if exec_time and exec_time > 0:
        sec = exec_time / 1000.0
        if breakdown and isinstance(breakdown, dict):
            ret_sec = (breakdown.get("retrieval_ms") or 0) / 1000.0
            syn_sec = (breakdown.get("synthesis_ms") or 0) / 1000.0
            return (
                f'<div style="font-size: 11px; color: #64748b; margin-top: 6px; font-weight: 500;">'
                f'⚡ Answered in {sec:.2f}s &nbsp;•&nbsp; Retrieval: {ret_sec:.2f}s | Synthesis: {syn_sec:.2f}s'
                f'</div>'
            )
        return f'<div style="font-size: 11px; color: #64748b; margin-top: 6px; font-weight: 500;">⚡ Answered in {sec:.2f}s</div>'
    return ""


def _handle_suggestion_click(prompt: str):
    """Callback when a suggestion button is clicked so state is set prior to script execution."""
    st.session_state.pending_query = prompt


# ---------------------------------------------------------------------------
# Input detection & Session State Pre-update
# ---------------------------------------------------------------------------

messages: list = st.session_state.messages

query_to_run = None

if st.session_state.pending_query:
    query_to_run = st.session_state.pending_query
    st.session_state.pending_query = None

is_generating = bool(query_to_run)
chat_input = st.chat_input(
    "Ask about book prices, availability, or recommendations…",
    disabled=is_generating,
)
if chat_input:
    query_to_run = chat_input

if query_to_run:
    messages.append({"role": "user", "content": query_to_run})


# ---------------------------------------------------------------------------
# Header & Navigation
# ---------------------------------------------------------------------------

health = _health()
is_online = health is not None and health.get("neo4j_connected", False)
dot_cls = "status-online" if is_online else "status-offline"
status_txt = "Connected" if is_online else "Offline"

# Embossed header card — visually separates title from the chat area below
st.markdown(f"""
<div style="
    background: linear-gradient(135deg, rgba(15,15,30,0.95) 0%, rgba(26,16,64,0.9) 55%, rgba(13,26,46,0.95) 100%);
    border: 1px solid rgba(99,102,241,0.18);
    border-radius: 16px;
    padding: 18px 24px 16px 24px;
    margin-bottom: 18px;
    position: relative;
    overflow: hidden;
    box-shadow:
        0 1px 0 rgba(255,255,255,0.06) inset,
        0 -1px 0 rgba(0,0,0,0.4) inset,
        0 8px 32px rgba(0,0,0,0.45),
        0 0 0 1px rgba(0,0,0,0.3);
">
    <!-- radial glow top-right -->
    <div style="
        position: absolute; top: -40px; right: -40px;
        width: 160px; height: 160px; border-radius: 50%;
        background: radial-gradient(circle, rgba(99,102,241,0.18) 0%, transparent 70%);
        pointer-events: none;
    "></div>
    <div style="font-size: 22px; font-weight: 700; color: #f8fafc; letter-spacing: -0.3px; line-height: 1.2;">
        📖 BookScout AI
    </div>
    <div style="font-size: 12px; color: #64748b; margin-top: 5px;">
        AI-Powered Book Discovery &nbsp;•&nbsp;
        <span class="status-dot {dot_cls}"></span>
        <span style="color: #94a3b8; font-weight: 500;">{status_txt}</span>
    </div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Chat interface
# ---------------------------------------------------------------------------

STARTER_SUGGESTIONS = [
    "Which store has the cheapest thriller books?",
    "What is the price of A Good Girl's Guide to Murder?",
    "Compare prices for Atomic Habits across stores",
    "What books are available across the bookstores?",
    "Books by Colleen Hoover under LKR 3,000",
    "Find top-rated fantasy fiction novels in stock",
]

# Refresh local reference after any rerun
messages = st.session_state.messages

welcome_placeholder = st.empty()

if len(messages) == 0:
    # Welcome / starter screen
    with welcome_placeholder.container():
        st.markdown('<div class="welcome-heading">✨ How can I help you find books today?</div>', unsafe_allow_html=True)
        st.markdown('<div class="welcome-sub">Select a quick suggestion below, or type your question in the chat box.</div>', unsafe_allow_html=True)

        col1, col2 = st.columns(2)
        for i, prompt in enumerate(STARTER_SUGGESTIONS):
            target = col1 if i % 2 == 0 else col2
            with target:
                st.button(
                    f"💡 {prompt}",
                    key=f"starter_{i}",
                    use_container_width=True,
                    on_click=_handle_suggestion_click,
                    args=(prompt,),
                )
else:
    welcome_placeholder.empty()
    # Render conversation history up to current state
    history_to_render = messages[:-1] if query_to_run else messages
    for idx, msg in enumerate(history_to_render):
        with st.chat_message(msg["role"]):
            content = msg["content"]
            if msg["role"] == "assistant":
                content = _sanitize_answer(content)
            st.markdown(content)
            if msg["role"] == "assistant":
                badge_html = _format_timing_badge(msg)
                if badge_html:
                    st.markdown(badge_html, unsafe_allow_html=True)

            # Follow-up suggestions only on the last assistant message and only when NO query is in-flight
            if msg["role"] == "assistant" and idx == len(history_to_render) - 1 and not query_to_run:
                sug_list = msg.get("suggestions", [])
                if sug_list:
                    st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
                    cols = st.columns(len(sug_list))
                    for s_i, sug in enumerate(sug_list):
                        with cols[s_i]:
                            st.button(
                                f"👉 {sug}",
                                key=f"sug_{idx}_{s_i}",
                                use_container_width=True,
                                on_click=_handle_suggestion_click,
                                args=(sug,),
                            )

# ---------------------------------------------------------------------------
# Execute & Stream current query (if submitted)
# ---------------------------------------------------------------------------

if query_to_run:
    with st.chat_message("user"):
        st.markdown(query_to_run)

    # Stream assistant response
    with st.chat_message("assistant"):
        history_payload = [
            {"role": m["role"], "content": m["content"]}
            for m in messages[:-1]
        ]

        collected_meta = {
            "suggestions": [],
            "sources": [],
            "cypher_query": "",
            "execution_time_ms": 0.0,
            "latency_breakdown": None,
            "error": None,
        }

        # Real-time step status placeholder
        status_placeholder = st.empty()
        status_placeholder.markdown(
            '<div class="ai-status-pill"><span class="ai-spinner"></span> 🔍 Searching bookstore inventories &amp; database...</div>',
            unsafe_allow_html=True,
        )

        def stream_consumer():
            for event in _chat_stream(query_to_run, history_payload):
                ev_type = event.get("type")
                if ev_type == "status":
                    stage = event.get("stage", "searching")
                    msg = event.get("message", "Processing...")
                    icon = "🔍" if stage == "searching" else ("📊" if stage == "analyzing" else "🧠")
                    status_placeholder.markdown(
                        f'<div class="ai-status-pill"><span class="ai-spinner"></span> {icon} {msg}</div>',
                        unsafe_allow_html=True,
                    )
                elif ev_type == "token":
                    status_placeholder.empty()
                    content = event.get("content", "")
                    yield content
                elif ev_type == "done":
                    status_placeholder.empty()
                    collected_meta["suggestions"] = event.get("suggestions", [])
                    collected_meta["sources"] = event.get("sources", [])
                    collected_meta["cypher_query"] = event.get("cypher_query", "")
                    collected_meta["execution_time_ms"] = event.get("execution_time_ms", 0.0)
                    collected_meta["latency_breakdown"] = event.get("latency_breakdown")
                elif ev_type == "error":
                    status_placeholder.empty()
                    collected_meta["error"] = event.get("error")

        answer = st.write_stream(stream_consumer())
        status_placeholder.empty()

        if collected_meta["error"] and not answer:
            answer = "⚠️ **Something went wrong.** Please try again in a moment."
            st.error(answer)

        # Sanitize final answer
        sanitized_answer = _sanitize_answer(answer or "")

        # Save assistant message with execution time metadata
        new_assistant_msg = {
            "role": "assistant",
            "content": sanitized_answer,
            "suggestions": collected_meta.get("suggestions", []),
            "execution_time_ms": collected_meta.get("execution_time_ms", 0.0),
            "latency_breakdown": collected_meta.get("latency_breakdown"),
        }
        messages.append(new_assistant_msg)

        # Render timing badge for the live response
        badge_html = _format_timing_badge(new_assistant_msg)
        if badge_html:
            st.markdown(badge_html, unsafe_allow_html=True)

        # Render follow-up suggestions immediately under the streamed response
        sug_list = collected_meta.get("suggestions", [])
        if sug_list:
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            cols = st.columns(len(sug_list))
            for s_i, sug in enumerate(sug_list):
                with cols[s_i]:
                    st.button(
                        f"👉 {sug}",
                        key=f"sug_live_{len(messages)}_{s_i}",
                        use_container_width=True,
                        on_click=_handle_suggestion_click,
                        args=(sug,),
                    )

    # Rerun so chat input is re-rendered in active/enabled state for typing
    st.rerun()
