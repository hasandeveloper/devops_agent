"""app.agents.supervisor.route() is a pure table lookup, no LLM call -- these tests
stub out the one real domain agent (rds_agent.handle) so they run in milliseconds with
no network/API cost, while still exercising supervisor's actual dispatch logic.
"""

import pytest

from app.agents import supervisor


async def test_github_actions_not_implemented():
    with pytest.raises(NotImplementedError):
        await supervisor.route({"source": "github_actions", "payload": {}})


@pytest.mark.parametrize("namespace", ["AWS/ECS", "AWS/EC2", "AWS/EBS", "AWS/ApplicationELB"])
async def test_unbuilt_domain_agents_not_implemented(namespace):
    raw_event = {"source": "cloudwatch", "payload": {"Trigger": {"Namespace": namespace}}}
    with pytest.raises(NotImplementedError):
        await supervisor.route(raw_event)


async def test_unrecognized_namespace_raises_value_error():
    raw_event = {"source": "cloudwatch", "payload": {"Trigger": {"Namespace": "AWS/Lambda"}}}
    with pytest.raises(ValueError):
        await supervisor.route(raw_event)


async def test_missing_namespace_raises_value_error():
    raw_event = {"source": "cloudwatch", "payload": {}}
    with pytest.raises(ValueError):
        await supervisor.route(raw_event)


async def test_rds_namespace_dispatches_to_rds_agent(monkeypatch):
    called_with = {}

    async def fake_handle(raw_event):
        called_with["raw_event"] = raw_event
        return "fake-incident-id"

    monkeypatch.setattr(supervisor.rds_agent, "handle", fake_handle)

    raw_event = {"source": "cloudwatch", "payload": {"Trigger": {"Namespace": "AWS/RDS"}}}
    result = await supervisor.route(raw_event)

    assert result == "fake-incident-id"
    assert called_with["raw_event"] is raw_event
