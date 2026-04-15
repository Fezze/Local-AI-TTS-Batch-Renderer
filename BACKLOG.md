# BACKLOG

## Otwarte

- [ ] P0: Utrwalic provider layer (priority, fallback, pelne logowanie decyzji providera dla CLI i batch).
- [ ] P0: Rozszerzyc testy snapshot/regression dla manifestow i kolejnosci chunkow/rozdzialow na fixture `md` i `epub`.
- [ ] P1: Dodac opcjonalny tryb `safe` (np. `--safe-workers`) ograniczajacy obciazenie na slabszych maszynach bez zmiany domyslnego `2x GPU + 1x CPU`.
- [ ] P1: Dodac wsparcie AMD: `ROCmExecutionProvider` (Linux) i `DmlExecutionProvider` (Windows) z instrukcjami instalacji.
- [ ] P1: Poprawic sciezke CPU (presety i limity workerow/chunkingu dla slabszych maszyn).
- [ ] P1: Przygotowac wsparcie ARM (glownie Linux ARM64) wraz z checklista zaleznosci audio i ONNX.
- [ ] P1: Dodac ogolne zalecenia antywirusowe i diagnostyke wydajnosci I/O dla srodowisk developerskich (bez host-specyficznych wyjatkow).
- [ ] P2: Dodac interfejs wielu modeli TTS (nie tylko Kokoro) z jednolitym kontraktem renderera.
- [ ] P3: Dodac nowe formaty wejscia: `DOCX`.
- [ ] P3: Dodac nowe formaty wejscia: `MOBI`.
- [ ] P4: Ujednolicic CLI i profile uruchomienia dla srodowisk dev/prod.
