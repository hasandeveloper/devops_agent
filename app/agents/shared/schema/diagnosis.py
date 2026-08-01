from pydantic import BaseModel, Field

from app.models import RiskTier


class Diagnosis(BaseModel):
    """Structured output every domain agent's diagnose node must produce.

    Maps directly onto the incidents table -- title/description/risk_tier.
    """

    title: str = Field(description="Short one-line summary of the incident, e.g. 'Aurora ACU pinned at ceiling'")
    description: str = Field(description="What's happening, likely cause, and relevant context gathered")
    risk_tier: RiskTier = Field(description="low: informational, medium: needs attention, high: needs urgent action")
