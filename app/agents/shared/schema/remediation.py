from pydantic import BaseModel, Field


class RemediationCandidateDecision(BaseModel):
    """One decision about one candidate query -- the LLM must address every
    candidate offered, not invent new ones. propose_remediation.py validates
    `pid` against the candidate list before trusting any of these.
    """

    pid: int = Field(description="The backend pid this decision is about, copied from the candidate list")
    should_propose: bool = Field(description="Whether this query is worth flagging for a human to cancel")
    rationale: str = Field(description="Why (or why not) -- shown to the human approver in Slack")


class RemediationProposal(BaseModel):
    """Structured output for propose_remediation.py's cancel-query decision.

    One entry per candidate from get_long_running_queries -- the model filters
    (e.g. don't flag an obvious backup/vacuum job) rather than picking a single winner.
    """

    proposals: list[RemediationCandidateDecision] = Field(
        description="One decision per candidate query offered, in the same order"
    )
