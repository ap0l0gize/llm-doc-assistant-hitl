"""Schemas of the documents returned by the agents.

Every field is required (no defaults) and missing information is an explicit
null / empty list. This keeps the same schema working in structured-output
mode across providers and leaves the model nothing to "fill in".

Field descriptions are part of the prompt sent to the model, so they are in Polish.
The model tends to copy phrases from a description into the value ("do weryfikacji
przez lekarza", "nie podano"), so descriptions say what the value is, not how to
treat it.

There is no ICD-10 field: choosing the code goes with the diagnosis, which is the
doctor's decision, so the doctor enters it during the review (src/review_format.py).
"""

from pydantic import BaseModel, Field

NOT_STATED = "null, jeśli lekarz tego nie podał"


class DictatedDrug(BaseModel):
    brand_name: str | None = Field(
        description="Nazwa handlowa produktu (np. 'Eliquis') dokładnie tak, jak podał lekarz; "
                    "null, jeśli lekarz podał tylko substancję czynną")
    substance: str | None = Field(
        description="Substancja czynna (nazwa powszechnie stosowana, np. 'apiksaban') tak, jak podał lekarz; "
                    "null, jeśli lekarz podał tylko nazwę handlową - nie ustalaj substancji samodzielnie")
    strength: str | None = Field(description=f"Moc, np. '500 mg'; {NOT_STATED}")
    form: str | None = Field(description=f"Postać, np. 'tabletki'; {NOT_STATED}")
    dosage: str | None = Field(description=f"Sposób dawkowania, np. '3 x 1 tabl. przez 7 dni'; {NOT_STATED}")
    quantity: str | None = Field(description=f"Ilość, np. '1 opakowanie'; {NOT_STATED}")


class DictatedPrescription(BaseModel):
    items: list[DictatedDrug] = Field(
        description="Leki, które lekarz zleca na tę receptę; bez leków, które pacjent ma jedynie kontynuować "
                    "bez zmian, i bez leków, które lekarz odwołał lub wykluczył; pusta lista, jeśli żadnych")


class PatientInstructions(BaseModel):
    recommendations: list[str] = Field(
        description="Zalecenia podyktowane przez lekarza, prostym językiem, bez listy leków z recepty")
    warning_signs: list[str] = Field(description="Kiedy pacjent ma zgłosić się pilnie; pusta lista, jeśli nie podano")
    follow_up: str | None = Field(
        description="Kiedy albo z czym pacjent ma zgłosić się na kontrolę, także bez daty "
                    "(np. 'za tydzień', 'z wynikiem posiewu'); null tylko wtedy, gdy lekarz nie wspomniał o kontroli")


class VisitNote(BaseModel):
    history: str = Field(description="Wywiad")
    physical_examination: str = Field(description="Badanie przedmiotowe")
    diagnosis: str | None = Field(description=f"Rozpoznanie słowami lekarza; {NOT_STATED}")


class VerifierIssue(BaseModel):
    field: str = Field(description="Nazwa pola karty wizyty, którego dotyczy uwaga")
    problem: str = Field(description="Co się nie zgadza z dyktowaniem albo czego brakuje")


class Verification(BaseModel):
    issues: list[VerifierIssue] = Field(description="Pusta lista, jeśli karta jest zgodna z dyktowaniem")
