"""Deterministic dispatch -- no LLM call. Every CloudWatch alarm payload
already states its own namespace; every GitHub event states its own source.
There's no judgment call to make here, only a table lookup.
"""

from app.agents.domains.rds import builder as rds_agent


async def route(raw_event: dict) -> str:
    if raw_event["source"] == "github_actions":
        raise NotImplementedError("CI/CD agent not yet implemented")

    namespace = (raw_event.get("payload") or {}).get("Trigger", {}).get("Namespace")

    if namespace == "AWS/RDS":
        return await rds_agent.handle(raw_event)
    if namespace in ("AWS/ECS", "AWS/EC2", "AWS/EBS"):
        raise NotImplementedError("ECS agent not yet implemented")
    if namespace == "AWS/ApplicationELB":
        raise NotImplementedError("ALB agent not yet implemented")

    raise ValueError(f"no domain agent for namespace={namespace!r}, source={raw_event['source']!r}")
