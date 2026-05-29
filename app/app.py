import os
import time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from db import (
    _make_pool,
    delete_all_runs,
    fetch_runs_with_counts,
    init_schema,
    insert_run,
)
from robots import ROBOTS_PATH, load_robots

ALARM_PATH = Path(__file__).parent / "resources" / "done.mp3"

DEFAULT_DURATION_SECONDS = int(os.environ.get("DEFAULT_DURATION_SECONDS", "300"))

# Password gating the "erase all data" button. Set ERASE_PASSWORD in Railway.
# If unset, the erase control stays hidden so data can't be wiped by accident.
ERASE_PASSWORD = os.environ.get("ERASE_PASSWORD", "")

EVENT_LABELS = {
    "hit_wall": "🧱 Hit wall",
    "hit_robot": "🤖 Hit other robot",
    "stuck": "🛑 Stuck",
    "other": "❓ Other issue",
}

# Setup-phase widget keys. Streamlit deletes a widget's session_state entry
# the moment that widget stops being rendered, so once we leave the setup
# phase these are gone. We snapshot them into RUN_* keys on Start.
WIDGET_KEYS = ("robot_number", "algorithm", "duration")
RUN_KEYS = tuple(f"run_{k}" for k in WIDGET_KEYS)


@st.cache_data
def get_robots(_mtime: float) -> list[dict]:
    # _mtime is part of the cache key so edits to robots.ods invalidate
    # the cache without a process restart.
    return load_robots()


def robots_now() -> list[dict]:
    return get_robots(ROBOTS_PATH.stat().st_mtime)


@st.cache_resource
def get_pool():
    pool = _make_pool()
    init_schema(pool)
    return pool


@st.cache_resource
def load_alarm() -> bytes:
    return ALARM_PATH.read_bytes()


def reset_state():
    for key in (
        "phase",
        "start_ts",
        "elapsed_at_stop",
        "events",
        "submitted_run_id",
        *WIDGET_KEYS,
        *RUN_KEYS,
    ):
        st.session_state.pop(key, None)


def ensure_state():
    st.session_state.setdefault("page", "activity")
    st.session_state.setdefault("phase", "setup")
    st.session_state.setdefault("events", [])
    st.session_state.setdefault("algorithm", "taxis")
    st.session_state.setdefault("duration", DEFAULT_DURATION_SECONDS)


def render_setup():
    st.header("Set up a run")

    robots = robots_now()
    numbers = [r["number"] for r in robots]
    by_number = {r["number"]: r for r in robots}

    # Drop a stale selection (e.g. if the roster was edited between sessions).
    if st.session_state.get("robot_number") not in by_number:
        st.session_state.pop("robot_number", None)

    def _label(n: int) -> str:
        r = by_number[n]
        return f"#{r['number']:02d} — {r['name']} ({r['ears']})"

    st.selectbox(
        "Robot",
        options=numbers,
        key="robot_number",
        format_func=_label,
    )
    selected = by_number[st.session_state.robot_number]

    st.radio(
        "Algorithm",
        options=["taxis", "kinesis"],
        key="algorithm",
        captions=["min of 2 sensors, random turn", "compare L/R, turn away from closer"],
    )

    st.number_input(
        "Duration (seconds)",
        min_value=10,
        max_value=600,
        step=10,
        key="duration",
    )

    if st.button("▶ Start", type="primary", use_container_width=True):
        st.session_state.run_robot_name = selected["name"]
        st.session_state.run_algorithm = st.session_state.algorithm
        st.session_state.run_ears = selected["ears"]
        st.session_state.run_duration = st.session_state.duration
        st.session_state.run_robot_number = selected["number"]
        st.session_state.phase = "running"
        st.session_state.start_ts = time.time()
        st.session_state.events = []
        st.session_state.pop("elapsed_at_stop", None)
        st.rerun()

    st.divider()
    if st.button("📊 Dashboard", use_container_width=True):
        st.session_state.page = "dashboard"
        st.rerun()


def render_running():
    elapsed = time.time() - st.session_state.start_ts
    duration = st.session_state.run_duration
    remaining = max(0.0, duration - elapsed)

    if remaining <= 0:
        st.session_state.elapsed_at_stop = min(elapsed, float(duration))
        st.session_state.phase = "review"
        st.session_state.beep_pending = True
        st.rerun()

    st.markdown(
        "<div style='text-align:center;font-size:1.35rem;font-weight:700;"
        f"margin:0.25rem 0 0'>🤖 {st.session_state.run_robot_name}</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        "<div style='text-align:center;font-size:0.95rem;opacity:0.7;"
        f"margin:0 0 0.25rem'>{st.session_state.run_algorithm} · "
        f"{st.session_state.run_ears}</div>",
        unsafe_allow_html=True,
    )
    mins, secs = divmod(int(remaining), 60)
    st.markdown(
        f"<div style='text-align:center;font-size:clamp(1.75rem,10vw,3rem);"
        f"font-weight:600;line-height:1.1;margin:0'>{mins:02d}:{secs:02d}</div>",
        unsafe_allow_html=True,
    )
    st.progress(remaining / duration)

    counts = count_events(st.session_state.events)
    for ev_type, label in EVENT_LABELS.items():
        st.markdown(
            "<div style='text-align:center;font-weight:600;"
            f"margin:0.5rem 0 0.25rem'>{label}</div>",
            unsafe_allow_html=True,
        )
        minus_col, cnt_col, plus_col = st.columns([1, 1, 1])
        with minus_col:
            if st.button(
                "−",
                key=f"sub_{ev_type}",
                disabled=counts[ev_type] == 0,
                use_container_width=True,
            ):
                for i in range(len(st.session_state.events) - 1, -1, -1):
                    if st.session_state.events[i][0] == ev_type:
                        st.session_state.events.pop(i)
                        break
                st.rerun()
        cnt_col.markdown(
            "<div style='text-align:center;font-size:1.75rem;"
            "font-weight:700;line-height:3.25rem'>"
            f"{counts[ev_type]}</div>",
            unsafe_allow_html=True,
        )
        with plus_col:
            if st.button(
                "+",
                key=f"btn_{ev_type}",
                use_container_width=True,
            ):
                st.session_state.events.append((ev_type, elapsed))
                st.rerun()
        st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)

    st.divider()
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("⏭ End early", use_container_width=True):
            st.session_state.elapsed_at_stop = time.time() - st.session_state.start_ts
            st.session_state.phase = "review"
            st.rerun()
    with c2:
        if st.button("✖ Cancel run", use_container_width=True):
            reset_state()
            st.rerun()

    # auto-refresh roughly once per second so the countdown ticks
    time.sleep(1)
    st.rerun()


def render_review():
    st.header("Review and submit")
    if st.session_state.pop("beep_pending", False):
        st.audio(load_alarm(), format="audio/mp3", autoplay=True)
    counts = count_events(st.session_state.events)
    elapsed = st.session_state.get(
        "elapsed_at_stop", float(st.session_state.run_duration)
    )
    st.markdown(
        f"**Robot:** {st.session_state.run_robot_name}  \n"
        f"**Condition:** {st.session_state.run_algorithm} × {st.session_state.run_ears}  \n"
        f"**Duration set / ran:** {st.session_state.run_duration}s / {elapsed:.1f}s  \n"
        f"**Events logged:** {len(st.session_state.events)}"
    )

    cols = st.columns(len(EVENT_LABELS))
    for col, (ev_type, label) in zip(cols, EVENT_LABELS.items()):
        col.metric(label, counts[ev_type])

    c1, c2 = st.columns(2)
    with c1:
        if st.button("✅ Submit", type="primary", use_container_width=True):
            try:
                run_id = insert_run(
                    get_pool(),
                    robot_name=st.session_state.run_robot_name,
                    algorithm=st.session_state.run_algorithm,
                    ears=st.session_state.run_ears,
                    duration_seconds=st.session_state.run_duration,
                    elapsed_seconds=elapsed,
                    started_at=datetime.fromtimestamp(
                        st.session_state.start_ts, tz=timezone.utc
                    ),
                    events=st.session_state.events,
                )
            except Exception as exc:
                st.error(f"Submit failed: {exc}")
            else:
                st.session_state.submitted_run_id = run_id
                st.session_state.phase = "submitted"
                st.rerun()
    with c2:
        if st.button("🗑 Discard", use_container_width=True):
            reset_state()
            st.rerun()


def render_submitted():
    st.success(f"Submitted! Run #{st.session_state.submitted_run_id} saved.")
    if st.button("➕ New run", type="primary", use_container_width=True):
        # Pre-fill the setup widgets with values from the run just submitted.
        if "run_robot_number" in st.session_state:
            st.session_state.robot_number = st.session_state.run_robot_number
        st.session_state.algorithm = st.session_state.get("run_algorithm", "taxis")
        st.session_state.duration = st.session_state.get(
            "run_duration", DEFAULT_DURATION_SECONDS
        )
        for key in ("phase", "start_ts", "events", "submitted_run_id", *RUN_KEYS):
            st.session_state.pop(key, None)
        st.rerun()


def count_events(events):
    counts = {k: 0 for k in EVENT_LABELS}
    for ev_type, _ in events:
        counts[ev_type] = counts.get(ev_type, 0) + 1
    return counts


def scroll_to_top():
    # Streamlit keeps the previous scroll position when we switch pages via
    # session_state, so the dashboard would open scrolled down. This component
    # is injected at the very top of the dashboard, so scrolling the iframe
    # itself into view drags the parent page to the top — robust across
    # Streamlit versions and mobile browsers without guessing which element
    # actually scrolls. We also poke the likely scroll containers directly,
    # and retry a few times because mobile layout settles after first paint.
    components.html(
        """
        <script>
            const win = window.parent;
            const doc = win.document;
            const frame = window.frameElement;
            const toTop = () => {
                if (frame && frame.scrollIntoView) {
                    frame.scrollIntoView({block: "start"});
                }
                win.scrollTo(0, 0);
                const sel = 'section.stMain, [data-testid="stMain"],'
                    + ' [data-testid="stAppViewContainer"], .main, .appview-container';
                doc.querySelectorAll(sel).forEach((el) => { el.scrollTop = 0; });
                doc.documentElement.scrollTop = 0;
                doc.body.scrollTop = 0;
            };
            [0, 100, 300, 600].forEach((t) => setTimeout(toTop, t));
        </script>
        """,
        height=0,
    )


def render_dashboard():
    scroll_to_top()
    st.header("Dashboard")
    if st.button("← Back", use_container_width=False):
        st.session_state.page = "activity"
        st.rerun()

    if msg := st.session_state.pop("erase_msg", None):
        st.success(msg)

    try:
        rows = fetch_runs_with_counts(get_pool())
    except Exception as exc:
        st.error(f"Could not load runs: {exc}")
        return

    if not rows:
        st.info("No runs submitted yet.")
        return

    total_runs = len(rows)
    total_elapsed = sum(r["elapsed_seconds"] for r in rows)
    total_events = sum(r["total_events"] for r in rows)
    m1, m2, m3 = st.columns(3)
    m1.metric("Runs", total_runs)
    m2.metric("Total time", f"{total_elapsed / 60:.1f} min")
    m3.metric("Events", total_events)

    st.subheader("By condition")
    # Aggregate: mean events/sec per (algorithm, ears) per event type.
    buckets: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (r["algorithm"], r["ears"])
        b = buckets.setdefault(
            key,
            {"runs": 0, "elapsed": 0.0,
             "hit_wall": 0, "hit_robot": 0, "stuck": 0, "other": 0},
        )
        b["runs"] += 1
        b["elapsed"] += r["elapsed_seconds"]
        for ev in EVENT_LABELS:
            b[ev] += r[ev]

    agg_rows = []
    for (algo, ears), b in sorted(buckets.items()):
        elapsed = b["elapsed"] or 1.0  # avoid div by zero
        total = b["hit_wall"] + b["hit_robot"] + b["stuck"] + b["other"]
        agg_rows.append({
            "algorithm": algo,
            "ears": ears,
            "runs": b["runs"],
            "elapsed (s)": round(b["elapsed"], 1),
            "wall /s":  round(b["hit_wall"]  / elapsed, 4),
            "robot /s": round(b["hit_robot"] / elapsed, 4),
            "stuck /s": round(b["stuck"]     / elapsed, 4),
            "other /s": round(b["other"]     / elapsed, 4),
            "total /s": round(total / elapsed, 4),
            "events": total,
        })
    st.dataframe(agg_rows, use_container_width=True, hide_index=True)

    st.subheader("Runs")
    run_rows = []
    for r in rows:
        elapsed = r["elapsed_seconds"] or 1.0
        run_rows.append({
            "id": r["id"],
            "started": r["started_at"].strftime("%Y-%m-%d %H:%M"),
            "robot name": r["robot_name"],
            "algorithm": r["algorithm"],
            "ears": r["ears"],
            "set (s)": r["duration_seconds"],
            "ran (s)": round(r["elapsed_seconds"], 1),
            "wall": r["hit_wall"],
            "hit robot": r["hit_robot"],
            "stuck": r["stuck"],
            "other": r["other"],
            "hits/s": round(r["total_events"] / elapsed, 4),
        })
    st.dataframe(run_rows, use_container_width=True, hide_index=True)

    render_erase()


def render_erase():
    # Hidden entirely unless ERASE_PASSWORD is configured, so the destructive
    # control can't be reached on a deployment that hasn't opted in.
    if not ERASE_PASSWORD:
        return
    st.divider()
    with st.expander("⚠️ Erase all data"):
        st.warning("This permanently deletes every run and all logged events.")
        pw = st.text_input("Password", type="password", key="erase_pw")
        if st.button("🗑 Erase all data", type="primary", use_container_width=True):
            if pw == ERASE_PASSWORD:
                try:
                    n = delete_all_runs(get_pool())
                except Exception as exc:
                    st.error(f"Erase failed: {exc}")
                else:
                    st.session_state.pop("erase_pw", None)
                    st.session_state.erase_msg = f"Erased {n} run(s)."
                    st.rerun()
            else:
                st.error("Incorrect password.")


MOBILE_CSS = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
/* Tighten Streamlit's default vertical rhythm so a page fits on a phone
   screen without scrolling. Streamlit reserves ~6rem of top padding for the
   header bar and puts a 1rem gap between every element; both are wasteful on
   mobile. */
[data-testid="stMainBlockContainer"] {
    padding-top: 1.5rem; padding-bottom: 2rem;
}
[data-testid="stMain"] [data-testid="stVerticalBlock"] {
    gap: 0.5rem;
}
[data-testid="stHeading"] {margin-bottom: 0.25rem;}
h1 {font-size: 1.6rem; padding: 0;}
h2 {font-size: 1.25rem; padding-top: 0.25rem;}
hr {margin: 0.5rem 0;}
.stButton > button {min-height: 3.25rem; font-size: 1.05rem;}
/* Hide the "Press Enter to apply" hint under text inputs — confusing on
   mobile keyboards. Tapping outside the field (e.g. tapping Start) still
   commits the value. */
[data-testid="InputInstructions"] {display: none;}
/* Keep st.columns rows side-by-side on phones — Streamlit otherwise
   wraps them when each column gets too narrow, which breaks the
   [-] count [+] stepper. The min-width:0 lets columns actually
   shrink to fit; without it the flex item's intrinsic min-width
   pushes the rightmost button off-screen on narrow viewports. */
[data-testid="stHorizontalBlock"] {flex-wrap: nowrap !important;}
[data-testid="stHorizontalBlock"] > div {min-width: 0 !important;}
/* Hide the audio player UI — we use st.audio only for the end-of-run
   alarm and don't want a visible player on the review page. */
[data-testid="stAudio"] {display: none;}
/* Colour the algorithm options to match the robot's lights: taxis = green,
   kinesis = red. Options render in declaration order (see render_setup), so
   the 1st radio label is taxis and the 2nd is kinesis. Scoped to this widget
   via its st-key-<key> wrapper class so no other radios are affected. */
.st-key-algorithm [role="radiogroup"] label {
    border-radius: 0.5rem; padding: 0.4rem 0.6rem; margin-bottom: 0.25rem;
}
.st-key-algorithm [role="radiogroup"] label:nth-of-type(1) {
    background: rgba(46, 160, 67, 0.14);
}
.st-key-algorithm [role="radiogroup"] label:nth-of-type(1) p {
    color: #1b7a2f; font-weight: 700;
}
.st-key-algorithm [role="radiogroup"] label:nth-of-type(2) {
    background: rgba(214, 40, 40, 0.14);
}
.st-key-algorithm [role="radiogroup"] label:nth-of-type(2) p {
    color: #c62828; font-weight: 700;
}
</style>
"""


def main():
    st.set_page_config(page_title="BioSonar Activity", page_icon="🦇", layout="centered")
    st.markdown(MOBILE_CSS, unsafe_allow_html=True)
    ensure_state()

    if st.session_state.page == "dashboard":
        st.title("🦇 BioSonar Activity")
        render_dashboard()
        return

    phase = st.session_state.phase
    if phase != "running":
        st.title("🦇 BioSonar Activity")
    if phase == "setup":
        render_setup()
    elif phase == "running":
        render_running()
    elif phase == "review":
        render_review()
    elif phase == "submitted":
        render_submitted()


if __name__ == "__main__":
    main()
