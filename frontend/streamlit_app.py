"""
Streamlit Chatbot UI for BookScout AI — AI-Powered Book Discovery.
Client-facing demo — no technical/sensitive information exposed.
"""

import os
import re
import json
import time
import uuid
import shutil
import threading
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

# Local folder to store active session files (wiped on fresh start)
_SESSIONS_DIR = Path(__file__).resolve().parent / "chat_sessions"

# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="BookScout AI — AI-Powered Book Discovery",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

/* ── Sidebar ──────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #0f0f1a 0%, #13131f 100%);
    border-right: 1px solid rgba(255,255,255,0.06);
}
[data-testid="stSidebar"] .block-container {
    padding-top: 1.2rem;
}

.sidebar-logo {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 0 4px 16px 4px;
    border-bottom: 1px solid rgba(255,255,255,0.07);
    margin-bottom: 14px;
}
.sidebar-logo-icon {
    font-size: 28px;
    line-height: 1;
}
.sidebar-logo-text {
    font-size: 15px;
    font-weight: 700;
    color: #f1f5f9;
    line-height: 1.2;
}
.sidebar-logo-sub {
    font-size: 11px;
    color: #64748b;
    font-weight: 400;
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
.status-label   { font-size: 12px; color: #94a3b8; vertical-align: middle; }

/* Chat session list items */
.sess-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 10px;
    border-radius: 10px;
    margin-bottom: 4px;
    cursor: pointer;
    transition: background 0.15s;
}
.sess-item:hover { background: rgba(99,102,241,0.1); }
.sess-item.active {
    background: rgba(99,102,241,0.18);
    border: 1px solid rgba(99,102,241,0.35);
}
.sess-title { font-size: 13px; color: #e2e8f0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 170px; }

/* ── Main area ─────────────────────────────────────────────── */
.hero-wrap {
    background: linear-gradient(135deg, #0f0f1a 0%, #1a1040 50%, #0d1a2e 100%);
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 18px;
    padding: 28px 32px 22px 32px;
    margin-bottom: 22px;
    position: relative;
    overflow: hidden;
}
.hero-wrap::before {
    content: '';
    position: absolute;
    top: -60px; right: -60px;
    width: 200px; height: 200px;
    border-radius: 50%;
    background: radial-gradient(circle, rgba(99,102,241,0.15) 0%, transparent 70%);
    pointer-events: none;
}
.hero-title {
    font-size: 26px;
    font-weight: 700;
    color: #f8fafc;
    margin: 0 0 6px 0;
    letter-spacing: -0.3px;
}
.hero-sub {
    font-size: 13px;
    color: #64748b;
    margin: 0 0 16px 0;
}
.badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 9999px;
    font-size: 11px;
    font-weight: 600;
    margin-right: 6px;
    margin-bottom: 4px;
    background: rgba(99,102,241,0.12);
    color: #a5b4fc;
    border: 1px solid rgba(99,102,241,0.25);
    transition: background 0.2s;
}
.badge:hover { background: rgba(99,102,241,0.22); }

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

/* Suggestion buttons in chat */
.suggestion-row button {
    font-size: 12px !important;
    padding: 6px 12px !important;
    border-radius: 8px !important;
}

/* Confirmation banner */
.confirm-box {
    background: rgba(239,68,68,0.1);
    border: 1px solid rgba(239,68,68,0.3);
    border-radius: 12px;
    padding: 14px 18px;
    margin: 8px 0;
}
.confirm-box p {
    margin: 0 0 10px 0;
    font-size: 13px;
    color: #fca5a5;
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
# Session file helpers
# ---------------------------------------------------------------------------

def _ensure_sessions_dir():
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _wipe_sessions_dir():
    """Delete all session JSON files — called once on cold start."""
    if _SESSIONS_DIR.exists():
        shutil.rmtree(_SESSIONS_DIR)
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def _session_file(session_id: str) -> Path:
    return _SESSIONS_DIR / f"{session_id}.json"


def _save_session(session: dict):
    _ensure_sessions_dir()
    try:
        with open(_session_file(session["id"]), "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _delete_session_file(session_id: str):
    p = _session_file(session_id)
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------

def _new_session_data() -> dict:
    return {
        "id": str(uuid.uuid4())[:8],
        "title": "New Conversation",
        "messages": [],
        "created_at": time.time(),
    }


# Cold-start: wipe stale files once per interpreter process
if "cold_start_done" not in st.session_state:
    _wipe_sessions_dir()
    st.session_state.cold_start_done = True

if "session" not in st.session_state:
    st.session_state.session = _new_session_data()

if "pending_query" not in st.session_state:
    st.session_state.pending_query = None

# Confirmation state
if "confirm_delete" not in st.session_state:
    st.session_state.confirm_delete = False   # True = show "Are you sure?" for delete
if "confirm_new" not in st.session_state:
    st.session_state.confirm_new = False       # True = show "Are you sure?" for new chat

# Title was generated flag
if "title_generated" not in st.session_state:
    st.session_state.title_generated = False


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


def _generate_title(messages: list) -> str:
    try:
        payload = {"messages": messages[:3]}
        r = requests.post(f"{_API_URL}/generate-title", json=payload, timeout=15)
        if r.status_code == 200:
            return r.json().get("title", "New Conversation")
    except Exception:
        pass
    return "New Conversation"


def _trigger_bg_title(messages: list, session_id: str):
    """Run conversation title generation asynchronously in background."""
    def _worker():
        try:
            title = _generate_title(messages)
            if title and title != "New Conversation":
                sf = _session_file(session_id)
                if sf.exists():
                    with open(sf, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    data["title"] = title
                    with open(sf, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()


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


def _handle_suggestion_click(prompt: str):
    """Callback when a suggestion button is clicked so state is set prior to script execution."""
    st.session_state.pending_query = prompt


# ---------------------------------------------------------------------------
# Convenience references
# ---------------------------------------------------------------------------

sess = st.session_state.session
messages: list = sess["messages"]


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:

    # Logo / Branding
    st.markdown("""
    <div class="sidebar-logo">
        <div class="sidebar-logo-icon">📚</div>
        <div>
            <div class="sidebar-logo-text">BookScout AI</div>
            <div class="sidebar-logo-sub">AI-Powered Book Discovery</div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # Connection status — simple dot only, no technical details
    health = _health()
    is_online = health is not None and health.get("neo4j_connected", False)
    dot_cls = "status-online" if is_online else "status-offline"
    status_txt = "Connected" if is_online else "Offline"
    st.markdown(
        f'<span class="status-dot {dot_cls}"></span>'
        f'<span class="status-label">{status_txt}</span>',
        unsafe_allow_html=True,
    )

    st.markdown("<div style='margin-bottom:16px'></div>", unsafe_allow_html=True)

    # ── "New Conversation" button — only visible once a conversation has started ──
    has_messages = len(messages) > 0

    if has_messages:
        if st.session_state.confirm_new:
            st.markdown("""
            <div class="confirm-box">
                <p>⚠️ Starting a new chat will <strong>permanently delete</strong> this conversation.</p>
            </div>
            """, unsafe_allow_html=True)
            col_yes, col_no = st.columns(2)
            with col_yes:
                if st.button("Yes, start fresh", key="confirm_new_yes", type="primary", use_container_width=True):
                    _delete_session_file(sess["id"])
                    st.session_state.session = _new_session_data()
                    st.session_state.pending_query = None
                    st.session_state.confirm_delete = False
                    st.session_state.confirm_new = False
                    st.session_state.title_generated = False
                    st.rerun()
            with col_no:
                if st.button("Cancel", key="confirm_new_no", use_container_width=True):
                    st.session_state.confirm_new = False
                    st.rerun()
        else:
            if st.button("➕ New Conversation", use_container_width=True, type="primary"):
                st.session_state.confirm_new = True
                st.rerun()

        st.markdown("---")

    # ── Current conversation entry (shown only once it has started) ──
    if has_messages:
        title = sess.get("title", "New Conversation")
        display_title = title if len(title) <= 30 else title[:27] + "..."

        if st.session_state.confirm_delete:
            st.markdown("""
            <div class="confirm-box">
                <p>🗑️ Are you sure you want to delete this conversation? This cannot be undone.</p>
            </div>
            """, unsafe_allow_html=True)
            col_d1, col_d2 = st.columns(2)
            with col_d1:
                if st.button("Delete", key="confirm_del_yes", type="primary", use_container_width=True):
                    _delete_session_file(sess["id"])
                    st.session_state.session = _new_session_data()
                    st.session_state.pending_query = None
                    st.session_state.confirm_delete = False
                    st.session_state.confirm_new = False
                    st.session_state.title_generated = False
                    st.rerun()
            with col_d2:
                if st.button("Cancel", key="confirm_del_no", use_container_width=True):
                    st.session_state.confirm_delete = False
                    st.rerun()
        else:
            col_title, col_del = st.columns([5, 1])
            with col_title:
                st.markdown(
                    f'<div class="sess-item active">'
                    f'<span style="font-size:16px">💬</span>'
                    f'<span class="sess-title">{display_title}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            with col_del:
                st.markdown("<div style='padding-top:4px'></div>", unsafe_allow_html=True)
                if st.button("🗑️", key="del_sess", help="Delete this conversation"):
                    st.session_state.confirm_delete = True
                    st.session_state.confirm_new = False
                    st.rerun()

    # ── Footer note ──
    st.markdown(
        "<div style='position:absolute;bottom:16px;left:16px;right:16px;"
        "font-size:11px;color:#334155;text-align:center;'>"
        "Conversations are not saved between sessions."
        "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Main content area
# ---------------------------------------------------------------------------

# Hero banner
stats = health.get("stats", {}) if health else {}

st.markdown(f"""
<div class="hero-wrap">
    <div class="hero-title">📖 Sri Lankan Bookstore Price Finder</div>
    <div class="hero-sub">Compare prices &amp; availability across all major Sri Lankan bookstores instantly.</div>
</div>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Input detection (evaluated before rendering so suggestions hide immediately)
# ---------------------------------------------------------------------------

query_to_run = None

if st.session_state.pending_query:
    query_to_run = st.session_state.pending_query
    st.session_state.pending_query = None

is_generating = bool(query_to_run or st.session_state.get("pending_query"))
chat_input = st.chat_input(
    "Ask about book prices, availability, or recommendations…",
    disabled=is_generating,
)
if chat_input:
    query_to_run = chat_input

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
sess = st.session_state.session
messages = sess["messages"]

welcome_placeholder = st.empty()

if len(messages) == 0 and not query_to_run:
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
    # Render conversation history
    for idx, msg in enumerate(messages):
        with st.chat_message(msg["role"]):
            content = msg["content"]
            if msg["role"] == "assistant":
                content = _sanitize_answer(content)
            st.markdown(content)

            # Follow-up suggestions only on the last assistant message and only when NO query is in-flight
            if msg["role"] == "assistant" and idx == len(messages) - 1 and not query_to_run:
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
    # Append user message
    sess["messages"].append({"role": "user", "content": query_to_run})
    _save_session(sess)

    with st.chat_message("user"):
        st.markdown(query_to_run)

    # Stream assistant response
    with st.chat_message("assistant"):
        history_payload = [
            {"role": m["role"], "content": m["content"]}
            for m in sess["messages"][:-1]
        ]

        collected_meta = {
            "suggestions": [],
            "sources": [],
            "cypher_query": "",
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

        # Save assistant message
        new_assistant_msg = {
            "role": "assistant",
            "content": sanitized_answer,
            "suggestions": collected_meta.get("suggestions", []),
        }
        sess["messages"].append(new_assistant_msg)
        _save_session(sess)

        # Render follow-up suggestions immediately under the streamed response
        sug_list = collected_meta.get("suggestions", [])
        if sug_list:
            st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)
            cols = st.columns(len(sug_list))
            for s_i, sug in enumerate(sug_list):
                with cols[s_i]:
                    st.button(
                        f"👉 {sug}",
                        key=f"sug_live_{len(sess['messages'])}_{s_i}",
                        use_container_width=True,
                        on_click=_handle_suggestion_click,
                        args=(sug,),
                    )

    # ── Non-blocking LLM title generation after first complete exchange ──
    if not st.session_state.title_generated and len(sess["messages"]) >= 2:
        title_msgs = [{"role": m["role"], "content": m["content"]} for m in sess["messages"][:3]]
        _trigger_bg_title(title_msgs, sess["id"])
        st.session_state.title_generated = True
