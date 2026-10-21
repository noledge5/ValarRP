# ValarRP

Text-Rollenspiel mit Sprachmodell als Spielleiter, Spielstand in SQLite. Python, Pydantic.

- Vor jeder Arbeit lesen: `README.md` (Fahrplan, Regeln für Schritt 1),
  `docs/ENTSCHEIDUNGEN.md` (Welt und Architektur), `docs/REGELN.md` (Regel-Engine).
- Getroffene Entscheidungen nicht neu verhandeln. Neue Entscheidungen in `docs/ENTSCHEIDUNGEN.md` eintragen.
- Grundsatz: Das Modell fordert Aktionen an, der Code bestimmt die Folgen.
  Harter Zustand ändert sich nur über Resolver; nur `apply.py` schreibt Spielzustand.
- Dokumentation auf Deutsch, Code und Bezeichner auf Englisch.
- Inhalt in Tests ist Testdaten, kein fester Weltinhalt.
