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


@st.cache_resource
def get_pool():
    pool = _make_pool()
    init_schema(pool)
    return pool


def reset_state():
    for key in (
        "phase",
        "robot_name",
        "algorithm",
        "ears",
        "duration",
        "start_ts",
        "events",
        "submitted_run_id",
    ):
        st.session_state.pop(key, None)


def ensure_state():
    st.session_state.setdefault("phase", "setup")
    st.session_state.setdefault("events", [])


def render_setup():
    st.header("Set up a run")

    st.text_input("Robot name", key="robot_name", placeholder="e.g. Wall-E")

    col1, col2 = st.columns(2)
    with col1:
        st.radio(
            "Algorithm",
            options=["taxis", "kinesis"],
            key="algorithm",
            captions=["min of 2 sensors, random turn", "compare L/R, turn away from closer"],
        )
    with col2:
        st.radio(
            "Ear position",
            options=["aligned", "angled"],
            key="ears",
        )

    st.number_input(
        "Duration (seconds)",
        min_value=10,
        max_value=600,
        value=st.session_state.get("duration", DEFAULT_DURATION_SECONDS),
        step=10,
        key="duration",
    )

    disabled = not st.session_state.get("robot_name", "").strip()
    if st.button("▶ Start", type="primary", disabled=disabled, use_container_width=True):
        st.session_state.phase = "running"
        st.session_state.start_ts = time.time()
        st.session_state.events = []
        st.rerun()


def render_running():
    elapsed = time.time() - st.session_state.start_ts
    remaining = max(0.0, st.session_state.duration - elapsed)

    if remaining <= 0:
        st.session_state.phase = "review"
        st.rerun()

    mins, secs = divmod(int(remaining), 60)
    st.markdown(
        f"<h1 style='text-align:center;font-size:6rem;margin:0'>{mins:02d}:{secs:02d}</h1>",
        unsafe_allow_html=True,
    )
    st.progress(remaining / st.session_state.duration)
    st.caption(
        f"Robot **{st.session_state.robot_name}** · "
        f"{st.session_state.algorithm} · {st.session_state.ears}"
    )

    cols = st.columns(len(EVENT_LABELS))
    counts = count_events(st.session_state.events)
    for col, (ev_type, label) in zip(cols, EVENT_LABELS.items()):
        with col:
            if st.button(label, key=f"btn_{ev_type}", use_container_width=True):
                st.session_state.events.append((ev_type, elapsed))
                st.rerun()
            st.metric(label="count", value=counts[ev_type], label_visibility="collapsed")

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
        f"**Robot:** {st.session_state.robot_name}  \n"
        f"**Condition:** {st.session_state.algorithm} × {st.session_state.ears}  \n"
        f"**Duration:** {st.session_state.duration}s  \n"
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
                    robot_name=st.session_state.robot_name.strip(),
                    algorithm=st.session_state.algorithm,
                    ears=st.session_state.ears,
                    duration_seconds=st.session_state.duration,
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
        # Preserve robot_name and condition selections to make repeated trials easy.
        robot_name = st.session_state.get("robot_name", "")
        algorithm = st.session_state.get("algorithm", "taxis")
        ears = st.session_state.get("ears", "aligned")
        duration = st.session_state.get("duration", DEFAULT_DURATION_SECONDS)
        reset_state()
        st.session_state.robot_name = robot_name
        st.session_state.algorithm = algorithm
        st.session_state.ears = ears
        st.session_state.duration = duration
        st.rerun()


def count_events(events):
    counts = {k: 0 for k in EVENT_LABELS}
    for ev_type, _ in events:
        counts[ev_type] = counts.get(ev_type, 0) + 1
    return counts


def main():
    st.set_page_config(page_title="Biology Day Logger", page_icon="🤖", layout="centered")
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
