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
    """Calls the model and returns an instance of the given Pydantic schema."""
    return _model().with_structured_output(schema).invoke(
        [("system", system_prompt), ("human", user_prompt)]
    )
