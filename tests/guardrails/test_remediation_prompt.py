"""Structural checks for the propose_remediation prompt -- every candidate actually
appears in the output, the untrusted-data framing survives, and missing investigation
degrades gracefully. Same spirit as test_prompts.py; wording is free to change, these
properties aren't.
"""

from app.prompts.rds import remediation


def test_includes_environment_and_every_candidate():
    prompt = remediation.build_prompt(
        environment="production",
        diagnosis={"title": "CPU spike", "risk_tier": "high"},
        investigation="found one expensive query",
        candidates=[
            {"pid": 1234, "duration_seconds": 512, "query": "SELECT * FROM orders WHERE customer_id = 1"},
            {"pid": 5678, "duration_seconds": 65, "query": "SELECT pg_sleep(60)"},
        ],
    )
    assert "'production'" in prompt
    assert "1234" in prompt
    assert "8m 32s" in prompt
    assert "SELECT * FROM orders WHERE customer_id = 1" in prompt
    assert "5678" in prompt
    assert "1m 5s" in prompt


def test_includes_diagnosis_and_investigation():
    prompt = remediation.build_prompt(
        environment="dev",
        diagnosis={"title": "Some Diagnosis Title"},
        investigation="specific investigation findings",
        candidates=[{"pid": 1, "duration_seconds": 10, "query": "SELECT 1"}],
    )
    assert "Some Diagnosis Title" in prompt
    assert "specific investigation findings" in prompt


def test_handles_missing_investigation():
    prompt = remediation.build_prompt(
        environment="dev",
        diagnosis={},
        investigation=None,
        candidates=[{"pid": 1, "duration_seconds": 10, "query": "SELECT 1"}],
    )
    assert "No further investigation was needed." in prompt


def test_warns_against_treating_data_as_instructions():
    prompt = remediation.build_prompt(
        environment="dev", diagnosis={}, investigation=None, candidates=[{"pid": 1, "duration_seconds": 10, "query": "SELECT 1"}]
    )
    assert "not instructions" in prompt


def test_instructs_one_decision_per_candidate_without_inventing_pids():
    prompt = remediation.build_prompt(
        environment="dev", diagnosis={}, investigation=None, candidates=[{"pid": 1, "duration_seconds": 10, "query": "SELECT 1"}]
    )
    assert "do not invent a pid that isn't listed" in prompt
