# ValarRP

Text-Rollenspiel in der eigenen Welt **Valar**: rau, düster und politisch wie The Witcher,
mit Magie aus Stürmen und Kristallen. Ein Sprachmodell erzählt als Spielleiter,
der Code führt den Spielstand in SQLite.

- Welt und Entscheidungen: [docs/ENTSCHEIDUNGEN.md](docs/ENTSCHEIDUNGEN.md)
- Regel-Engine: [docs/REGELN.md](docs/REGELN.md)

## Grundidee

Das Modell **fordert Aktionen an**, der Code **bestimmt ihre Folgen**.
Aktionen sind Pydantic-Modelle. In Schritt 1 kommen sie aus Tests und später aus einer Kommandozeile.
In Schritt 2 füllt das Modell genau diese Schemas aus.

## Fahrplan

1. **Kern ohne Modell:** SQLite, Transaktionen, Resolver, Idempotenz, Revision. Test-Quest: Abgabe.
   - 1b: Probenablauf (offene Probe, analoger Wurf, Ticks, Skill-Aufstieg).
   - Danach weitere Aktionen (Bewegen, Untersuchen, Reden, Sammeln, Zustand anzeigen) und eine kleine Kommandozeile.
2. **Modell anbinden:** OpenAI-kompatibler Client (OpenRouter oder Ollama), Prüfung gegen die Schemas,
   Kostenbremse, Härtetest mit mehreren Modellen.
3. **Weboberfläche:** Docker auf der Synology, Zugriff über Tailscale.

## Regeln für Schritt 1

Diese Regeln prüfen die Tests:

- Abgelehnte Aktionen werden gespeichert, ändern aber keinen Zustand und erhöhen weder Zeit noch Revision.
- Nur angenommene Aktionen erhöhen `campaign.revision`.
- Belohnungen kommen aus den Inhaltsdefinitionen, nie aus der Aktion.
- Ein Spielzug mit veralteter Revision wird abgelehnt. Der Client liest den Stand neu und versucht es
  mit einer **neuen** `request_id`.
- Dieselbe `request_id` ein zweites Mal liefert das gespeicherte Ergebnis, ohne erneute Wirkung.
- Würfe werden mit dem Spielzug gespeichert. Tests nutzen einen eingespeisten Zufallsgenerator mit festem Seed.
- Nur `apply.py` schreibt Spielzustand. Resolver lesen nur.

## Geplanter Aufbau

```text
valar/
  models.py      # Action, Effect, TurnResult (Pydantic)
  content.py     # Testinhalt, vorerst fest im Code
  db.py          # Schema und Verbindung
  rules.py       # Regeltabellen und Rechenfunktionen aus REGELN.md; kein Datenbankzugriff
  resolve.py     # liest Zustand, gibt Effekte zurück; schreibt nie
  apply.py       # der einzige Code, der Spielzustand schreibt
  turns.py       # submit_turn(): Idempotenz, Revision, Transaktion
tests/
  test_turn_in_quest.py   # Szenario: die acht Tests unten, plus Entscheidungen 37 und 39
  test_roll_flow.py       # Szenario Schritt 1b: der Probenablauf
  unit/                   # Unit-Tests je Baustein: models, content, db, rules, resolve, apply, turns
  helpers.py, conftest.py # gemeinsame Hilfen und Fixtures
.github/workflows/
  tests.yml               # CI: pytest auf Python 3.12 und 3.13
```

Alle Tabellen bekommen `campaign_id`; Spieler haben eine `player_id`.

## Tests ausführen

```bash
pip install pydantic pytest
python -m pytest
```

## Testfall für Schritt 1

Reine Testdaten, kein fester Weltinhalt: Ein Schürfer steht im Zunfthaus, hat 5 Kristallsplitter und
einen aktiven Auftrag der Zunft. Abgabe → 0 Splitter, +100 Pfennig (1 Gulden), Auftrag erledigt.

Tests in dieser Reihenfolge:

1. Abgabe erledigt den Auftrag genau einmal.
2. Zweite Abgabe wird abgelehnt, keine doppelte Belohnung.
3. Doppelte `request_id` spielt das Ergebnis ohne Wirkung erneut ab.
4. Zu wenige Splitter (4): abgelehnt, nichts ändert sich, Revision bleibt.
5. Falscher Ort: abgelehnt.
6. Veraltete Revision: abgelehnt.
7. Fehler mitten im Anwenden (z. B. beim Geld): alles wird zurückgerollt, kein Spielzug gespeichert.
8. Stand und Duplikat-Schutz überleben das Schließen und Neuöffnen der Datenbank.

Die wichtigsten Beweise sind Test 7 (alles oder nichts) und Test 3 (nichts doppelt).

## Testfall für Schritt 1b

Probenablauf nach [REGELN.md](docs/REGELN.md). Ausgangslage: Spielfigur mit STR 14 (+2), GES 8 und
Klingenwaffen 30 (+3), also Bonus +5. Gegen SG 12 (DIFF-03) reicht eine 7. Reine Testdaten.

1. Ein Versuch öffnet eine Probe (`awaiting_roll`) und zeigt Skill, SG, Bonus, mögliche Ticks und Grund.
2. Der eingetippte Wurf löst die Probe auf und wird mit dem Spielzug gespeichert.
3. Ausgänge: natürliche 20 und 1, Erfolg, knapp daneben (SG − 1, SG − 2), Fehlschlag. Ticks nur bei Erfolg.
4. Solange eine Probe offen ist, wird jede andere Aktion abgelehnt, auch von anderen Spielfiguren.
5. Ein Wurf ohne offene Probe, oder von einer anderen Spielfigur, wird abgelehnt.
6. SG und Bonus gelten, wie sie dem Spieler gezeigt wurden, auch wenn sich der Skill dazwischen ändert.
7. Ticks werden zum Skill-Aufstieg (mit Übertrag); der 10. Aufstieg bringt Level 2 und 1 Attributspunkt.
8. Routine (SG − Bonus ≤ 5) bringt keine Ticks.
9. Derselbe Wurf zweimal liefert das gespeicherte Ergebnis ohne neue Wirkung; ein Fehler beim Anwenden
   rollt alles zurück, die Probe bleibt offen.
10. Veraltete Revision beim Wurf: abgelehnt, die Probe bleibt offen.
11. Die offene Probe überlebt das Schließen und Neuöffnen der Datenbank.
12. Unbekannter Skill: abgelehnt, keine Probe.

Noch nicht in 1b: Vergleichsproben mit NPC-Wurf, die Regel „einmal Ticks pro Skill und Szene“, Zeit für
freie Versuche, Folgen von Patzer und knapp daneben (siehe „Offen“ in ENTSCHEIDUNGEN.md).
