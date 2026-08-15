from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult


class TokenBudgetExceeded(Exception):
    """A single run's cumulative LLM token usage crossed its configured cap.

    Caught as non-retryable in jobs/webhooks_job.py -- retrying a run that already blew
    its budget just spends the same excess again for no benefit.
    """


class TokenBudgetTracker(AsyncCallbackHandler):
    """Accumulates token usage across every LLM call inside one agent run.

    Pass an instance via config={"callbacks": [tracker]} to create_agent's .ainvoke() --
    LangChain propagates it to every LLM call inside that agent's own internal loop
    automatically, so this doesn't need threading through each round trip by hand.

    Important limitation, confirmed empirically rather than assumed: an exception
    raised from inside a callback (on_llm_end) is caught and only logged by LangChain's
    own callback manager, so this CANNOT interrupt an agent loop mid-flight -- it only
    accumulates. Call check() yourself after .ainvoke() returns to actually enforce the
    cap. The real mid-flight ceiling on a runaway loop is investigate_further.py's
    recursion_limit; this is a cost-observability + "stop before diagnose/persist" gate
    layered on top of it, not a replacement for it.
    """

    def __init__(self, max_tokens: int):
        self.max_tokens = max_tokens
        self.total_tokens = 0

    async def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        usage = (response.llm_output or {}).get("token_usage", {})
        self.total_tokens += usage.get("total_tokens", 0)

    def check(self) -> None:
        if self.total_tokens > self.max_tokens:
            raise TokenBudgetExceeded(f"used {self.total_tokens} tokens, exceeding the {self.max_tokens} cap")
