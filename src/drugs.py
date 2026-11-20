"""Matching dictated drugs against the Polish Registry of Medicinal Products.

This is done by plain code, not the model, so nothing outside the registry can
end up on a prescription.

The registry (Rejestr Produktów Leczniczych, RPL) is the official CSV export from
rejestry.ezdrowie.gov.pl. It is not committed to the repository; download it with

    python -m src.drugs

A doctor may dictate a brand name ("Eliquis"), the active substance ("apiksaban")
or both; with both, the substance is a cross-check on the brand. Common names in
the registry are Latin ("Amoxicillinum") while doctors dictate Polish names
("amoksycylina"), so both are reduced to a shared spelling key and compared word
by word.

A product is selected only when the dictation determines it: the strength must
match exactly and the form (dictated, or implied by the dosage) must match. When
it does not, nothing is selected and a flag tells the doctor why; picking "the
first similar product" is how v1 put a 2.5 mg anticoagulant on a 5 mg prescription.
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
from rapidfuzz.distance import Levenshtein

DATA_DIR = Path(__file__).parent.parent / "data"
RPL_URL = "https://rejestry.ezdrowie.gov.pl/api/rpl/medicinal-products/public-pl-report/get-csv"
# The export keeps its original filename, which carries the snapshot date
# (e.g. ..._stan_na_dzien_20261110.csv); the newest snapshot in data/ is used.
RPL_GLOB = "Rejestr_Produktow_Leczniczych_*.csv"

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

# Polish and Latin spell the same substance differently (amoksycylina / amoxicillinum). The last
# rule merges z and s: a Latin "s" between vowels is a Polish "z" (budesonidum / budezonid,
# prednisonum / prednizon), while Latin also has "z" (azithromycinum).
SPELLING_RULES = (("ph", "f"), ("th", "t"), ("qu", "kw"), ("x", "ks"), ("v", "w"), ("y", "i"), ("c", "k"), ("z", "s"))
# Latin and Polish inflectional endings (amoxicillinum, metformini, amoksycylina, amoksycylinę).
WORD_ENDINGS = ("um", "us", "a", "e", "i")
# Adjective endings after that: "kwas klawulanowy" / "acidum clavulanicum" -> "klawulan".
ADJECTIVE_ENDINGS = ("ow", "ik")
MIN_STEM_LENGTH = 4
# Polish words with a different Latin equivalent, as spelling keys ("kwas foliowy" / "acidum folicum").
TOKEN_ALIASES = {"kwas": "akid"}

# Words are compared one by one; an approximate match tolerates one wrong, missing or extra
# letter (dictation, speech recognition), and only in words long enough for that to be safe.
# One edit, not a similarity ratio: 'prednizon' and 'prednizolon' are two different drugs.
MIN_FUZZY_TOKEN = 6
MAX_EDITS = 1
# A shorter stem of the same word: "foli" (foliowy) and "folik" (folicum).
MIN_PREFIX_TOKEN = 4
MAX_PREFIX_EXTRA = 2


def _plain(text: str) -> str:
    """Lowercase without Polish diacritics."""
    text = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _spelling_key(text: str) -> str:
    """Reduces a Polish or Latin drug name to a common spelling, e.g. both
    'amoksycylinę' and 'Amoxicillinum' become 'amoksikilin'."""
    text = _plain(text)
    for old, new in SPELLING_RULES:
        text = text.replace(old, new)
    text = re.sub(r"([a-z])\1+", r"\1", text)
    return " ".join(_strip_ending(word) for word in re.findall(r"[a-z0-9]+", text))


def _strip(word: str, endings: tuple[str, ...]) -> str:
    for ending in endings:
        if word.endswith(ending) and len(word) - len(ending) >= MIN_STEM_LENGTH:
            return word[: -len(ending)]
    return word


def _strip_ending(word: str) -> str:
    return _strip(_strip(word, WORD_ENDINGS), ADJECTIVE_ENDINGS)


def _name_tokens(text: str | None) -> tuple[str, ...]:
    # Numbers are part of the strength, not the name ("Concor Cor 2,5").
    return tuple(TOKEN_ALIASES.get(t, t) for t in _spelling_key(text or "").split() if not t.isdigit())


def _token_match(query: str, candidate: str) -> bool:
    if query == candidate:
        return True
    shorter, longer = sorted((query, candidate), key=len)
    if len(shorter) >= MIN_PREFIX_TOKEN and longer.startswith(shorter) and len(longer) - len(shorter) <= MAX_PREFIX_EXTRA:
        return True
    return len(shorter) >= MIN_FUZZY_TOKEN and Levenshtein.distance(query, candidate) <= MAX_EDITS


def _name_match(query: tuple[str, ...], candidate: tuple[str, ...]) -> bool:
    """Every dictated word must match a whole word of the registry name. (v1 compared
    whole strings with WRatio, which matched 'witamina' with the product 'Itami'.)"""
    return bool(query) and all(any(_token_match(q, c) for c in candidate) for q in query)


def _find(query: tuple[str, ...], fields: tuple[str, ...]) -> list[dict]:
    """Products whose name in one of the fields matches the query."""
    if not query:
        return []
    return [p for p in _registry() if any(_name_match(query, p[field]) for field in fields)]


# --- strength --------------------------------------------------------------

MASS_TO_MG = {"g": 1000.0, "mg": 1.0, "mcg": 0.001, "ug": 0.001, "µg": 0.001}
MULTIPLIERS = {"mln": 1_000_000.0, "tys": 1_000.0}
# Units as dictated ("pięćdziesiąt mikrogramów" comes out of the model as "50 mikrogramów").
UNIT_WORDS = (
    (re.compile(r"mikrogram\w*"), "mcg"),
    (re.compile(r"miligram\w*"), "mg"),
    (re.compile(r"\bgram\w*"), "g"),
    (re.compile(r"jednost\w*\s+mi[eę]dzynarodow\w*"), "iu"),
)
# Inhalers and sprays give the amount per dose ("100 mcg/dawkę", "(160 mcg + 4,5 mcg)/dawkę inh.");
# the amount itself is compared, the form tells an inhaler from a tablet.
PER_DOSE = re.compile(r"\s*(?:/|\bna\b)\s*dawk\w*")
THOUSANDS = re.compile(r"(?<=\d)[  ](?=\d{3}(?!\d))")  # "1 000 000"
STRENGTH_PART = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?:(?P<mult>mln|tys)\.?\s*)?(?P<unit>mg|mcg|µg|ug|g|j\.?\s?m\.?|iu|%)?"
    r"(?:\s*/\s*(?P<per>\d+(?:[.,]\d+)?)?\s*(?P<per_unit>ml|g)\b)?",
    re.IGNORECASE,
)


def _float(text: str) -> float:
    return float(text.replace(",", "."))


def parse_strength(text: str | None) -> tuple | None:
    """Normalizes a strength to comparable amounts: '1 g' == '1000 mg', '50 mikrogramów' ==
    '50 mcg', '1 mln j.m.' == '1 000 000 j.m.', '500 mg/5 ml' == '100 mg/ml', and a
    concentration per ml or per g ('2,5%' == '25 mg/g'; a gel and a solution differ by form).
    A combination ('875 mg + 125 mg') gives one amount per substance. None if unparsable."""
    if not text:
        return None
    text = text.lower()
    for pattern, unit in UNIT_WORDS:
        text = pattern.sub(unit, text)
    text = PER_DOSE.sub(" ", text.replace("(", " ").replace(")", " "))
    text = re.sub(r"\s+na\s+", "/", text)  # dictated "500 mg na 5 ml"
    while THOUSANDS.search(text):
        text = THOUSANDS.sub("", text)
    amounts = []
    for part in text.split("+"):
        match = STRENGTH_PART.search(part)
        if not match:
            return None
        value = _float(match["value"]) * MULTIPLIERS.get(match["mult"] or "", 1.0)
        unit = (match["unit"] or "").replace(" ", "").replace(".", "")
        if unit in MASS_TO_MG:
            value, unit = value * MASS_TO_MG[unit], "mg"
        elif unit in ("jm", "iu"):
            unit = "IU"
        elif unit == "%":  # g per 100 ml (or 100 g)
            value, unit = value * 10, "mg/conc"
        if match["per_unit"]:
            value /= _float(match["per"]) if match["per"] else 1.0
            unit = f"{unit}/conc"
        amounts.append((round(value, 6), unit))
    return tuple(sorted(amounts))


def same_strength(dictated: str | None, registered: str) -> bool:
    """Exact comparison: '2,5 mg' is not '5 mg'. (v1 accepted any shared number.)"""
    a, b = parse_strength(dictated), parse_strength(registered)
    if a is None or b is None:
        numbers = lambda t: {n.replace(",", ".") for n in re.findall(r"\d+(?:[.,]\d+)?", t or "")}
        return bool(dictated) and numbers(dictated) == numbers(registered)
    return a == b


# --- form ------------------------------------------------------------------

# The first group with a keyword in the (plain) form text decides, so order matters:
# "proszek do sporządzania zawiesiny" is a suspension, and modified-release tablets are
# not interchangeable with ordinary ones.
FORM_GROUPS = (
    ("parenteralna", ("wstrzyk", "infuz", "wlew")),
    # the route before the physical form: "krople do oczu, zawiesina" are eye drops,
    # "aerozol inhalacyjny, zawiesina" is an inhaler
    ("do oczu", ("do oczu", "oczn")),
    ("do nosa", ("do nosa", "donos")),
    ("do uszu", ("do uszu",)),
    ("wziewna", ("inhal", "wziew", "aerozol")),
    ("zawiesina", ("zawiesin",)),
    ("czopki", ("czop",)),
    ("plaster", ("plaster", "transderm")),
    ("zewnetrzna", ("masc", "krem", "zel")),
    ("krople", ("krople",)),
    ("syrop", ("syrop",)),
    ("tabletki o zmodyfikowanym uwalnianiu", ("przedluzon", "zmodyfikowan")),
    ("tabletki", ("tabl",)),
    ("kapsulki", ("kapsul",)),
    ("granulat/saszetka", ("granul", "saszet")),
    ("proszek", ("proszek",)),
    ("roztwor doustny", ("roztwor",)),
)


def form_group(text: str | None) -> str | None:
    text = _plain(text or "")
    for group, keywords in FORM_GROUPS:
        if any(keyword in text for keyword in keywords):
            return group
    return None


# --- registry --------------------------------------------------------------

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
def _registry() -> list[dict]:
    """Authorized human products, with precomputed matching keys in fields starting with '_'."""
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
            products.append({
                **product,
                "_name": _name_tokens(product["name"]),
                "_common_name": _name_tokens(product["common_name"]),
                "_form": form_group(product["form"]),
                "_substances": product["common_name"].count("+") + 1,
            })
    return products


@lru_cache
def _by_id() -> dict[str, dict]:
    return {p["rpl_id"]: p for p in _registry()}


def _public(products: list[dict]) -> list[dict]:
    return [{k: v for k, v in p.items() if not k.startswith("_")} for p in products]


def product_by_id(rpl_id: str) -> dict | None:
    """The registry product with this RPL identifier (used when the doctor picks a product by hand)."""
    product = _by_id().get(str(rpl_id).strip())
    return _public([product])[0] if product else None


def _rank(products: list[dict], query: tuple[str, ...]) -> list[dict]:
    """Single-substance products first ('paracetamol' should not prefer paracetamol +
    caffeine), then the closest name, then alphabetically so the result is reproducible."""
    joined = " ".join(query)
    return sorted(products, key=lambda p: (p["_substances"], -fuzz.ratio(joined, " ".join(p["_name"])), p["name"]))


def match_drug(brand: str | None, substance: str | None, strength: str | None = None,
               form: str | None = None, dosage: str | None = None, limit: int = 3) -> dict:
    """Finds the registry product for a dictated drug.

    Returns {"selected": product or None, "candidates": [...], "flags": [...]}. Without a
    selection the flags say why and the candidates are what the doctor can choose from:
      brand_substance_conflict  the brand is a product of a different substance
      brand_not_found           unknown brand; matched by the substance only
      no_registry_match         nothing in the registry matches the name
      choose_product            no strength dictated, so the product cannot be determined
      strength_not_in_registry  no product of this drug has the dictated strength
      form_not_in_registry      the strength exists, but not in the dictated form
      form_not_stated           the strength exists in several forms (e.g. tablets and infusion)
    """
    brand_q, substance_q = _name_tokens(brand), _name_tokens(substance)
    by_substance = _find(substance_q, ("_common_name", "_name"))
    by_brand = _find(brand_q, ("_name",))
    query = brand_q or substance_q
    flags = []

    def without_selection(products: list[dict], flag: str) -> dict:
        return {"selected": None, "candidates": _public(_rank(products, query)[:limit]), "flags": flags + [flag]}

    if brand_q and substance_q:
        pool = [p for p in by_brand if p in by_substance]
        if by_brand and not pool:  # "Heviran, walacyklowir": Heviran is acyclovir
            return without_selection(by_brand, "brand_substance_conflict")
        if not by_brand:
            flags.append("brand_not_found")
            pool = by_substance
    else:
        pool = by_brand or by_substance
    if not pool:
        return {"selected": None, "candidates": [], "flags": flags + ["no_registry_match"]}

    if not strength:
        return without_selection(pool, "choose_product")
    exact = [p for p in pool if same_strength(strength, p["strength"])]
    if not exact:
        return without_selection(pool, "strength_not_in_registry")

    # The form as dictated, or else as implied by the dosage ("2 tabletki doraźnie").
    group = form_group(form) or form_group(dosage)
    if group:
        in_form = [p for p in exact if p["_form"] == group]
        if not in_form:
            return without_selection(exact, "form_not_in_registry")
        exact = in_form
    elif len({p["_form"] for p in exact}) > 1:
        one_per_form = {}
        for p in _rank(exact, query):
            one_per_form.setdefault(p["_form"], p)
        return without_selection(list(one_per_form.values()), "form_not_stated")

    ranked = _rank(exact, query)
    return {"selected": _public(ranked[:1])[0], "candidates": _public(ranked[1:limit]), "flags": flags}


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
