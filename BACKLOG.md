# BACKLOG

- [ ] P0: Utrwalic provider layer (priority, fallback, pelne logowanie decyzji providera dla CLI i batch).
- [x] P0: Rozbic `src/local_tts_renderer/cli_core.py` (1900+ linii) na mniejsze moduly <=500 linii bez zmiany logiki renderowania.
- [x] P0: Podniesc coverage krytycznych flow `cli_core` i `scheduler_runtime` (obecnie najwieksze luki) po rozbiciu modulowym.
- [x] P0: Wydzielic logike z `src/local_tts_renderer/cli.py` i `src/local_tts_renderer/scheduler.py` do `*_core.py`, zostawiajac cienkie fasady publiczne.
- [ ] P0: Rozszerzyc testy snapshot/regression dla manifestow i kolejnosci chunkow/rozdzialow na fixture `md` i `epub`.
- [x] P0: Dodac testy scheduler+resume dla `--max-parts-per-run` (powrot joba po `return code 75`, brak duplikacji partow, poprawny final manifest).
- [ ] P1: Dodac opcjonalny tryb `safe` (np. `--safe-workers`) ograniczajacy obciazenie na slabszych maszynach bez zmiany domyslnego `2x GPU + 1x CPU`.
- [ ] P1: Dodac wsparcie AMD: `ROCmExecutionProvider` (Linux) i `DmlExecutionProvider` (Windows) z instrukcjami instalacji.
- [ ] P1: Poprawic sciezke CPU (presety i limity workerow/chunkingu dla slabszych maszyn).
- [ ] P1: Przygotowac wsparcie ARM (glownie Linux ARM64) wraz z checklista zaleznosci audio i ONNX.
- [ ] P1: Dodac ogolne zalecenia antywirusowe i diagnostyke wydajnosci I/O dla srodowisk developerskich (bez host-specyficznych wyjatkow).
- [ ] P2: Dodac interfejs wielu modeli TTS (nie tylko Kokoro) z jednolitym kontraktem renderera.
- [ ] P3: Dodac nowe formaty wejscia: `DOCX`.
- [ ] P3: Dodac nowe formaty wejscia: `MOBI`.
- [ ] P4: Ujednolicic CLI i profile uruchomienia dla srodowisk dev/prod.

## Ostatnio zamkniete

- [x] Przeniesione domyslne parametry, progi i magiczne wartosci do `src/local_tts_renderer/defaults.py` jako jedno zrodlo prawdy.
- [x] Dodany bezpieczny limit dlugosci chunkow (`--max-phoneme-chars`) z zachowaniem granic zdan, zeby unikac ucinania fonemow.
- [x] Ujednolicone domyslne parametry (voice/speed/max_chars/silence/max_part/heartbeat/output_dir) w jednym module `defaults.py` zamiast rozjechanych wartosci CLI vs batch.
- [x] Dodane `--md-single-chapter` i `--max-chapter-chars` dla Markdown oraz skrót `d` do przełączania debug w kontrolkach batch.
- [x] Podniesione coverage: total 71% -> 74% przez doslownie zaciecie luk w `cli_entry`, `cli_parsing`, `cli_runtime`, `scheduler_jobs` i `scheduler_process` (55 testow).
- [x] Podniesione coverage: `cli_core` 33% -> 48%, `scheduler_runtime` 13% -> 78%, total 51% -> 65% (50 testow).
- [x] Rozbity `cli_core.py` na `cli_entry.py` + `cli_render_flow.py` + utility moduly; `cli_core.py` zostawiony jako cienka fasada (83 linie), testy regresji zielone (50/50).
- [x] Naprawiony bug zawiechy workerow po cooldown: warunek `idle_since` w `scheduler_runtime` zmieniony na realny timeout (`idle_since > now`).

- [x] Dodane testy coverage dla `input_parsers`, `scheduler_args/core/jobs/logging/process/runtime-light` + wrapperow; coverage wzrosl z 32% do 51%.
- [x] Dodany `pytest-cov` do `requirements-dev.txt` oraz coverage gate w CI (`--cov-fail-under=50`).
- [x] Dodany preflight `scripts/doctor.py` (python/paths/models/providers/temp) oraz podpiecie do skryptow startowych (`start.ps1/.sh`, `start-batch.ps1/.sh`).
- [x] Rozbity scheduler na moduly (`scheduler_args`, `scheduler_jobs`, `scheduler_logging`, `scheduler_process`, `scheduler_runtime`); kazdy plik <=500 linii.
- [x] Dodana regula governance: pliki kodu powyzej 500 linii musza byc rozbijane na mniejsze moduly.
- [x] Naprawiony deadlock locka bootstrapu GPU przy wyjatku podczas spawnu workera (gwarantowane zwolnienie locka + log `worker_exception`).
- [x] Dodane serializowanie bootstrapu GPU (`--serialize-gpu-bootstrap` domyslnie wlaczone), aby unikac deadlocku przy rownoleglym `onnxruntime` init.
- [x] Naprawione regresje po refaktorze: domyslna obsluga `aggressive_gpu_recovery` w schedulerze, stabilne testy bez systemowego `%TEMP%`, poprawne porownanie stemu multipart (`chapter-part`).
- [x] Dodany watchdog bootstrapu workera (osobny `bootstrap_silence_timeout`) oraz logi `[batch:wait]/[batch:timeout]` bez `--debug`, z informacja o fazie.
- [x] Naprawione locki `WinError 32` na resume partow (unikalne temp-part per chapter + bezpieczne cleanup retry).
- [x] Poprawione nazewnictwo multipart EPUB (`chapter-part`) i tracknumber ID3; part 1 numerowany jako `-01` gdy chapter ma kolejne party.
- [x] Dodany tryb `--max-parts-per-run` do resetu procesu/VRAM miedzy partami duzych chapterow.
- [x] Dodane sterowanie konsola batch (`p`, `r`, `1..9`) i recovery GPU po timeout/CUDA.
