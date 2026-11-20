"""Single place to choose the model. Switching provider = changing LLM_MODEL in .env."""

import os
from functools import lru_cache

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

load_dotenv()


@lru_cache
def _model():
    return init_chat_model(os.environ.get("LLM_MODEL", "anthropic:claude-haiku-5-5"))


def generate_structured(schema, system_prompt: str, user_prompt: str):
    """Calls the model and returns an instance of the given Pydantic schema.

    Uses the provider's native structured output (the schema is enforced while
    generating), not tool calling: with tool calling the model sometimes produced
    malformed arguments (a field glued into the text of the previous one), which
    failed validation in about 40% of visit notes in the benchmark."""
    return _model().with_structured_output(schema, method="json_schema").invoke(
        [("system", system_prompt), ("human", user_prompt)]
    )
