"""LangGraph 教案确认闸门（interrupt / resume）。"""

from __future__ import annotations

import os
import uuid

import pytest

assert os.environ.get("MOCK_LLM", "").lower() in {"1", "true", "yes"}


def test_plan_confirm_interrupt_and_resume(sample_lesson_input):
    from lesson_prep.graph import run_preparation

    payload = dict(sample_lesson_input)
    payload["agent_profile"] = "full"
    thread_id = f"test-gate-{uuid.uuid4().hex[:8]}"

    paused = run_preparation(
        payload,
        pause_after_plan=True,
        thread_id=thread_id,
    )
    assert paused.get("awaiting_plan_confirm") is True
    assert paused.get("lesson_plan") is not None
    assert paused.get("thread_id") == thread_id
    assert paused.get("slides") is None

    resumed = run_preparation(
        payload,
        thread_id=thread_id,
        resume_value={
            "lesson_plan": paused["lesson_plan"],
            "teaching_anchors": paused.get("teaching_anchors"),
            "lesson_plan_qa": paused.get("lesson_plan_qa"),
        },
        pause_after_plan=False,
    )
    assert resumed.get("awaiting_plan_confirm") is False
    assert resumed.get("slides") is not None
    assert resumed.get("exercise_paper") is not None
    assert resumed.get("blackboard") is not None
