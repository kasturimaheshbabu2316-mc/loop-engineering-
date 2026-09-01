"""EdgeDash Career Intelligence Dashboard.

A premium, interactive, and beautifully designed Streamlit dashboard for viewing
pipeline cycles, matched listings, and skill gaps. Strictly read-only.
"""

from datetime import datetime, timezone
import os
import streamlit as st

from edgedash.config import load_config
from edgedash import storage

# Page Config
st.set_page_config(
    page_title="EdgeDash Career Intelligence Portal",
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

# Import Inter and Outfit fonts for premium typography
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Outfit:wght@500;600;700;800&display=swap');

    /* Global Overrides */
    .stApp {
        background-color: #0b0f19;
        color: #e2e8f0;
        font-family: 'Inter', sans-serif;
    }
    
    /* Headers typography */
    h1, h2, h3, h4, h5, h6 {
        font-family: 'Outfit', sans-serif !important;
        font-weight: 700 !important;
        color: #f8fafc !important;
    }

    /* Streamlit metrics card overrides */
    div[data-testid="metric-container"] {
        background-color: #111827;
        border: 1px solid #1f2937;
        padding: 20px 24px;
        border-radius: 16px;
        box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.4);
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }
    div[data-testid="metric-container"]:hover {
        transform: translateY(-4px);
        border-color: #2563eb;
        box-shadow: 0 20px 30px -10px rgba(37, 99, 235, 0.3);
    }
    div[data-testid="stMetricLabel"] {
        color: #9ca3af !important;
        font-size: 0.9em !important;
        font-weight: 600 !important;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }
    div[data-testid="stMetricValue"] {
        color: #f9fafb !important;
        font-size: 2em !important;
        font-weight: 700 !important;
    }
    
    /* Sidebar styling */
    div[data-testid="stSidebar"] {
        background-color: #0d1222;
        border-right: 1px solid #1e293b;
    }
    
    /* Warning/Info boxes custom overrides */
    .stAlert {
        border-radius: 12px !important;
        background-color: #1e1b4b !important;
        border: 1px solid #312e81 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# Caching wrappers
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
    st.title("💼 EdgeDash Career Intelligence")
    st.warning(f"Database file not found at '{db_path}'. Run a cycle first to create it.")
    st.stop()

# Force Reload Button in the Sidebar (Constraint)
with st.sidebar:
    st.image("https://img.icons8.com/clouds/200/000000/work.png", width=110)
    st.markdown("### EdgeDash Control Room")
    st.info("Continuous pipeline verifications and matches telemetry.")
    
    if st.button("🔄 Force Reload Data", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
        
    st.markdown("---")
    st.markdown("**Profile Settings**")
    st.write(f"🔍 Title: `{config.target_role}`")
    st.write(f"📍 City: `{config.target_city}`")

# Fetch data
total_listings, total_scored = fetch_counts(db_path)
last_pass_time = fetch_last_passing_time(db_path)
newest_verifier = fetch_newest_verifier(db_path)
activity_log = fetch_activity_log(db_path)

if not activity_log:
    st.title("💼 EdgeDash Activity Dashboard")
    st.info("No cycles logged yet. Run the pipeline to populate.")
    st.stop()

# Formatting helper
def format_ts(ts_str):
    if not ts_str:
        return "N/A"
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return ts_str[:19].replace("T", " ")

# Verdict evaluation
newest_is_failed = False
newest_verdict = "Unknown"
if newest_verifier:
    notes = newest_verifier.get("notes") or ""
    if "pass" in notes.lower() or newest_verifier.get("status") == "ok":
        newest_verdict = "Pass"
    else:
        newest_verdict = "Fail"
        newest_is_failed = True
else:
    latest_orch = activity_log[0]["orchestrator"]
    outcome = latest_orch.get("status")
    if outcome == "partial":
        newest_verdict = "Degraded"
        newest_is_failed = True
    elif outcome == "nothing_to_do":
        newest_verdict = "Nothing to Do"
    elif outcome == "complete":
        newest_verdict = "Complete"

# Title banner
st.title("💼 EdgeDash Career Intelligence Portal")

# Warning banner if the newest cycle failed (Constraint 1)
if newest_is_failed:
    if last_pass_time:
        st.error(
            f"⚠️ **Warning**: The newest cycle failed or completed with degraded status. "
            f"Matches and skill gaps shown below are loaded from the last verified cycle on **{format_ts(last_pass_time)}**."
        )
    else:
        st.error(
            "⚠️ **Warning**: The newest cycle failed or completed with degraded status. "
            "No verified cycle data is available yet."
        )

# Metrics Grid
m_col1, m_col2, m_col3, m_col4 = st.columns(4)
with m_col1:
    st.metric("Total Listings", total_listings)
with m_col2:
    st.metric("Scored Matches", total_scored)
with m_col3:
    st.metric("Last Verified Cycle", format_ts(last_pass_time) if last_pass_time else "None yet")
with m_col4:
    st.metric("Verdict Status", newest_verdict)

st.markdown("---")

# Dynamic Tabs
tab_ask, tab_listings, tab_gaps, tab_activity = st.tabs([
    "💬 Ask Your Data",
    "🔥 Scored Job Matches", 
    "⚠️ Skill Gaps Analysis",
    "🕵️ Agent Activity Log",
])

# ---------------------------------------------------------------------------
# TAB 0: ASK YOUR DATA (Two-call pipeline query interface per Rules 42-45)
# ---------------------------------------------------------------------------
with tab_ask:
    st.header("💬 Ask Your Career Data")
    st.markdown(
        "Ask questions about hiring trends, match quality, skill gaps, and market demand. "
        "Answers are generated strictly from verified database records (Rules 42–45)."
    )

    # 3 Example Question Buttons (Requirement 6)
    st.markdown("##### 💡 Suggested Questions")
    b_col1, b_col2, b_col3 = st.columns(3)

    ex1 = "Which companies are hiring recently?"
    ex2 = "What are my top skill gaps?"
    ex3 = "How in-demand is Python?"

    if "current_question" not in st.session_state:
        st.session_state["current_question"] = ""

    with b_col1:
        if st.button("🏢 Companies Hiring", use_container_width=True):
            st.session_state["current_question"] = ex1
    with b_col2:
        if st.button("⚠️ Top Skill Gaps", use_container_width=True):
            st.session_state["current_question"] = ex2
    with b_col3:
        if st.button("📊 Python Demand", use_container_width=True):
            st.session_state["current_question"] = ex3

    # Input form for natural language questions
    with st.form("ask_data_form", clear_on_submit=False):
        user_input = st.text_input(
            "Enter your question:",
            value=st.session_state.get("current_question", ""),
            placeholder="e.g. Which companies posted jobs in the last 7 days? or What are my top skill gaps?",
        )
        submitted = st.form_submit_button("🔍 Ask Pipeline", use_container_width=True)

    active_q = user_input if submitted else st.session_state.get("current_question", "")

    if active_q:
        with st.spinner("Analyzing verified career data…"):
            try:
                from edgedash.query import ask
                answer = ask(active_q, config=config, db_path=db_path)

                badge_color = "#3b82f6" if answer.tool_used else "#ef4444"
                badge_text = f"Tool: {answer.tool_used}" if answer.tool_used else "Unanswerable with current tools"

                st.markdown(
                    f"""
                    <div style="background-color: #111827; border: 1px solid #1f2937; border-left: 4px solid {badge_color}; border-radius: 12px; padding: 20px; margin: 20px 0; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3);">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                            <span style="font-weight: 700; color: #93c5fd; font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.05em;">AI Synthesis</span>
                            <span style="background: rgba(59, 130, 246, 0.15); color: #60a5fa; padding: 3px 10px; border-radius: 20px; font-size: 0.8em; font-weight: 600; border: 1px solid rgba(59, 130, 246, 0.3);">{badge_text}</span>
                        </div>
                        <div style="font-size: 1.05em; color: #f8fafc; line-height: 1.6; white-space: pre-wrap;">{answer.text}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                # Render data rows underneath in a table per Rule 44
                if answer.rows:
                    st.markdown("##### 📋 Retrieved Data Rows (Rule 44)")
                    import pandas as pd
                    df = pd.DataFrame(answer.rows)
                    st.dataframe(df, use_container_width=True, hide_index=True)
                elif answer.tool_used:
                    st.info("No matching rows found in the database for the given filters.")
            except Exception as exc:
                st.error(f"Error executing query: {exc}")


# ---------------------------------------------------------------------------
# TAB 1: SCORED JOB MATCHES (Premium HTML Cards with sliders/search)
# ---------------------------------------------------------------------------
with tab_listings:
    st.header("🔥 Scored Job Matches")
    st.markdown("Filter and inspect matched roles from the last verified pipeline run.")
    
    verified_time = last_pass_time
    raw_listings = fetch_verified_listings(db_path, verified_time)
    
    if not raw_listings:
        st.info("No scored listings found for the last verified cycle.")
    else:
        f_col1, f_col2 = st.columns([2, 1])
        with f_col1:
            search_query = st.text_input("🔍 Search listings by title, company, or keywords:", "")
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
                    
        st.write(f"Showing **{len(filtered_listings)}** matches out of {len(raw_listings)} verified listings.")
        
        # Render gorgeous HTML cards for each listing
        for idx, l in enumerate(filtered_listings[:25]):
            score = l.get("fit_score", 0)
            title = l.get("title", "Unknown Role")
            company = l.get("company", "Unknown Company")
            location = l.get("location", "Unknown Location")
            url = l.get("url", "#")
            reason = l.get("fit_reason", "No explanation available.")
            desc = l.get("description", "No description text available.")
            
            # Determine score badge styling based on value
            badge_color = "#10b981" if score >= 80 else ("#f59e0b" if score >= 60 else "#ef4444")
            badge_bg = "rgba(16, 185, 129, 0.15)" if score >= 80 else ("rgba(245, 158, 11, 0.15)" if score >= 60 else "rgba(239, 68, 68, 0.15)")
            
            st.markdown(
                f"""
                <div style="background: #111827; padding: 20px; border-radius: 16px; border: 1px solid #1f2937; margin-bottom: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
                        <h4 style="margin: 0; color: #f9fafb; font-weight: 600; font-size: 1.15em; font-family: 'Outfit', sans-serif;">{title}</h4>
                        <span style="background: {badge_bg}; color: {badge_color}; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 0.9em; border: 1px solid rgba(255,255,255,0.05);">{score}/100</span>
                    </div>
                    <div style="color: #9ca3af; font-size: 0.9em; margin-bottom: 12px; font-weight: 500;">
                        🏢 {company} &nbsp;•&nbsp; 📍 {location}
                    </div>
                    <div style="background: #1f2937; padding: 12px 16px; border-radius: 10px; color: #d1d5db; font-size: 0.95em; line-height: 1.4; border-left: 4px solid #2563eb; margin-bottom: 10px;">
                        <strong>Analysis Summary:</strong> {reason}
                    </div>
                    <div style="margin-top: 10px;">
                        <a href="{url}" target="_blank" style="color: #60a5fa; text-decoration: none; font-size: 0.9em; font-weight: 600;">Apply / View Listing →</a>
                    </div>
                </div>
                """, 
                unsafe_allow_html=True
            )
            with st.expander("📄 Click to expand full description details"):
                st.info(desc)

# ---------------------------------------------------------------------------
# TAB 2: SKILL GAPS ANALYSIS (Visual HTML Progress Indicators)
# ---------------------------------------------------------------------------
with tab_gaps:
    st.header("⚠️ Skill Gaps Analysis")
    st.markdown("Prioritize skills to learn based on frequency and opportunity costs computed from matched job openings.")
    
    raw_gaps = fetch_verified_gaps(db_path, verified_time)
    
    if not raw_gaps:
        st.info("No skill gaps found for the last verified cycle.")
    else:
        gap_search = st.text_input("🔍 Filter skills by keyword:", "")
        
        filtered_gaps = []
        for g in raw_gaps:
            skill = g.get("skill", "")
            if gap_search.lower() in skill.lower():
                filtered_gaps.append(g)
                
        if not filtered_gaps:
            st.warning("No skills match your filter query.")
        else:
            # Find the max listings blocked to normalize progress bar
            max_blocked = max(g.get("listings_blocked", 1) for g in filtered_gaps) if filtered_gaps else 1
            
            # Display visually premium progress bar grids
            for index, g in enumerate(filtered_gaps[:25], 1):
                skill = g.get("skill", "Unknown")
                blocked = g.get("listings_blocked", 0)
                opp_cost = g.get("opportunity_cost", 0.0) or 0.0
                mean_score = g.get("mean_score", 0.0) or 0.0
                top_score = g.get("top_score", 0) or 0
                
                pct = int((blocked / max_blocked) * 100.0)
                
                st.markdown(
                    f"""
                    <div style="background: #111827; padding: 16px 20px; border-radius: 14px; border: 1px solid #1f2937; margin-bottom: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);">
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                            <span style="font-family: 'Outfit', sans-serif; font-weight: 700; font-size: 1.1em; color: #f9fafb;">#{index} &nbsp;{skill}</span>
                            <span style="color: #3b82f6; font-weight: 600; font-size: 0.95em;">{blocked} Listing(s) Blocked</span>
                        </div>
                        <div style="width: 100%; background: #1f2937; height: 10px; border-radius: 5px; overflow: hidden; margin-bottom: 10px; border: 1px solid #374151;">
                            <div style="width: {pct}%; background: linear-gradient(90deg, #3b82f6 0%, #2563eb 100%); height: 100%; border-radius: 5px;"></div>
                        </div>
                        <div style="display: flex; justify-content: space-between; font-size: 0.85em; color: #9ca3af; font-weight: 500;">
                            <span>💰 Opportunity Cost: <strong style="color:#f3f4f6;">{opp_cost:.2f}</strong></span>
                            <span>📊 Mean Match Score: <strong style="color:#f3f4f6;">{mean_score:.1f}</strong> &nbsp;•&nbsp; Top Score: <strong style="color:#f3f4f6;">{top_score}</strong></span>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

# ---------------------------------------------------------------------------
# TAB 3: AGENT ACTIVITY LOG (Custom HTML logs)
# ---------------------------------------------------------------------------
with tab_activity:
    st.header("🕵️ Agent Activity Log")
    st.markdown("Track continuous integration runs, verdict logs, and verify durations.")
    
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
        
        # Define colored tag markup based on verdict
        if ver:
            ver_notes = ver.get("notes") or ""
            if "pass" in ver_notes.lower():
                verdict = '<span style="background: rgba(16, 185, 129, 0.15); color: #34d399; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 0.85em; border: 1px solid rgba(16, 185, 129, 0.3);">🟢 PASS</span>'
            else:
                verdict = '<span style="background: rgba(239, 68, 68, 0.15); color: #f87171; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 0.85em; border: 1px solid rgba(239, 68, 68, 0.3);">🔴 FAIL</span>'
                failed_check = ver_notes
        else:
            orch_status = orch.get("status")
            if orch_status == "nothing_to_do":
                verdict = '<span style="background: rgba(16, 185, 129, 0.15); color: #34d399; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 0.85em; border: 1px solid rgba(16, 185, 129, 0.3);">🟢 PASS (NO OP)</span>'
            elif orch_status == "partial":
                verdict = '<span style="background: rgba(245, 158, 11, 0.15); color: #fbbf24; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 0.85em; border: 1px solid rgba(245, 158, 11, 0.3);">🟡 DEGRADED</span>'
                failed_check = orch_notes
            elif orch_status == "complete":
                verdict = '<span style="background: rgba(16, 185, 129, 0.15); color: #34d399; padding: 4px 12px; border-radius: 20px; font-weight: 700; font-size: 0.85em; border: 1px solid rgba(16, 185, 129, 0.3);">🟢 PASS</span>'

        log_rows.append({
            "Verdict": verdict,
            "Timestamp": format_ts(started_at),
            "Agents Run": ran_agents or "None",
            "Skipped Details": skipped_agents or "None",
            "Verification Failure / Details": failed_check,
            "Duration": duration_str,
        })

    # Render a premium styled HTML table for activity log
    table_rows_html = ""
    for r in log_rows:
        table_rows_html += f"""
        <tr style="border-bottom: 1px solid #1f2937; height: 50px;">
            <td style="padding: 12px 16px;">{r['Verdict']}</td>
            <td style="padding: 12px 16px; color: #f3f4f6; font-weight: 500;">{r['Timestamp']}</td>
            <td style="padding: 12px 16px; color: #cbd5e1;"><code>{r['Agents Run']}</code></td>
            <td style="padding: 12px 16px; color: #9ca3af;">{r['Skipped Details']}</td>
            <td style="padding: 12px 16px; color: #9ca3af; font-size: 0.9em; max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;" title="{r['Verification Failure / Details']}">{r['Verification Failure / Details']}</td>
            <td style="padding: 12px 16px; color: #3b82f6; font-weight: 600;">{r['Duration']}</td>
        </tr>
        """
        
    st.markdown(
        f"""
        <div style="overflow-x: auto; border: 1px solid #1f2937; border-radius: 16px; background-color: #111827; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3); margin-top: 10px;">
            <table style="width: 100%; border-collapse: collapse; text-align: left; font-family: 'Inter', sans-serif;">
                <thead>
                    <tr style="border-bottom: 1px solid #1f2937; background-color: #0f172a; height: 45px;">
                        <th style="padding: 12px 16px; color: #9ca3af; font-weight: 600; font-size: 0.85em; text-transform: uppercase;">Verdict</th>
                        <th style="padding: 12px 16px; color: #9ca3af; font-weight: 600; font-size: 0.85em; text-transform: uppercase;">Timestamp</th>
                        <th style="padding: 12px 16px; color: #9ca3af; font-weight: 600; font-size: 0.85em; text-transform: uppercase;">Agents Run</th>
                        <th style="padding: 12px 16px; color: #9ca3af; font-weight: 600; font-size: 0.85em; text-transform: uppercase;">Skipped</th>
                        <th style="padding: 12px 16px; color: #9ca3af; font-weight: 600; font-size: 0.85em; text-transform: uppercase;">Failure Details</th>
                        <th style="padding: 12px 16px; color: #9ca3af; font-weight: 600; font-size: 0.85em; text-transform: uppercase;">Duration</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows_html}
                </tbody>
            </table>
        </div>
        """,
        unsafe_allow_html=True
    )
