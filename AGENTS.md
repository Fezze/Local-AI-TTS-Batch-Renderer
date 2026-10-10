# AGENTS.md

## Core rules

- Ograniczaj odpowiedzi do najważniejszych informacji.
- Nie zmieniaj logiki renderowania bez testu regresji lub snapshotu zachowania.
- Pliki kodu powyżej 500 linii muszą zostać rozbite na mniejsze moduły.
- Przed zakończeniem zmian uruchom adekwatne testy; pełna regresja to `python -m pytest -q`.
- Aktywne zadania zapisuj wyłącznie w `BACKLOG.md`; decyzje architektoniczne utrzymuj w `docs/ARCHITECTURE.md`.

Szczegóły pracy developerskiej: [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).
