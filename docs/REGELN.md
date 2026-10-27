# Regeln

Regel-Engine von ValarRP. Grundlage ist die Engine aus einem früheren Rollenspiel des Autors,
angepasst nach [ENTSCHEIDUNGEN.md](ENTSCHEIDUNGEN.md). Die IDs bleiben stabil, der Code verweist darauf.

Was hier steht, rechnet der **Code**. Das Sprachmodell erzählt nur.

## Ablauf einer Probe

1. Der Spieler beschreibt, was er tut.
2. Das Modell schlägt Skill, Schwierigkeitsstufe (DIFF) und einen kurzen Grund vor.
   Bei einer Vergleichsprobe würfelt der Code für den NPC; dessen Ergebnis ist der SG.
3. Der Code prüft den Vorschlag, rechnet SG und Bonus und zeigt dem Spieler: Skill, SG, Grund und den
   Einsatz (was Erfolg an Geld oder Ware bringt, was ein Fehlschlag kostet; Entscheidung 231).
   Der Spielzug steht jetzt auf `awaiting_roll`. Die Handlung ist angehalten.
4. Der Spieler würfelt **echt und analog** einen W20 und tippt das Ergebnis ein (1–20).
5. Der Code rechnet Bonus und Malus dazu, bestimmt den Ausgang, vergibt Ticks und wendet Folgen an.
6. Das Modell erzählt passend zum Ausgang weiter.

Es ist immer nur **eine** Probe offen.

## 1. Kern-Engine (MECH)

| ID | Name | Regel | Beschreibung |
|---|---|---|---|
| MECH-01 | Basis-Wurf | W20 + ATTR-MOD + SKILL-BONUS | Standard für alle Proben. |
| MECH-02 | Skill-Bonus | abrunden(SKILL / 10) | Skill 18 → +1, Skill 20 → +2. |
| MECH-03 | Kritischer Erfolg | natürliche 20 | Automatischer Erfolg, doppelte Wirkung bzw. doppelter Schaden. |
| MECH-04 | Patzer | natürliche 1 | Automatischer Fehlschlag mit Folge. |
| MECH-05 | Vergleichsprobe | Spieler-Wurf gegen NPC-Wurf | Bei Konflikten (Schleichen gegen Wahrnehmung). NPC würfelt der Code. |
| MECH-06 | Schwierigkeit (SG) | aus Tabelle DIFF oder Vergleichsprobe | Muss erreicht oder übertroffen werden. |
| MECH-07 | Knapp daneben | Gesamt = SG − 1 oder SG − 2 | Erfolg mit Preis oder Haken. |
| MECH-08 | Attribut-Modifikator | abrunden((Wert − 10) / 2) | 9 → −1, 10 → 0, 11 → 0, 12 → +1. |
| MECH-09 | Zwei Leit-Attribute | höheres zählt | Bei „STR / GES“ zählt der höhere Modifikator. |

Ausgänge: **Erfolg** (Gesamt ≥ SG), **knapp daneben** (MECH-07), **Fehlschlag**.
Natürliche 20 und 1 gehen vor.

## 2. Schwierigkeit und Ticks (DIFF)

| ID | Stufe | SG | Beschreibung | Ticks bei Erfolg |
|---|---|---|---|---|
| DIFF-01 | Sehr leicht | 8 | Routine, Alltag. | +1 |
| DIFF-02 | Leicht | 10 | Für Ungeübte machbar. | +1 |
| DIFF-03 | Durchschnitt | 12 | Herausforderung unter Stress. | +2 |
| DIFF-04 | Schwer | 14 | Profi-Niveau. | +3 |
| DIFF-05 | Sehr schwer | 16 | Expertenaufgabe. | +4 |
| DIFF-06 | Extrem | 20 | Heroische Tat. | +6 |
| DIFF-07 | Legendär | 24 | Für Meister kaum zu schaffen. | +8 |

**Gegen Grinden:**
- Keine Ticks, wenn für den Erfolg höchstens eine 5 nötig war (SG − Bonus ≤ 5). Das war Routine.
- Höchstens einmal Ticks pro Skill und Szene.

## 3. Attribute (ATTR)

Wertebereich 1–20, Durchschnitt 10. Modifikator nach MECH-08.

| ID | Kürzel | Name | Relevant für |
|---|---|---|---|
| ATTR-01 | STR | Stärke | Nahkampfschaden, Tragen, Einschüchtern, Athletik |
| ATTR-02 | GES | Geschick | Fernkampf, Ausweichen, Diebeskunst, Balance, Initiative |
| ATTR-03 | KON | Konstitution | Lebenspunkte, Ausdauer, Widerstand, Körperladung |
| ATTR-04 | INT | Intelligenz | Wissen, Taktik, Handwerk, Alchemie, Erste Hilfe |
| ATTR-05 | WEI | Weisheit | Wahrnehmung, Überleben, Menschenkenntnis, Willenskraft, Ziehen |
| ATTR-06 | CHA | Charisma | Überzeugen, Anführen, Feilschen, Verführen |

## 4. Vitalwerte (VIT)

| ID | Name | Regel | Beschreibung |
|---|---|---|---|
| VIT-01 | Max. LP | KON + 10 + (Level × 2) | Schaden wird hiervon abgezogen. |
| VIT-02 | Wunden-Schwelle | 50 % der Max-LP | Einzeltreffer darüber → schwere Wunde (COND-01). |
| VIT-03 | Natürliche Heilung | KON-MOD + Level pro Nacht Schlaf | Langsam. |
| VIT-04 | K.O. und Tod | 0 LP = K.O., −10 LP = tot | K.O.: Code würfelt auf Folgen-Tabelle. Tod ist endgültig. |

## 5. Skills (SKILL)

Skala 0–100.

| ID | Kategorie | Skill | Leit-Attribut |
|---|---|---|---|
| SKILL-101 | Kampf (Nah) | Klingenwaffen | STR / GES |
| SKILL-102 | Kampf (Nah) | Wuchtwaffen | STR |
| SKILL-103 | Kampf (Nah) | Stangenwaffen | STR / GES |
| SKILL-104 | Kampf (Nah) | Waffenloser Kampf | STR / GES |
| SKILL-105 | Kampf (Fern) | Bogen | GES |
| SKILL-106 | Kampf (Fern) | Armbrust | GES |
| SKILL-107 | Kampf (Fern) | Wurfwaffen | STR / GES |
| SKILL-108 | Kampf (Taktik) | Parade / Schild | STR / KON |
| SKILL-109 | Kampf (Taktik) | Rüstungsgewöhnung | KON / STR |
| SKILL-110 | Kampf (Taktik) | Kriegskunst | INT |
| SKILL-201 | Diebeskunst | Schleichen | GES |
| SKILL-202 | Diebeskunst | Schlösser knacken | GES |
| SKILL-203 | Diebeskunst | Taschendiebstahl | GES |
| SKILL-204 | Diebeskunst | Fallenkunde | INT / GES |
| SKILL-301 | Soziales | Überzeugen | CHA |
| SKILL-302 | Soziales | Täuschen | CHA |
| SKILL-303 | Soziales | Einschüchtern | STR / CHA |
| SKILL-304 | Soziales | Feilschen | CHA / INT |
| SKILL-305 | Soziales | Menschenkenntnis | WEI |
| SKILL-306 | Soziales | Anführen | CHA |
| SKILL-307 | Soziales | Verführen | CHA |
| SKILL-401 | Wissen | Erste Hilfe | INT / WEI |
| SKILL-402 | Wissen | Überleben | WEI |
| SKILL-403 | Körper | Athletik | STR / KON |
| SKILL-404 | Körper | Akrobatik | GES |
| SKILL-405 | Wissen | Wahrnehmung | WEI |
| SKILL-406 | Handwerk | Handwerk (Allgemein) | INT / GES |
| SKILL-407 | Handwerk | Alchemie | INT |
| SKILL-408 | Handwerk | Naturkunde | INT / WEI |
| SKILL-409 | Wissen | Monsterkunde | INT / WEI |
| SKILL-501 | Energie | Ziehen *(nur mit Begabung)* | WEI |

## 6. Fortschritt (PROG)

| ID | Stufe | Skill-Bereich | Ticks für +1 Skillpunkt |
|---|---|---|---|
| PROG-01 | Novize | 0–20 | 3 |
| PROG-02 | Lehrling | 21–40 | 5 |
| PROG-03 | Geselle | 41–60 | 8 |
| PROG-04 | Experte | 61–80 | 12 |
| PROG-05 | Meister | 81–100 | 20 |

**Charakter-Level:** 10 Skill-Aufstiege (beliebige Skills) = +1 Level und +1 Attributspunkt (max. 20).
Startlevel ist 1. Von 0 auf 100 in einem Skill braucht es rund 960 Ticks.

## 7. Kampf (COMB)

„ATTR“ meint hier immer den Modifikator.

| ID | Name | Regel | Beschreibung |
|---|---|---|---|
| COMB-01 | Initiative | W20 + GES-MOD | Reihenfolge. |
| COMB-02 | Angriff | W20 + ATTR-MOD + SKILL-BONUS | Muss die VW des Gegners erreichen. |
| COMB-03 | Verteidigung (VW) | 10 + GES-MOD + Schild | Passiver Schutzwert. |
| COMB-04 | Schaden | Waffe + ATTR-MOD − RS | Netto-Schaden = LP-Verlust. |

## 8. Waffen (WEP)

| ID | Name | Schaden | Attribut | Besonderheit |
|---|---|---|---|---|
| WEP-001 | Faust | 1W2 | STR | Nicht tödlich. |
| WEP-002 | Dolch | 1W4 | GES | Leicht, wurfbar. |
| WEP-003 | Kurzschwert | 1W6 | GES / STR | Schnell. |
| WEP-004 | Langschwert | 1W8 | STR | Standard. |
| WEP-005 | Zweihänder | 1W10 | STR | Zweihändig. |
| WEP-006 | Axt / Beil | 1W6 | STR | Bonus gegen Holz und Schilde. |
| WEP-007 | Streitkolben | 1W8 | STR | Rüstungsbrechend (RS −1). |
| WEP-008 | Kriegshammer | 1W12 | STR | Zweihändig, RS −2. |
| WEP-009 | Speer | 1W8 | GES / STR | Reichweite. |
| WEP-010 | Hellebarde | 1W10 | STR | Zweihändig, Reichweite. |
| WEP-011 | Kurzbogen | 1W6 | GES | Schnellfeuer. |
| WEP-012 | Langbogen | 1W8 | GES | Reichweite, braucht STR 12. |
| WEP-013 | Armbrust (leicht) | 1W8 | – | Nachladen 1 Runde. |
| WEP-014 | Armbrust (schwer) | 1W10 | – | Nachladen 2 Runden, durchschlagend. |

## 9. Rüstung (ARM)

| ID | Name | RS | GES-Malus |
|---|---|---|---|
| ARM-001 | Stoff | 0 | 0 |
| ARM-002 | Leder / Fell | 1 | 0 |
| ARM-003 | Beschlagen | 2 | −1 |
| ARM-004 | Kette | 3 | −2 |
| ARM-005 | Schuppe | 4 | −3 |
| ARM-006 | Platte | 5 | −4 |
| ARM-007 | Holzschild | +1 VW | 0 |
| ARM-008 | Turmschild | +3 VW | −2 |

Platte bleibt bewusst stark; Wuchtwaffen sind der Konter.

## 10. Zustände (COND)

| ID | Kategorie | Auslöser | Wirkung |
|---|---|---|---|
| COND-01 | Wunde | Treffer über Wunden-Schwelle | −2 auf alle Proben, Bewegung halbiert. |
| COND-02 | Blutung | Schnittwunde | 1W4 LP pro Zeitabschnitt. |
| COND-03 | K.O. | 0 LP / Kopftreffer | Handlungsunfähig. |
| COND-04 | Gift | giftige Pflanzen und Tiere | Stündlich KON-Probe, sonst Schaden oder Übelkeit. |
| COND-05 | Erschöpfung | keine Rast | Stufenweise Attributs-Abzüge (−1 bis −5). |
| COND-06 | Durst | Wassermangel | KON sinkt stark, Halluzinationen. |
| COND-07 | Hunger | keine Tagesration | STR sinkt, Schwäche. |
| COND-08 | Kälte | kaltes Klima | GES sinkt, Erfrierung (1W6). |
| COND-09 | Hitze | heißes Klima | INT sinkt, Hitzschlag (1W4). |
| COND-10 | Sturmbrand | Überladung beim Ziehen, Sturm ohne Schutz | Schaden, Fieber oder Wahn; kann Mutation auslösen. *(Zahlen offen)* |

## 11. Materialien (MAT)

Aus der alten Engine übernommen. **Noch prüfen**, ob sie zu Valar passen.

| ID | Quelle | Für | Wirkung |
|---|---|---|---|
| MAT-01 | Eisen-Eiche | Schilde | +1 Haltbarkeit. |
| MAT-02 | Chitin-Panzer | Rüstung | Leichter (halbe Behinderung). |
| MAT-03 | Meteoriten-Erz | Klingen | +1 Schaden, rostfrei. |
| MAT-04 | Obsidian | Spitzen | +2 Schaden, zerbrechlich (Patzer). |
| MAT-05 | Seide | Kleidung | Hitzeresistenz. |

## 12. Energie (ENRG)

Zahlen sind noch offen; hier steht nur das Gerüst.

- **Kristalle** haben Ladung und Reinheit. Sie entladen sich langsam, reinere langsamer.
- **Körperladung** (nur Ziehende): Obergrenze hängt von KON ab. Mehr ziehen als halten → COND-10.
- **Wirkungen:** Stoß, Glut, Schild, Lebensstrom, Spüren. Jede kostet Ladung, braucht eine Probe
  (Ziehen oder Artefakt-Nutzung) und hat einen festen Effekt. Mehr Ladung → mehr Wirkung.
- **Artefakte:** ein Gegenstand, eine Wirkung, Ladung aus eingesetztem Kristall.

## 13. Charaktererschaffung (CHAR)

- **Attribute:** alle starten bei 8, dazu 18 Punkte. Höchstens 15 zum Start.
- **Skills:** 2 Hauptskills auf 30, 4 Nebenskills auf 15, alle anderen 0.
- **Ziehen** kostet einen Hauptskill-Platz.
- **Hintergrund:** frei erzählt, wird weicher Zustand (Herkunft, Kontakte, Feinde).
- **Ausrüstung:** knappes Startbudget aus der Preisliste plus ein persönlicher Gegenstand ohne großen Wert.

Beispiel: Hauptskill 30 (+3) und Attribut 14 (+2) = +5. Gegen SG 12 reicht eine 7, also etwa 70 %.

## 14. Geld (GELD)

1 Gulden = 10 Groschen = 100 Pfennig. Gespeichert wird in Pfennig als ganze Zahl.

| Anker | Preis |
|---|---|
| Ein Tag Arbeit als Tagelöhner | 2 Groschen |
| Langschwert | 2 Gulden |
| Kleiner, mittelreiner Kristall | 1 Gulden (etwa eine Woche Lohn) |
| Startbudget | etwa 3 Gulden |

Einsatz einer Probe (Entscheidungen 196 und 231): höchstens so viel, wie die Quelle hat (ein NPC seinen Beutel),
und höchstens nach Stufe: DIFF-01 5, DIFF-02 10, DIFF-03 30, DIFF-04 1 Gulden, DIFF-05 3 Gulden, DIFF-06 10 Gulden,
DIFF-07 30 Gulden. Kritisch: Geld doppelt, nie über die Quelle. Fehlschlag mit Verlust: die Hälfte.
