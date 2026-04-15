# BACKLOG

Szczegoly i pelny kontekst dla ponizszych pozycji sa w [local_tts_renderer_review_and_refactor_guidance.md](local_tts_renderer_review_and_refactor_guidance.md).

## P0

- [x] Rozszerzyc testy snapshot/regression dla manifestow i kolejnosci chunkow/rozdzialow na fixture `md` i `epub`.
- [ ] Dokonczyc rozdzial `cli_parsing` od `input_parsers` tak, zeby cache, prezentacja i parsowanie byly rozdzielone bez cienkich re-exportow. `cli_cache.py` i `cli_presentation.py` sa juz wydzielone, zostalo doczyszczenie resztek fasad/importow.
- [ ] Usunac pozostaly barrel w `cli_core.py` i zastapic go jawnymi eksportami lub tymczasowymi shimami o waskim zakresie.
- [ ] Domknac pozostale zaleznosci CLI w render/audio flow, zwlaszcza tam gdzie nadal sa punkty wstrzykniecia oparte o fasady.
- [ ] Uporzadkowac `scheduler_core.py` tak, zeby byl composition root, a nie logic owner. `scheduler_setup.py` juz wydziela budowe runtime, zostalo dalsze odchudzenie glownej petli.
- [ ] Ujednolicic wewnetrzny styl importow na relatywny tam, gdzie to mozliwe.
- [x] Zastapic top-level `sys.path` bootstrap entrypointow pakietowymi entrypointami z metadanych pakietu. Dodatkowo zostal minimalny bootstrap tylko w `md_to_audio.py` i `run_tts_batch.py` dla bezposredniego uruchamiania skryptow.

## P1

- [ ] Dodac opcjonalny tryb `safe` (`--safe-workers`) ograniczajacy obciazenie na slabszych maszynach bez zmiany domyslnego `2x GPU + 1x CPU`.
- [ ] Dodac wsparcie AMD: `ROCmExecutionProvider` na Linux i `DmlExecutionProvider` na Windows, razem z instrukcjami instalacji.
- [ ] Poprawic sciezke CPU: presety i limity workerow/chunkingu dla slabszych maszyn.
- [ ] Przygotowac wsparcie ARM, glownie Linux ARM64, wraz z checklista zaleznosci audio i ONNX.
- [ ] Dodac ogolne zalecenia antywirusowe i diagnostyke wydajnosci I/O dla srodowisk developerskich.

## P2

- [ ] Dodac interfejs wielu modeli TTS, nie tylko Kokoro, z jednolitym kontraktem renderera.

## P3

- [ ] Dodac nowe formaty wejscia: `DOCX`.
- [ ] Dodac nowe formaty wejscia: `MOBI`.

## P4

- [ ] Ujednolicic CLI i profile uruchomienia dla srodowisk dev/prod.
