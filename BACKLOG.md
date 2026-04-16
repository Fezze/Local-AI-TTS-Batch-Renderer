# BACKLOG

## P0

- [ ] Uporzadkowac `scheduler_core.py` tak, zeby byl composition root, a nie logic owner.
- [ ] Ujednolicic wewnetrzny styl importow na relatywny tam, gdzie to mozliwe.

## Done / Architecture decisions

- [x] Zbudowano warstwe source ingestion i wspolny model posredni w `src/local_tts_renderer/sources`.
- [x] Markdown i EPUB sa obslugiwane przez `sources/markdown.py` oraz `sources/epub.py`.
- [x] CLI i batch orchestration uzywaja `SourceDocument` przez registry zamiast format-specific branching.
- [x] Markdown-specific opcje sa zamkniete w `MarkdownIngestOptions`.
- [x] `cli_core.py` jest minimalnym compatibility shimem.
- [x] `input_parsers.py` zostaje cienka warstwa kompatybilnosci. Nie dodawac tam nowej logiki ingestion.

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
