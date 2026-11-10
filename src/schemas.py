"""Schemas of the documents returned by the agents.

Every field is required (no defaults) and missing information is an explicit
null / empty list. This keeps the same schema working in structured-output
mode across providers and leaves the model nothing to "fill in".

Field descriptions are part of the prompt sent to the model, so they are in Polish.
"""

from pydantic import BaseModel, Field

NOT_STATED = "null, jeśli lekarz tego nie podał - nie zgaduj"


class DictatedDrug(BaseModel):
    name: str = Field(description="Nazwa leku lub substancji dokładnie tak, jak podał lekarz")
    strength: str | None = Field(description=f"Moc, np. '500 mg'; {NOT_STATED}")
    form: str | None = Field(description=f"Postać, np. 'tabletki'; {NOT_STATED}")
    dosage: str | None = Field(description=f"Sposób dawkowania, np. '3 x 1 tabl. przez 7 dni'; {NOT_STATED}")
    quantity: str | None = Field(description=f"Ilość, np. '1 opakowanie'; {NOT_STATED}")


class DictatedPrescription(BaseModel):
    items: list[DictatedDrug] = Field(description="Leki wymienione przez lekarza; pusta lista, jeśli żadnych")


class PatientInstructions(BaseModel):
    recommendations: list[str] = Field(description="Zalecenia dla pacjenta prostym językiem, bez listy leków")
    warning_signs: list[str] = Field(description="Kiedy pacjent ma zgłosić się pilnie; pusta lista, jeśli nie podano")
    follow_up: str | None = Field(description=f"Termin wizyty kontrolnej; {NOT_STATED}")


class VisitNote(BaseModel):
    history: str = Field(description="Wywiad")
    physical_examination: str = Field(description="Badanie przedmiotowe")
    diagnosis: str | None = Field(description=f"Rozpoznanie słowami lekarza; {NOT_STATED}")
    icd10_suggestion: str | None = Field(description="Proponowany kod ICD-10 do weryfikacji przez lekarza; null bez rozpoznania")


class VerifierIssue(BaseModel):
    field: str = Field(description="Nazwa pola karty wizyty, którego dotyczy uwaga")
    problem: str = Field(description="Co się nie zgadza z dyktowaniem albo czego brakuje")


class Verification(BaseModel):
    issues: list[VerifierIssue] = Field(description="Pusta lista, jeśli karta jest zgodna z dyktowaniem")
