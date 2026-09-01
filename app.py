"""EdgeDash Career Intelligence Dashboard.

A premium, modern LinkedIn-inspired professional dashboard for career intelligence,
job match evaluation, skill gap analytics, and continuous verification telemetry.
Strictly read-only.
"""

from __future__ import annotations

from datetime import datetime, timezone
import html
import os
from pathlib import Path
import streamlit as st

from edgedash.config import load_config
from edgedash.env import load_env
from edgedash import storage

load_env()

# ---------------------------------------------------------------------------
# Page Configuration & Global Branding
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="EdgeDash | LinkedIn Career Intelligence",
    page_icon="💼",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Load configuration dynamically (Rule 3)
@st.cache_resource
def get_config():
    return load_config("config.yaml")

config = get_config()
db_path = config.db_path

# ---------------------------------------------------------------------------
# LinkedIn-Inspired CSS Design System
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Outfit:wght@500;600;700;800&display=swap');

    /* Global Theme Overrides */
    .stApp {
        background-color: #0b0f19;
        color: #e2e8f0;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    }

    /* Headings */
    h1, h2, h3, h4, h5, h6 {
        font-family: 'Outfit', 'Inter', sans-serif !important;
        font-weight: 700 !important;
        color: #f8fafc !important;
        letter-spacing: -0.02em;
    }

    /* Top Navigation Banner */
    .linkedin-navbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        background: linear-gradient(180deg, #1e293b 0%, #0f172a 100%);
        border: 1px solid #334155;
        border-radius: 16px;
        padding: 14px 24px;
        margin-bottom: 20px;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
    }
    .linkedin-logo {
        display: flex;
        align-items: center;
        gap: 12px;
    }
    .logo-badge {
        background: #0a66c2;
        color: #ffffff;
        font-family: 'Outfit', sans-serif;
        font-weight: 800;
        font-size: 1.25em;
        width: 38px;
        height: 38px;
        border-radius: 8px;
        display: flex;
        align-items: center;
        justify-content: center;
        box-shadow: 0 2px 10px rgba(10, 102, 194, 0.4);
    }
    .navbar-title {
        font-size: 1.2em;
        font-weight: 700;
        color: #ffffff;
        margin: 0;
        line-height: 1.2;
    }
    .navbar-subtitle {
        font-size: 0.8em;
        color: #94a3b8;
        margin: 0;
    }

    /* Profile / Candidate Banner */
    .candidate-card {
        background: #111827;
        border: 1px solid #1f2937;
        border-radius: 16px;
        padding: 20px 24px;
        margin-bottom: 20px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
        position: relative;
        overflow: hidden;
    }
    .candidate-card::before {
        content: "";
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 4px;
        background: linear-gradient(90deg, #0a66c2 0%, #38bdf8 50%, #10b981 100%);
    }
    .candidate-info {
        display: flex;
        align-items: center;
        gap: 18px;
    }
    .candidate-avatar {
        width: 56px;
        height: 56px;
        border-radius: 50%;
        background: linear-gradient(135deg, #0a66c2 0%, #0284c7 100%);
        color: #ffffff;
        font-weight: 800;
        font-size: 1.3em;
        display: flex;
        align-items: center;
        justify-content: center;
        border: 2px solid #38bdf8;
        box-shadow: 0 4px 12px rgba(10, 102, 194, 0.35);
    }

    /* Metric Cards */
    div[data-testid="metric-container"] {
        background-color: #111827;
        border: 1px solid #1f2937;
        padding: 18px 22px;
        border-radius: 14px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.25);
        transition: all 0.25s ease;
    }
    div[data-testid="metric-container"]:hover {
        transform: translateY(-2px);
        border-color: #0a66c2;
        box-shadow: 0 12px 24px -6px rgba(10, 102, 194, 0.25);
    }
    div[data-testid="stMetricLabel"] {
        color: #94a3b8 !important;
        font-size: 0.85em !important;
        font-weight: 600 !important;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    div[data-testid="stMetricValue"] {
        color: #f8fafc !important;
        font-size: 1.85em !important;
        font-weight: 700 !important;
        font-family: 'Outfit', sans-serif !important;
    }

    /* Tab Customizations */
    div[data-baseweb="tab-list"] {
        gap: 8px;
        background-color: #0f172a;
        padding: 6px;
        border-radius: 12px;
        border: 1px solid #1e293b;
    }
    button[data-baseweb="tab"] {
        border-radius: 8px !important;
        font-weight: 600 !important;
        font-size: 0.9em !important;
        padding: 8px 18px !important;
        color: #94a3b8 !important;
        background-color: transparent !important;
        border: none !important;
        transition: all 0.2s ease !important;
    }
    button[data-baseweb="tab"][aria-selected="true"] {
        background-color: #0a66c2 !important;
        color: #ffffff !important;
        box-shadow: 0 2px 8px rgba(10, 102, 194, 0.4) !important;
    }

    /* LinkedIn Job Feed Post Card */
    .job-post-card {
        background: #111827;
        border: 1px solid #1e293b;
        border-radius: 16px;
        padding: 22px 24px;
        margin-bottom: 18px;
        box-shadow: 0 6px 16px rgba(0, 0, 0, 0.25);
        transition: all 0.2s ease;
    }
    .job-post-card:hover {
        border-color: #3b82f6;
        box-shadow: 0 10px 25px rgba(59, 130, 246, 0.15);
    }
    .company-badge-avatar {
        width: 44px;
        height: 44px;
        border-radius: 10px;
        background: linear-gradient(135deg, #1e293b 0%, #334155 100%);
        border: 1px solid #475569;
        color: #38bdf8;
        font-weight: 800;
        font-size: 1.1em;
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
    }
    .fit-score-badge {
        font-family: 'Outfit', sans-serif;
        font-weight: 700;
        font-size: 0.9em;
        padding: 5px 14px;
        border-radius: 20px;
        display: inline-flex;
        align-items: center;
        gap: 5px;
    }
    .insight-quote-box {
        background: #0f172a;
        border-left: 3px solid #0a66c2;
        border-radius: 8px;
        padding: 12px 16px;
        margin: 14px 0;
        color: #cbd5e1;
        font-size: 0.92em;
        line-height: 1.5;
    }
    .apply-btn {
        background: #0a66c2;
        color: #ffffff !important;
        font-weight: 600;
        font-size: 0.9em;
        padding: 8px 18px;
        border-radius: 24px;
        text-decoration: none;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        box-shadow: 0 2px 6px rgba(10, 102, 194, 0.3);
        transition: all 0.2s ease;
    }
    .apply-btn:hover {
        background: #004182;
        box-shadow: 0 4px 12px rgba(10, 102, 194, 0.5);
    }

    /* Sidebar Styling */
    div[data-testid="stSidebar"] {
        background-color: #0a0e1a;
        border-right: 1px solid #1e293b;
    }

    /* Custom scrollbar */
    ::-webkit-scrollbar {
        width: 8px;
        height: 8px;
    }
    ::-webkit-scrollbar-track {
        background: #0b0f19;
    }
    ::-webkit-scrollbar-thumb {
        background: #1e293b;
        border-radius: 4px;
    }
    ::-webkit-scrollbar-thumb:hover {
        background: #334155;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Caching Data Wrappers
# ---------------------------------------------------------------------------
@st.cache_data(ttl=30)
def fetch_counts(path):
    return storage.get_total_counts(path)

@st.cache_data(ttl=30)
def fetch_last_passing_time(path):
    return storage.get_last_passing_verifier_time(path)

@st.cache_data(ttl=30)
def fetch_newest_verifier(path):
    return storage.get_newest_verifier_cycle(path)

@st.cache_data(ttl=30)
def fetch_verified_listings(path, ver_time):
    return storage.get_verified_listings(path, ver_time, limit=100)

@st.cache_data(ttl=30)
def fetch_verified_gaps(path, ver_time):
    return storage.get_verified_skill_gaps(path, ver_time, limit=100)

@st.cache_data(ttl=30)
def fetch_activity_log(path):
    return storage.get_activity_log(path, limit=30)

# Check database existence
if not os.path.exists(db_path):
    st.title("💼 EdgeDash | Career Intelligence")
    st.warning(f"Database file not found at '{db_path}'. Run a cycle first to initialize telemetry.")
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar Navigation & Profile Settings
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown(
        """
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 15px;">
            <div style="background:#0a66c2; color:white; font-weight:800; font-size:1.1em; width:32px; height:32px; border-radius:6px; display:flex; align-items:center; justify-content:center;">in</div>
            <div>
                <div style="font-weight:700; color:#f8fafc; font-size:1.05em;">EdgeDash Career</div>
                <div style="font-size:0.75em; color:#94a3b8;">Intelligence Engine</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    
    if st.button("🔄 Force Reload Data", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    st.markdown("##### 👤 Target Candidate Profile")
    st.write(f"🎯 **Role:** `{config.target_role}`")
    st.write(f"📍 **Location:** `{config.target_city}`")
    st.write(f"⏳ **Experience:** `{config.experience_years} Years`")
    st.write(f"⚡ **LLM Model:** `{config.llm_model}`")
    st.write(f"🛡️ **Fit Threshold:** `{config.min_fit_score}%`")

    st.markdown("---")
    st.markdown("##### 🔑 My Core Skills")
    skills_html = " ".join([f'<span style="background: #1e293b; color: #38bdf8; padding: 2px 8px; border-radius: 12px; font-size: 0.75em; display: inline-block; margin: 2px; border: 1px solid #334155;">{html.escape(s)}</span>' for s in config.my_skills[:12]])
    st.markdown(skills_html, unsafe_allow_html=True)

# Fetch data snapshots
total_listings, total_scored = fetch_counts(db_path)
last_pass_time = fetch_last_passing_time(db_path)
newest_verifier = fetch_newest_verifier(db_path)
activity_log = fetch_activity_log(db_path)

if not activity_log:
    st.title("💼 EdgeDash Activity Dashboard")
    st.info("No cycles logged yet. Run the pipeline to populate.")
    st.stop()

# Helper: Timestamp formatting
def format_ts(ts_str):
    if not ts_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y • %H:%M UTC")
    except Exception:
        return ts_str[:19].replace("T", " ")

# Verdict evaluation
newest_is_failed = False
newest_verdict = "Unknown"
if newest_verifier:
    notes = newest_verifier.get("notes") or ""
    if "pass" in notes.lower() or newest_verifier.get("status") == "ok":
        newest_verdict = "Verified Pass"
    else:
        newest_verdict = "Needs Attention"
        newest_is_failed = True
else:
    latest_orch = activity_log[0]["orchestrator"]
    outcome = latest_orch.get("status")
    if outcome == "partial":
        newest_verdict = "Degraded"
        newest_is_failed = True
    elif outcome == "nothing_to_do":
        newest_verdict = "Up to Date"
    elif outcome == "complete":
        newest_verdict = "Verified Pass"

# ---------------------------------------------------------------------------
# Top Header & LinkedIn Profile Card
# ---------------------------------------------------------------------------
st.markdown(
    f"""
    <div class="linkedin-navbar">
        <div class="linkedin-logo">
            <div class="logo-badge">in</div>
            <div>
                <div class="navbar-title">EdgeDash | Professional Job Matching & Career Hub</div>
                <div class="navbar-subtitle">Automated candidate matching, skill gap valuation, and verified pipeline insights</div>
            </div>
        </div>
        <div style="display: flex; align-items: center; gap: 8px;">
            <span style="background: rgba(16, 185, 129, 0.15); color: #34d399; padding: 5px 12px; border-radius: 20px; font-size: 0.8em; font-weight: 600; border: 1px solid rgba(16, 185, 129, 0.3);">
                ● Live Intelligence Active
            </span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Warning banner if newest cycle failed (Constraint 1)
if newest_is_failed:
    if last_pass_time:
        st.error(
            f"⚠️ **Notice**: The newest pipeline run completed with degraded status. "
            f"Matches and gaps below are isolated to the last fully verified snapshot on **{format_ts(last_pass_time)}**."
        )
    else:
        st.error(
            "⚠️ **Notice**: The newest cycle failed or completed with degraded status. "
            "No verified cycle data is available yet."
        )

# LinkedIn Analytics & Overview Metrics
m_col1, m_col2, m_col3, m_col4 = st.columns(4)
with m_col1:
    st.metric("Total Market Openings", total_listings, help="Total job listings ingested across monitored platforms.")
with m_col2:
    st.metric("Profile Matches Scored", total_scored, help="Listings analyzed and scored against your profile.")
with m_col3:
    st.metric("Last Verified Snapshot", format_ts(last_pass_time) if last_pass_time else "None yet", help="Timestamp of the last fully verified cycle.")
with m_col4:
    st.metric("Pipeline Health", newest_verdict, help="Status of the automated verification engine.")

st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Navigation Tabs
# ---------------------------------------------------------------------------
tab_ask, tab_listings, tab_gaps, tab_activity = st.tabs([
    "💬 AI Career Copilot",
    "🔥 LinkedIn Job Matches", 
    "⚠️ Skill Gap Insights",
    "🕵️ Pipeline Activity Log",
])

# ---------------------------------------------------------------------------
# TAB 0: AI CAREER COPILOT (Two-Call Pipeline per Rules 42-45)
# ---------------------------------------------------------------------------
with tab_ask:
    st.markdown(
        """
        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">
            <div style="background: rgba(10, 102, 194, 0.15); color: #38bdf8; border-radius: 8px; width: 32px; height: 32px; display: flex; align-items: center; justify-content: center; font-size: 1.1em;">✨</div>
            <h3 style="margin: 0; font-size: 1.3em;">Ask Your Career Data</h3>
        </div>
        <p style="color: #94a3b8; font-size: 0.95em; margin-bottom: 16px;">
            Inquire directly about recent company hiring volumes, top candidate fit scores, skill demand, and market blockers.
            All responses are synthesized exclusively from verified database records with zero extrapolation (Rules 42–45).
        </p>
        """,
        unsafe_allow_html=True,
    )

    # 3 Example Question Prompt Chips
    st.markdown("##### 💡 Suggested Questions")
    b_col1, b_col2, b_col3 = st.columns(3)

    ex1 = "Which companies are hiring recently?"
    ex2 = "What are my top skill gaps?"
    ex3 = "How in-demand is Python?"

    if "current_question" not in st.session_state:
        st.session_state["current_question"] = ""

    with b_col1:
        if st.button("🏢 Hiring Companies", use_container_width=True):
            st.session_state["current_question"] = ex1
    with b_col2:
        if st.button("⚠️ Top Skill Gaps", use_container_width=True):
            st.session_state["current_question"] = ex2
    with b_col3:
        if st.button("📊 Python Market Demand", use_container_width=True):
            st.session_state["current_question"] = ex3

    # Natural Language Form
    with st.form("ask_data_form", clear_on_submit=False):
        user_input = st.text_input(
            "Enter your question for Career Copilot:",
            value=st.session_state.get("current_question", ""),
            placeholder="e.g. Which companies posted jobs in the last 7 days? or What are my top skill gaps?",
        )
        submitted = st.form_submit_button("🔍 Ask Career Copilot", use_container_width=True)

    active_q = user_input if submitted else st.session_state.get("current_question", "")

    if active_q:
        with st.spinner("Analyzing verified career records…"):
            try:
                from edgedash.query import ask
                answer = ask(active_q, config=config, db_path=db_path)

                badge_color = "#38bdf8" if answer.tool_used else "#f87171"
                badge_bg = "rgba(10, 102, 194, 0.2)" if answer.tool_used else "rgba(239, 68, 68, 0.15)"
                badge_text = f"Tool: {answer.tool_used}" if answer.tool_used else "Unanswerable with available tools"

                st.markdown(
                    f"""
                    <div style="background-color: #111827; border: 1px solid #1f2937; border-left: 4px solid {badge_color}; border-radius: 14px; padding: 22px; margin: 18px 0; box-shadow: 0 10px 25px -3px rgba(0, 0, 0, 0.35);">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <span style="background: #0a66c2; color: #ffffff; width: 22px; height: 22px; border-radius: 50%; font-size: 0.75em; display: inline-flex; align-items: center; justify-content: center; font-weight: 700;">AI</span>
                                <span style="font-weight: 700; color: #f8fafc; font-size: 0.95em;">Career Copilot Synthesis</span>
                            </div>
                            <span style="background: {badge_bg}; color: {badge_color}; padding: 4px 12px; border-radius: 20px; font-size: 0.8em; font-weight: 600; border: 1px solid rgba(255, 255, 255, 0.08);">{html.escape(badge_text)}</span>
                        </div>
                        <div style="font-size: 1.02em; color: #e2e8f0; line-height: 1.65; white-space: pre-wrap;">{html.escape(answer.text)}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                # Render data rows underneath in a table per Rule 44
                if answer.rows:
                    st.markdown("##### 📋 Verified Source Records (Rule 44)")
                    import pandas as pd
                    df = pd.DataFrame(answer.rows)
                    st.dataframe(df, use_container_width=True, hide_index=True)
                elif answer.tool_used:
                    st.info("No records match the requested parameters in the database.")
            except Exception as exc:
                st.error(f"Error executing query: {exc}")

# ---------------------------------------------------------------------------
# TAB 1: SCORED JOB MATCHES (LinkedIn Job Post Card Experience)
# ---------------------------------------------------------------------------
with tab_listings:
    st.markdown(
        """
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px;">
            <div>
                <h3 style="margin: 0; font-size: 1.3em;">🎯 Recommended Job Matches</h3>
                <p style="color: #94a3b8; font-size: 0.9em; margin: 0;">Ranked by compatibility with your experience, role preferences, and verified skills.</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    verified_time = last_pass_time
    raw_listings = fetch_verified_listings(db_path, verified_time)

    if not raw_listings:
        st.info("No scored listings available in the latest verified snapshot.")
    else:
        f_col1, f_col2 = st.columns([2, 1])
        with f_col1:
            search_query = st.text_input("🔍 Filter matches by title, company, or tech stack:", "")
        with f_col2:
            min_score_filter = st.slider("🎯 Minimum Match Score:", 0, 100, int(config.min_fit_score))

        filtered_listings = []
        for l in raw_listings:
            score = l.get("fit_score", 0) or 0
            title = l.get("title", "") or ""
            company = l.get("company", "") or ""
            description = l.get("description", "") or ""
            reason = l.get("fit_reason", "") or ""

            if score >= min_score_filter:
                match_search = (
                    search_query.lower() in title.lower() or
                    search_query.lower() in company.lower() or
                    search_query.lower() in description.lower() or
                    search_query.lower() in reason.lower()
                )
                if match_search:
                    filtered_listings.append(l)

        st.markdown(f"<div style='color:#94a3b8; font-size:0.9em; margin-bottom:14px;'>Showing <strong>{len(filtered_listings)}</strong> curated matches from {len(raw_listings)} verified listings.</div>", unsafe_allow_html=True)

        # Render LinkedIn-style job cards
        for idx, l in enumerate(filtered_listings[:25]):
            score = l.get("fit_score", 0) or 0
            title = l.get("title", "Job Opening")
            company = l.get("company", "Hiring Company")
            location = l.get("location", "Bengaluru (Hybrid/Remote)")
            url = l.get("url", "#")
            reason = l.get("fit_reason", "Candidate profile matches essential technical prerequisites.")
            desc = l.get("description", "Full job specification available on source listing.")

            # Initial avatar letters
            company_initials = "".join([w[0].upper() for w in company.split()[:2]]) if company else "CO"

            # Badge Styling
            if score >= 80:
                badge_bg = "rgba(16, 185, 129, 0.15)"
                badge_color = "#34d399"
                badge_border = "rgba(16, 185, 129, 0.4)"
                fit_label = "🔥 Strong Match"
            elif score >= 60:
                badge_bg = "rgba(245, 158, 11, 0.15)"
                badge_color = "#fbbf24"
                badge_border = "rgba(245, 158, 11, 0.4)"
                fit_label = "⚡ Good Match"
            else:
                badge_bg = "rgba(239, 68, 68, 0.15)"
                badge_color = "#f87171"
                badge_border = "rgba(239, 68, 68, 0.4)"
                fit_label = "⚠️ Moderate Fit"

            st.markdown(
                f"""
                <div class="job-post-card">
                    <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; margin-bottom: 12px;">
                        <div style="display: flex; gap: 14px; align-items: center;">
                            <div class="company-badge-avatar">{html.escape(company_initials)}</div>
                            <div>
                                <h4 style="margin: 0; font-size: 1.15em; color: #f8fafc;">{html.escape(title)}</h4>
                                <div style="color: #94a3b8; font-size: 0.88em; margin-top: 2px;">
                                    <strong style="color: #cbd5e1;">{html.escape(company)}</strong> &nbsp;•&nbsp; 📍 {html.escape(location)} &nbsp;•&nbsp; <span style="color: #38bdf8;">Verified Opening</span>
                                </div>
                            </div>
                        </div>
                        <div style="text-align: right; flex-shrink: 0;">
                            <span class="fit-score-badge" style="background: {badge_bg}; color: {badge_color}; border: 1px solid {badge_border};">
                                {fit_label} &nbsp;<strong>{score}%</strong>
                            </span>
                        </div>
                    </div>
                    
                    <div class="insight-quote-box">
                        <strong style="color: #38bdf8;">💡 Why you're a fit:</strong> {html.escape(reason)}
                    </div>
                    
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 14px; padding-top: 10px; border-top: 1px solid #1e293b;">
                        <span style="font-size: 0.82em; color: #64748b;">Ingested & Scored by EdgeDash Intelligence Engine</span>
                        <a href="{html.escape(url)}" target="_blank" class="apply-btn">
                            Apply on Source ↗
                        </a>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            with st.expander(f"📄 View complete description for {title}"):
                st.write(desc)

# ---------------------------------------------------------------------------
# TAB 2: SKILL GAPS ANALYSIS (LinkedIn Skill Assessments Style)
# ---------------------------------------------------------------------------
with tab_gaps:
    st.markdown(
        """
        <div style="margin-bottom: 14px;">
            <h3 style="margin: 0; font-size: 1.3em;">⚠️ High-Impact Skill Gaps</h3>
            <p style="color: #94a3b8; font-size: 0.9em; margin: 0;">Targeted skills to learn ranked by blocked job listings and opportunity costs in your market.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    raw_gaps = fetch_verified_gaps(db_path, verified_time)

    if not raw_gaps:
        st.info("No skill gaps detected in the latest verified snapshot.")
    else:
        gap_search = st.text_input("🔍 Filter skill gaps by name:", "")

        filtered_gaps = []
        for g in raw_gaps:
            skill = g.get("skill", "")
            if gap_search.lower() in skill.lower():
                filtered_gaps.append(g)

        if not filtered_gaps:
            st.warning("No skills match your filter criteria.")
        else:
            max_blocked = max(g.get("listings_blocked", 1) for g in filtered_gaps) if filtered_gaps else 1

            for index, g in enumerate(filtered_gaps[:25], 1):
                skill = g.get("skill", "Unknown")
                blocked = g.get("listings_blocked", 0)
                opp_cost = g.get("opportunity_cost", 0.0) or 0.0
                mean_score = g.get("mean_score", 0.0) or 0.0
                top_score = g.get("top_score", 0) or 0

                pct = int((blocked / max_blocked) * 100.0)

                st.markdown(
                    f"""
                    <div style="background: #111827; padding: 18px 22px; border-radius: 14px; border: 1px solid #1e293b; margin-bottom: 12px; box-shadow: 0 4px 10px rgba(0,0,0,0.2);">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                            <div style="display: flex; align-items: center; gap: 10px;">
                                <span style="background: #1e293b; color: #94a3b8; width: 26px; height: 26px; border-radius: 50%; font-size: 0.8em; display: inline-flex; align-items: center; justify-content: center; font-weight: 700; border: 1px solid #334155;">#{index}</span>
                                <span style="font-family: 'Outfit', sans-serif; font-weight: 700; font-size: 1.12em; color: #f8fafc;">{html.escape(skill)}</span>
                            </div>
                            <span style="color: #38bdf8; font-weight: 600; font-size: 0.9em; background: rgba(10, 102, 194, 0.15); padding: 3px 12px; border-radius: 14px; border: 1px solid rgba(10, 102, 194, 0.3);">
                                🔒 {blocked} Listing(s) Blocked
                            </span>
                        </div>
                        <div style="width: 100%; background: #0f172a; height: 8px; border-radius: 6px; overflow: hidden; margin: 10px 0; border: 1px solid #1e293b;">
                            <div style="width: {pct}%; background: linear-gradient(90deg, #0a66c2 0%, #38bdf8 100%); height: 100%; border-radius: 6px;"></div>
                        </div>
                        <div style="display: flex; justify-content: space-between; font-size: 0.85em; color: #94a3b8;">
                            <span>📈 Opportunity Cost Score: <strong style="color: #38bdf8;">{opp_cost:.2f}</strong></span>
                            <span>🎯 Average Match Score: <strong style="color: #f8fafc;">{mean_score:.1f}%</strong> &nbsp;•&nbsp; Peak Score: <strong style="color: #34d399;">{top_score}%</strong></span>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

# ---------------------------------------------------------------------------
# TAB 3: PIPELINE ACTIVITY LOG (Enterprise Telemetry Table)
# ---------------------------------------------------------------------------
with tab_activity:
    st.markdown(
        """
        <div style="margin-bottom: 14px;">
            <h3 style="margin: 0; font-size: 1.3em;">🕵️ Enterprise Pipeline Telemetry</h3>
            <p style="color: #94a3b8; font-size: 0.9em; margin: 0;">Audit trail of autonomous orchestrator runs, agent verifications, and execution metrics.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    log_rows = []
    for cycle in activity_log:
        orch = cycle["orchestrator"]
        ver = cycle["verifier"]

        started_at = orch.get("started_at")
        finished_at = orch.get("finished_at")
        duration_str = "N/A"
        if started_at and finished_at:
            try:
                s_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                f_dt = datetime.fromisoformat(finished_at.replace("Z", "+00:00"))
                duration_str = f"{(f_dt - s_dt).total_seconds():.1f}s"
            except Exception:
                pass

        orch_notes = orch.get("notes") or ""
        ran_agents = ""
        skipped_agents = ""
        for part in orch_notes.split():
            if part.startswith("ran="):
                ran_agents = part.split("=")[1]
            elif part.startswith("skipped="):
                skipped_agents = part.split("=")[1]
                if skipped_agents.isdigit():
                    skipped_agents = f"{skipped_agents} skipped"

        verdict = "Unknown"
        failed_check = "None"

        if ver:
            ver_notes = ver.get("notes") or ""
            if "pass" in ver_notes.lower():
                verdict = '<span style="background: rgba(16, 185, 129, 0.15); color: #34d399; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 0.82em; border: 1px solid rgba(16, 185, 129, 0.3);">🟢 PASS</span>'
            else:
                verdict = '<span style="background: rgba(239, 68, 68, 0.15); color: #f87171; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 0.82em; border: 1px solid rgba(239, 68, 68, 0.3);">🔴 FAIL</span>'
                failed_check = ver_notes
        else:
            orch_status = orch.get("status")
            if orch_status == "nothing_to_do":
                verdict = '<span style="background: rgba(16, 185, 129, 0.15); color: #34d399; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 0.82em; border: 1px solid rgba(16, 185, 129, 0.3);">🟢 PASS (NO OP)</span>'
            elif orch_status == "partial":
                verdict = '<span style="background: rgba(245, 158, 11, 0.15); color: #fbbf24; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 0.82em; border: 1px solid rgba(245, 158, 11, 0.3);">🟡 DEGRADED</span>'
                failed_check = orch_notes
            elif orch_status == "complete":
                verdict = '<span style="background: rgba(16, 185, 129, 0.15); color: #34d399; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 0.82em; border: 1px solid rgba(16, 185, 129, 0.3);">🟢 PASS</span>'

        log_rows.append({
            "Verdict": verdict,
            "Timestamp": format_ts(started_at),
            "Agents Run": ran_agents or "None",
            "Skipped Details": skipped_agents or "None",
            "Verification Failure / Details": failed_check,
            "Duration": duration_str,
        })

    # Render styled HTML table for activity log
    table_rows_html = ""
    for r in log_rows:
        table_rows_html += f"""
        <tr style="border-bottom: 1px solid #1e293b; height: 50px;">
            <td style="padding: 12px 16px;">{r['Verdict']}</td>
            <td style="padding: 12px 16px; color: #f8fafc; font-weight: 500; font-size: 0.9em;">{r['Timestamp']}</td>
            <td style="padding: 12px 16px; color: #38bdf8;"><code>{html.escape(r['Agents Run'])}</code></td>
            <td style="padding: 12px 16px; color: #94a3b8; font-size: 0.9em;">{html.escape(r['Skipped Details'])}</td>
            <td style="padding: 12px 16px; color: #94a3b8; font-size: 0.88em; max-width: 320px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="{html.escape(r['Verification Failure / Details'])}">{html.escape(r['Verification Failure / Details'])}</td>
            <td style="padding: 12px 16px; color: #38bdf8; font-weight: 600; font-size: 0.9em;">{r['Duration']}</td>
        </tr>
        """

    st.markdown(
        f"""
        <div style="overflow-x: auto; border: 1px solid #1e293b; border-radius: 14px; background-color: #111827; box-shadow: 0 10px 25px -3px rgba(0, 0, 0, 0.3); margin-top: 10px;">
            <table style="width: 100%; border-collapse: collapse; text-align: left; font-family: 'Inter', sans-serif;">
                <thead>
                    <tr style="border-bottom: 1px solid #1e293b; background-color: #0f172a; height: 46px;">
                        <th style="padding: 12px 16px; color: #94a3b8; font-weight: 600; font-size: 0.82em; text-transform: uppercase;">Verdict</th>
                        <th style="padding: 12px 16px; color: #94a3b8; font-weight: 600; font-size: 0.82em; text-transform: uppercase;">Timestamp</th>
                        <th style="padding: 12px 16px; color: #94a3b8; font-weight: 600; font-size: 0.82em; text-transform: uppercase;">Agents Run</th>
                        <th style="padding: 12px 16px; color: #94a3b8; font-weight: 600; font-size: 0.82em; text-transform: uppercase;">Skipped</th>
                        <th style="padding: 12px 16px; color: #94a3b8; font-weight: 600; font-size: 0.82em; text-transform: uppercase;">Failure Details</th>
                        <th style="padding: 12px 16px; color: #94a3b8; font-weight: 600; font-size: 0.82em; text-transform: uppercase;">Duration</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows_html}
                </tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True,
    )
