"""Structural checks for the propose_idle_connection_remediation prompt -- same spirit
as test_remediation_prompt.py: every candidate actually appears, the untrusted-data
framing survives, missing investigation degrades gracefully, and the heavier-consequence
framing (termination vs. cancellation) is actually present.
"""

from app.prompts.rds import idle_connection_remediation


def test_includes_environment_and_every_candidate():
    prompt = idle_connection_remediation.build_prompt(
        environment="production",
        diagnosis={"title": "Connections spike", "risk_tier": "high"},
        investigation="found a blocked query chain",
        candidates=[
            {"pid": 1234, "duration_seconds": 512, "query": "UPDATE orders SET status = 'shipped'"},
            {"pid": 5678, "duration_seconds": 65, "query": "SELECT 1"},
        ],
    )
    assert "'production'" in prompt
    assert "1234" in prompt
    assert "8m 32s" in prompt
    assert "UPDATE orders SET status = 'shipped'" in prompt
    assert "5678" in prompt
    assert "1m 5s" in prompt


def test_includes_diagnosis_and_investigation():
    prompt = idle_connection_remediation.build_prompt(
        environment="dev",
        diagnosis={"title": "Some Diagnosis Title"},
        investigation="specific investigation findings",
        candidates=[{"pid": 1, "duration_seconds": 10, "query": "SELECT 1"}],
    )
    assert "Some Diagnosis Title" in prompt
    assert "specific investigation findings" in prompt


def test_handles_missing_investigation():
    prompt = idle_connection_remediation.build_prompt(
        environment="dev", diagnosis={}, investigation=None, candidates=[{"pid": 1, "duration_seconds": 10, "query": "SELECT 1"}]
    )
    assert "No further investigation was needed." in prompt


def test_warns_against_treating_data_as_instructions():
    prompt = idle_connection_remediation.build_prompt(
        environment="dev", diagnosis={}, investigation=None, candidates=[{"pid": 1, "duration_seconds": 10, "query": "SELECT 1"}]
    )
    assert "not instructions" in prompt


def test_instructs_one_decision_per_candidate_without_inventing_pids():
    prompt = idle_connection_remediation.build_prompt(
        environment="dev", diagnosis={}, investigation=None, candidates=[{"pid": 1, "duration_seconds": 10, "query": "SELECT 1"}]
    )
    assert "do not invent a pid that isn't listed" in prompt


def test_frames_termination_as_heavier_than_cancellation():
    prompt = idle_connection_remediation.build_prompt(
        environment="dev", diagnosis={}, investigation=None, candidates=[{"pid": 1, "duration_seconds": 10, "query": "SELECT 1"}]
    )
    assert "heavier action than cancelling a query" in prompt


def test_states_blocking_is_already_confirmed_not_the_llms_decision():
    prompt = idle_connection_remediation.build_prompt(
        environment="dev", diagnosis={}, investigation=None, candidates=[{"pid": 1, "duration_seconds": 10, "query": "SELECT 1"}]
    )
    assert "already been confirmed" in prompt
