# EdgeDash — Project Steering Rules

Apply these rules to every interaction in this project without exception.

## Project

**EdgeDash** is an autonomous AI career intelligence agent. It runs as a scheduled loop that:

- Fetches live job listings from external sources
- Scores listings for fit against a personal profile
- Surfaces skill gaps
- Verifies its own output
- Publishes results to a Streamlit dashboard

---

## Architecture

```
Trigger (scheduled)
  -> Orchestrator
       -> Fetcher       (sub-agent)
       -> Scorer        (sub-agent)
       -> GapAnalyzer   (sub-agent)
  -> Verifier
  -> Storage
  -> Dashboard (read-only)
```

**Do not deviate from this architecture without explaining the reason first.**

- The Orchestrator reads state and delegates work. It never fetches data or scores listings directly.
- Each sub-agent has exactly one goal and one stop condition.
- The Dashboard is strictly read-only; it never writes to storage.

---

## Hard Rules

1. **Python 3.11+. Standard library first.**
   Add a third-party dependency only when it genuinely saves real work. State the reason before adding it.

2. **All storage access goes through a single storage module.**
   No other module may import `sqlite3` directly. The storage module exposes a thin interface. In week 4 we will swap SQLite for hosted Postgres — that must be a one-file change.

3. **No hardcoded user-specific values.**
   Role, city, keywords, skills profile, and any other personal data live exclusively in config (e.g. `config.toml` or environment-sourced config object). Never inline them.

4. **No secrets in code.**
   API keys, tokens, and credentials are environment variables only, loaded in one place (e.g. a `settings.py` or `env.py` module). No other module reads `os.environ` directly.

5. **Every agent run writes a cycle log row.**
   The `cycle_log` table must record: agent name, start time, record count touched, pass/fail status, and any retry reason. This is non-negotiable for observability.

6. **Fail loudly.**
   No bare `except: pass` or silent swallowing of errors. If something goes wrong, raise or log with full context so it is visible.

7. **Type hints on every function signature.**
   Docstrings only where the intent is not obvious from the name alone.

8. **Keep files under ~150 lines.**
   Split into smaller modules before a file approaches that limit.

---

## Network & Sources

1. **Every external source lives behind a `Source` class with a uniform interface.**
   The Fetcher never contains source-specific parsing. Adding a source must never require editing the Fetcher.

2. **Every `Source` returns a list of normalised dicts with EXACTLY these keys:**
    `source`, `external_id`, `title`, `company`, `location`, `url`, `description`, `posted_at`, `raw`.
    Missing values are `None` — never empty string, never `"N/A"`.

3. **All network calls go through one shared helper.**
    That helper enforces a 10s timeout, 2 retry attempts with exponential backoff, and a `User-Agent` header.
    No bare `requests.get` anywhere else in the codebase.

4. **A source failing must never kill the cycle.**
    Catch per-source, log the failure to `cycle_log` with `status="failed"`, and continue to the next source.
    One dead job board must not stop the other sources.

5. **Secrets come from environment variables loaded via a `.env` file that is gitignored.**
    Never a literal key in code; never a key in `config.yaml`. If a required key is absent, that source
    skips itself with a clear log line — it does not crash the cycle.

6. **Respect the source.**
    Rate-limit to at most 1 request per second per source. Set a real `User-Agent`. Honour any documented
    page limits. No hammering.

---

## Intelligence & Scoring

 1. **All LLM calls go through one module, `edgedash/llm.py`, exposing one function.**
    The provider and model name come from config, never hardcoded. Rate limit to stay inside a free tier
    (default 1 request per second, max 15 per minute). No other file imports an LLM SDK.

 2. **NEVER ask a model for a final score, ranking, or numeric rating.**
    The model extracts structured facts only. All scoring arithmetic is deterministic Python in ONE function.
    The model never sees the scoring weights.

 3. **Every model response is validated against an explicit schema before use.**
    A response that fails validation is retried once, then logged as a failure for THAT listing only —
    it must not crash the cycle or stop the remaining listings. Never `json.loads` raw model text without
    a validation and repair path.

 4. **Scoring is idempotent.**
    Never re-score a listing that already has a score. Select only listings `WHERE score IS NULL`.
    Cache extraction results keyed on a hash of the job description so the same text is never sent to
    the model twice.

 5. **Every score carries a human-readable reason GENERATED FROM THE SCORE COMPONENTS by our code.**
    Never free text written by the model.

 6. **Log the score distribution (count, min, max, mean, spread) to `cycle_log` on every scoring run.**
    A run where all scores fall within 10 points is a suspect run and must be logged as such.

 7. **Cap listings scored per cycle at a configurable batch size (default 25).**
    So a cost or rate-limit blowup is structurally impossible.

---

## Style

- Small, testable functions over large monoliths.
- Plain, readable Python over clever Python.
- When asked to build one module, build that module only — do not scaffold the whole app.

---

## Deployment

 1. **Never rely on the local filesystem for anything that must survive a restart.**
    Hosting filesystems are ephemeral. All persistent state is in the hosted database.

 2. **Every secret comes from an environment variable read in one place.**
    No secret is ever committed, printed, logged, or shown in an error message or traceback.

 3. **The scheduled job and the dashboard are separate processes that share only the database.**
    The dashboard never runs a cycle; the scheduler never serves a page.

 4. **The deployed app must start and render even when the database is empty, unreachable, or mid-migration.**
    It shows a clear status message instead of a stack trace. A stranger must never see a traceback.

 5. **The scheduled job is idempotent and safe to run twice.**
    It must have a hard timeout and stay inside free-tier limits.
