"""Schematy dokumentów zwracanych przez agentów.

Wszystkie pola są wymagane (bez wartości domyślnych), a brak informacji to
jawny null / pusta lista - dzięki temu ten sam schemat działa w trybie
structured output u różnych dostawców i model nie ma czego "dopowiadać".
"""

from pydantic import BaseModel, Field

NIE_PODANO = "null, jeśli lekarz tego nie podał - nie zgaduj"


class LekPodyktowany(BaseModel):
    nazwa: str = Field(description="Nazwa leku lub substancji dokładnie tak, jak podał lekarz")
    dawka: str | None = Field(description=f"Moc, np. '500 mg'; {NIE_PODANO}")
    postac: str | None = Field(description=f"Postać, np. 'tabletki'; {NIE_PODANO}")
    dawkowanie: str | None = Field(description=f"Sposób dawkowania, np. '3 x 1 tabl. przez 7 dni'; {NIE_PODANO}")
    ilosc: str | None = Field(description=f"Ilość, np. '1 opakowanie'; {NIE_PODANO}")


class ReceptaLLM(BaseModel):
    pozycje: list[LekPodyktowany] = Field(description="Leki wymienione przez lekarza; pusta lista, jeśli żadnych")


class Zalecenia(BaseModel):
    zalecenia: list[str] = Field(description="Zalecenia dla pacjenta prostym językiem, bez listy leków")
    objawy_alarmowe: list[str] = Field(description="Kiedy pacjent ma zgłosić się pilnie; pusta lista, jeśli nie podano")
    kontrola: str | None = Field(description=f"Termin wizyty kontrolnej; {NIE_PODANO}")


class KartaWizyty(BaseModel):
    wywiad: str
    badanie_przedmiotowe: str
    rozpoznanie: str | None = Field(description=f"Rozpoznanie słowami lekarza; {NIE_PODANO}")
    icd10_propozycja: str | None = Field(description="Proponowany kod ICD-10 do weryfikacji przez lekarza; null bez rozpoznania")


class Uwaga(BaseModel):
    pole: str = Field(description="Nazwa pola karty wizyty, którego dotyczy uwaga")
    problem: str = Field(description="Co się nie zgadza z dyktowaniem albo czego brakuje")


class Weryfikacja(BaseModel):
    uwagi: list[Uwaga] = Field(description="Pusta lista, jeśli karta jest zgodna z dyktowaniem")
