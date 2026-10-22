# Entscheidungen

Ergebnis der ersten Planungsrunde (Oktober 2026). Jede Entscheidung mit kurzem Grund.
Regeln und Zahlen stehen in [REGELN.md](REGELN.md).

## Produkt und Technik

1. **Ein Mensch, eine Kampagne.** Tabellen bekommen trotzdem gleich `campaign_id` und `player_id`,
   weil Nachrüsten später teuer ist.
2. **Läuft als Server auf der Synology (Docker), erreichbar über Tailscale im Browser** von allen eigenen Geräten.
   GitHub Pages geht nicht: Python, SQLite und der API-Schlüssel brauchen einen laufenden Server.
   Zuerst kommt eine Kommandozeile, die Weboberfläche später.
3. **`request_id` und Revisionsprüfung sind Pflicht**, nicht Luxus: Im Browser über mobiles Netz
   kommen doppelte Anfragen wirklich vor.
4. **Sprachmodell austauschbar.** Ein Client im OpenAI-Format mit drei Einstellungen (Adresse, Modell, Schlüssel).
   Meist über OpenRouter, alternativ lokal über Ollama (Rechner mit 16 GB VRAM).
   Jede Modellantwort wird gegen die Pydantic-Schemas geprüft; ungültig → ein Wiederholungsversuch, dann Fehler.
   Der Spielstand ändert sich dabei nie.
5. **Kostenbremse im Code:** Monatsbudget wird mitgezählt, bei Erreichen stoppt das Spiel.
6. **Härtetest in Schritt 2:** 3–4 Testszenen an 2–3 Modelle (z. B. Gemini Flash, ein Claude-Modell, ein lokales);
   die Wahl fällt nach echten Ergebnissen.

## Kern-Architektur

7. **Das Modell fordert Aktionen an, der Code bestimmt die Folgen.**
8. **Harter und weicher Zustand.**
   - Hart: Geld, Gegenstände, Kristalle und Ladung, Verletzungen, Ort, Auftragsstatus, Skills, Zeit.
     Ändert sich **nur** über Resolver.
   - Weich: Fakten, Beziehungen, Gerüchte, Stimmungen. Darf das Modell über enge Aktionen (z. B. `record_fact`) schreiben.
   - Alles ohne eigenen Resolver läuft als **„Versuch“**: Probe würfeln, Ergebnis ändert nur weichen Zustand
     oder ermöglicht eine andere Probe. Häufige Versuchstypen bekommen später eigene Resolver.
9. **Proben mit echten Würfeln.** Der Spieler würfelt analog einen W20 und tippt das Ergebnis ein.
   Ein Spielzug kann im Status `awaiting_roll` stehen. Pro Kampagne ist höchstens **eine** Probe offen
   (soll von der Datenbank erzwungen werden).
10. **Wer den SG festlegt:** Das Modell schlägt nur Skill + Schwierigkeitsstufe (DIFF-01 … DIFF-07) + kurzen Grund vor.
    Der Code prüft, rechnet SG, Bonus und mögliche Ticks. Bei Vergleichsproben würfelt der **Code** für den NPC
    (Werte aus der Datenbank, gespeicherter Seed). **Der Spieler sieht Skill, SG und Grund vor dem Wurf.**
11. **Neue NPCs und Orte.**
    - NPCs: Das Modell wählt eine Vorlage aus fester Liste (Bauer, Söldner, Händler, Wache, Schürfer, Priester, Bandit …)
      plus Name und Beschreibung; der Code legt sie mit Vorlagenwerten und etwas Zufall an.
      Gespeichert wird ein NPC erst, wenn der Spieler mit ihm spricht oder handelt.
    - Orte: nur Unterorte bestehender Orte. Keine neuen Städte oder Regionen.
    - Wichtige Figuren schreibt der Autor selbst; das Modell darf sie nicht anlegen oder töten, solange kein Resolver es erlaubt.
12. **Zeit.** Der Code legt die Dauer jeder Aktion fest; Reisezeiten stehen im Inhalt. Bei freien Versuchen wählt das Modell
    nur eine Kategorie (Augenblick, Stunde, halber Tag). Beim Tageswechsel: Wetter würfeln, Ration abziehen
    (sonst Hunger), Heilung nach Schlaf, Erschöpfung prüfen. Die Uhr läuft nur über Spielzüge, nicht in Echtzeit.
13. **Tod.** Endgültig für die Figur, aber die Welt bleibt; die nächste Figur spielt in derselben Welt.
    Bei K.O. würfelt der Code auf einer Folgen-Tabelle. Zurückspulen gibt es nur als Admin-Werkzeug für Systemfehler.

## Welt: Valar

14. **Valar ist der Name der Welt.** Eigene Welt, hat nichts mit Tolkien zu tun. Nichts aus der alten Welt
    (Lythara, Kaelth, Sahraen) wird übernommen.
15. **Stimmung wie The Witcher:** rau, düster, politisch, moralisch grau.
16. **Kultur: deutsches Spätmittelalter.** Flickenteppich kleiner Fürstentümer, Reichsstädte, Zünfte,
    ein Städtebund (wie die Hanse), Fehden, Landsknechte. Deutsche Namen.
17. **Magie kommt aus Stürmen.** Magische Stürme und Wetterlagen bringen Energie. Sie lässt sich in Kristallen speichern.
    Manche Tiere und manche Menschen speichern Energie oder ziehen sie aus der Natur.
18. **Kristalle sind ein wertvoller Rohstoff mit Handwerk drumherum** (wie Salz oder Silber): werden geschürft,
    gehandelt, geschmuggelt, besteuert und in Gegenstände eingebaut. Keine Industrie, kein Steampunk.
    Kristalle haben **Ladung und Reinheit** und **entladen sich langsam**; je reiner, desto langsamer.
    Im Code: Ladung zu einem Zeitpunkt speichern, aktuellen Wert beim Lesen ausrechnen.
19. **Energie ist neutral.** Es gibt nur eine Sorte Kristall. Elemente kommen vom Ort.
20. **Ziehen ist eine seltene Begabung**, bei der Charaktererschaffung wählbar. Skill „Ziehen“ (WEI),
    Körperladung mit Obergrenze nach KON. Zu viel ziehen → Zustand „Sturmbrand“.
21. **Fünf Wirkungen:** Stoß, Glut, Schild, Lebensstrom, Spüren. Jede kostet Ladung, braucht eine Probe und hat
    einen festen Effekt in Zahlen. Ziehende wirken sie mit Körperladung, alle anderen über **Artefakte**
    (ein Gegenstand, eine Wirkung, Ladung aus eingesetztem Kristall). Kreative Anwendungen laufen über „Versuch“.
22. **Stürme sind Spielmechanik.** Der Code würfelt pro Spieltag und Region. Es gibt Vorzeichen, Gefahr während
    des Sturms und danach frische Kristalle an festen Stellen sowie aufgeladene Monster.
    **Feste Sturmgebiete** mit den Kristallfeldern, dazu seltene wandernde Stürme überall sonst.
23. **Monster sind Tiere mit zu viel Energie.** Die Energie sitzt im **Kern** (Beute mit Ladung und Reinheit).
    Mehr Ladung → größer, stärker, verdrehter. Jede Monsterart ist ein Inhaltseintrag mit Engine-Werten
    plus **Ladungsstufe**, die die Werte erhöht.
    **Mutationen:** Tabelle mit festen Wirkungen; der Code würfelt pro Monster passend zur Ladungsstufe 0–3 Mutationen.
24. **Elementwesen** entstehen, wo ein Sturm viel Energie ablädt und sie sich mit dem Ort verdichtet
    (Stein im Gebirge, Glut in Vulkanasche, Nebel im Moor, Wasser auf See). Sie haben keinen Kern, sie *sind* Energie.
    Besiegt zerfallen sie zu Kristallen hoher Reinheit. Ohne neue Stürme verblassen sie langsam.
25. **Menschen können zu Monstern werden.** Jede Überladung kann eine bleibende Mutation hinterlassen (der Code würfelt).
    Mutationen sind **fast nur Fluch**: kaum Kräfte, sichtbare Zeichen, Verstecken wird schwerer.
    Letzte Stufe = die Figur ist verloren (zählt wie Tod), **bleibt aber als Monster in der Welt**.
26. **Glaube.** Eine Kirche hält die Stürme für heilig und Ziehende für „Gezeichnete“. Sie beansprucht alle Ziehenden
    für den Kirchendienst; wer sich versteckt, gilt als Ketzer und wird gejagt.
    **In den Städten ist die Kirche mächtig, auf dem Land schwindet ihr Einfluss:** Dort herrschen Aberglaube und
    Naturverbundenheit, Stürme gelten als natürlicher Teil der Welt.
27. **Vier Mächte:** Kirche (Ziehende, Deutung der Stürme), Kristallzunft (Monopol auf Verarbeitung),
    Fürsten (Kristallfelder, Steuern), Städtebund (Handel).
28. **Eine ausgearbeitete Startregion**, die Nachbarländer nur als Skizze (davon erzählen ja, hinreisen nein).
29. **Ausgangslage: Kristallrausch mit Fehde.** Vor einigen Monaten hat ein Jahrhundertsturm die Gegend verwüstet und
    ein neues, reiches Kristallfeld hinterlassen. Glücksritter, Schürfer, Zunft, Kirche und Schmuggler strömen hin;
    zwei Fürsten beanspruchen das Land. Der Sturm hat die Monster aufgeladen.
    Die Region: eine schnell gewachsene Bretterstadt am Kristallfeld, alte Dörfer mit Naturglauben im Umland,
    das Sturmgebiet, Wald und Moor.

## Spielfigur

30. **Freier Hintergrund**, kein fester Beruf. Den Hintergrund erzählt der Spieler; er wird weicher Zustand.
31. **Attribute per Punktekauf**, **Skills frei gewählt** (Zahlen in REGELN.md).
32. **Währung: Gulden, Groschen, Pfennig** (1 : 10 : 100), gespeichert als ganze Zahl in Pfennig.
33. **Knappes Startbudget** plus ein persönlicher Gegenstand ohne großen Wert.

## Erzählung

34. Zweite Person, Gegenwart. 1–3 Absätze pro Zug, länger nur bei neuen Orten oder großen Momenten.
    Das Modell handelt und spricht nie für die Spielfigur. Keine Auswahlmenüs; jeder Zug endet offen.
    NPCs in wörtlicher Rede mit eigener Stimme. Proben werden sichtbar angekündigt.
35. **Härte:** Gewalt deutlich, Sex nur angedeutet. Ein klarer Rahmen-Prompt beschreibt erwachsene Dark Fantasy.
    Keine Tricks, die Sicherheitsfilter der Anbieter aushebeln.

## Vorgehen

36. **Schritt 1** wie im Plan: Transaktion, Idempotenz und Revision mit einer Abgabe-Quest (siehe README).
    **Schritt 1b:** Probenablauf (offene Probe, Wurf, Ticks, Skill-Aufstieg).

## Umsetzung Schritt 1

37. **Nur `apply.py` schreibt**, Spielzustand und Spielzug-Protokoll. Resolver laufen im Nur-Lese-Modus der
    Datenbank (`PRAGMA query_only`): Ein Schreibversuch scheitert sofort, statt still zu wirken.
38. **Ein Spielzug ist eine Transaktion** (`BEGIN IMMEDIATE`). Fehler beim Anwenden: Rollback, kein Protokolleintrag,
    der Fehler geht nach oben, die `request_id` bleibt frei. Abgelehnte Züge sind keine Fehler:
    Sie werden gespeichert und haben keine Wirkung.
39. **Gleiche `request_id` mit anderer Aktion oder anderem Spieler ist ein Fehler** (`RequestIdConflict`),
    kein stilles Abspielen eines fremden Ergebnisses.
40. **Aktionen nennen nur das Nötigste.** `SubmitQuestItems` enthält nur die `quest_id`; Gegenstand, Menge, Ort und
    Belohnung stehen im Inhalt. Der Spieler kommt vom Aufrufer (Sitzung), nie aus der Aktion.
41. **Zeit in Spielminuten** (`campaign.game_minutes`). Die Dauer je Aktion steht im Inhalt
    (Testwert: 10 Minuten für die Abgabe).
42. **Die Datenbank erzwingt, was sie kann:** `STRICT`-Tabellen, `CHECK` gegen negative Mengen und negatives Geld,
    und im Protokoll: Nur angenommene Züge erhöhen die Revision, und zwar um genau 1.

## Umsetzung Schritt 1b

43. **Die offene Probe** ist eine Zeile in `open_roll`. Ihr Primärschlüssel `campaign_id` erzwingt höchstens eine
    offene Probe je Kampagne (Entscheidung 9). Der Versuch steht im Protokoll mit dem Status `awaiting_roll`
    und zählt als angenommener Zug (Revision +1), der Wurf danach ebenfalls. Solange eine Probe offen ist,
    lehnt der Code jede andere Aktion ab (`roll_pending`), auch von anderen Spielfiguren.
    Würfeln darf nur die Spielfigur, die die Probe eröffnet hat.
44. **SG, Bonus, mögliche Ticks und Grund werden beim Öffnen berechnet und gespeichert.** Der Wurf rechnet mit
    genau diesen Werten, auch wenn sich der Skill dazwischen ändert. Der Spieler sieht sie vor dem Wurf.
45. **Das Modell nennt nur Skill, Stufe (DIFF-01 bis DIFF-07) und Grund** (Entscheidung 10). SG, Bonus und Ticks
    kann es nicht setzen: Zusatzfelder werden abgelehnt. Ein unbekannter Skill wird als Spielzug abgelehnt.
46. **Der Spieler tippt nur seinen W20-Wurf** (ganze Zahl von 1 bis 20). Der Wurf wird mit dem Spielzug
    gespeichert (`check_json`: Wurf, Bonus, Summe, SG, Ausgang, Ticks, Aufstiege). Ein eingespeister
    Zufallsgenerator kommt erst mit der Vergleichsprobe, weil bisher nur der Spieler würfelt.
47. **Ausgang:** Natürliche 20 (kritischer Erfolg) und natürliche 1 (Patzer) gehen vor. Sonst: Gesamt ≥ SG ist
    ein Erfolg, SG − 1 und SG − 2 sind knapp daneben, alles darunter ein Fehlschlag. Ticks gibt es nur bei
    Erfolg und kritischem Erfolg. *Auslegung:* Der kritische Erfolg verdoppelt die Ticks nicht, „knapp daneben“
    bringt keine.
48. **Fortschritt:** Das Band des *Zielwerts* bestimmt die Kosten: Skill 1 bis 20 kostet je 3 Ticks, 21 bis 40
    je 5, 41 bis 60 je 8, 61 bis 80 je 12, 81 bis 100 je 20. *Auslegung:* Damit braucht 0 auf 100 genau
    960 Ticks, wie in REGELN.md steht (nach dem Band des Ausgangswerts wären es 943). Überschüssige Ticks
    bleiben für den nächsten Punkt, mehrere Aufstiege pro Wurf sind möglich, bei Skill 100 verfallen Ticks.
49. **Level und Attributspunkte:** Level = 1 + (Summe aller Skill-Aufstiege) / 10, abgeleitet und nicht
    gespeichert. *Auslegung:* Jeder Levelaufstieg bringt 1 Attributspunkt in einen Vorrat
    (`player.attribute_points`); das Verteilen (Attribut höchstens 20) folgt später.
    Startwerte aus der Charaktererschaffung zählen nicht als Aufstiege.
50. **Gegen Grinden:** Die Regel „keine Ticks, wenn SG − Bonus ≤ 5“ gilt und wird schon beim Öffnen gerechnet.
    Die Regel „höchstens einmal Ticks pro Skill und Szene“ wartet, bis es Szenen gibt.
51. **Regeltabellen** stehen in `valar/rules.py` (nur Rechnen, kein Datenbankzugriff). Attribute sind Spalten in
    `player` (Standard 10), Skills stehen in `player_skill` (fehlende Zeile = Wert 0). SKILL-501 (Ziehen) fehlt,
    bis es die Begabung gibt. Versuch und Wurf lassen die Spielzeit unverändert.
52. **Schema-Version 2** ohne Migration, solange es keine echten Spielstände gibt. Eine Datei mit Version 1
    wird abgelehnt.

## Festlegung: Szene

53. **Szene** (nur für die Regel „höchstens einmal Ticks pro Skill und Szene“): ein Abschnitt am selben Ort ohne
    Tageswechsel. Sie endet bei einem Ortswechsel und bei einem Tageswechsel. Beides bestimmt der Code;
    das Modell kann keine Szene beginnen oder beenden, sonst ließen sich Ticks farmen. Gemerkt wird je Spielfigur
    und Skill die Szene der letzten Ticks. Gebaut wird das, wenn es Bewegen und den Tageswechsel gibt.
    Vorläufig: Solange davon nichts gebaut ist, lässt sich die Definition ohne Aufwand ändern.
    Gebaut wird sie in Schritt 1c (Entscheidung 58); umgesetzt nach Entscheidung 68.

## Fahrplan bis zum spielbaren Kern

Planungsrunde Oktober 2026. Der Fahrplan mit Testfällen steht im [README](../README.md#fahrplan).

54. **Spielbarer Kern:** Ein Mensch spielt im Terminal mit dem Modell als Spielleiter in einem Testgebiet:
    Figur erschaffen, umsehen, gehen, untersuchen, reden, sammeln, frei versuchen, Vergleichsproben,
    Aufträge abgeben, Tage vergehen lassen. **Ohne Kampf und Handel**; die laufen bis dahin als freier Versuch
    (Entscheidung 8). Kampf ist der erste Schritt nach dem Kern, Handel der zweite.
55. **Orte im Inhalt:** Orte und ihre Unterorte (Entscheidung 11) mit Verbindungen und Reisezeit (Entscheidung 12).
    Die Aktion zum Bewegen nennt nur das Ziel; die Dauer steht im Inhalt.
56. **Abfragen sind keine Spielzüge.** Umsehen und Zustand anzeigen lesen nur: kein Protokoll, keine Revision,
    keine Zeit.
57. **Tageswechsel um Mitternacht der Spielzeit.** Der Tag wird aus `game_minutes` abgeleitet, die Startuhrzeit
    steht im Inhalt. Ein Zug über mehrere Mitternächte löst jeden Tageswechsel aus. In 1c endet damit nur die Szene;
    in 1h kommen Schlafen, Ration oder Hunger, Erschöpfung und Wetter ohne Stürme dazu. Heilung kommt mit dem Kampf.
58. **Szenenregel (zu Entscheidung 53):** Sie wird wie die Routine-Regel schon beim Öffnen gerechnet und gezeigt.
    Es zählt die Szene beim Öffnen. Nur vergebene Ticks verbrauchen die Szene, ein Fehlschlag nicht.
59. **Dauer freier Versuche:** Augenblick 5, Stunde 60, halber Tag 360 Spielminuten. Das Modell wählt die Kategorie,
    der Code die Minuten. Die Dauer wird beim Öffnen gespeichert und beim Wurf gutgeschrieben.
    Ab Schritt 1c ersetzt das den Satz „Versuch und Wurf lassen die Spielzeit unverändert“ aus Entscheidung 51.
60. **Patzer und „knapp daneben“ wirken nur in der Erzählung.** Für den harten Zustand zählen beide wie ein
    Fehlschlag; die Zeit vergeht trotzdem. Das Modell erzählt Preis, Haken und Patzer (MECH-04, MECH-07).
61. **Probe mit Folgen:** Aktionen mit eigenem Resolver und Probe (Untersuchen, Sammeln, später Kampf) nehmen
    Skill und Stufe aus dem Inhalt, nicht vom Modell. Was jeder Ausgang bewirkt, hält die offene Probe beim Öffnen
    fest; der Wurf wendet genau das an (wie Entscheidung 44).
62. **Zufall des Codes:** ein Seed je Kampagne, daraus der Zufall je Spielzug. Was der Code würfelt, steht im
    Spielzug; eine wiederholte `request_id` würfelt nicht neu. Tests speisen einen Generator mit festem Seed ein
    (Entscheidung 46). Kommt mit Schritt 1e.
63. **Vergleichsprobe:** Der Gegen-Skill kommt aus einer festen Paar-Tabelle in `valar/rules.py`
    (z. B. Schleichen gegen Wahrnehmung). Das Modell nennt Skill und NPC. Der NPC würfelt beim Öffnen
    (W20 + Bonus aus seinen Werten); sein Gesamt ist der SG und wird vor dem Wurf gezeigt. Gleichstand gewinnt
    der Spieler (MECH-06). Ticks nach der höchsten DIFF-Stufe, deren SG das NPC-Gesamt nicht übersteigt;
    Routine- und Szenenregel gelten. Ein Skill ohne Paar wird abgelehnt.
    *Auslegung:* Natürliche 20 und 1 zählen nur beim Spieler, beim NPC zählt nur die Summe.
64. **Untersuchen nach Wissen:** Was die Figur erfährt, hängt von ihren Skills und Attributen ab. Der Inhalt
    beschreibt Objekte in Stufen: eine Grundbeschreibung für alle, weitere Stufen mit Skill und Stufe
    (z. B. Naturkunde DIFF-03 für die Heilwirkung einer Pflanze).
    - Passiv: Ist 10 + Bonus (MECH-01 ohne Wurf) mindestens der SG, ist die Stufe sofort sichtbar, auch beim Umsehen.
      Kein Wurf, kein Spielzug.
    - Aktiv: Untersuchen öffnet eine Probe im Skill der nächsten unbekannten Stufe. Das Gesamt öffnet alle Stufen
      dieses Skills bis zu seiner Höhe; der Code merkt sie sich je Spielfigur.
    - Versteckte Dinge sind Stufen mit Wahrnehmung.
    - Das Modell bekommt nur Stufen, die die Figur kennt, und erzählt nicht mehr, als sie weiß.
65. **Kommandozeile vor dem Modell:** deutsche Befehle, nur Standardbibliothek. Sie ist das Werkzeug für Autor und
    Tests und bleibt neben dem freien Text, wenn das Modell kommt.
66. **Ticks bestätigt (zu Entscheidung 47):** Legendär (DIFF-07, SG 24) bringt +8 Ticks. Der kritische Erfolg
    verdoppelt die Ticks nicht, „knapp daneben“ bringt keine.
67. **Modell-Anbindung:** Tests laufen gegen eine Attrappe, ohne Netz. Echte Aufrufe gibt es nur im Spiel und im
    Härtetest. Der Schlüssel kommt nur aus der Umgebung und steht nie in Datenbank, Protokoll oder Fehlermeldung.
    Eine wiederholte `request_id` ruft das Modell nicht noch einmal auf.

## Umsetzung Schritt 1c

68. **Die Szene wird abgeleitet, nicht gespeichert** (zu Entscheidungen 53 und 57). Jede Spielfigur hat
    `arrived_at`, die Spielminute ihrer Ankunft am Ort. Die laufende Szene beginnt mit dem Späteren von Ankunft
    und letzter Mitternacht; dieser Beginn in Spielminuten kennzeichnet die Szene. Um Mitternacht wird daher
    nichts geschrieben, und die neue Szene gilt für alle Figuren der Kampagne zugleich. Je Skill merkt sich
    `ticked_scene` die Szene der letzten Ticks. Eine neue Figur kommt zur aktuellen Spielzeit an.
69. **Bewegen** (`move`) nennt nur das Ziel. Abgelehnt wird in dieser Reihenfolge: unbekannter Ort
    (`unknown_location`), der eigene Ort (`already_there`), keine direkte Verbindung (`not_connected`).
    Verbindungen gelten in beide Richtungen gleich lang. *Auslegung:* Es gibt keine Wegsuche; ein Weg über
    mehrere Orte sind mehrere Züge. Jede Ankunft beginnt eine neue Szene, auch zurück am selben Ort.
70. **Der Inhalt prüft sich beim Laden:** Verbindungen zu unbekannten Orten, doppelte Verbindungen,
    Unterorte ohne Verbindung zu ihrem Ort, Kreise bei Unterorten und Aufträge an unbekannten Orten sind Fehler.
71. **Dauer im Versuch:** `duration` ist Pflicht (`moment`, `hour`, `half_day`), die Minuten stehen in
    `rules.ATTEMPT_MINUTES`. Das Öffnen kostet keine Zeit; der Wurf schreibt die beim Öffnen gespeicherten
    Minuten gut, bei jedem Ausgang. Die offene Probe speichert dazu die Szene beim Öffnen. Der Wurf merkt die
    Szene nur, wenn Ticks vergeben wurden (`ticks_on_success` > 0 und Erfolg).
72. **Abfragen** stehen in `valar/queries.py` (`look`, `status`). Sie lesen im Nur-Lese-Modus in einer
    Lese-Transaktion, damit alle Werte zum selben Stand gehören. Unbekannte Kampagne oder Spielfigur ist ein
    Fehler, kein Spielzug.
73. **Schema-Version 3** ohne Migration (wie Entscheidung 52). Dateien mit Version 1 oder 2 werden abgelehnt.
74. **Testgebiet:** Bretterstadt mit den Unterorten Zunfthaus und Schenke, Kristallfeld, Dorf; Spielbeginn um
    8 Uhr. Reine Testdaten, kein fester Weltinhalt.

## Offen (eigene Sitzungen)

Jeder Punkt nennt den Schritt, den er blockiert.

- Gedächtnis des Modells: was es pro Zug an Zustand, Verlauf und Fakten bekommt; ob eine Modellantwort
  Aktion und weiche Einträge in einem Spielzug bündelt. **Blockiert 2b.**
- Rahmen-Prompt (Entscheidung 35). **Blockiert 2b.**
- Monatsbudget der Kostenbremse, Anbieter-Konto und Schlüssel. **Blockiert 2a (Budget) und 2c.**
- Paar-Tabelle der Vergleichsproben. **Blockiert 1g**; ein Vorschlag kommt mit dem Schritt.
- Zahlen für den Tageswechsel: Ration, Hunger, Erschöpfungsstufen, Wettertabelle ohne Stürme. **Blockiert 1h.**
- Namen: Startregion, Bretterstadt, Dörfer, die zwei Fürsten, Kirche, Zunft, Städtebund, Stürme.
  Wichtige NPCs der Startregion. *Blockiert den Kern nicht* (Testinhalt), nur den echten Weltinhalt.
- Preisliste, Startausrüstung. *Blockiert den Kern nicht* (1i nutzt eine Testliste), nur den Handel.
- K.O.-Folgentabelle, Monsterarten, Mutationstabellen (Tiere und Menschen). Blockiert den Kampf, nach dem Kern.
- Zahlen für Energie: Körperladung, Kosten und Effekte der fünf Wirkungen, Sturmbrand, Entladungsrate.
  Blockiert Magie und die Begabung „Ziehen“ bei der Erschaffung, nach dem Kern.
- Materialien (MAT) aus der alten Engine prüfen: passen sie zu Valar? Blockiert Handwerk, nach dem Kern.
