import json
import textwrap

_INCIDENT_TEMPLATE = """\
    Incident {index}

    Risk Tier:
    {risk_tier}

    Title:
    {title}

    Description:
    {description}
    """

_PROMPT_TEMPLATE = """\
    You are an experienced Site Reliability Engineer (SRE)
    diagnosing AWS RDS/Aurora CloudWatch alarms.

    Analyze the provided alarm, AWS context, and historical incidents.

    Rules:
    - Only use provided information.
    - Do not invent missing details.
    - Clearly separate facts from possible causes.
    - Use similar incidents only as supporting evidence.

    ## CloudWatch Alarm

    {payload}

    ## AWS Context

    {context}

    ## Similar Past Incidents

    {similar_incidents}

    ## Expected Output

    Return:

    Title:
    A short meaningful incident title.

    Description:

    Current Situation:
    What is happening.

    Evidence:
    Relevant metrics and AWS context.

    Likely Causes:
    Possible causes ranked by likelihood.

    Recommended Investigation:
    Next checks an SRE should perform.

    Risk Tier:
    Must be one of:
    - low
    - medium
    - high
    """


def _format_json(data: dict) -> str:
    return json.dumps(data, indent=2, default=str)


def _format_similar_incidents(similar_incidents: list[dict]) -> str:
    if not similar_incidents:
        return "No similar past incidents found."

    results = []

    for index, incident in enumerate(similar_incidents, start=1):
        risk_tier = incident.get("risk_tier")

        if hasattr(risk_tier, "value"):
            risk_tier = risk_tier.value

        formatted = textwrap.dedent(_INCIDENT_TEMPLATE).format(
            index=index,
            risk_tier=risk_tier,
            title=incident.get("title"),
            description=incident.get("description"),
        )
        results.append(formatted.strip())

    return "\n\n".join(results)


def build_prompt(payload: dict, context: dict, similar_incidents: list[dict]) -> str:
    formatted = textwrap.dedent(_PROMPT_TEMPLATE).format(
        payload=_format_json(payload),
        context=_format_json(context),
        similar_incidents=_format_similar_incidents(similar_incidents),
    )
    return formatted.strip()
