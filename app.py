import os
import time
from datetime import datetime, timezone

import streamlit as st

from db import _make_pool, init_schema, insert_run

DEFAULT_DURATION_SECONDS = int(os.environ.get("DEFAULT_DURATION_SECONDS", "120"))

EVENT_LABELS = {
    "hit_wall": "🧱 Hit wall",
    "hit_robot": "🤖 Hit other robot",
    "stuck": "🛑 Stuck",
    "other": "❓ Other issue",
}

# Setup-phase widget keys. Streamlit deletes a widget's session_state entry
# the moment that widget stops being rendered, so once we leave the setup
# phase these are gone. We snapshot them into RUN_* keys on Start.
WIDGET_KEYS = ("robot_name", "algorithm", "ears", "duration")
RUN_KEYS = tuple(f"run_{k}" for k in WIDGET_KEYS)


@st.cache_resource
def get_pool():
    pool = _make_pool()
    init_schema(pool)
    return pool


def reset_state():
    for key in (
        "phase",
        "start_ts",
        "events",
        "submitted_run_id",
        *WIDGET_KEYS,
        *RUN_KEYS,
    ):
        st.session_state.pop(key, None)


def ensure_state():
    st.session_state.setdefault("phase", "setup")
    st.session_state.setdefault("events", [])
    st.session_state.setdefault("robot_name", "")
    st.session_state.setdefault("algorithm", "taxis")
    st.session_state.setdefault("ears", "aligned")
    st.session_state.setdefault("duration", DEFAULT_DURATION_SECONDS)


def render_setup():
    st.header("Set up a run")

    st.text_input("Robot name", key="robot_name", placeholder="e.g. Wall-E")

    st.radio(
        "Algorithm",
        options=["taxis", "kinesis"],
        key="algorithm",
        captions=["min of 2 sensors, random turn", "compare L/R, turn away from closer"],
    )
    st.radio(
        "Ear position",
        options=["aligned", "angled"],
        key="ears",
    )

    st.number_input(
        "Duration (seconds)",
        min_value=10,
        max_value=600,
        step=10,
        key="duration",
    )

    disabled = not st.session_state.robot_name.strip()
    if st.button("▶ Start", type="primary", disabled=disabled, use_container_width=True):
        st.session_state.run_robot_name = st.session_state.robot_name.strip()
        st.session_state.run_algorithm = st.session_state.algorithm
        st.session_state.run_ears = st.session_state.ears
        st.session_state.run_duration = st.session_state.duration
        st.session_state.phase = "running"
        st.session_state.start_ts = time.time()
        st.session_state.events = []
        st.rerun()


def render_running():
    elapsed = time.time() - st.session_state.start_ts
    duration = st.session_state.run_duration
    remaining = max(0.0, duration - elapsed)

    if remaining <= 0:
        st.session_state.phase = "review"
        st.rerun()

    mins, secs = divmod(int(remaining), 60)
    st.markdown(
        f"<h1 style='text-align:center;font-size:clamp(3rem,18vw,6rem);margin:0'>"
        f"{mins:02d}:{secs:02d}</h1>",
        unsafe_allow_html=True,
    )
    st.progress(remaining / duration)
    st.caption(
        f"Robot **{st.session_state.run_robot_name}** · "
        f"{st.session_state.run_algorithm} · {st.session_state.run_ears}"
    )

    counts = count_events(st.session_state.events)
    items = list(EVENT_LABELS.items())
    for row_start in (0, 2):
        cols = st.columns(2)
        for col, (ev_type, label) in zip(cols, items[row_start:row_start + 2]):
            with col:
                if st.button(label, key=f"btn_{ev_type}", use_container_width=True):
                    st.session_state.events.append((ev_type, elapsed))
                    st.rerun()
                st.markdown(
                    "<div style='text-align:center;font-size:1.75rem;"
                    "font-weight:600;line-height:1;margin:0.25rem 0'>"
                    f"{counts[ev_type]}</div>",
                    unsafe_allow_html=True,
                )
                _, sub_col, _ = st.columns([1, 2, 1])
                with sub_col:
                    if st.button(
                        "−1",
                        key=f"sub_{ev_type}",
                        disabled=counts[ev_type] == 0,
                        use_container_width=True,
                        help="Undo last",
                    ):
                        for i in range(len(st.session_state.events) - 1, -1, -1):
                            if st.session_state.events[i][0] == ev_type:
                                st.session_state.events.pop(i)
                                break
                        st.rerun()
                st.markdown("<div style='height:0.5rem'></div>", unsafe_allow_html=True)

    st.divider()
    c1, c2 = st.columns([1, 1])
    with c1:
        if st.button("⏭ End early", use_container_width=True):
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
    counts = count_events(st.session_state.events)
    st.markdown(
        f"**Robot:** {st.session_state.run_robot_name}  \n"
        f"**Condition:** {st.session_state.run_algorithm} × {st.session_state.run_ears}  \n"
        f"**Duration:** {st.session_state.run_duration}s  \n"
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
        st.session_state.robot_name = st.session_state.get("run_robot_name", "")
        st.session_state.algorithm = st.session_state.get("run_algorithm", "taxis")
        st.session_state.ears = st.session_state.get("run_ears", "aligned")
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


MOBILE_CSS = """
<style>
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
.stButton > button {min-height: 3.25rem; font-size: 1.05rem;}
/* Hide the "Press Enter to apply" hint under text inputs — confusing on
   mobile keyboards. Tapping outside the field (e.g. tapping Start) still
   commits the value. */
[data-testid="InputInstructions"] {display: none;}
</style>
"""


def main():
    st.set_page_config(page_title="Biology Day Logger", page_icon="🤖", layout="centered")
    st.markdown(MOBILE_CSS, unsafe_allow_html=True)
    st.title("🤖 Biology Day — Robot Logger")
    ensure_state()

    phase = st.session_state.phase
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
