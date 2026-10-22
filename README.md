# ValarRP

Text-Rollenspiel in der eigenen Welt **Valar**: rau, düster und politisch wie The Witcher,
mit Magie aus Stürmen und Kristallen. Ein Sprachmodell erzählt als Spielleiter,
der Code führt den Spielstand in SQLite.

- Welt und Entscheidungen: [docs/ENTSCHEIDUNGEN.md](docs/ENTSCHEIDUNGEN.md)
- Regel-Engine: [docs/REGELN.md](docs/REGELN.md)

## Grundidee

Das Modell **fordert Aktionen an**, der Code **bestimmt ihre Folgen**.
Aktionen sind Pydantic-Modelle. Bis Schritt 1i kommen sie aus Tests und der Kommandozeile.
Ab Schritt 2 füllt das Modell genau diese Schemas aus.

## Fahrplan

**Ziel ist der spielbare Kern** (Entscheidung 54): Ein Mensch spielt im Terminal mit dem Modell als
Spielleiter in einem kleinen Testgebiet. Die Figur wird erschaffen, kann sich umsehen, gehen, untersuchen,
reden, sammeln, frei etwas versuchen, Vergleichsproben machen, Aufträge abgeben und Tage vergehen lassen.
Kampf und Handel gehören nicht zum Kern; bis dahin laufen sie als freier Versuch (Entscheidung 8).

| Schritt | Inhalt | Stand |
|---|---|---|
| 1 | Kern ohne Modell: Transaktion, Idempotenz, Revision, Abgabe-Quest | fertig |
| 1b | Probenablauf: offene Probe, Wurf, Ticks, Skill-Aufstieg | fertig |
| 1c | Orte, Bewegen, Szene, Dauer freier Versuche | fertig |
| 1c2 | Koordinaten, Reisezeit aus der Karte, Karte als GeoJSON | fertig |
| 1d | Kommandozeile ohne Modell | fertig |
| 1d2 | Welt-Wiki im OKF-Format: Lore, Regionen, Vorkommen | fertig |
| 1e | Zufall, Probe mit Folgen: Untersuchen und Sammeln aus dem Wiki | fertig |
| 1f | NPCs, Reden, weicher Zustand, Kampagnen-Seiten im Wiki | fertig |
| 1g | Vergleichsprobe mit NPC | fertig |
| 1h | Tageswechsel mit Folgen: Schlafen, Ration, Hunger, Erschöpfung, Wetter, erzählte Knappheit | fertig |
| 1i | Charaktererschaffung, Attributspunkte verteilen | fertig |
| 1j | Kontextbauer: Auswahl, Budget und Suche für das Modell, ohne Modell | fertig |
| 2a | Modell-Client: Prüfung gegen Schemas, Wiederholung, Kostenbremse | fertig |
| 2b | Spielleiter-Schleife: freier Text → Aktion → Code → Erzählung | fertig |
| 3 | Weboberfläche: Docker auf der Synology, Zugriff über Tailscale; vorgezogen (Entscheidung 167) | fertig |
| 2c | Härtetest mit 2–3 Modellen, Modellwahl | als Nächstes, dann **spielbarer Kern** |

Jeder Schritt bringt Szenario-Tests (wie `test_turn_in_quest.py`) und Unit-Tests je Baustein.
Immer mitgeprüft werden: Ablehnung ohne Wirkung, Wiederholung derselben `request_id` ohne neue Wirkung,
Rollback bei Fehler, Bestand nach Neuöffnen der Datenbank, `roll_pending` bei offener Probe.
Das Schema bekommt je Schritt eine neue Version, ohne Migration (Entscheidung 52).

### 1c: Orte, Bewegen, Szene, Dauer

Ziel: Die Figur bewegt sich zwischen Orten des Testinhalts, die Uhr läuft, der Code beendet Szenen,
Ticks gibt es höchstens einmal je Skill und Szene, freie Versuche kosten Zeit.
Entscheidungen 53, 55 bis 59.

1. Bewegen zu einem verbundenen Ort: Ort wechselt, Reisezeit aus dem Inhalt, Revision +1.
2. Unbekannter oder nicht verbundener Ort, oder der eigene Ort: abgelehnt, nichts ändert sich.
3. Ort ↔ Unterort funktioniert wie jede Verbindung, mit Dauer aus dem Inhalt.
4. Ortswechsel beginnt eine neue Szene. Mitternacht beginnt eine neue Szene, auch ohne Ortswechsel.
5. Zweiter Erfolg im selben Skill und derselben Szene: keine Ticks, schon beim Öffnen so gezeigt.
   Anderer Skill oder neue Szene: wieder Ticks. Ein Fehlschlag verbraucht die Szene nicht.
6. Der Versuch nennt eine Dauer (Augenblick, Stunde, halber Tag); der Wurf schreibt 5, 60 oder 360 Minuten gut.
   Ein Versuch über Mitternacht beendet danach die Szene.
7. Szene oder Minuten in der Aktion (Zusatzfelder): abgelehnt.
8. Umsehen und Zustand anzeigen sind Abfragen: kein Spielzug, Revision und Zeit bleiben gleich.

Abhängig von: nichts Neuem.

### 1c2: Koordinaten und Karte

Ziel: Alles liegt auf einer Karte, Reisezeiten kommen aus ihr, die Karte lässt sich mit üblichen Werkzeugen
ansehen und bearbeiten. Entscheidungen 75 bis 79.

1. Jeder Ort hat einen eigenen Punkt in Metern, auch jeder Unterort.
2. Reisezeit aus Weglänge und Wegart (Straße, Pfad, Gelände); das Bewegen schreibt genau diese Zeit gut.
3. Eine feste Reisezeit im Inhalt geht vor.
4. Zwischenpunkte verlängern den Weg.
5. Umsehen zeigt je Ausgang Himmelsrichtung und Entfernung, ohne Spielzug.
6. GeoJSON hin und zurück ergibt denselben Inhalt. Ein verschobener Ort ändert die Reisezeit.
   Unbekannte Orte, Wegarten oder Punkte außerhalb der Karte: abgelehnt.
7. `docs/testgebiet.geojson` passt zum Inhalt.

Abhängig von: 1c.

### 1d: Kommandozeile ohne Modell

Ziel: Spielen im Terminal ohne Modell, als Werkzeug für Autor und Tests (Entscheidung 65).
Kampagne aus dem Testinhalt anlegen (feste Testfigur), Befehle für alle Aktionen, Anzeige von Ort, Tag
und Uhrzeit, Figur, Geld in Gulden/Groschen/Pfennig und offener Probe.

1. Jeder Befehl wird zur richtigen Aktion (z. B. `gehe`, `schau`, `versuche`, `wurf 14`, `abgeben`).
2. Falsche Eingabe: Hilfetext, kein Spielzug.
3. Jede Eingabe bekommt eine neue `request_id`. Veraltete Revision: Stand neu lesen, mit neuer `request_id` erneut.
4. Ein ganzer Ablauf läuft per Skript über die Standardeingabe.
5. Beenden und neu starten: Spielstand und offene Probe sind wieder da.

Abhängig von: 1c.

### 1d2: Welt-Wiki

Ziel: Ein wachsendes Wiki im OKF-Format, aus dem der Code Felder und das Modell Text liest.
Entscheidungen 87 bis 99.

1. Jede Seite hat Felder und Text. Fehlender Typ, unbekannte Felder im Block `valar` oder falsche Werte:
   Laden scheitert mit Datei und Grund. Andere Felder sind nach OKF frei.
2. Links auf fehlende Seiten werden gemeldet, ohne Fehler. `index.md` je Ordner passt zu den Seiten.
3. Orte im Inhalt zeigen auf eine Ort-Seite, die Region und Landschaft nennt; ein Verweis ins Leere
   ist ein Fehler.
4. Abfrage: Welche Seiten gehören zu einem Ort (Ort, Region, Landschaft, was dort vorkommt)?
5. Erste Kanon-Seiten: Rauhenau, Sturmhagen, Glasgrund, Lindenheim, die Mächte, Grollwetter.

Abhängig von: 1c2. Die Seiten selbst können über Übergaben entstehen.

### 1e: Zufall, Probe mit Folgen, Untersuchen und Sammeln

Ziel: Aktionen mit eigenem Resolver und Probe, auf Grundlage des Wikis (Entscheidung 92). Skill und Stufe
kommen aus dem Inhalt, die Folgen je Ausgang hält die offene Probe fest (Entscheidung 61). Der Code würfelt
mit gespeichertem Seed (Entscheidung 62).

1. Zufall: gleicher Seed, gleiches Ergebnis. Was der Code würfelt, steht im Spielzug; eine Wiederholung würfelt nicht neu.
2. Untersuchen ohne Stufen: Die Figur bekommt den Text der Seite, nichts Hartes ändert sich.
3. Seiten mit Stufen (Entscheidung 64): Eine Stufe mit SG ≤ 10 + Bonus ist ohne Wurf sichtbar, auch beim
   Umsehen. Aktiv untersuchen probt im Skill der nächsten unbekannten Stufe; das Gesamt öffnet alle Stufen bis
   zu seiner Höhe, für diese Figur dauerhaft. Bekanntes wird nicht noch einmal geprobt.
4. Versteckte Dinge sind Stufen mit Wahrnehmung.
5. Sammeln: Was laut Wiki an Region oder Landschaft des Orts vorkommt. Skill, Stufe und Menge aus Feldern und
   Häufigkeit, Menge gewürfelt, Zeit vergeht. Ohne Wiki-Seite, kein Gegenstand oder kommt hier nicht vor: abgelehnt.
6. Knapp daneben und Patzer zählen hart als Fehlschlag (Entscheidung 60); die Zeit vergeht trotzdem.
7. Die Folgen gelten beim Wurf so, wie sie beim Öffnen gespeichert wurden, auch wenn sich Inhalt oder Werte ändern.

Abhängig von: 1c, 1d2. Umgesetzt nach den Entscheidungen 109 bis 114.

### 1f: NPCs, Reden, weicher Zustand

Ziel: NPCs nach Entscheidung 11, gespeichert erst beim Ansprechen. Erste enge Aktion für weichen Zustand
(`record_fact`, Entscheidung 8).

1. Neuen NPC ansprechen: Vorlage aus der festen Liste, Name, Beschreibung. Der Code legt Werte aus Vorlage
   und Zufall an, der NPC steht am Ort der Figur.
2. Unbekannte Vorlage oder Werte vom Modell (Zusatzfelder): abgelehnt.
3. Bekannten NPC am selben Ort ansprechen: angenommen, Dauer aus dem Inhalt. An einem anderen Ort: abgelehnt.
4. Wichtige Figuren aus dem Inhalt kann das Modell weder anlegen noch verändern.
5. `record_fact` schreibt nur weichen Zustand, mit Längengrenze; harter Zustand bleibt unberührt.
6. Neue Kampagnen-Seite im Wiki (Entscheidung 91): Vorlage, Name, Text mit Längengrenze; harte Felder setzt
   der Code aus der Vorlage. Felder vom Modell oder unbekannte Vorlage: abgelehnt. Die Seite gilt nur in
   dieser Kampagne und lässt sich als OKF-Datei ausgeben.

Abhängig von: 1e (Zufall). Umgesetzt nach den Entscheidungen 115 bis 120.

### 1g: Vergleichsprobe mit NPC

Ziel: MECH-05 nach Entscheidung 63.

1. Der NPC würfelt beim Öffnen. Sein Gesamt ist der SG und wird vor dem Wurf gezeigt.
2. Der Gegen-Skill kommt aus der Paar-Tabelle. Skill ohne Paar, oder NPC nicht am Ort: abgelehnt.
3. Gleichstand gewinnt der Spieler.
4. Die Ticks richten sich nach der DIFF-Stufe des SG; Routine- und Szenenregel gelten.
5. Der NPC-Wurf bleibt bei Wiederholung und nach Neuöffnen gleich, auch wenn sich NPC-Werte ändern.

Abhängig von: 1e, 1f. Paar-Tabelle: Entscheidung 100. Umgesetzt nach den Entscheidungen 125 bis 128.

### 1h: Tageswechsel mit Folgen

Ziel: Entscheidung 12, schlank (Entscheidung 57). Schlafen, Ration oder Hunger, Erschöpfung, Wetter ohne Stürme.

1. Schlafen bis zum Morgen; die Dauer bestimmt der Code.
2. Je Mitternacht genau einmal: Ration abziehen, sonst Hunger. Wetter würfeln, im Zustand sichtbar.
3. Ein Zug über zwei Mitternächte löst beide Tageswechsel aus.
4. Ohne Schlaf entsteht Erschöpfung in Stufen.
5. Hunger und Erschöpfung wirken als Malus und sind im Bonus beim Öffnen schon eingerechnet.
6. Erzählte Knappheit (Entscheidung 123): Das Modell setzt sie mit Ablaufzeit für ein Ding an einem Ort.
   Sammeln wird schwerer oder abgelehnt; nach Ablauf gilt sie nicht mehr. Erleichtern kann das Modell nichts.

Abhängig von: 1c, 1e. Zahlen: Entscheidung 101. Umgesetzt nach den Entscheidungen 129 bis 135.
Heilung kommt mit dem Kampf, vorher gibt es keinen Schaden.

### 1i: Charaktererschaffung

Ziel: CHAR aus REGELN.md über die Kommandozeile. Attributspunkte aus dem Vorrat verteilen (Entscheidung 49).

1. Punktekauf: alle 8, dazu 18 Punkte, höchstens 15. Abweichung: abgelehnt.
2. Skills: 2 Hauptskills auf 30, 4 Nebenskills auf 15. Ziehen bleibt gesperrt, bis es Zahlen für Energie gibt.
3. Startbudget und Startausrüstung aus einer Testliste; Hintergrund wird weicher Zustand.
4. Attributspunkte verteilen: nur aus dem Vorrat, höchstens 20.

Abhängig von: 1d. Die echte Preisliste ist nicht nötig. Umgesetzt nach den Entscheidungen 148 bis 152.

### 1j: Kontextbauer

Ziel: Der Code baut den Kontext eines Zugs für das Modell, klein genug für kleine Modelle (Entscheidungen 137
bis 143). Noch ohne Modell.

1. Gleicher Spielstand, gleicher Kontext. Die Reihenfolge folgt der Rangfolge aus Entscheidung 138.
2. Das Budget wird nie überschritten; was wegfällt, steht im Spielzug.
3. Ort, anwesende NPCs, Gepäck und Aufträge lösen ihre Seiten aus; Name oder Alias in der Eingabe ebenso.
4. Standard ist der eine Satz je Seite; den ganzen Text gibt es nur für Ort und Gegenstand des Zugs.
5. Nachschlagen findet Abschnitte über Titel, Alias und Text (FTS5) und liefert nie eine lange Seite ganz.
6. Wissensstufen, die die Figur nicht kennt, und Geheimnisse von NPCs außerhalb der Szene stehen nie im Kontext.
7. Nur erlaubte Aktionen werden angeboten; bei offener Probe nur Erzählen, würfeln tut der Spieler.

Abhängig von: 1d2 bis 1h. Umgesetzt nach den Entscheidungen 144 bis 147.

### 2a: Modell-Client

Ziel: Entscheidungen 4 und 5, nach Entscheidung 67. Getestet wird ohne Netz, gegen eine Attrappe.

1. Gültige Antwort wird zur Aktion. Ungültig oder mit Zusatzfeldern: genau ein Wiederholungsversuch,
   dann Fehler. Der Spielstand ändert sich nie.
2. Kosten werden mitgezählt. Ist das Monatsbudget erreicht, stoppt das Spiel vor dem nächsten Aufruf.
3. Der Schlüssel kommt nur aus der Umgebung und steht nie in Datenbank, Protokoll oder Fehlermeldung.
4. Zeitüberschreitung oder Netzfehler: Fehler ohne Wirkung.

Abhängig von: nichts im Code. Anbieter und Budget: Entscheidung 153 (Gemini und Mistral kostenlos, dann DeepSeek oder DeepInfra; 10 bis 20 €).
Umgesetzt nach den Entscheidungen 154 bis 157.

### 2b: Spielleiter-Schleife

Ziel: Der Spieler schreibt frei. Das Modell wählt eine Aktion, der Code löst auf, das Modell erzählt
das Ergebnis (Entscheidung 34). Proben werden angekündigt, gewürfelt wird in der Kommandozeile.

1. Ein Zug mit der Attrappe: Aktion, Auflösung, Erzählung.
2. Das Modell kann keine harten Werte setzen; die Erzählung bekommt nur das Ergebnis des Codes.
3. Das Modell bekommt nur Wissen, das die Figur hat (Entscheidung 64).
4. Eine wiederholte `request_id` ruft das Modell nicht noch einmal auf.

Abhängig von: 1c bis 1j, 2a. Gedächtnis und Rahmen-Prompt: Entscheidungen 158 bis 162.
Umgesetzt nach den Entscheidungen 163 bis 166.

### 3: Weboberfläche (vorgezogen)

Ziel: Spielen im Browser statt im Terminal, Schlüssel direkt eingeben (Entscheidungen 2, 167 bis 169).
Spielen mit Spielleiter (freier Text, Würfeln), Zustand und Umsehen, Figur erschaffen, Schlüssel und Budget verwalten.
Ein Schlüssel liegt in einer eigenen Datei im Datenordner und wird nie wieder angezeigt.

1. Ohne Figur führt die Startseite zur Erschaffung. Das Formular legt die Figur an; eine Abweichung zeigt den Grund,
   die Eingaben bleiben stehen, nichts wird angelegt.
2. Freier Text geht an den Spielleiter: Erzählung und harte Folgen stehen auf der Seite, der Verlauf bleibt. Bei
   offener Probe gibt es ein Würfelfeld, der Wurf wird erzählt. Ein Fehler des Modells wird gezeigt, nichts passiert.
3. Jedes Formular trägt eine eigene `request_id`. Doppelt abgeschickt: kein neuer Aufruf, kein neuer Spielzug,
   dieselbe Anzeige. Mit anderer Eingabe oder ohne gültige `request_id`: abgelehnt. Fehlt die Erzählung, holt ein
   Knopf sie mit derselben `request_id` nach.
4. Umsehen, Zustand, Notizen und Hilfe sind Abfragen ohne Spielzug. Ohne `modelle.toml` gelten die Befehle der
   Kommandozeile.
5. Schlüssel setzen, ändern, löschen. Die Seite zeigt nur die letzten vier Zeichen; der Schlüssel steht nie in HTML
   oder Datenbank. Die Datei hat die Rechte 0600. Die Umgebung geht vor; sonst nimmt der Client den aus der Datei.
6. Budget: Kosten des Monats sehen, Grenzen ändern; Unsinn wird abgelehnt. Neue Grenzen gelten sofort.
7. Schutz: unbekannter Hostname, POST von fremder Herkunft, zu große oder fremde Formulare: abgelehnt, nichts passiert.
   Text des Modells wird maskiert; strenge Sicherheitskopfzeilen, kein JavaScript.
8. Nach einem Neustart des Servers sind Verlauf und offene Probe wieder da.

Abhängig von: 2b. Umgesetzt nach den Entscheidungen 171 bis 177.

### 2c: Härtetest

Ziel: Entscheidung 6. 3–4 Testszenen an 2–3 Modelle, bewertet nach Schema-Treue, Regeltreue und Erzählung.
Ergebnis ist die Modellwahl als neue Entscheidung. Damit ist der Kern spielbar.

Erst der Ablauf, ohne Netz gegen eine Attrappe geprüft; dann der Lauf in einer Cloud-Sitzung mit Schlüsseln
(Entscheidung 170).

1. Vier feste Szenen (Ort, Auftrag und Wege; Probe und Wurf; Leute; Grenzen), jede in frischem Spielstand mit der
   Testfigur. Gleiche Antworten ergeben denselben Bericht.
2. Schema-Treue aus dem Kostenprotokoll: gültige Antworten, ungültige, Fehler. Ein Fehler beendet nur den Schritt.
3. Regeltreue: gewählte Aktion gegen die erwarteten, abgelehnte Züge. Bleibt eine Probe unerwartet offen, würfelt
   der Ablauf eine 10 und wertet sie nicht.
4. Bericht als Markdown: Übersicht je Modell, dann jeder Schritt mit der Erzählung wörtlich, zum Bewerten von Hand.
5. Jeder Anbieter allein, ohne Kette; ohne Schlüssel übersprungen. Das Budget gilt über den ganzen Lauf.
6. Schlüssel nur aus der Umgebung, nie im Bericht.

```bash
cp haertetest.example.toml haertetest.toml   # Modelle und Preise beim Anbieter nachsehen
python -m valar.benchmark --modelle haertetest.toml --bericht docs/haertetest/2026-10.md
```

Umgesetzt nach den Entscheidungen 178 bis 182.

### Bewusst später

- **Kampf** (COMB, Waffen, Rüstung, LP-Schaden, Heilung, K.O.-Folgen, Tod und neue Figur): erster Schritt nach dem Kern.
- **Handel** (Kaufen, Verkaufen, Feilschen, Preisliste): zweiter Schritt nach dem Kern.
- **Weltinhalt** der Startregion (Namen, Orte, wichtige NPCs): eigene Sitzungen, jederzeit möglich; der Kern läuft mit Testinhalt.
- Energie und Magie (Kristall-Ladung, Ziehen, fünf Wirkungen, Artefakte, Sturmbrand), Stürme, Monster,
  Mutationen, Elementwesen, Kirche und Ketzerei.
- Materialien und Handwerk.
- Migrationen für echte Spielstände.

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
  queries.py     # Abfragen ohne Spielzug: Umsehen, Zustand
  geo.py         # Karte als GeoJSON: Export, Rückübernahme, Kommandozeile
  wiki.py        # Welt-Wiki (OKF): Laden, Prüfen, Seiten zu einem Ort, index.md, Übergaben einlesen
  search.py      # Nachschlagen: Volltextsuche (FTS5) über Abschnitte, erweiterbar um Vektorsuche
  context.py     # Kontextbauer: was das Modell je Zug bekommt, mit Budget
  character.py   # Charaktererschaffung: Bogen prüfen (Punktekauf, Skills, Ausrüstung)
  llm.py         # Modell-Client: Anbieter-Kette, Schema-Prüfung, Wiederholung, Kosten und Budget
  gm.py          # Spielleiter-Schleife: Aufruf A (Aktion), Code, Aufruf B (Erzählung), Verlauf und Chronik
  spielleiter.md # Rahmen-Prompt (Entscheidung 162)
  cli.py         # Kommandozeile ohne Modell: deutsche Befehle, Skriptbetrieb
  web.py         # Weboberfläche: WSGI aus der Standardbibliothek, Formulare ohne JavaScript
  keys.py        # Schlüssel und Budget-Grenzen aus der Weboberfläche, als Dateien im Datenordner
  benchmark.py   # Härtetest: Testszenen gegen mehrere Modelle, Bericht als Markdown
tests/
  test_turn_in_quest.py   # Szenario: die acht Tests unten, plus Entscheidungen 37 und 39
  test_roll_flow.py       # Szenario Schritt 1b: der Probenablauf
  test_move_and_scene.py  # Szenario Schritt 1c: Orte, Bewegen, Szene, Dauer
  test_map.py             # Szenario Schritt 1c2: Koordinaten, Reisezeit, GeoJSON
  test_command_line.py    # Szenario Schritt 1d: Kommandozeile
  test_world_wiki.py      # Szenario Schritt 1d2: Welt-Wiki
  test_examine_and_gather.py  # Szenario Schritt 1e: Zufall, Untersuchen, Sammeln
  test_npcs_and_facts.py  # Szenario Schritt 1f: NPCs, weicher Zustand, Kampagnen-Seiten
  test_contest.py         # Szenario Schritt 1g: Vergleichsprobe mit NPC
  test_day_change.py      # Szenario Schritt 1h: Tageswechsel, Schlaf, Wetter, Knappheit
  test_character_creation.py  # Szenario Schritt 1i: Erschaffung, Attributspunkte verteilen
  test_context.py         # Szenario Schritt 1j: Kontextbauer, Nachschlagen, Suchqualität
  test_model_client.py    # Szenario Schritt 2a: Modell-Client gegen eine Attrappe, ohne Netz
  test_game_master.py     # Szenario Schritt 2b: Spielleiter-Schleife gegen eine Attrappe, ohne Netz
  test_web.py             # Szenario Schritt 3: Weboberfläche als WSGI, ohne Netz und ohne Browser
  test_benchmark.py       # Szenario Schritt 2c: Ablauf des Härtetests gegen eine Attrappe, ohne Netz
  data/suchfragen.json    # Testfragen an das Welt-Wiki, Maßstab für die Suche
  unit/                   # Unit-Tests je Baustein: models, content, db, rules, resolve, apply, turns, queries, geo, cli, wiki
welt/                     # Welt-Wiki im OKF-Format: echter Weltinhalt, je Ordner eine index.md
docs/
  testgebiet.geojson      # Karte des Testgebiets, von einem Test aktuell gehalten
  helpers.py, conftest.py # gemeinsame Hilfen und Fixtures
.github/workflows/
  tests.yml               # CI: pytest auf Python 3.12 und 3.13, Docker-Abbild bauen
Dockerfile, docker-compose.yml  # Betrieb auf der Synology (Schritt 3)
```

Alle Tabellen bekommen `campaign_id`; Spieler haben eine `player_id`.

## Tests ausführen

```bash
pip install pydantic pyyaml pytest
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

Noch nicht in 1b: die Regel „einmal Ticks pro Skill und Szene“ und Zeit für freie Versuche (kommen in 1c),
Vergleichsproben mit NPC-Wurf (1g). Patzer und knapp daneben wirken nur in der Erzählung (Entscheidung 60).

## Im Browser spielen

Zum Ausprobieren auf dem eigenen Rechner:

```bash
python -m valar.web --daten daten   # dann http://localhost:8000 öffnen
```

Der Datenordner enthält den Spielstand `spiel.db`, die Modell-Einstellungen `modelle.toml` (Vorlage
`modelle.example.toml`), die in der Seite eingegebenen Schlüssel (`schluessel.json`) und geänderte Budget-Grenzen
(`budget.json`). Ohne `modelle.toml` gibt es keinen Spielleiter, dann gelten die Befehle der Kommandozeile.
Schlüssel lassen sich unter „Einstellungen“ eintragen; eine Umgebungsvariable geht vor.

**Auf der Synology** (Docker, nur über Tailscale; Entscheidung 175):

1. Tailscale auf der Synology einrichten (Paketzentrum) und ihre Tailscale-Adresse (`100.…`) und ihren Namen in der
   Tailscale-Verwaltung nachsehen.
2. Das Repo in einen Ordner auf der Synology legen, z. B. `/volume1/docker/valarrp`, und dort eine Datei `.env`
   anlegen:

   ```bash
   TAILSCALE_IP=100.x.y.z
   VALAR_HOSTS=synology,synology.tailxxxx.ts.net,100.x.y.z
   PUID=1026   # eigene Benutzer-ID auf der Synology (id im Terminal), damit der Datenordner beschreibbar ist
   PGID=100
   ```

3. Ordner `daten` daneben anlegen, `modelle.example.toml` als `daten/modelle.toml` hineinkopieren.
4. Starten: `docker compose up -d --build` (oder im Container Manager als Projekt mit dieser `docker-compose.yml`).
5. Im Browser eines Geräts im Tailnet: `http://synology:8000` (oder der volle Name bzw. die Adresse aus `VALAR_HOSTS`).

Der Port hängt nur an der Tailscale-Adresse; aus dem Heimnetz oder dem Internet ist die Seite nicht erreichbar.
Eine Anmeldung gibt es nicht, den Zugang regelt Tailscale. Ein anderer Hostname als in `VALAR_HOSTS` wird abgelehnt.
Updates: Repo aktualisieren, dann wieder `docker compose up -d --build`; der Spielstand bleibt in `daten/`.
Läuft Tailscale beim Hochfahren der Synology noch nicht, kann Docker den Port nicht binden; dann den Container im
Container Manager neu starten.

## Spielen ohne Modell

```bash
python -m valar.cli --db spiel.db             # interaktiv; ohne Figur zuerst die Erschaffung
python -m valar.cli --db spiel.db < ablauf.txt  # ganzer Ablauf per Skript, auch die Erschaffung
python -m valar.cli --db spiel.db --testfigur   # neue Kampagne mit der Testfigur, ohne Erschaffung
python -m valar.cli --db spiel.db --neu         # eine weitere Figur erschaffen
python -m valar.cli --db spiel.db --figur Grete # mit dieser Figur spielen, wenn es mehrere gibt
```

Die Erschaffung fragt Name, Attribute (`STR 12 GES 14 …`, genau 18 Punkte), 2 Hauptskills, 4 Nebenskills,
Ausrüstung, persönlichen Gegenstand und Hintergrund. Eine falsche Antwort wird erklärt und die Frage wiederholt.

Befehle: `schau`, `status`, `gehe Sturmhagen`, `versuche Klingenwaffen 3 stunde Grund …`, `wurf 14`,
`betrachte Sturmkappe`, `untersuche Glasgrund`, `sammle Sturmkappe`, `triff Bauer "Grete Müller" Alte Bäuerin`,
`rede Grete`, `vergleiche Schleichen Grete augenblick Grund …`, `notiere gerücht …`, `notizen`, `schlafe`, `knapp Blutwurz weg woche Grund …`, `suche Frage …`, `kontext Eingabe …`, `seite heilkraut "Graue Minze" Text …`, `steigere Stärke`, `abgeben`, `hilfe`, `ende`. Der Spielstand bleibt in der Datei, auch eine offene Probe.

## Modell-Anbieter einrichten

`modelle.example.toml` nach `modelle.toml` kopieren und Modellnamen und Preise beim Anbieter prüfen. Die Schlüssel
kommen in die Umgebung, nie in eine Datei im Projekt; in der Weboberfläche lassen sie sich auch unter „Einstellungen“
eintragen (Entscheidung 168):

```bash
export GEMINI_API_KEY=…   # aistudio.google.com
export MISTRAL_API_KEY=…  # console.mistral.ai
export DEEPSEEK_API_KEY=… # platform.deepseek.com
```

## Spielen mit Spielleiter

```bash
python -m valar.cli --db spiel.db --modell modelle.toml
```

Schreib frei, was die Figur tut oder sagt. Das Modell wählt die Aktion, der Code bestimmt die Folgen, das Modell
erzählt; danach stehen die harten Folgen. Proben würfelst du selbst und tippst `wurf 14`. Ohne `!` bleiben nur
`schau`, `status`, `notizen`, `hilfe`, `ende` und `wurf ZAHL` Befehle; jeder andere Befehl beginnt mit `!`,
z. B. `!gehe Sturmhagen` (ohne Erzählung). Den Rahmen-Prompt ändert man in `valar/spielleiter.md`.

## Karte ansehen und bearbeiten

Die Karte des Testgebiets liegt in [docs/testgebiet.geojson](docs/testgebiet.geojson). GitHub zeigt die Datei
direkt als Karte. Zum Bearbeiten die Datei in [geojson.io](https://geojson.io) ziehen, Punkte oder Wege
verschieben, speichern und prüfen:

```bash
python -m valar.geo check bearbeitet.geojson   # prüft und listet geänderte Lagen und Reisezeiten
python -m valar.geo export > docs/testgebiet.geojson
```

Die Karte liegt am Nullpunkt der Erde (Entscheidung 77); darunterliegendes Meer ist bedeutungslos.

## Welt-Wiki bearbeiten

Das Wiki liegt in [welt/](welt/index.md): eine Markdown-Seite je Begriff, oben Felder, darunter Text für das
Modell. GitHub zeigt die Seiten direkt an. Neue Seiten kommen von Hand oder als Übergabe von einem anderen
Modell (Entscheidung 98):

```bash
python -m valar.wiki import uebergabe.txt   # Seiten anlegen; überschreibt nie
python -m valar.wiki check                  # Fehler und Hinweise
python -m valar.wiki index                  # index.md neu schreiben
python -m valar.wiki export spiel.db > kampagne.txt   # Kampagnen-Seiten für den Kanon ausgeben
```

Jede Änderung an `welt/` kommt über einen PR; die CI prüft das Wiki (Entscheidung 104).

**Mit Obsidian** (Entscheidung 105): Ordner `welt/` als Tresor öffnen. Die Einstellungen in
`welt/.obsidian/app.json` sorgen für Markdown-Links, die der Prüfer versteht. Abgleich mit GitHub über das Plugin
Obsidian Git. Am Handy kleine Korrekturen direkt im Browser auf GitHub.

## Testfall für Schritt 1c

Die Tests 1 bis 8 aus dem Fahrplan stehen in `tests/test_move_and_scene.py`, mit Nummern im Testnamen.
Ausgangslage: die Spielfigur aus 1b im Zunfthaus, Spielbeginn 8 Uhr. Testgebiet nach Entscheidungen 74 und 86:

```text
Zunfthaus ─5─ Sturmhagen ─5─ Schenke
                 │  │
                90  240
                 │  │
        Glasgrund ─180─ Lindenheim          (Minuten)
```

Umgesetzt nach den Entscheidungen 68 bis 73. Mitgeprüft: Bewegen bei offener Probe (`roll_pending`),
Wiederholung, veraltete Revision, Rollback beim Ankommen und beim Merken der Szene, Neuöffnen der Datenbank.
