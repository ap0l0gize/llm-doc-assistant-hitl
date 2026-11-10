"""Matching dictated drug names against the Polish Registry of Medicinal Products.

This is done by plain code, not the model, so nothing outside the registry can
end up on a prescription.

The registry (Rejestr Produktów Leczniczych, RPL) is the official CSV export from
rejestry.ezdrowie.gov.pl. It is not committed to the repository; download it with

    python -m src.drugs

Common names in the registry are Latin ("Amoxicillinum"), while doctors dictate
Polish names ("amoksycylina"), so both are reduced to a shared spelling key
before fuzzy matching.
"""

import csv
import re
import unicodedata
import urllib.request
from datetime import date
from fnmatch import fnmatch
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz

DATA_DIR = Path(__file__).parent.parent / "data"
RPL_URL = "https://rejestry.ezdrowie.gov.pl/api/rpl/medicinal-products/public-pl-report/get-csv"
# The export keeps its original filename, which carries the snapshot date
# (e.g. ..._stan_na_dzien_20261110.csv); the newest snapshot in data/ is used.
RPL_GLOB = "Rejestr_Produktow_Leczniczych_*.csv"
SIMILARITY_THRESHOLD = 80

# Registry column (Polish, as published) -> field name used in this project.
COLUMNS = {
    "Identyfikator Produktu Leczniczego": "rpl_id",
    "Nazwa Produktu Leczniczego": "name",
    "Nazwa powszechnie stosowana": "common_name",
    "Moc": "strength",
    "Postać farmaceutyczna": "form",
    "Kod ATC": "atc_code",
}
PRODUCT_TYPE_COLUMN = "Rodzaj preparatu"
HUMAN_PRODUCT = "Ludzki"  # the other type is "Weterynaryjny"
VALIDITY_COLUMN = "Ważność pozwolenia"
INDEFINITE = "Bezterminowe"
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Polish and Latin spell the same substance differently (amoksycylina / amoxicillinum).
SPELLING_RULES = (("ph", "f"), ("th", "t"), ("qu", "kw"), ("x", "ks"), ("v", "w"), ("y", "i"), ("c", "k"))
# Latin and Polish inflectional endings (amoxicillinum, metformini, amoksycylina, amoksycylinę).
WORD_ENDINGS = ("um", "us", "a", "e", "i")
MIN_STEM_LENGTH = 4


def _spelling_key(text: str) -> str:
    """Reduces a Polish or Latin drug name to a common spelling, e.g. both
    'amoksycylinę' and 'Amoxicillinum' become 'amoksikilin'."""
    text = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    for old, new in SPELLING_RULES:
        text = text.replace(old, new)
    text = re.sub(r"([a-z])\1+", r"\1", text)
    return " ".join(_strip_ending(word) for word in re.findall(r"[a-z0-9]+", text))


def _strip_ending(word: str) -> str:
    for ending in WORD_ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= MIN_STEM_LENGTH:
            return word[: -len(ending)]
    return word


def _is_authorized(validity: str, today: str) -> bool:
    """False for expired or suspended ("Pozwolenie zawieszone") authorizations.
    Empty validity is used for central (EU) authorizations."""
    if validity in ("", INDEFINITE):
        return True
    return bool(ISO_DATE.fullmatch(validity)) and validity >= today


def registry_path() -> Path:
    snapshots = sorted(DATA_DIR.glob(RPL_GLOB))
    if not snapshots:
        raise FileNotFoundError(f"No drug registry export in {DATA_DIR}. Download it with: python -m src.drugs")
    return snapshots[-1]


@lru_cache
def _registry() -> list[tuple[dict, str, str]]:
    """Authorized human products as (product, name key, common name key)."""
    today = date.today().isoformat()
    products, seen = [], set()
    with open(registry_path(), encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, delimiter=";"):
            if row[PRODUCT_TYPE_COLUMN] != HUMAN_PRODUCT or not _is_authorized(row[VALIDITY_COLUMN], today):
                continue
            product = {field: row[column] for column, field in COLUMNS.items()}
            # Parallel imports repeat the same product under separate authorizations.
            identity = (product["name"], product["strength"], product["form"])
            if identity in seen:
                continue
            seen.add(identity)
            products.append((product, _spelling_key(product["name"]), _spelling_key(product["common_name"])))
    return products


def _numbers(text: str | None) -> set[str]:
    return set(re.findall(r"\d+", text or ""))


def match_drug(name: str, strength: str | None = None, limit: int = 3) -> list[dict]:
    """Returns up to `limit` registry products matching the dictated drug, best first."""
    query = _spelling_key(name)
    candidates = []
    for product, name_key, common_name_key in _registry():
        similarity = max(fuzz.WRatio(query, name_key), fuzz.WRatio(query, common_name_key))
        if similarity < SIMILARITY_THRESHOLD:
            continue
        strength_match = bool(strength) and bool(_numbers(strength) & _numbers(product["strength"]))
        candidates.append({**product, "similarity": round(similarity), "strength_match": strength_match})
    # On a tie, products with fewer active substances first (dictated "dekstrometorfan"
    # should not prefer a dexpanthenol + dextromethorphan combination).
    candidates.sort(key=lambda c: (c["strength_match"], c["similarity"], -c["common_name"].count("+")), reverse=True)
    return candidates[:limit]


def download_registry() -> Path:
    """Downloads the current registry export into data/ under the server's filename."""
    with urllib.request.urlopen(RPL_URL, timeout=300) as response:
        filename = Path(response.headers.get_filename() or "").name
        if not fnmatch(filename, RPL_GLOB):
            filename = f"Rejestr_Produktow_Leczniczych_{date.today():%Y%m%d}.csv"
        path = DATA_DIR / filename
        path.write_bytes(response.read())
    return path


if __name__ == "__main__":
    print(f"Saved drug registry to {download_registry()}")
