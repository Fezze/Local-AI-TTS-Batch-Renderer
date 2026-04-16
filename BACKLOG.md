# BACKLOG

Szczegoly i pelny kontekst dla ponizszych pozycji sa w [local_tts_renderer_review_and_refactor_guidance.md](local_tts_renderer_review_and_refactor_guidance.md).

## P0

- [ ] Zbudowac realna warstwe source ingestion i wspolny model posredni. Pierwszy krok jest zaczety w `src/local_tts_renderer/sources`: model, registry oraz ingester Markdown/EPUB.
- [ ] Rozbic `input_parsers.py`: przeniesc Markdown i EPUB do `sources/markdown.py` oraz `sources/epub.py`, zostawiajac tylko neutralne shared utilities.
- [ ] Usunac format-specific branching z CLI i batch orchestration (`.epub`, `.md`) przez uzycie `SourceDocument`.
- [x] Zastapic hardcoded directory scan `.md`/`.epub` rejestrem wspieranych suffixow z `sources`.
- [ ] Odseparowac Markdown-specific opcje (`md_single_chapter`, `max_chapter_chars`) od generic loading API.
- [ ] Zweryfikowac i odchudzic `cli_core.py`; `__all__` nie moze reklamowac nazw, ktore nie sa jawnie importowane i wspierane.
- [ ] Uporzadkowac `scheduler_core.py` tak, zeby byl composition root, a nie logic owner.
- [ ] Ujednolicic wewnetrzny styl importow na relatywny tam, gdzie to mozliwe.

## P1

- [ ] Dodac opcjonalny tryb `safe` (`--safe-workers`) ograniczajacy obciazenie na slabszych maszynach bez zmiany domyslnego `2x GPU + 1x CPU`.
- [ ] Dodac wsparcie AMD: `ROCmExecutionProvider` na Linux i `DmlExecutionProvider` na Windows, razem z instrukcjami instalacji.
- [ ] Poprawic sciezke CPU: presety i limity workerow/chunkingu dla slabszych maszyn.
- [ ] Przygotowac wsparcie ARM, glownie Linux ARM64, wraz z checklista zaleznosci audio i ONNX.
- [ ] Dodac ogolne zalecenia antywirusowe i diagnostyke wydajnosci I/O dla srodowisk developerskich.

## P2

- [ ] Dodac interfejs wielu modeli TTS, nie tylko Kokoro, z jednolitym kontraktem renderera.

## P3

- [ ] Dodac nowe formaty wejscia tylko przez `sources` registry i SourceDocument: `DOCX`.
- [ ] Dodac nowe formaty wejscia tylko przez `sources` registry i SourceDocument: `MOBI`.
- [ ] Dodac nowe formaty wejscia tylko przez `sources` registry i SourceDocument: `PDF`.

## P4

- [ ] Ujednolicic CLI i profile uruchomienia dla srodowisk dev/prod.
