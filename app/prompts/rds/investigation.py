_PROMPT_TEMPLATE = (
    "You're investigating an RDS alarm for the '{environment}' environment. You already have "
    "the alarm payload, cluster status (context.cluster_info, including the writer instance's "
    "identifier under 'members'), recent metric trend, connection counts, and lock waits. "
    "Decide whether deeper investigation is warranted using the tools available:\n"
    "- get_performance_insights_top_sql: which SQL is consuming the most DB load recently -- "
    "pass the writer instance's identifier from context.cluster_info.members\n"
    "- explain_query_for_pid: get the query plan for a specific backend PID you've already seen "
    "(always pass environment='{environment}' when calling this)\n"
    "If nothing looks abnormal, don't call any tool -- say so directly. Otherwise, summarize "
    "what you found in a few sentences. Don't speculate beyond what the tools actually returned."
)


def build_prompt(environment: str) -> str:
    return _PROMPT_TEMPLATE.format(environment=environment)
