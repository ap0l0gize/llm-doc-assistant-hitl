"""Jedno miejsce wyboru modelu. Zmiana dostawcy = zmiana LLM_MODEL w .env."""

import os
from functools import lru_cache

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

load_dotenv()


@lru_cache
def _model():
    return init_chat_model(os.environ.get("LLM_MODEL", "anthropic:claude-haiku-5-5"))


def ustrukturyzuj(schemat, system: str, tresc: str):
    """Wywołuje model i zwraca obiekt zgodny ze schematem Pydantic."""
    return _model().with_structured_output(schemat).invoke(
        [("system", system), ("human", tresc)]
    )
