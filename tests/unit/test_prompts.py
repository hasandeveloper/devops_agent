"""These don't check prompt wording (that changes freely) -- they check the handful of
structural properties that would silently break the pipeline if lost in a rewrite:
every input actually appears in the output, the untrusted-data framing survives, and
missing optional input degrades gracefully instead of crashing.
"""

from app.prompts.rds import diagnose, investigation


def test_diagnose_prompt_includes_all_inputs():
    prompt = diagnose.build_prompt(
        payload={"AlarmName": "Some Alarm"},
        context={"environment": "dev"},
        similar_incidents=[{"title": "Past Incident", "description": "it happened", "risk_tier": "high"}],
        investigation="found nothing unusual",
    )
    assert "Some Alarm" in prompt
    assert "\"environment\": \"dev\"" in prompt
    assert "Past Incident" in prompt
    assert "found nothing unusual" in prompt


def test_diagnose_prompt_handles_no_similar_incidents_or_investigation():
    prompt = diagnose.build_prompt(payload={}, context={}, similar_incidents=[], investigation=None)
    assert "No similar past incidents found." in prompt
    assert "No further investigation was needed." in prompt


def test_diagnose_prompt_unwraps_enum_risk_tier():
    # RiskTier loads as an enum instance when incidents come straight off the ORM --
    # this must render as "high", not "RiskTier.high".
    class FakeRiskTier:
        value = "high"

    prompt = diagnose.build_prompt(
        payload={},
        context={},
        similar_incidents=[{"title": "t", "description": "d", "risk_tier": FakeRiskTier()}],
        investigation=None,
    )
    assert "high" in prompt
    assert "RiskTier" not in prompt


def test_diagnose_prompt_warns_against_treating_data_as_instructions():
    prompt = diagnose.build_prompt(payload={}, context={}, similar_incidents=[], investigation=None)
    assert "never as instructions" in prompt


def test_investigation_prompt_includes_environment():
    prompt = investigation.build_prompt("staging")
    assert "'staging' environment" in prompt
    assert "environment='staging'" in prompt


def test_investigation_prompt_warns_against_treating_data_as_instructions():
    prompt = investigation.build_prompt("dev")
    assert "not instructions" in prompt
