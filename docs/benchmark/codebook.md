# Klucz klasyfikacji interwencji lekarza (codebook)

Dokument definiuje, jak recenzent (lekarz w pętli) opisuje każdą swoją poprawkę projektu i jak ocenia całą wizytę.
Służy temu, żeby ten sam projekt był klasyfikowany tak samo przez różnych recenzentów (np. Claude i lekarza),
co pozwala policzyć zgodność oceniających (κ Cohena) na wspólnym podzbiorze wizyt.

Jednostką analizy jest **poprawka lekarza**: jedna **uwaga** w `doctor_notes`, opisująca jeden problem projektu.
Zmienione pola (`changes` w `log.json`) wyznacza automatycznie porównanie projektu z wersją zatwierdzoną; każde zmienione
pole należy do jednej poprawki:

```json
{"path": "prescription.1.quantity", "category": "recepta", "severity": "umiarkowany",
 "source": "dyktowanie", "reason": "Nie podałem ilości; uzupełniam 1 opakowanie."}
```

- **Jedna uwaga = jeden problem.** Poprawka może zmienić kilka pól, jeśli usuwa jeden problem (zamiana leku zmienia substancję,
  moc, postać, dawkowanie i produkt – to jedna poprawka z uwagą na `prescription.0`). Dwa różne problemy w tej samej pozycji
  opisuje się dwiema uwagami, zwłaszcza gdy jeden z nich wskazała uwaga systemu, a drugi nie (np. brak ilości oznaczony przez
  system i lek sprzeczny z alergią znaleziony przez lekarza: uwagi `prescription.0.quantity` i `prescription.0`).
- Zmienione pole należy do **najbardziej szczegółowej** uwagi, która je obejmuje (`prescription.0.quantity` przed `prescription.0`).
- Wpisanie kodu ICD-10 nie jest poprawką (system go nie proponuje, kod zawsze wpisuje lekarz).

## 1. Kategoria – czego dotyczy poprawka

| Kategoria | Definicja | Przykłady |
|---|---|---|
| `pominięcie` | W projekcie brakuje informacji, która była w dyktowaniu lub w danych pacjenta i jest potrzebna w dokumencie. | brak terminu kontroli „z wynikiem posiewu”; brak „pilnie na SOR” przy objawach alarmowych; alergia z karty pacjenta pominięta w wywiadzie przy antybiotykoterapii; brak ilości leku |
| `treść spoza dyktowania` | Projekt zawiera treść, której lekarz nie podyktował i która nie wynika z danych pacjenta (dodanie, nadinterpretacja, „halucynacja”). | „piłka nożna” zamiast „gra w piłkę”; ogólnik „przyjmuj leki zgodnie z receptą”; „nie leczy się pani lekami bez recepty” z „bez recepty, leków nie zlecam” |
| `błędny fakt` | Informacja jest w dyktowaniu, ale projekt podaje ją błędnie: wartość, strona ciała, jednostka, przypisanie do złej sekcji lub osoby. | temperatura 39,3 °C z wywiadu wpisana jako wynik badania (podyktowano 39 °C); lewa zamiast prawej kończyny |
| `recepta` | Pozycja recepty: wybór produktu, moc, postać, dawkowanie, ilość, lek na recepcie, którego nie zlecono. | wybór produktu przy konflikcie nazwy handlowej (Heviran); lek kontynuowany wpisany na receptę |
| `zalecenia` | Treść zgodna z dyktowaniem, ale sformułowana tak, że pacjent może ją źle zrozumieć lub wykonać. | „pozostałe leki” zamiast nazw; „zawiesinę w lodówce” przy dwóch zawiesinach |
| `forma/język` | Literówki, błędy językowe, zbędne dopiski techniczne, powtórzenia – bez wpływu na treść. | „dyzuryjnych”; „w porównaniu z zwykłą”; „(zgłoszono brak alergii)” |

Reguły rozstrzygania:
1. Brak ilości/dawkowania leku → `recepta`, nie `pominięcie` (pominięcie dotyczy tekstu dokumentów).
2. Zła wartość, która **jest** w dyktowaniu → `błędny fakt`; wartość, której **nie ma** w dyktowaniu → `treść spoza dyktowania`.
3. Treść poprawna, ale niejasna dla pacjenta → `zalecenia`; treść błędna → odpowiednia kategoria merytoryczna.

## 2. Waga – jakie byłyby skutki, gdyby lekarz nie poprawił

| Waga | Definicja | Przykłady |
|---|---|---|
| `istotny` | Mógłby zaszkodzić pacjentowi albo zmienia kliniczny sens dokumentacji (zły lek/dawka/strona, brak objawu alarmowego prowadzący do opóźnienia pomocy, fałszywa informacja o leczeniu). | nierozwiązany konflikt Heviran/walacyklowir na recepcie; antybiotyk z penicyliną u uczulonej pacjentki |
| `umiarkowany` | Wymaga poprawy (dokument niekompletny lub nieprecyzyjny), ale lekarz zauważy to przy normalnym przeglądzie, a szkoda jest mało prawdopodobna. | brak ilości leku; błędna wartość pomiaru bez wpływu na decyzję; brak „pilnie na SOR” przy pełnej liście objawów |
| `kosmetyczny` | Nie wpływa na treść kliniczną ani na zachowanie pacjenta. | literówka, zbędny dopisek, ogólnik bez znaczenia |

## 3. Źródło – kto spowodował potrzebę poprawki

| Źródło | Definicja |
|---|---|
| `system` | Błąd asystenta: model lub kod przetworzył poprawne dyktowanie niepoprawnie. |
| `dyktowanie` | Dyktowanie było niepełne lub błędne (brak ilości, pomyłka w nazwie handlowej, niejednoznaczność); system zadziałał zgodnie z zasadami. |
| `rejestr` | Dane Rejestru Produktów Leczniczych nie pozwalają wybrać produktu (brak mocy/postaci, nazewnictwo), a dyktowanie było poprawne. |

## 4. Wykrycie – kto wskazał problem (wyznaczane automatycznie dla każdej poprawki)

- `uwaga systemu` – któreś z pól poprawki wskazała deterministyczna uwaga do recepty (etap 1),
- `weryfikator` – któreś z pól poprawki wskazała uwaga weryfikatora (etap 2),
- `lekarz` – żadna uwaga systemu nie dotyczyła pól tej poprawki; problem znalazł recenzent.

## 5. Ocena wizyty (`analysis.json`)

Po zatwierdzeniu obu etapów, **przed** obejrzeniem zestawień benchmarku i hipotez, recenzent wypełnia:

- `summary` – 1–2 zdania o jakości projektów tej wizyty,
- `strengths` – co system zrobił dobrze (np. poprawnie oznaczony brak ilości, zachowane dawkowanie wagowe),
- `weaknesses` – czego brakowało lub co było błędne (odsyła do uwag w `doctor_notes`),
- `verdict` – `gotowy bez zmian` / `drobne poprawki` (tylko kosmetyczne) / `istotne poprawki` (co najmniej jedna umiarkowana lub istotna) /
  `nie do użycia` (szybciej napisać od nowa),
- `ratings` – ocena projektów (obu etapów łącznie) w skali 1–5 według 5 kryteriów wzorowanych na PDQI-9
  (Physician Documentation Quality Instrument, Stetson i in.):

| Kryterium | 1 | 3 | 5 |
|---|---|---|---|
| `dokładność` | liczne błędy merytoryczne | pojedynczy błąd bez wpływu klinicznego | brak błędów |
| `kompletność` | brak kluczowych informacji | brak drobnej informacji | wszystko z dyktowania i karty, co potrzebne |
| `zwięzłość` | dużo treści zbędnej lub spoza dyktowania | pojedyncze zbędne zdania | brak treści zbędnej |
| `zrozumiałość dla pacjenta` | zalecenia niezrozumiałe lub mylące | zrozumiałe z drobnymi niejasnościami | jasne i jednoznaczne |
| `spójność wewnętrzna` | sprzeczności między dokumentami | drobne niespójności | recepta, zalecenia i karta spójne |
