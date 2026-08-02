import json


def build_prompt(payload: dict, context: dict) -> str:
    return (
        "You are diagnosing an AWS RDS/Aurora CloudWatch alarm for a DevOps monitoring system.\n\n"
        f"Alarm payload:\n{json.dumps(payload, indent=2, default=str)}\n\n"
        "Additional context gathered (cluster config + recent metric trend):\n"
        f"{json.dumps(context, indent=2, default=str)}\n\n"
        "Produce a diagnosis: a short title, a description of what's happening and the likely "
        "cause, and a risk tier (low/medium/high)."
    )
