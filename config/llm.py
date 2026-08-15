from langchain_core.language_models.chat_models import BaseChatModel

from config import settings


def get_llm(temperature: float = 0) -> BaseChatModel:
    """The single place every domain agent gets its LLM from.

    Swapping providers is a config change (LLM_PROVIDER + matching API
    key/model in .env) -- no agent code touches a provider class directly.
    """
    if settings.llm_provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=temperature,
            timeout=settings.llm_timeout_seconds,
        )

    if settings.llm_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=settings.anthropic_model,
            api_key=settings.anthropic_api_key,
            temperature=temperature,
            timeout=settings.llm_timeout_seconds,
        )

    raise ValueError(f"unsupported llm_provider: {settings.llm_provider!r}")
