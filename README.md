# llm-doc-assistant-hitl

Prototyp asystenta dokumentacji medycznej. Model językowy przygotowuje na podstawie dyktowania lekarza projekty recepty, zaleceń dla pacjenta i wpisu do karty wizyty; lekarz przegląda, poprawia i zatwierdza każdy dokument, zanim zostanie zapisany.

Projekt badawczy w ramach przedmiotu *Badania Naukowe*. Pytanie badawcze: jaką część pracy administracyjnej lekarza da się przyspieszyć przepływem pracy z modelem językowym i człowiekiem w pętli (human-in-the-loop), bez przekazywania systemowi jakichkolwiek decyzji medycznych.

> **Status:** prototyp (proof of concept). Wszystkie dane pacjentów są syntetyczne. Nie do użytku klinicznego.

## Jak to działa

Wizyta ma dwa etapy, każdy zakończony przeglądem przez lekarza. Lekarz weryfikuje projekt, poprawia go w razie potrzeby i zatwierdza; wizyta przechodzi wtedy do kolejnego etapu. Projekty nie wracają do modelu: każda zmiana po projekcie jest zmianą lekarza.

```
Etap 1 (w trakcie wizyty)
  dyktowanie ─┬─> agent recepty ──> dopasowanie RPL (kod) ─┐
              └─> agent zaleceń ───────────────────────────┴─> uwagi (kod) ─> LEKARZ: poprawia, zatwierdza

Etap 2 (po wizycie)
  dyktowanie ──> agent karty wizyty ──> weryfikator ──> LEKARZ: poprawia, zatwierdza ──> dokumentacja końcowa
```

- **Agent recepty:** wyodrębnia z dyktowania zlecane leki: nazwę handlową i/lub substancję czynną, moc, postać, dawkowanie i ilość (leki, które pacjent tylko kontynuuje, są pomijane). Produkty są wyszukiwane w Rejestrze Produktów Leczniczych zwykłym kodem, nie przez model, więc na receptę nie może trafić nic spoza rejestru. Rejestr podaje nazwy powszechnie stosowane po łacinie (`Amoxicillinum`), a lekarze dyktują nazwy polskie (`amoksycylina`), dlatego obie są sprowadzane do wspólnej pisowni i porównywane słowo po słowie. Nazwa handlowa podyktowana razem z substancją jest z nią sprawdzana krzyżowo. Produkt jest wybierany tylko wtedy, gdy dyktowanie go jednoznacznie określa (dokładna moc, więc 2,5 mg to nie 5 mg; podyktowana postać albo wynikająca z dawkowania); w przeciwnym razie nic nie jest wybierane, a flaga mówi lekarzowi dlaczego (`brand_substance_conflict`, `strength_not_in_registry`, `form_not_stated`, ...). Brakujące pola są oznaczane, a nie zgadywane.
- **Agent zaleceń:** pisze zalecenia dla pacjenta prostym językiem, objawy alarmowe i termin kontroli.
- **Agent karty wizyty:** tworzy wpis do karty: wywiad, badanie przedmiotowe i rozpoznanie. Kodu ICD-10 system nie proponuje: wybór kodu idzie w parze z rozpoznaniem, więc wpisuje go lekarz podczas przeglądu.
- **Uwagi do recepty:** generowane kodem (bez modelu) z brakujących pól i flag dopasowania, np. „pantoprazol: nie podano ilości”, „walacyklowir: nazwa handlowa Heviran to produkt innej substancji (Aciclovirum) niż podyktowana - wybierz produkt”.
- **Weryfikator:** zgłasza informacje w karcie, które nie wynikają z dyktowania, oraz istotne informacje z dyktowania, których w karcie brakuje.
- **Lekarz:** graf zatrzymuje się (`interrupt()`), dopóki lekarz nie zatwierdzi projektu (zwykle po poprawkach).

Dane pacjenta (imię i nazwisko, choroby przewlekłe, leki stałe, alergie) są wczytywane z bazy po numerze PESEL, więc lekarz dyktuje tylko to, co wydarzyło się podczas wizyty. Leki i zalecenia zatwierdzone w etapie 1 trafiają do karty wizyty automatycznie.

## Instalacja

Wymaga Pythona 3.11+.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m src.drugs    # pobranie rejestru leków (~17 MB) do data/
cp .env.example .env
```

W pliku `.env` uzupełnij klucz API i model w formacie `dostawca:model`, np. `anthropic:claude-haiku-5-5`, `openai:gpt-5-mini` albo `ollama:llama3.1` dla modelu lokalnego.

Rejestr to oficjalny eksport z [rejestry.ezdrowie.gov.pl](https://rejestry.ezdrowie.gov.pl/rpl/search/public), generowany codziennie. Nie jest dodawany do repozytorium; plik zachowuje oryginalną nazwę z datą stanu (`Rejestr_Produktow_Leczniczych_calosciowy_stan_na_dzien_RRRRMMDD.csv`), a używany jest najnowszy plik w `data/`. Uwzględniane są tylko produkty lecznicze stosowane u ludzi z ważnym (niewygasłym i niezawieszonym) pozwoleniem.

Aby używać nagrań audio zamiast dyktowania tekstem, doinstaluj także `faster-whisper`.

## Użycie

```bash
.venv/bin/python run.py data/visits/visit_001.json                 # przegląd w terminalu
.venv/bin/python run.py data/visits/visit_001.json --editor code   # projekt otwiera się w VS Code
.venv/bin/python run.py data/visits/visit_001.json --doctor-files  # zatwierdzenie plikiem zamiast Enter
```

### Przegląd przez lekarza

Na każdym etapie projekt jest zapisywany do `out/<visit_id>/stageN_draft.json`, a uwagi systemu do `stageN_issues.json` (i wypisywane w terminalu). Lekarz poprawia plik, zapisuje go i naciska **Enter**; wizyta przechodzi do kolejnego etapu. Z opcją `--doctor-files` zamiast Enter program czeka na plik `out/<visit_id>/stageN.done` (np. dla symulowanego lekarza).

Projekt zawiera wyłącznie treść dokumentów:

- **recepta:** substancja, nazwa handlowa, moc, postać, dawkowanie, ilość oraz wybrany produkt jako jedna linia, np. `"Eliquis · 5 mg · Tabletki powlekane · RPL 100288512"`; aby zmienić produkt, wklej linię z `candidates` albo wpisz produkt z numerem RPL;
- **zalecenia:** `recommendations`, `warning_signs`, `follow_up`;
- **karta wizyty:** `history`, `physical_examination`, `diagnosis` oraz puste pole `icd10`, w które lekarz wpisuje kod rozpoznania (np. `"J18.9"`).

Przy zatwierdzeniu dokument jest sprawdzany: poprawny JSON, każda pozycja recepty z produktem (istniejący numer RPL), dawkowaniem i ilością, kod ICD-10 w poprawnym formacie. Jeśli czegoś brakuje, program wypisuje komunikat i czeka na poprawkę.

W polu `doctor_notes` lekarz opisuje swoje poprawki (wymagane w benchmarku), według klucza klasyfikacji w [`docs/benchmark/codebook.md`](docs/benchmark/codebook.md):

```json
{"path": "prescription.1.quantity", "category": "recepta", "severity": "umiarkowany", "source": "dyktowanie", "reason": "..."}
```

- **kategoria:** `pominięcie`, `treść spoza dyktowania`, `błędny fakt`, `recepta`, `zalecenia`, `forma/język`;
- **waga:** `istotny` (mógłby zaszkodzić pacjentowi albo zmienia kliniczny sens dokumentacji), `umiarkowany` (wymaga poprawki, lekarz ją zauważy), `kosmetyczny`;
- **źródło:** `system` (błąd asystenta), `dyktowanie` (brak lub pomyłka w dyktowaniu), `rejestr` (dane rejestru leków).

Po zakończeniu wizyty program zapisuje szablon `analysis.json`, w którym recenzent opisuje całą wizytę: podsumowanie, mocne strony, braki, werdykt i ocenę projektów w skali 1–5 według 5 kryteriów wzorowanych na PDQI-9.

### Wejście

Plik wizyty (`data/visits/*.json`) zawiera PESEL pacjenta i dyktowanie obu etapów, jako tekst (`stage1_dictation`) albo ścieżkę do nagrania (`stage1_audio`).

### Wyjście

| Plik | Zawartość |
|---|---|
| `documentation.json` | Końcowy wpis do karty wizyty z zatwierdzoną receptą, zaleceniami i kodem ICD-10 |
| `stageN_draft.json` | Projekt systemu w postaci, w jakiej zobaczył go lekarz (lekarz edytuje ten plik; po zatwierdzeniu przywracana jest wersja systemu) |
| `stageN_approved.json` | Wersja zatwierdzona przez lekarza, z jego uwagami (`doctor_notes`) |
| `stageN_issues.json` | Uwagi systemu: do recepty (z kodu) i weryfikatora (do karty) |
| `log.json` | Przegląd: zmiany lekarza (ścieżka, rodzaj, przed → po, liczba znaków), uwagi lekarza, uwagi systemu |
| `analysis.json` | Opis wizyty przez recenzenta (wypełniany po zatwierdzeniu obu etapów) |
| `run_failures.json` | Tylko w benchmarku i tylko gdy przebieg wizyty się nie powiódł: komunikat błędu |

## Benchmark

Dwadzieścia niezależnych wizyt w POZ (`data/visits/visit_101.json` … `visit_120.json`) na trzech poziomach trudności:

- **A – podstawowe (5):** uporządkowane dyktowanie, 0–1 lek, do 2 cech utrudniających;
- **B – typowe (9):** naturalna kolejność dyktowania, 1–5 leków, 3–5 cech (m.in. przedłużenie leków przewlekłych, preparaty złożone, inhalatory, żele, dziecko, ciąża);
- **C – trudne (6):** dyktowanie jak z rozpoznawania mowy (wtrącenia, autokorekty, błędnie zapisane nazwy), wielolekowość ze zmianami leczenia, sprzeczności z kartą pacjenta.

Każdy plik ma klucz `benchmark`: poziom, dziedzinę, **cechy** z katalogu (np. `autokorekta`, `lateralizacja`, `konflikt-alergii`, `przedluzenie-recepty`), **hipotezy** zapisane przed uruchomieniem (gdzie system powinien mieć trudność).

Benchmark zawsze przebiega z lekarzem w pętli. Mierzy, jak bardzo lekarz musi ingerować: ile projektów zatwierdzono bez zmian, ile i jakich poprawek było (dokument, kategoria, waga, źródło) i kto wskazał problem (uwaga systemu, weryfikator, lekarz). Jednostką jest poprawka lekarza, czyli jedna uwaga w `doctor_notes` (np. zamiana antybiotyku to jedna poprawka, choć zmienia kilka pól); liczba zmienionych pól i znaków jest podawana dodatkowo. Nie ma automatycznej oceny względem odpowiedzi wzorcowej – miarą jest przegląd lekarza: poprawki opisane w `doctor_notes`, ocena każdej wizyty w `analysis.json` oraz hipotezy sprawdzone ręcznie po przebiegu (`out/benchmark/hypotheses_review.json`: `{"H101-1": {"confirmed": true, "evidence": "…"}}`).

```bash
.venv/bin/python benchmark.py                      # wszystkie wizyty, lekarz zatwierdza plikami stageN.done
.venv/bin/python benchmark.py --reviewer terminal  # lekarz zatwierdza w terminalu (Enter)
.venv/bin/python benchmark.py --summary-only       # zestawienie wyników z out/ (bez wywołań modelu)
.venv/bin/python benchmark.py --summary-only --export results/v4_doctor   # tabele CSV do raportu
.venv/bin/python benchmark_report.py results/v4_doctor docs/benchmark/generated   # fragmenty LaTeX raportu
.venv/bin/python -m pytest tests                   # testy dopasowania leków do rejestru i grupowania poprawek
```

Raport (`docs/benchmark/benchmark.tex`, pdfLaTeX, np. w Overleaf razem z katalogiem `generated/`) bierze wszystkie liczby, tabele i opisy wizyt z fragmentów w `docs/benchmark/generated/`, budowanych przez `benchmark_report.py` z wyeksportowanego przebiegu.

Wyniki trafiają do `out/benchmark/results_<reviewer>.json`; skrót SHA-256 plików scenariuszy z chwili uruchomienia potwierdza, że scenariusze i hipotezy nie zmieniły się po fakcie. Eksport (`--export`) zapisuje tabele w formacie „jeden wiersz = jedna obserwacja”, gotowe do wczytania w LaTeX (`pgfplotstable`, `csvsimple`):

| Plik | Wiersz |
|---|---|
| `visits.csv` | wizyta: poziom, cechy, liczba leków na recepcie, liczba poprawek wg wagi i źródła, liczba zmienionych pól, werdykt, oceny 1–5 |
| `corrections.csv` | poprawka lekarza: etap, dokument, ścieżka, kategoria, waga, źródło, kto wskazał, liczba pól i znaków, przed → po, uzasadnienie |
| `field_changes.csv` | pojedyncze zmienione pole: rodzaj zmiany, poprawka, do której należy, przed → po |
| `issues.csv` | uwaga systemu: czy doprowadziła do zmiany |
| `features.csv` | para wizyta × cecha z liczbą poprawek (analiza, które cechy wymagają interwencji) |
| `hypotheses.csv` | hipoteza: czy się potwierdziła |
| `visits/<visit_id>/` | dla każdego etapu projekt i wersja zatwierdzona, `log.json` (zmiany, uwagi systemu i lekarza) i `analysis.json` |

## Struktura projektu

```
run.py            uruchamianie wizyty z terminala; lekarz zatwierdza dokumenty, edytując pliki JSON
benchmark.py      uruchamianie scenariuszy benchmarku i zestawienie interwencji lekarza
benchmark_report.py  fragmenty LaTeX raportu z wyeksportowanego przebiegu
results/v4_doctor/   wyniki przebiegu benchmarku (CSV, projekty i wersje zatwierdzone wizyt)
docs/benchmark/   klucz klasyfikacji interwencji (codebook.md) i raport z benchmarku (LaTeX, generated/)
tests/            testy dopasowania leków do rejestru i grupowania poprawek (pytest)
src/graph.py      graf LangGraph: agenci i punkty zatwierdzania
src/schemas.py    schematy Pydantic dokumentów
src/drugs.py      pobieranie rejestru leków i dopasowanie podyktowanych leków do produktów
src/llm.py        wybór modelu (jedno miejsce zmiany dostawcy)
src/stt.py        rozpoznawanie mowy przez faster-whisper (opcjonalne)
data/             syntetyczni pacjenci i wizyty (oraz pobrany rejestr leków)
```

Kod, identyfikatory, nazwy pól dokumentów i komentarze w kodzie są po angielsku; pozostałe teksty projektu (dokumentacja, prompty, opisy pól schematów, dyktowania, scenariusze) są po polsku, ponieważ asystent tworzy polską dokumentację medyczną.

## Ograniczenia i dalsze kroki

- Stan wizyty jest trzymany w pamięci, więc oba etapy muszą przebiec w jednej sesji. Do zastąpienia checkpointerem SQLite.
- Planowane: interfejs Streamlit z nagrywaniem z mikrofonu i edytowalnymi formularzami.
- Planowane: porównanie modeli chmurowych i lokalnych oraz dokumentacji ręcznej i wspomaganej.
