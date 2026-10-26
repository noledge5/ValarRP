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
64. **Untersuchen nach Wissen** (eingeschränkt durch Entscheidung 92): Was die Figur erfährt, hängt von ihren Skills und Attributen ab. Der Inhalt
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

## Koordinaten und Karte (Schritt 1c2)

Planungsrunde Oktober 2026: Alles soll auf einer Karte verortet sein, die Karte so weit wie möglich automatisch.

75. **Ein Koordinatensystem für alles:** eine ebene Karte, x nach Osten, y nach Norden, in ganzen Metern,
    höchstens 1 000 km vom Nullpunkt. Jeder Ort hat einen Punkt, auch jeder Unterort. Was an einem Ort ist
    (Spielfigur, später NPCs und Fundstellen), hat dessen Punkt; Spielfiguren stehen immer an einem Ort, nie frei
    dazwischen. Die Koordinaten stehen im Inhalt, nicht im Spielstand.
76. **Reisezeit gerechnet, überschreibbar** (ersetzt die Handwerte aus Entscheidung 55; die Aktion bleibt gleich).
    Ein Weg führt von Ort zu Ort über Zwischenpunkte. Reisezeit = Weglänge durch Tempo der Wegart, auf ganze
    Minuten aufgerundet, mindestens 1. Tempo zu Fuß (Vorschlag, Testwerte): Straße 5 km/h, Pfad 4 km/h,
    Gelände 2,5 km/h. Eine feste Zeit im Inhalt (`minutes`) geht vor, z. B. für Fähre oder Pass.
77. **Karte als GeoJSON** (offenes Kartenformat): `valar/geo.py` exportiert Orte als Punkte und Wege als Linien
    und legt die Karte dafür an den Nullpunkt der Erde (1 Grad = 111 319,49 m; die Verzerrung bleibt dort unter
    1,5 %, im Testgebiet unter 0,01 %). So zeigen GitHub, geojson.io, QGIS oder uMap sie ohne Einrichtung. Eine
    bearbeitete Datei übernimmt Lage, Zwischenpunkte, Wegart und feste Zeiten zurück und wird wie jeder Inhalt
    geprüft. `docs/testgebiet.geojson` hält ein Test aktuell. Quelle bleibt der Inhalt im Code; der Weltinhalt
    kann später direkt als GeoJSON entstehen.
78. **Umsehen zeigt je Ausgang Himmelsrichtung und Entfernung:** acht Richtungen (N, NO, O, SO, S, SW, W, NW)
    in Luftlinie, Entfernung als Weglänge. Das Modell bekommt später diese Angaben und erfindet keine Geografie.
79. **Schema bleibt Version 3**, weil sich nur der Inhalt ändert.

## Umsetzung Schritt 1d

80. **Testfigur im Inhalt** (`start_player`): Ort, Geld, Attribute, Skills, Gepäck, aktive Aufträge.
    `apply.create_campaign_from_content` legt Kampagne und Figur in einer Transaktion an. Ersetzt wird das durch
    die Charaktererschaffung (1i).
81. **Falsche Eingabe oder Abgelehnt:** Unbekannter Befehl, fehlende oder unlesbare Angaben ergeben den Hilfetext
    und keinen Spielzug. Ein unbekannter Ort oder eine unbekannte Skill-ID gehen dagegen als Aktion an den Kern
    und werden dort als Spielzug abgelehnt. So stehen die Regeln nur im Kern.
82. **Namen statt IDs:** Orte und Skills per Name oder ID, ohne Rücksicht auf Groß- und Kleinschreibung,
    Leerzeichen und Bindestriche; Teilwörter zählen nicht. Stufe als 1 bis 7, `DIFF-03` oder Wort (leicht,
    schwer …), Dauer als Wort (augenblick, stunde, halbtag). Namen mit Leerzeichen in Anführungszeichen.
83. **Revision wie ein echter Client:** Die Kommandozeile merkt sich die Revision ihres letzten Zugs. Ist sie
    veraltet, liest sie den Stand neu und versucht es genau einmal mit neuer `request_id`.
84. **Skriptbetrieb:** Kommt die Eingabe nicht vom Terminal, steht jede Eingabe mit Eingabeaufforderung in der
    Ausgabe; so ist die Ausgabe ein lesbares Protokoll. `ende` oder das Dateiende beenden.

## Welt: Namen der Startregion

Oktober 2026. Vorschläge aus einer Übergabe an ein anderes Modell, ausgewählt ohne Widerspruch zu den
Entscheidungen 19 und 20 und ohne doppelte Wortteile. Übrige Vorschläge: [NAMENSVORRAT.md](NAMENSVORRAT.md).

85. **Namen der Startregion:**
    - Region: **Rauhenau**.
    - Bretterstadt am Kristallfeld: **Sturmhagen**, im Volksmund **Guldenbretter**.
    - Kristallfeld: **Glasgrund**.
    - Alte Dörfer: **Wolfsrode**, **Moorstetten**, **Lindenheim** (mächtige Linde, Naturkult, Hain mit Opfersteinen).
    - Moor: **Schwarzbruch**. Wald: **Rabenforst**.
    - Die zwei Fürsten: **Graf Albrecht von Dornhagen** auf Burg Dornwacht, berechnend und verschuldet, sucht den
      Glasgrund mit Urkunden, Söldnern und Zugeständnissen an die Kirche. **Markgraf Konrad von Hohenbrück** auf
      Burg Falkenstein, gibt sich als Beschützer der freien Bauern und duldet Plünderung, wo sie ihm nützt.
    - Kirche: **Heilige Kirche des Ersten Sturms**, Priester heißen **Sturmväter** und **Sturmväterinnen**.
    - Zunft: **Ehrbare Kristallmeisterzunft**. Städtebund: **Bund der Salz- und Kristallfahrer**.
    - Magische Stürme heißen im Volk **Grollwetter**, bei der Kirche **Gnadensturm**.
86. **Das Testgebiet trägt diese Namen:** Sturmhagen (mit Zunfthaus und Schenke), Glasgrund, Lindenheim.
    Die IDs im Code (`boomtown`, `crystal_field`, `village` …) bleiben, Lage und Zahlen bleiben Testwerte
    (zu Entscheidung 74).

## Welt-Wiki (OKF)

Planungsrunde Oktober 2026: Die Welt soll Logik und Lore haben, aber nicht jeder Umgang mit jedem Ding kann
in Zahlen geregelt sein. Das Modell soll Wissen über die Welt bekommen und daraus selbst etwas leisten.

87. **Welt-Wiki im OKF-Format** (Open Knowledge Format: ein Ordner mit Markdown-Dateien, eine Seite je Begriff,
    oben ein paar Felder, darunter freier Text, Seiten verlinken einander, `index.md` je Ordner, Änderungen in
    `log.md`). Es liegt in `welt/` und ist echter Weltinhalt: Regionen, Orte, Klima, Pflanzen, Tiere, NPC-Typen,
    Mächte, Bräuche, Ereignisse. Tests nutzen ein eigenes kleines Test-Wiki.
88. **Felder für den Code, Text für das Modell.** Die Felder einer Seite sind geprüft (Typ, Region oder
    Landschaft, Häufigkeit, Wert, Skill und Stufe, wo nötig). Der Text ist frei und geht an das Modell.
    Häufigkeit steht als Stufe (häufig, gelegentlich, selten, sehr selten); die Zahlen dazu stehen in
    `valar/rules.py` und kommen mit dem Schritt, der sie braucht.
89. **Das Wiki ist Leitplanke, keine vollständige Liste.** Das Modell bekommt die Seiten, die zur Lage passen
    (Ort, Region, was in der Szene vorkommt), und erzählt daraus. Umgang mit Dingen ohne eigene Regel läuft wie
    bisher erzählt oder als Versuch (Entscheidung 8). Welche Seiten das Modell je Zug bekommt, gehört zum offenen
    Punkt „Gedächtnis des Modells“.
90. **Brücke zum harten Zustand:** Ins Gepäck kommt nur, was eine Wiki-Seite hat, die es zum Gegenstand macht.
    Ob es am Ort vorkommt, prüft der Code an Region oder Landschaft. Menge, Wert und Zufall bestimmt der Code aus
    den Feldern, nie das Modell.
91. **Das Wiki wächst in zwei Ebenen:**
    - **Kanon** (`welt/`, in Git): schreibt der Autor, gern mit Hilfe von Modellen über Übergaben.
    - **Kampagne** (in der Datenbank): Das Modell darf neue Seiten direkt anlegen, über eine enge Aktion mit
      Längengrenze (weicher Zustand, Entscheidung 8). Sie gelten nur in dieser Kampagne. Harte Felder setzt das
      Modell nicht selbst: Es wählt eine Vorlage (z. B. „Heilkraut, selten“), der Code setzt die Werte
      (wie bei NPCs, Entscheidung 11).
    - In den Kanon kommt eine Kampagnen-Seite nur, wenn der Autor sie prüft. Der Code kann sie dafür als
      OKF-Datei ausgeben.
92. **Untersuchen und Sammeln stützen sich auf das Wiki** (ändert Entscheidung 64 und Schritt 1e):
    - Wissensstufen sind möglich, aber keine Pflicht. Eine Seite ohne Stufen bekommt das Modell als Text;
      mehr erfährt die Figur über einen Versuch. Stufen nur, wo Wissen hart zählt (z. B. Gift erkennen,
      Versteck finden). Für Stufen gilt Entscheidung 64 weiter.
    - Sammeln braucht keine fest eingetragene Fundstelle. Was laut Wiki an Region oder Landschaft des Orts
      vorkommt, lässt sich sammeln. Skill, Stufe und Menge folgen aus den Feldern und der Häufigkeit.
93. **Neuer Schritt 1d2 „Welt-Wiki“ vor 1e:** Gerüst in `welt/`, Laden mit Prüfung der Felder und Links,
    erste Seiten aus den Entscheidungen 14 bis 29 und 85. Orte im Inhalt können auf eine Wiki-Seite zeigen und
    bekommen Region und Landschaft. Kampagnen-Seiten kommen mit 1f.

## Umsetzung Schritt 1d2

94. **OKF Version 0.2** (aktuelle Fassung der Spezifikation). Streng geprüft wird nur der Block `valar`;
    alle anderen Felder sind frei und bleiben erhalten, wie OKF es verlangt. **Fehler** verhindern das Laden:
    fehlende oder unlesbare Felder, fehlendes `type`, falscher Block `valar`, Verweise im Block auf fehlende
    Seiten oder falsche Typen, doppelte IDs, unbekannte Skills, Kreise bei Regionen. **Hinweise** stören nicht:
    Links ins Leere (noch ungeschriebenes Wissen), unbekannter Typ, unbekannter `status`, fehlende oder
    veraltete `index.md`, Log-Überschriften ohne Datum.
95. **Seiten-ID ist der Dateiname** ohne `.md`: nur a-z, 0-9 und Bindestriche, Umlaute als ae, oe, ue, ss;
    eindeutig im ganzen Wiki. Links am besten vom Wiki-Anfang aus (`/orte/sturmhagen.md`).
96. **Typen und Felder im Block `valar`:**
    - Region: `parent` (Oberregion). Ort: `region`, `landscapes`. Landschaft: keine.
    - Pflanze, Tier, Rohstoff: `occurs`, `item`. Gegenstand: `item`. NPC-Typ, Ereignis, Brauch: `occurs`.
    - Person, Macht, Wissen: keine. Alle Typen: `knowledge` (Wissensstufen nach Entscheidung 64).
    - `occurs` ist eine Liste aus Ort, Landschaft oder Region mit Häufigkeit (`common`, `uncommon`, `rare`,
      `very_rare`). Passen mehrere, zählt das genaueste: Ort, dann Landschaft, dann die nächste Region.
    - `item` hat `value_pennies` und optional `gather_skill`. Nur Seiten mit `item` können ins Gepäck.
    - Seiten mit `status: deprecated` kommen nicht mehr vor.
97. **Region und Landschaft kommen von der Ort-Seite im Wiki** (genauer als Entscheidung 93). Ein Ort im Inhalt
    zeigt mit `wiki` auf seine Seite; ein Unterort ohne eigene Seite nimmt die seines Oberorts.
    Zunfthaus und Schenke gehören so zu Sturmhagen.
98. **Übergaben:** Jede Seite beginnt mit einer Zeile `=== pfad.md ===`. `python -m valar.wiki import` prüft
    jeden Pfad streng (nur im Wiki-Ordner, nur erlaubte Namen), entfernt Code-Zäune, überschreibt nie und
    schreibt dann nichts. Danach werden die `index.md` neu geschrieben und alles geprüft.
    Herkunft steht in `generated` (z. B. `gemini/<modell>`); neue Seiten tragen `status: draft`.
    Entwürfe zählen im Spiel mit. Geprüft ist eine Seite, wenn `verified` eine Kennung mit `human:` nennt.
99. **Sicherheit:** YAML nur über `yaml.safe_load` (PyYAML ist neue Abhängigkeit), so führt keine Seite Code aus.
    Seiten über 100 kB, Verknüpfungen und versteckte Ordner werden nicht gelesen.

## Vergleichsproben: Paar-Tabelle

Oktober 2026. Vorschlag aus einer Übergabe an ein anderes Modell, unverändert übernommen.

100. **Paar-Tabelle** (zu Entscheidung 63), kommt mit Schritt 1g nach `valar/rules.py`.
     Spieler-Skill gegen NPC-Skill:

     | Skill | gegen | Beispiel |
     |---|---|---|
     | SKILL-201 Schleichen | SKILL-405 Wahrnehmung | an einer Wache vorbeischleichen |
     | SKILL-203 Taschendiebstahl | SKILL-405 Wahrnehmung | unbemerkt einen Beutel ziehen |
     | SKILL-301 Überzeugen | SKILL-305 Menschenkenntnis | jemanden von der eigenen Unschuld überzeugen |
     | SKILL-302 Täuschen | SKILL-305 Menschenkenntnis | einer Wache eine falsche Geschichte auftischen |
     | SKILL-303 Einschüchtern | SKILL-305 Menschenkenntnis | einen Zeugen zum Schweigen bringen |
     | SKILL-304 Feilschen | SKILL-304 Feilschen | um den Preis einer Ware feilschen |
     | SKILL-305 Menschenkenntnis | SKILL-302 Täuschen | durchschauen, ob jemand lügt |
     | SKILL-306 Anführen | SKILL-306 Anführen | im Streit um die Führung den Ton angeben |
     | SKILL-307 Verführen | SKILL-305 Menschenkenntnis | Vertrauen durch Charme gewinnen |
     | SKILL-403 Athletik | SKILL-403 Athletik | Wettlauf |
     | SKILL-404 Akrobatik | SKILL-404 Akrobatik | Wettklettern, Duell auf dem Seil |
     | SKILL-405 Wahrnehmung | SKILL-201 Schleichen | einen Schleicher im Schatten entdecken |

     Ohne Paar (feste Schwierigkeit, als Vergleichsprobe abgelehnt): Schlösser knacken, Fallenkunde, Erste Hilfe,
     Überleben, Handwerk, Alchemie, Naturkunde, Monsterkunde. Kampf-Skills laufen später über den Kampf.

## Tageswechsel und NPC-Vorlagen: Zahlen

Oktober 2026. Vorschläge aus Übergaben an ein anderes Modell, mit Korrekturen übernommen.

101. **Zahlen für den Tageswechsel** (zu Entscheidungen 12 und 57), kommen mit Schritt 1h.
     - **Ration:** 3 Pfennig. Um Mitternacht mit Ration sinkt der Hunger um 1 Stufe, ohne steigt er um 1.
       Höchstens Stufe 4.
     - **Hunger:** 0 Satt, 1 Hungrig (−1), 2 Mager (−2), 3 Ausgemergelt (−4), 4 Auszehrend (−6).
     - **Erschöpfung** nach Stunden ohne Schlaf: ab 18 Müde (−1), ab 24 Übermüdet (−2), ab 36 Erschöpft (−4),
       ab 48 Gerädert (−6). Je 6 Stunden Schlaf am Stück baut 1 Stufe ab.
     - Die Mali von Hunger und Erschöpfung zählen zusammen auf jede Probe.
     - **Wetter (Herbst)**, W20 je Tag: 1–4 kühler Altweibersommer (keine Wirkung); 5–8 Frühnebel
       (Wahrnehmung −2); 9–12 nasskalter Dauerregen (Reisezeit × 1,5); 13–15 Schneeregen (Reisezeit × 1,5,
       Proben −1); 16–18 Böenwind (Wahrnehmung −2); 19–20 Unwetter (Reisezeit × 2, Wahrnehmung −2).
       *Auslegung:* Unwetter ist gewöhnliches Wetter, kein Grollwetter. Jahreszeiten kommen später;
       bis dahin gilt die Herbsttabelle.
102. **NPC-Vorlagen** (zu Entscheidung 11), kommen mit Schritt 1f: [npc-vorlagen.json](npc-vorlagen.json).
     Zehn Vorlagen mit Spannen für Attribute, Skills und Geld; der Code würfelt darin. Neu zur Liste kommen
     Schmuggler, Kräuterfrau und Zunftprüfer; sie bekommen später Wiki-Seiten. Gepäck ist vorerst nur
     Beschreibung, denn ins Gepäck kommt nur, was eine Wiki-Seite hat (Entscheidung 90). Sprechweise und
     Haltung stehen ausführlicher auf den NPC-Typ-Seiten im Wiki.

## Welt-Wiki: Ablage, Pflege, Zugriff des Modells

Oktober 2026, nach einer Zweitmeinung zu den Entscheidungen 87 bis 99.

103. **`welt/` im Repo ist die einzige Quelle für Weltwissen.** Die Spieldatenbank hält den Zustand der Kampagne
     (Inventar, Beziehungen, Verletzungen, Aufträge, Geschehenes) und Kampagnen-Seiten, nie Kanon.
     NotebookLM und ähnliche Werkzeuge dienen nur zum Fragen und zur Suche nach Widersprüchen, nie als Quelle.
104. **Jede Änderung an `welt/` läuft über einen PR**, auch Übergaben und Änderungen von Claude. Vor dem PR prüft
     die CI das Wiki: Fehler und Hinweise lassen sie scheitern (gültiges YAML, Pflichtfelder, eindeutige IDs,
     Typen und Bezüge, Links, `index.md`). Der Import überschreibt nie. Ob Geheimes im freien Text steht, prüft
     der Mensch im PR.
105. **Pflege mit Obsidian** (Programm für Markdown-Notizen): Der Ordner `welt/` ist der Tresor. Die geteilten
     Einstellungen in `welt/.obsidian/app.json` stellen Markdown-Links mit Pfad vom Wiki-Anfang ein und zeigen
     die Felder als Quelltext. Abgleich am Rechner über das Plugin Obsidian Git. Am Handy: lesen in der
     GitHub-App oder Obsidian, kleine Korrekturen im Browser auf GitHub (das ergibt einen PR), größere Arbeiten
     über eine Claude-Sitzung oder eine Übergabe. Kein bezahlter Abgleich.
     *Auslegung:* Ein Link ohne `/`, `./` oder `../` gilt erst vom Ordner der Seite, dann vom Wiki-Anfang aus.
     Wikilinks (`[[…]]`) sind ein Hinweis, weil OKF sie nicht kennt.
106. **Seitenauswahl für das Modell in Stufen, deterministisch:** (1) Ort und Region mit Landschaft und
     Vorkommen, (2) anwesende NPCs mit ihren Typen, (3) Gegenstände, Ereignisse und laufende Handlungsstränge,
     (4) gezieltes Nachschlagen. Nachschlagen ist eine Abfrage ohne Spielzug über eine Volltextsuche in SQLite
     (FTS5), keine Vektordatenbank. Jeder Spielzug speichert, welche Seiten ans Modell gingen. Kommt mit 2b.
107. **Drei Arten von Wissen:** Wahrheit der Welt (Wiki), Wissen eines NPCs, Wissen der Spielfigur. Das Modell
     bekommt Wissensstufen erst, wenn die Figur sie kennt. Ein Geheimnis eines NPCs bekommt es nur, solange
     dieser NPC in der Szene ist, mit der Anweisung, es nicht zu verraten. Gerüchte sind keine Wiki-Seiten,
     sondern weiche Fakten der Kampagne mit Quelle und wer sie kennt. Einzelheiten im offenen Punkt
     „Gedächtnis des Modells“.
108. **Kampagnen-Seiten gelten sofort in ihrer Kampagne** (bestätigt Entscheidung 91), damit das Modell die Welt
     im Spiel weiterbauen kann. Kein eigener Vorschlagsordner: Der PR ist der Vorschlag für den Kanon.

## Umsetzung Schritt 1e

109. **Zufall des Codes** (zu Entscheidung 62): Jede Kampagne hat einen `seed`, bei der Anlage zufällig gezogen;
     Tests setzen ihn fest. Der Zufall eines Spielzugs kommt aus Seed und Revision vor dem Zug
     (`rules.turn_rng`). Was der Code würfelt, steht in der offenen Probe und damit im Spielzug; eine
     wiederholte `request_id` liefert das gespeicherte Ergebnis.
110. **Folgen einer Probe** (zu Entscheidung 61): Die offene Probe hält die Folgen eines Erfolgs fest
     (`on_success`: Gegenstände dazu, Wissensstufe gelernt). Der Wurf wendet sie bei Erfolg an, bei jedem anderen
     Ausgang nichts. Ticks, Routine- und Szenenregel und Dauer gelten wie beim Versuch.
111. **Untersuchen** (zu den Entscheidungen 64 und 92):
     - *Betrachten* ist eine Abfrage ohne Spielzug: Text der Seite und die Wissensstufen, die die Figur kennt.
     - Die Aktion `examine` nennt nur die Seite. Erlaubt sind Seiten am Ort: der Ort selbst, Regionen,
       Landschaften und was dort vorkommt.
     - Geprobt wird die Stufe mit dem niedrigsten SG, die die Figur weder gelernt hat noch ohne Wurf sieht.
       Ein Erfolg öffnet alle noch nicht gelernten Stufen dieses Skills bis zur Höhe des Gesamts, mindestens die
       geprobte (auch bei der natürlichen 20).
     - Gelernte Stufen stehen dauerhaft je Figur in `player_knowledge`. Was ohne Wurf sichtbar ist, wird nicht
       gespeichert und folgt dem aktuellen Bonus.
     - Abgelehnt: `unknown_page`, `not_here`, `nothing_to_learn`. Dauer im Inhalt (Testwert 10 Minuten).
112. **Sammeln** (zu Entscheidung 92): Die Aktion `gather` nennt nur die Seite. Sie braucht `item` mit
     `gather_skill` und ein Vorkommen am Ort; es zählt das genaueste (Entscheidung 96). Aus der Häufigkeit folgen
     Stufe und Höchstmenge (Vorschlag, Testwerte): häufig leicht bis 6, gelegentlich durchschnitt bis 4,
     selten schwer bis 2, sehr selten sehr schwer genau 1. Die Menge würfelt der Code beim Öffnen, gleich verteilt.
     Im Gepäck steht der Gegenstand unter seiner Seiten-ID. Dauer im Inhalt (Testwert 60 Minuten).
     Abgelehnt: `unknown_page`, `not_gatherable`, `not_here`. Einen Vorrat gibt es nicht (Entscheidung 92).
113. **Umsehen zeigt das Wiki:** Region, Landschaften, was die Figur über den Ort weiß (versteckte Dinge sind
     Stufen mit Wahrnehmung), und was dort vorkommt, mit Häufigkeit, ob sammelbar, und dem Wissen der Figur.
     Kommandozeile: `betrachte`, `untersuche`, `sammle`, Dinge per Name.
114. **Schema-Version 4** ohne Migration: `campaign.seed`, `open_roll.on_success_json`, `player_knowledge`.

## Umsetzung Schritt 1f

115. **NPC-Vorlagen stehen im Wiki** (ändert Entscheidung 102): Die Werte stehen im Block `valar` der
     NPC-Typ-Seite unter `template`, als Spannen für Attribute, Skills und Geld; der Code prüft sie.
     `docs/npc-vorlagen.json` entfällt. Gepäck steht als Text auf der Seite. Schmuggler, Kräuterfrau und
     Zunftprüfer haben eigene Seiten (Entwurf). Eine Vorlage ist jede NPC-Typ-Seite mit `template`.
116. **NPC ansprechen:** `meet_npc` nennt Vorlage, Name und Beschreibung. Der Code legt den NPC am Ort der
     Spielfigur an und würfelt gleich verteilt in den Spannen (fehlendes Attribut 10, fehlender Skill 0); die
     Werte stehen im Spielzug. Die ID kommt aus dem Namen (`grete-mueller`, bei Gleichstand `-2`). Derselbe Name
     am selben Ort wird abgelehnt (`npc_exists`). `talk` nennt nur die ID und geht nur am selben Ort
     (`not_here`, `unknown_npc`). Dauer im Inhalt (Testwert 10 Minuten). NPCs bleiben vorerst, wo sie sind.
117. **Wichtige Figuren:** Ein NPC mit Name oder ID einer Person-Seite wird abgelehnt (`important_figure`).
     *Auslegung:* Es zählt der ganze Name; ein Vorname allein („Albrecht“) geht durch. Keine Aktion ändert die
     Werte eines NPCs.
118. **Weicher Fakt** (`record_fact`): Fakt oder Gerücht, Text bis 300 Zeichen, dazu optional Thema und
     Quelle (ein NPC der Kampagne). Er gehört der Spielfigur, die ihn notiert. Kostet keine Zeit, zählt aber als
     Spielzug. Ob eine Modellantwort Aktion und Fakt bündelt, bleibt offen (Gedächtnis des Modells).
119. **Kampagnen-Seiten** (`create_page`, zu den Entscheidungen 91 und 108): Seitenvorlage, Titel, Text bis
     1500 Zeichen. Die Vorlagen stehen in `rules.PAGE_TEMPLATES` (Testwerte): wissen, brauch, ereignis, tier,
     pflanze, heilkraut, seltenes-kraut. Vorkommen gilt an der Ort-Seite der Spielfigur mit der Häufigkeit der
     Vorlage, Wert und Sammel-Skill kommen aus der Vorlage. Personen, Orte und NPC-Typen legt das Modell nie an.
     Die ID kommt aus dem Titel; eine vorhandene ID (Kanon oder Kampagne) wird abgelehnt (`page_exists`).
     Die Seite gilt sofort in ihrer Kampagne für Umsehen, Betrachten, Untersuchen und Sammeln.
     `python -m valar.wiki export DB [KAMPAGNE]` gibt die Seiten als Übergabe aus; in den Kanon kommen sie
     über `import` und einen PR.
120. **Schema-Version 5** ohne Migration: `npc`, `npc_skill`, `fact`, `campaign_page`.

## Wirtschaft: Knappheit ohne feste Fundorte

Oktober 2026: Keine rechnerisch festen Fundorte, sondern eine erzählerisch sinnvolle Wirtschaft.

121. **Keine Fundorte mit Vorrat.** Sammeln begrenzen Zeit und Lebenshaltung: Es kostet Stunden, dazu kommen
     Ration, Hunger und Erschöpfung (1h). Die Erträge werden so abgestimmt, dass ein Sammeltag etwa einen
     Tagelohn bringt; die Zahlen kommen mit dem Wirtschaftsbild.
122. **Der Markt begrenzt, nicht der Fundort.** Preise hängen je Ort von Angebot und Nachfrage ab: Wer viel
     vom Gleichen verkauft, drückt den Preis, und Händler kaufen nicht unbegrenzt. Kristalle kauft nur die Zunft
     an; wer anderswo verkauft, schmuggelt. Kommt mit dem Handel nach dem Kern.
123. **Erzählte Knappheit:** Das Modell darf Knappheit als weichen Fakt mit Ablaufzeit setzen (abgegrast,
     gesperrt, vom Grollwetter verbrannt), für ein Ding an einem Ort. Der Code beachtet ihn beim Sammeln.
     Grundregel: Das Modell darf nur erschweren oder verhindern, nie erleichtern oder vermehren. Kommt mit 1h,
     weil der Ablauf an der Spielzeit hängt.
124. **Die Wirtschaft steht im Wiki:** neue Seitentypen Ware, Markt und Handelsweg. Der Text sagt, wer was
     kauft, wo etwas knapp ist, welche Zölle gelten und was geschmuggelt wird; der Code nimmt nur Grundpreis
     und Nachfrage aus den Feldern. Vorher braucht es ein Wirtschaftsbild (Tagelöhne, Grundpreise, Märkte,
     Handelswege), als Übergabe an ein anderes Modell.

## Umsetzung Schritt 1g

125. **Vergleichsprobe** (`contest`, zu Entscheidung 63): Das Modell nennt Skill, NPC, Dauer und Grund. Der
     Gegen-Skill kommt aus `rules.CONTEST_PAIRS` (die Paar-Tabelle aus Entscheidung 100). Abgelehnt wird in
     dieser Reihenfolge: unbekannter Skill (`unknown_skill`), Skill ohne Paar (`no_counter_skill`), unbekannter
     NPC (`unknown_npc`), NPC nicht am Ort (`not_here`).
126. **Der NPC würfelt beim Öffnen** mit dem Zufall des Codes (Entscheidung 109): W20 plus Bonus aus seinen
     Werten (MECH-01, fehlender Skill 0). Der Wurf steht in der offenen Probe (`npc_roll`) und damit im
     Spielzug; sein Gesamt ist der SG (mindestens 1). Spieler sehen ihn vor dem eigenen Wurf.
     Spätere Änderungen an den NPC-Werten ändern die offene Probe nicht.
127. **Ticks nach der Stufe des NPC-Gesamts:** die höchste DIFF-Stufe, deren SG es nicht übersteigt;
     unter SG 8 keine. Routine (SG − Bonus ≤ 5) und Szenenregel gelten mit dem NPC-Gesamt als SG. Gespeichert
     wird diese Stufe als `difficulty` (unter SG 8: DIFF-01, ohne Ticks).
128. **Dauer wie beim Versuch** (Augenblick, Stunde, halber Tag; Entscheidung 59). Schema-Version 6:
     `open_roll.npc_roll_json`, ohne Migration.

## Umsetzung Schritt 1h

129. **Tageswechsel im Spielzug:** Überschreitet die Zeit eines angenommenen Zugs Mitternacht, hängt der Code je
     Mitternacht einen Tageswechsel an (`day_change`), für alle Figuren der Kampagne. Je Figur: Hat sie eine
     Ration, isst sie eine und der Hunger sinkt um 1; sonst steigt er um 1, höchstens auf 4. Rationen, die der
     Zug selbst bringt oder nimmt, zählen schon. Der Hunger steht an der Figur.
130. **Wetter aus Seed und Tag:** Der W20 für einen Tag kommt aus Seed und Tagnummer; so ist das Wetter jedes
     Tages fest, ohne gespeichert zu werden, und steht im Tageswechsel. *Auslegung:* Gewürfelt wird um
     Mitternacht; der erste Tag ist ruhig (Kühler Altweibersommer). Das Wetter des Aufbruchstags verlängert
     Reisen, Malus auf Wahrnehmung und Proben zählen beim Öffnen.
131. **Erschöpfung wird gerechnet, nicht gespeichert:** offene Stufen nach dem letzten Schlaf plus je eine Stufe
     ab 18, 24, 36 und 48 Stunden wach, höchstens 4. Gespeichert sind nur die offenen Stufen und der Zeitpunkt des
     Aufwachens.
132. **Schlafen** (`sleep`) dauert bis 6 Uhr, höchstens 12 Stunden (Testwerte). Je 6 Stunden baut eine Stufe ab;
     danach zählen die wachen Stunden von vorn. Mitternacht im Schlaf löst den Tageswechsel aus.
133. **Malus beim Öffnen:** Hunger, Erschöpfung und Wetter stehen im Bonus, den der Spieler vor dem Wurf sieht
     (gilt für Versuch, Untersuchen, Sammeln und Vergleichsprobe). Was ohne Wurf sichtbar ist (Entscheidung 111),
     rechnet ohne Malus.
134. **Erzählte Knappheit** (`mark_scarcity`, zu Entscheidung 123): Ding, Stufe (knapp oder weg), Dauer (Tag,
     Woche, Monat) und Grund, nur für Dinge, die am Ort vorkommen. Sie gilt für die Ort-Seite, also auch in
     Unterorten. Knapp: Sammeln eine Stufe schwerer; weg: abgelehnt (`scarce`). Es zählt die stärkste, die gerade
     gilt. Kostet keine Zeit. Eine Stufe „reichlich“ gibt es nicht.
135. **Rationen** sind ein Gegenstand mit Wiki-Seite (`ration`, 3 Pfennig); die Testfigur startet mit dreien.
     Schema-Version 7: `player.hunger`, `player.fatigue_debt`, `player.awake_since`, `scarcity`.

## Wirtschaft: Referenzwerte statt Preisliste

136. **Werte schätzt das Modell an Referenzwerten.** Nicht jeder Gegenstand kann im Code stehen. Die Seite
     [Preise und Löhne](../welt/wissen/preise-und-loehne.md) gibt Tagelöhne, Grundpreise, Märkte und Handelswege
     als Orientierung; was fehlt, schätzt der Spielleiter im Verhältnis dazu. Der Code kennt Werte nur, wo er sie
     braucht (Gegenstände mit Seite, z. B. Ration und Kräuter). Die Pflanzenwerte sind daran angeglichen.
     Sammeln bleibt wie in Entscheidung 112. Die eigenen Seitentypen Ware, Markt und Handelsweg (Entscheidung 124)
     kommen erst, wenn der Handel sie braucht. Die Werte entwarf ein Agent in der Sitzung; Übergaben an andere
     Anbieter gibt es nicht mehr.

## Kontext für kleine Modelle

Oktober 2026: Gespielt wird auch mit kleinen und lokalen Modellen. Der Kontext je Zug bleibt klein, und der Code
regelt, was das Modell wann bekommt. Vorbilder: die Lorebooks von SillyTavern, NovelAI und AI Dungeon
(Einträge mit Auslösern, Rangfolge und Token-Budget), das Speichermodell von MemGPT/Letta (fester Kernblock,
Rest nur über Suche), die Gewichtung aus „Generative Agents“ (Relevanz, Aktualität, Wichtigkeit) und die
Hybrid-Suche in SQLite (Volltext FTS5 plus optional Vektoren, verbunden mit Reciprocal Rank Fusion).

137. **Der Spielzustand löst aus, Stichwörter ergänzen** (wie ein Lorebook, zu Entscheidung 106): Seiten kommen in
     den Kontext, weil sie zum Zustand gehören (Ort, anwesende NPCs, Gepäck, laufende Aufträge), oder weil ihr
     Name oder ein Alias in der Eingabe des Spielers oder der letzten Erzählung steht. Aliase stehen im Block
     `valar` (`aliases`, z. B. „Guldenbretter“ für Sturmhagen). Links ziehen höchstens eine Stufe weiter und
     zählen am wenigsten.
138. **Festes Budget mit Rangfolge:** je Zug eine Token-Grenze für Weltwissen (Vorschlag 2 000) mit festen Anteilen.
     Reihenfolge: Zustand der Figur (immer), Ort, anwesende NPCs, ausdrücklich genannte Seiten, Vorkommen am Ort
     (häufige zuerst, höchstens einige), verlinkte Seiten. Was nicht passt, fällt weg und wird im Spielzug
     vermerkt. Die Auswahl ist deterministisch: gleicher Stand, gleicher Kontext.
139. **Detailstufen je Seite** (progressive disclosure, wie OKF mit `index.md` und `description`): Titel,
     ein Satz (`description`), ein Abschnitt, ganzer Text. Standard ist der eine Satz; der ganze Text nur für den
     Ort und das, worum es im Zug geht. Lange Seiten werden an ihren Überschriften in Abschnitte geteilt, z. B.
     „Preise und Löhne“. Jede `description` muss allein verständlich sein und den Namen enthalten, weil das
     Modell oft nur sie sieht.
140. **Nachschlagen über Volltextsuche in SQLite** (FTS5 mit BM25) über Abschnitte, Titel und Aliase, aufgebaut beim
     Start aus Kanon und Kampagnen-Seiten. Eine Abfrage liefert wenige Abschnitte, nie ganze lange Seiten.
     Vektorsuche (sqlite-vec, mit FTS5 über Reciprocal Rank Fusion verbunden) kommt erst, wenn der Härtetest zeigt,
     dass Volltext nicht reicht; ein eigener Datenbank-Server ist nicht nötig.
141. **Gedächtnis vom Code geführt** (wie MemGPT, aber ohne Selbstpflege durch das Modell, die kleinen Modellen
     schwerfällt): ein Kernblock immer im Kontext (Figur, Ort, Ziele, kurze Zusammenfassung), die letzten Züge
     wörtlich, ältere nur als Zusammenfassung, alles andere (Fakten, Gerüchte, Wiki) nur über Auswahl und Suche.
     Fakten werden nach Relevanz (Ort, NPC, Thema), Aktualität und Wichtigkeit gewählt.
142. **Wenige, flache Aktionen:** Das Modell bekommt je Zug nur die Aktionen, die gerade erlaubt sind, mit flachen
     Schemas und festen Werten (Enums), wo es geht. „Nur erzählen“ ist immer eine Wahl, damit das Modell nicht in
     eine Aktion gezwungen wird. Erzwungene Ausgabe (Grammatik, JSON-Schema) nutzen wir, wo der Anbieter sie
     kann; geprüft wird trotzdem mit Pydantic.
143. **Neuer Schritt 1j „Kontextbauer“** vor dem Modell-Client: Der Code baut den Kontext eines Zugs nach 137 bis
     142, hält das Budget ein (Tests) und speichert, welche Seiten in welcher Stufe ins Modell gingen. Damit
     ist der offene Punkt „Gedächtnis des Modells“ zum größten Teil geklärt; offen bleiben die genauen Zahlen
     und der Rahmen-Prompt.

## Umsetzung Schritt 1j

144. **Volltextsuche jetzt, Vektorsuche vielleicht später** (zu Entscheidung 140): `valar/search.py` teilt Seiten an
     Überschriften und Absätzen in Abschnitte (höchstens 800 Zeichen) und sucht mit FTS5 und Trigrammen, sodass
     auch Wortteile treffen („kraut“ in „Sumpfkraut“). Füllwörter fallen weg, häufige Endungen werden abgeschnitten
     („Blutungen“ → „blut“). Titel und Aliase wiegen mehr als der Text. Die Abfrage besteht nur aus einzelnen,
     gequoteten Wörtern; Suchsyntax aus der Eingabe wirkt nie als Befehl. Suchen hängt an der Schnittstelle
     `Retriever`; eine Vektorsuche käme als zweiter Retriever dazu und würde mit `fuse` (Reciprocal Rank Fusion)
     verbunden. Maßstab ist `tests/data/suchfragen.json` (30 Fragen an das Welt-Wiki): Heute finden 29 die
     erwartete Seite unter den ersten drei; der Test verlangt mindestens 90 %. Fällt die Quote mit wachsendem
     Wiki, ist das der Anlass für die Vektorsuche.
145. **Kontextbauer** (`valar/context.py`): Zustandsblock (Ort, Zeit, Wetter, Hunger, Erschöpfung, Geld, Gepäck,
     Aufträge, Wege, offene Probe) außerhalb des Budgets; dann Weltwissen in der Rangfolge Ort, Leute, Notizen,
     Genanntes, Gepäck, Vorkommen (höchstens 6), Verweise. Passt ein Eintrag ganz nicht, kommt sein Satz; passt
     auch der nicht, fällt er weg. Tokens werden mit vier Zeichen je Token geschätzt. Genannt ist eine Seite, wenn
     Titel, ID oder Alias als ganzes Wort in der Eingabe oder der letzten Erzählung steht; Regionen und
     Landschaften zählen dabei nicht. Notizen: die zu Ort, anwesenden NPCs und Genanntem zuerst, dann die
     neuesten, höchstens 5.
146. **Aliase** stehen im Block `valar` jeder Seite (`aliases`). **Manifest im Spielzug:** `submit_turn` nimmt das
     Manifest des Kontextbauers an und speichert es (`turn.context_json`, Schema-Version 8).
147. **Aktionen bei offener Probe:** Das Modell kann nur erzählen; würfeln tut der Spieler. Sonst werden Aktionen
     nur angeboten, wenn sie gerade Sinn haben (Gehen nur mit Wegen, Reden und Vergleichsprobe nur mit NPCs,
     Sammeln nur mit Sammelbarem, Abgeben nur mit aktivem Auftrag). Kommandozeile: `suche` und `kontext` zeigen,
     was das Modell bekäme.

## Umsetzung Schritt 1i

148. **Punktekauf linear:** Je Punkt +1. Alle sechs Attribute starten bei 8, genau 18 Punkte kommen dazu, keines
     über 15; nicht genannte bleiben auf 8. Jede Abweichung ist ein Fehler mit Code (`points_not_spent`,
     `attribute_range`, `unknown_attribute`), und es wird nichts angelegt.
149. **Erschaffung ist kein Spielzug:** `valar/character.py` prüft den Bogen, ohne Datenbank;
     `apply.create_character` legt die Figur in einer eigenen Transaktion an, wie die Anlage einer Kampagne. Das
     erhöht weder Zeit noch Revision und steht nicht im Spielzug-Protokoll. Aus dem Namen wird die `player_id`
     (wie bei NPCs, Entscheidung 95), eindeutig je Kampagne. Die Figur kommt zur aktuellen Spielzeit ausgeruht am
     Startort des Inhalts an (`start_location_id`). Die Testfigur (Entscheidung 80) bleibt für Tests und
     `--testfigur`. Die Kommandozeile fragt Schritt für Schritt, prüft jede Antwort sofort und fragt bei einem
     Fehler noch einmal; `--neu` erschafft eine weitere Figur, `--figur` wählt eine, wenn es mehrere gibt.
150. **Skills:** genau 2 Hauptskills (30) und 4 Nebenskills (15), jeder nur einmal. Ziehen (SKILL-501) wird
     erkannt und als gesperrt abgelehnt (`skill_locked`), bis es Zahlen für Energie gibt.
151. **Startausrüstung als Testliste** im Inhalt (`start_kits`): Geld und Gegenstände, je etwa 3 Gulden wert; der
     Spieler wählt eine. **Persönlicher Gegenstand und Hintergrund sind weicher Zustand:** Text in `player`
     (`keepsake` höchstens 80, `background` höchstens 600 Zeichen, keine Steuerzeichen), ohne Regelwirkung. Der
     Kontextbauer zeigt beides als Eintrag „Die Figur“ direkt nach dem Ort. Zum harten Gegenstand wird der
     persönliche Gegenstand erst, wenn der Handel das braucht.
152. **Attributspunkte verteilen** ist die Aktion `raise_attribute` (Attribut und Punkte): nur aus dem Vorrat
     (`no_attribute_points`), kein Attribut über 20 (`attribute_max`), ohne Wurf und ohne Zeit; bei offener Probe
     abgelehnt wie jede Aktion. Angeboten wird sie dem Modell nur, wenn Punkte im Vorrat sind. Kommandozeile:
     `steigere Stärke 2`. Schema-Version 9.

## Modell-Anbieter

153. **Anbieter flexibel, kein Claude im Spiel** (zu Entscheidungen 4 bis 6). Reihenfolge, der Client wechselt
     selbst zum nächsten, wenn ein Kontingent erschöpft ist:
     1. Gemini-API von Google, Flash-Modelle im kostenlosen Kontingent.
     2. Mistral im kostenlosen Plan „Experiment“ (Eingaben dürfen zum Training genutzt werden; bewusst in Kauf
        genommen).
     3. Bezahlt und direkt beim Anbieter: DeepSeek (günstig, Rabatt auf wiederholten Kontext; Daten liegen in
        China, bewusst in Kauf genommen) oder DeepInfra für offene Modelle.
     4. OpenRouter nur für den Härtetest, um viele Modelle mit einem Schlüssel zu prüfen; im Spielbetrieb nicht,
        weil es beim Aufladen 5,5 % nimmt (mindestens 0,80 $).

     Die Claude-API ist zu teuer und fällt auch im Härtetest weg; dort treten Gemini Flash, Modelle der übrigen
     Anbieter und ein lokales Modell an. Alle sprechen das OpenAI-Format; es bleibt bei einem Client mit Adresse,
     Modell und Schlüssel je Anbieter, ohne eigenen Vermittler (kein LiteLLM). Preise prüft der Code nicht von
     außen; sie stehen als Einstellung je Modell und werden vor dem Härtetest beim Anbieter nachgesehen.
     **Kostenbremse:** 10 bis 20 € im Monat. Gezählt wird nur, was kostet (freie Kontingente zählen 0 €); ab
     10 € gibt es eine Warnung, bei 20 € stoppt das Spiel vor dem nächsten Aufruf. Beide Grenzen sind
     Einstellungen. Schlüssel legt der Autor selbst an; sie stehen nur in der Umgebung (Entscheidung 67).

## Umsetzung Schritt 2a

154. **Kette von Anbietern** (`valar/llm.py`): Der Client fragt die Anbieter in der Reihenfolge der Einstellungen.
     Nur ein erschöpftes Kontingent (HTTP 429) führt zum nächsten; Zeitüberschreitung, Netzfehler und andere
     Fehler sind ein Fehler ohne Wirkung, damit ein kaputter Anbieter auffällt. Anbieter ohne Schlüssel in der
     Umgebung werden übersprungen.
155. **Prüfung und ein Wiederholungsversuch:** Das JSON-Schema geht an den Anbieter, so weit er es kann
     (`output`: `json_schema`, `json_object` oder `none`). Die Antwort wird gegen das Pydantic-Schema geprüft;
     JSON in Codezäunen (```json) zählt. Ist sie ungültig, fragt der Client denselben Anbieter genau einmal neu,
     mit der Antwort und einem kurzen Hinweis, was nicht passte; dann ist es ein Fehler.
156. **Kosten:** Jeder Aufruf steht in `model_call` (Schema-Version 10), auch ungültige und fehlgeschlagene, mit
     Kosten in Millionstel Euro, aufgerundet. Meldet ein Anbieter keine Token-Zahlen, schätzt der Code (vier
     Zeichen je Token). Der Monat zählt nach UTC. Das Budget wird vor jedem Aufruf geprüft, auch vor der
     Wiederholung; ein einzelner Aufruf kann die Grenze also knapp überschreiten. Die Warnung steht im Ergebnis.
157. **Sicherheit:** Einstellungen in einer TOML-Datei (Vorlage `modelle.example.toml`) mit dem Namen der
     Umgebungsvariable, nie dem Schlüssel; ein Feld für den Schlüssel wird abgelehnt. Der Schlüssel steht nur im
     Kopf `Authorization`. Adressen nur `https://`, `http://` nur für den eigenen Rechner (Ollama). Der Client
     folgt keiner Weiterleitung, damit der Schlüssel nicht an eine andere Adresse geht, und liest höchstens 1 MB.
     Fehlermeldungen enthalten keinen Antworttext, der Schlüssel wird darin geschwärzt. HTTP läuft über die
     Standardbibliothek, ohne neue Abhängigkeit.

## Gedächtnis des Modells und Rahmen-Prompt

Oktober 2026, vor Schritt 2b. Klärt die beiden offenen Punkte, die 2b blockierten, mit Blick auf kleine Modelle
(Entscheidung 153). Ein echter Kontext brauchte zu dieser Zeit 300 bis 500 Tokens Weltwissen.

158. **Zahlen fürs Gedächtnis** (zu den Entscheidungen 138 und 141):
     - Rahmen-Prompt höchstens 800 Tokens, fest, immer an erster Stelle.
     - Zustand der Figur außerhalb des Budgets, wie bisher. Weltwissen bleibt bei 2 000 Tokens.
     - Notizen (Fakten, Gerüchte): höchstens 8 statt 5 (ändert Entscheidung 145), weil sie das Gedächtnis der
       Geschichte sind.
     - Verlauf wörtlich: die letzten 6 Wechsel (Eingabe und Erzählung), zusammen höchstens 1 500 Tokens; passt einer
       nicht mehr, fallen er und alle älteren in die Chronik.
     - Chronik: höchstens 300 Tokens, die neuesten Zeilen bleiben.
     - Eingabe des Spielers höchstens 500 Zeichen, Erzählung höchstens 2 000 Zeichen.
     - Zusammen etwa 2 000 bis 4 500 Tokens je Aufruf.
159. **Zwei Aufrufe je Zug** (zu Entscheidung 34): Aufruf A wählt die Aktion, der Code löst auf, Aufruf B erzählt das
     Ergebnis. „Nur erzählen“ bringt den Text gleich in A mit: ein Aufruf. Ein Wurf braucht nur B. Eine abgelehnte
     Aktion erzählt B als Grund in der Welt; A bekommt keinen zweiten Versuch. Bei veralteter Revision reicht der
     Code dieselbe Aktion einmal neu ein, ohne Modell.
160. **Bündeln** (klärt den offenen Punkt aus Entscheidung 118): A liefert genau eine Aktion. Die Erzählung (B oder
     „nur erzählen“) darf 0 bis 2 Notizen mitgeben; jede wird ein eigener Spielzug `record_fact` nach dem Hauptzug.
     Lehnt der Code eine ab, fällt nur sie weg. Bei offener Probe gibt es keine Notizen. Knappheit und neue Seiten
     bleiben eigene Aktionen in A.
161. **Chronik vom Code statt Zusammenfassung vom Modell** (zu Entscheidung 141): Je Zug schreibt der Code eine Zeile
     aus dem Ergebnis (Zeit, Aktion, Ausgang, Gewinn), z. B. „Tag 1, 08:05: gehen nach Sturmhagen“. Das kostet keinen
     Aufruf und erfindet nichts. Abgelehnte Züge und reines Erzählen bekommen keine Zeile; Erzählerisches halten die
     Notizen fest.
162. **Rahmen-Prompt** (Entscheidung 35) in [valar/spielleiter.md](../valar/spielleiter.md), auf Deutsch, weil die
     Ausgabe Deutsch ist. Kurze, nummerierte Regeln statt Fließtext: Welt und Härte (Gewalt deutlich, Sex nur
     angedeutet), Erzählregeln aus Entscheidung 34, was der Code bestimmt, Aufgaben A und B, nur JSON. Derselbe Text
     steht in A und B immer zuerst, damit Anbieter ihn zwischenspeichern können. Keine Tricks gegen die
     Sicherheitsfilter der Anbieter.

## Umsetzung Schritt 2b

163. **Wechsel** (`exchange`, Schema-Version 11): Eingabe, Spielzug der Aktion, Erzählung und Chronikzeile; Gedächtnis
     des Modells, kein harter Zustand, geschrieben nur in `apply.py`. Zu einer request_id gehören der Spielzug
     `<id>/a` (nach veralteter Revision `<id>/a2`) und die Notizen `<id>/n1`, `<id>/n2`. Dieselbe request_id liefert
     den gespeicherten Wechsel ohne Aufruf; mit anderer Eingabe oder Figur ist sie ein Fehler (`RequestIdConflict`).
     Scheitert B, steht die Aktion, der Wechsel bleibt ohne Erzählung, und dieselbe request_id holt nur B nach.
     Steht der Spielzug schon, der Wechsel aber nicht (Absturz), gilt die gespeicherte Aktion, ohne Aufruf A.
164. **Feste Ziele in Aufruf A** (zu Entscheidung 142): Für jede angebotene Aktion baut der Code ein Schema mit
     festen Werten aus dem Zustand: Wege, NPCs am Ort, Seiten am Ort, Sammelbares, NPC-Vorlagen, Skills, Skills mit
     Gegen-Skill, aktive Aufträge, Seitenvorlagen. Ein anderes Ziel ist eine ungültige Antwort (ein
     Wiederholungsversuch). Die Nachricht nennt dieselben Listen mit Namen und die Form jeder Aktion, weil Anbieter
     mit `json_object` das Schema nicht sehen. Antwort: `{"action": …}`. `record_fact` bietet A nicht an, Notizen
     kommen mit der Erzählung; Knappheit nur für Sammelbares, weil sie nur aufs Sammeln wirkt.
165. **Was Aufruf B bekommt:** den Kontext nach dem Zug, die gewählte Aktion in Worten und das Ergebnis des Codes
     (Ablehnung mit Grund, Wurf und Ausgang, Ankunft, Gegenstände, gelernte Wissensstufen, Tageswechsel, Schlaf,
     Dauer und Uhrzeit). Neue NPCs nur mit Name, Vorlage und Beschreibung, nie mit Werten; eine angekündigte Probe
     ohne ihre Folgen. Das Manifest im Spielzug nennt zusätzlich, wie viele Wechsel und Chronikzeilen ins Modell
     gingen.
166. **Kommandozeile mit Spielleiter** (`--modell modelle.toml`, zu Entscheidung 65): Freier Text geht an den
     Spielleiter. `schau`, `status`, `notizen`, `hilfe`, `ende` und `wurf ZAHL` bleiben Befehle, der Wurf wird erzählt.
     Jeder andere Befehl beginnt mit `!` und läuft ohne Erzählung. Erst kommt die Erzählung, dann die harten Folgen,
     wie der Code sie bestimmt hat. Ein Fehler des Modells wird gemeldet; dann ist nichts passiert.
     Steuerzeichen in Text des Modells (Erzählung, Notizen) entfernt der Code vor dem Speichern und Anzeigen.

## Weboberfläche vor dem Härtetest

Oktober 2026, nach Schritt 2b.

167. **Schritt 3 (Weboberfläche) kommt vor dem Härtetest 2c** (ändert den Fahrplan). Grund: Schlüssel direkt eingeben
     und ohne Terminal spielen.
168. **Schlüssel aus der Weboberfläche** (ändert die Entscheidungen 67 und 157 teilweise): Ein Schlüssel kann außer aus
     der Umgebung auch in der Weboberfläche eingegeben werden. Er liegt in einer eigenen Datei im Datenordner neben dem
     Spielstand, nur für den Server lesbar, und gilt, wenn die Umgebungsvariable fehlt. Nie in Datenbank, Repo,
     Protokoll oder Fehlermeldung. Die Seite zeigt ihn nie wieder, nur „gesetzt, endet auf …1234“; ändern und
     löschen geht.
169. **Umfang der ersten Weboberfläche:** Spielen mit Spielleiter (freier Text, Würfeln), Zustand und Umsehen, Figur
     erschaffen, Schlüssel und Budget verwalten. Zugriff nur über Tailscale (Entscheidung 2).
170. **Der Härtetest läuft in einer Cloud-Sitzung von Claude Code.** Die Schlüssel stehen dort als Umgebungsvariablen
     der Cloud-Umgebung (`GEMINI_API_KEY`, `MISTRAL_API_KEY`, `DEEPSEEK_API_KEY`, `DEEPINFRA_API_KEY`,
     `OPENROUTER_API_KEY`), nie im Chat. Die Umgebung erreicht alle fünf Anbieter (geprüft im Oktober 2026).

## Umsetzung Schritt 3

171. **Server aus der Standardbibliothek** (`valar/web.py`): WSGI mit `wsgiref`, je Anfrage ein Thread und eine
     eigene Datenbankverbindung; Züge, Erschaffung und Einstellungen laufen nacheinander unter einer Sperre. Keine
     neue Abhängigkeit und kein JavaScript: Seiten kommen fertig vom Server, Eingaben als Formular. Für einen Spieler
     hinter Tailscale reicht das; einen größeren Server braucht es erst mit mehreren Spielern.
172. **Eingaben wie in der Kommandozeile:** Die Seite reicht jede Eingabe an dieselbe Sitzung wie die Kommandozeile
     (`cli.Session`) und zeigt deren Ausgabe; Regeln und Anzeige gibt es so nur einmal. Mit Spielleiter geht freier
     Text an ihn, andere Befehle beginnen mit `!` (Entscheidung 166); ohne `modelle.toml` gelten die Befehle direkt.
     Umsehen, Zustand, Notizen und Hilfe sind Links und laufen schreibgeschützt. Ein Würfelfeld gibt es nur bei
     offener Probe. Die Seite zeigt die letzten 20 Wechsel (Eingabe und Erzählung).
173. **request_id im Formular** (Entscheidung 3): Jede Seite bringt ein Formular mit neuer request_id (`web-` und 32
     Hex-Zeichen). Doppelt abgeschickt wirkt es einmal und zeigt dasselbe Ergebnis; mit anderer Eingabe wird es
     abgelehnt. Ein Zweitversuch nach veralteter Revision hängt `-2` an. Fehlt die Erzählung zum letzten Zug, holt
     ein Knopf sie mit derselben request_id nach (Entscheidung 163).
174. **Schutz trotz Tailscale:** Erlaubt sind nur Hostnamen aus `--hosts` bzw. `VALAR_HOSTS` und localhost, gegen
     DNS-Rebinding. POST nur, wenn `Origin` zum Host passt oder der Browser `Sec-Fetch-Site: same-origin` meldet,
     gegen fremde Seiten im selben Browser. Formulare höchstens 64 KB und nur `application/x-www-form-urlencoded`.
     Jeder Text wird maskiert. Kopfzeilen: CSP ohne Skripte, kein Einbetten in fremde Seiten, kein Referrer, kein
     Zwischenspeichern. Eine Anmeldung gibt es nicht; den Zugang regelt Tailscale (Entscheidung 169).
175. **Docker auf der Synology:** Abbild aus `python:3.13-slim` mit Pydantic und PyYAML, Code und Welt-Wiki im Abbild,
     alle Daten im Ordner `/daten` (Spielstand, `modelle.toml`, Schlüssel, Budget). Läuft ohne Root. Die
     `docker-compose.yml` bindet den Port nur an die Tailscale-Adresse der Synology, damit die Seite aus dem Heimnetz
     nicht erreichbar ist. Die CI baut das Abbild bei jedem Push.
176. **Schlüsseldatei** (zu Entscheidung 168): `schluessel.json` im Datenordner, je Umgebungsvariable ein Schlüssel,
     Rechte 0600, geschrieben über eine Zwischendatei mit Austausch. Erlaubt sind 8 bis 400 druckbare ASCII-Zeichen
     ohne Leerzeichen, weil der Schlüssel im Kopf `Authorization` steht. Nur Variablen, die `modelle.toml` nennt.
     Eine kaputte Datei zählt als leer. Die Umgebung geht vor.
177. **Budget in der Weboberfläche:** Die Seite zeigt die Kosten des Monats (UTC) und beide Grenzen. Geänderte Grenzen
     liegen in `budget.json` im Datenordner und gehen `modelle.toml` vor. Nur endliche Zahlen, Stopp über 0, Warnung
     nicht über dem Stopp (Entscheidung 153).

## Umsetzung Schritt 2c (Härtetest) und Modellwahl

Oktober 2026. Ergebnis und Verläufe: [HAERTETEST.md](HAERTETEST.md).

178. **Der Client flacht das Schema für alle Anbieter ab** (klärt den offenen Punkt zu `$defs`, `oneOf`, `const`;
     zu Entscheidung 155). Gemini warf diese Teile weg und lieferte in Aufruf A nur `{"action": {"type": …}}`.
     `llm.provider_schema` setzt Verweise ein, nimmt `anyOf` statt `oneOf` und `enum` statt `const`, lässt Titel und
     Standardwerte weg und macht jedes Feld zur Pflicht, auch `type`; ein freies Feld bleibt über `null` frei.
     Geprüft wird die Antwort weiter gegen das Pydantic-Schema.
179. **Die Zeitgrenze gilt für den ganzen Aufruf** (zu Entscheidung 157). OpenRouter schickt beim Warten Leerzeichen;
     die Grenze von urllib je Lesen griff dann nie, ein Aufruf hing über 15 Minuten. Der Client liest die Antwort
     stückweise bis zu einer Frist; ein einzelnes Lesen wartet höchstens die Zeitgrenze.
180. **Härtetest als Werkzeug** (`valar/stress.py`, Kandidaten in `haertetest.toml`): feste Szenen, frischer Spielstand
     mit festem Seed, skriptierter Spieler. Der Code prüft die gewählte Aktion und den Zustand danach; die Erzählung
     wird von Hand gelesen. Bei OpenRouter verlangt der Test einen Unteranbieter, der `response_format` kann, wartet
     nach HTTP 429 kurz und meldet die echten Kosten. Echte Aufrufe nur von Hand, die Tests laufen ohne Netz.

181. **Modellwahl** (nach dem Härtetest, zu Entscheidung 153; Ergebnis in [HAERTETEST.md](HAERTETEST.md)). Kette:
     1. **Gemini Flash** (Google, freies Kontingent): die beste Erzählung, erfindet nichts, nennt den SG. Braucht einen
        Schlüssel aus einem Google-Projekt ohne Abrechnung; der bisherige gehört zu einem Projekt mit Vorauszahlung.
     2. **Mistral Medium** (Mistral direkt, freier Plan „Experiment“): wählt die Aktionen am treuesten, schnell.
     3. **Gemini Flash-Lite** (bezahlt): sehr schnell und billig, etwa 0,002 $ je Zug, als Reserve.

     DeepSeek fällt weg (wählte über OpenRouter oft „schlafen“), ebenso vorerst ein lokales Modell: Gemma 4 und
     Mistral Small 3.2 erfanden zu viel. Bezahlt kostete Gemini Flash etwa 0,008 $ je Zug, bei 100 Zügen am Tag über
     der Kostenbremse; darum nur im freien Kontingent. Mistral und Google bekommen eigene Zugänge, nicht über
     OpenRouter (Entscheidung 153).
182. **Unbekannte Orte im Rahmen-Prompt** (zu Regel 6, Entscheidung 162): Ein Ort, der nicht im Kontext steht, ist
     allen unbekannt, auch jedem NPC („Nie gehört.“); keine Richtung, kein Weg, keine Lage, keine Notiz. Vorher erfanden
     Mistral Medium und Flash-Lite Wege zu „Eisenburg“ und notierten sie als Fakt; danach lehnten beide in je drei
     Läufen ab.

## Betrieb auf der Synology und erster Blick auf die Seite

Oktober 2026, beim ersten Start auf der Synology.

183. **Port an allen Adressen, Heimnetz erlaubt** (ändert Entscheidung 175 und 169 teilweise). Das Tailscale-Paket
     der Synology läuft ohne eigene Netzwerkschnittstelle („Userspace“): Die Adresse `100.…` gibt es für Docker nicht,
     die Bindung daran schlug fehl, auch nach `tailscale configure-host`. Verbindungen an `100.…` kommen über die Synology
     selbst an. Darum bindet `docker-compose.yml` den Port an alle Adressen; die Seite ist über Tailscale und
     im Heimnetz erreichbar. Bewusst in Kauf genommen: Wer im Heimnetz ist, kann mitspielen und Schlüssel überschreiben
     oder löschen (lesen nicht) und so Budget verbrauchen. Weiter keine Anmeldung, keine Freigabe ins Internet; die
     Prüfung der Hostnamen (`VALAR_HOSTS`) bleibt.

184. **Eigenes JavaScript in der Weboberfläche** (ändert Entscheidungen 171 und 174 teilweise). Nach dem ersten Blick auf
     die Seite: Sie soll funktional und ansprechend werden, mit Chat-Oberfläche (Züge ohne Neuladen, automatisch nach
     unten, Text darf nach und nach erscheinen) und Live-Rückmeldung bei der Erschaffung (verbleibende Punkte). Erlaubt
     ist nur eigenes Skript vom eigenen Server, nichts von fremden Seiten (CSP `script-src 'self'`, kein Inline-Skript).
     Ohne JavaScript bleibt die Seite benutzbar. Regeln prüft weiter nur der Server; das Skript zeigt nur an.

## Überarbeitung der Weboberfläche

Oktober 2026, nach dem ersten Spieltest (zu Entscheidung 184). Design-Run mit drei Entwürfen: Kerzenschein (dunkel,
Gold), Pergament, Eisen und Asche (kaltes Grau, Rost).

185. **Aussehen „Pergament“** (vom Autor gewählt): helles, gealtertes Pergament auf dunklem Holz, Tinte und rote
     Auszeichnung wie in einer Handschrift. Fraktur (UnifrakturMaguntia) nur für Überschriften und die erste Initiale
     einer Erzählung; Fließtext Alegreya, Bedienung Alegreya Sans. Die Schriften liegen im Repo
     (`valar/static/schrift`, SIL Open Font License, Lizenztexte daneben), nur der Zeichensatz Latin. Keine Bilder,
     das Papier ist ein Farbverlauf. Am Handy eine Spalte, die Seitenleiste wird zur Schublade.
186. **Chat und Erschaffung** (Umsetzung von 184 und 185):
     - Die Spielseite ist ein Chat: Verlauf mit Eingabe und Erzählung, Eingabefeld unten (Enter schickt, Umschalt+Enter
       bricht um), der Verlauf beginnt unten (Spaltenumkehr in CSS, auch ohne Skript). Harte Folgen stehen unter der
       Erzählung in einem eigenen Kasten mit Etikett (Ort, Zeit, Probe, Geld …), der Kopf zeigt Tag, Ort, Geld, Wetter,
       Hunger, Erschöpfung und offene Probe. Die Folgen stehen beim jeweils letzten Zug; nach dem Neuladen zeigt der
       Verlauf Eingabe und Erzählung, der Kopf den aktuellen Stand.
     - Züge ohne Neuladen: Das Skript schickt dasselbe Formular an `/spiel/ID/zug` und bekommt JSON mit den fertigen
       Teilen der Seite (Kopf, Verlauf, Eingabe, Seitenleiste), gebaut von denselben Funktionen wie die ganze Seite.
       So gibt es die Anzeige nur einmal, und jeder Text ist schon auf dem Server maskiert; die CSP lässt in den Teilen
       nichts ausführen. Für den Weg gelten dieselben Regeln: request_id, Herkunft, Größe, nur Formulare. Ein
       Netzfehler lässt das Formular mit derselben request_id stehen; erneut senden wirkt einmal.
     - Seitenleiste mit Reitern Zustand, Umsehen, Notizen, Hilfe; ohne Skript schalten Links um (`?zeige=`). Wege beim
       Umsehen haben einen Knopf, der nur das Eingabefeld füllt.
     - Erschaffung in fünf Schritten (Name, Attribute, Skills, Ausrüstung, Herkunft) mit Zählern daneben: verbleibende
       Punkte, gewählte Haupt- und Nebenskills, Modifikator je Attribut, Bonus je gewähltem Skill. Skills nach den
       Kategorien aus REGELN.md (`rules.Skill.group`), jede Ausrüstung mit einem Satz (`StartKit.description`) und
       den Beschreibungen ihrer Gegenstände aus dem Wiki. Ohne Skript ein langes Formular. Geprüft wird nur auf dem
       Server; eine Abweichung öffnet den betroffenen Schritt, die Eingaben bleiben stehen.
187. **Formulare mit `Origin: null`** (zu Entscheidung 174, Fehler aus dem ersten Spieltest): Wegen
     `Referrer-Policy: no-referrer` schicken Browser bei Formularen `Origin: null`; die Prüfung lehnte damit die
     eigenen Formulare ab. Bei `null` zählt jetzt `Sec-Fetch-Site: same-origin`, das keine Seite selbst setzen kann.
     Fremde Herkunft, `null` ohne diese Meldung oder mit `cross-site` bleiben abgelehnt.

## Auslieferung auf die Synology

Oktober 2026.

188. **Mergen heißt veröffentlichen** (ändert Entscheidung 175 teilweise): Nach jedem Merge in den Standard-Branch baut
     die CI nach grünen Tests das Abbild für Intel/AMD und ARM und legt es öffentlich nach `ghcr.io/noledge5/valarrp`
     (`latest` und der Commit). Die Synology baut nicht mehr selbst; `docker-compose.yml` nennt das Abbild mit
     `pull_policy: always`. Beim Neu-Erstellen des Projekts holt sie die neueste Version; ein einfacher Neustart nicht,
     damit kein Update mitten im Spiel kommt. Kein automatischer Tausch (Watchtower), weil der Zugriff auf Docker der
     Synology bräuchte. Öffentlich ist dabei nur, was das öffentliche Repo ohnehin zeigt; Spielstand und Schlüssel
     liegen in `daten/` auf der Synology.

## Zweiter Blick auf die Seite

Oktober 2026, nach dem Update auf der Synology: Layout sichtbar, aber kein Eingabefeld.

189. **Spielseite robuster für Safari** (zu Entscheidungen 174, 186 und 187):
     - Der Spielbereich hat genau eine Zeile, die nie höher wird als der Bildschirm (`grid-template-rows: minmax(0, 1fr)`).
       Vorher wuchs sie mit dem Verlauf; der untere Teil mit dem Eingabefeld lag dann außerhalb und war wegen
       `overflow: hidden` nicht erreichbar. Ohne `dvh` (ältere Safari) wird der Bildschirm nicht festgehalten, die Seite
       fließt normal.
     - `Referrer-Policy: same-origin` statt `no-referrer`: Fremde Seiten erfahren weiter nichts, die eigenen Formulare
       tragen ihre echte Herkunft. Ältere Safari melden kein `Sec-Fetch-Site`; mit `Origin: null` wären ihre Formulare
       abgelehnt worden. Die Ausnahme für `null` aus Entscheidung 187 bleibt.
     - Enter schickt auch in Safari vor Version 16 ab (ohne `requestSubmit`).

## Ideen für die Oberfläche

Oktober 2026, vom Autor aus einer Liste gewählt. Alles nur Anzeige; keine Regel ändert sich, das Skript füllt höchstens
das Eingabefeld.

190. **Einfacher und intensiver spielen:**
     - **Vorschläge** über dem Eingabefeld aus dem Spielstand: Umsehen, mit NPCs am Ort reden, die nächsten Wege (bis 4),
       Sammelbares (bis 2), Schlafen ab 20 Uhr, vor 6 Uhr oder bei Erschöpfung. Ein Tipp füllt nur das Feld. Bei offener
       Probe gibt es keine Vorschläge, nur das Würfelfeld.
     - **Folgen im ganzen Verlauf:** Frühere Züge zeigen ihre harten Folgen aus dem gespeicherten Spielzug, mit denselben
       Zeilen wie beim Spielen, ohne die Uhrzeit (sie wäre der heutige Stand). Eine angesagte Probe steht als „Probe
       angesagt“ da, Notizen des Modells als „Notiert“. Ältere Züge lädt „Ältere Züge“ nach (`/spiel/ID/verlauf?vor=`,
       JSON, nur lesen; ohne Skript `?verlauf=` bis 200).
     - **Änderungen markieren:** Was sich nach einem Zug geändert hat, leuchtet kurz auf; Reiter mit Neuem bekommen einen
       Punkt, bis man sie öffnet. Erkannt über Schlüssel an den Elementen (`data-k`); die Uhrzeit zählt nicht.
     - **Kapitel-Trenner:** Nach einem Zug mit neuem Tag oder neuem Ort steht eine Zwischenüberschrift im Verlauf
       („Tag 2 · Sturmhagen“), aus den Effekten des Zugs.
191. **Übersicht:**
     - **Karte** als Reiter: Orte und Wege aus dem Inhalt als SVG, Norden oben, der eigene Ort rot. Gezeigt werden alle
       Orte des Inhalts (das Testgebiet), Unterorte nur in der Liste darunter. Ein von hier erreichbarer Ort ist
       umrandet; ein Tipp füllt das Eingabefeld.
     - **Personen** als Reiter: alle angelegten NPCs (angelegt wird erst beim Ansprechen, Entscheidung 11) mit Vorlage,
       Ort, Beschreibung und den Notizen über sie.
     - **Als App:** Manifest und Symbol (Fraktur-V auf Pergament, aus der Schrift im Repo gezeichnet). Zum
       Home-Bildschirm hinzugefügt, läuft die Seite im Vollbild ohne Browserleiste. Kein Service Worker: ohne Server gibt
       es nichts zu spielen.

192. **Spielbar am Handy; Handlung oder Rede** (nach dem Hinweis des Autors: erst spielbar, dann mehr):
     - Am Handy hält die Spielseite den Bildschirm nicht mehr fest. Sie fließt wie jede Webseite, Kopf und Eingabe kleben
       mit `position: sticky` oben und unten. Das Muster braucht weder `dvh` noch eine innere Scrollleiste und läuft auch
       in älterem Safari. Am Rechner bleibt der Chat mit eigenem Verlauf (Entscheidung 189).
     - Über dem Eingabefeld wählt man **Handlung** oder **Rede**. Bei Rede geht die Eingabe als `Ich sage: „…“` an den
       Spielleiter; so ist sie für jedes Modell eindeutig wörtliche Rede, ohne Änderung am Rahmen-Prompt. Im Verlauf
       steht sie kursiv in Anführungszeichen. Die Wahl bleibt über die Züge stehen; Vorschläge und Karte stellen auf
       Handlung. Befehle mit `!`, Würfe und Spielen ohne Spielleiter bleiben unverändert.

## Feinabstimmung Code und Modell

Oktober 2026, Gespräch mit dem Autor nach den ersten Spielversuchen.

193. **Zeitgrenze 60 Sekunden** (zu Entscheidungen 157 und 179): Gemini Flash brauchte im Härtetest bis 32 s je Aufruf,
     die Grenze lag bei 30 s; Züge scheiterten an Zeitüberschreitung. Der Standard im Code ist jetzt 60 s, `timeout_s`
     in `modelle.toml` geht weiter vor. Bei Zeitüberschreitung springt die Kette bewusst **nicht** zum nächsten
     Anbieter; der Zug scheitert ohne Wirkung wie bisher.

194. **Leitbild: ein echter Spielleiter mit Regelwerk in einer festen Welt.** Die Infrastruktur der Welt steht (Orte,
     Wege, Mächte, Preise); Geschichten und Figuren entstehen im Spiel, die Logik der Welt gilt immer. Erfolg bewirkt
     etwas, Scheitern kostet etwas. Das Modell erzählt und schlägt vor, der Code rechnet und hält die Welt stimmig;
     beide arbeiten zusammen, keiner blockiert den anderen. Der Autor will sich nie fühlen, als könne er nichts
     erreichen, und ebenso wenig: zu viel Geld, Erfolg ohne Wirkung, kein Scheitern, blasse NPCs, eine Welt, die sich
     widerspricht.
195. **Prioritäten:** Erzählung und Freiheit, dann Fairness und Konsequenz, dann Entdecken, dann Fortschritt.
196. **Vier Weltgesetze für jede Folge** (ändert die Entscheidungen 7, 8 und 60 teilweise; Gesetz 5 „Die Welt handelt
     auch ohne dich“ kommt später). Das Modell schlägt Folgen frei vor, auch harte, ohne Liste von Ausgängen; der Code
     prüft das Messbare allgemein, nicht je Fall:
     1. **Quelle:** Nichts Hartes entsteht aus dem Nichts. Geld, Waren, Wissen, Hilfe kommen von jemandem oder von
        irgendwo; die Quelle begrenzt die Menge (ein NPC höchstens seinen Beutel und sein Gepäck).
     2. **Maßstab:** Die Größe einer Wirkung wächst mit Einsatz und Ausgang (SG, Abstand, kritisch), für alles gleich:
        Preis beim Verhandeln, Lohn eines Auftrags, Gunst, Tiefe von Wissen. Ein Fehlschlag kostet, etwa halb so viel.
     3. **Wahrheit:** Was im Wiki oder im Spielstand steht, gilt. Erfunden wird nur, wo die Welt schweigt; das gilt dann
        in der Kampagne und geht in die Kanon-Prüfung.
     4. **Stetigkeit:** Nichts widerspricht dem Geschehenen. Haltungen, Orte, Absprachen ändern sich nur durch ein
        Ergebnis.

     Gemessen wird Materielles als **Wert in Tagelöhnen** (1 Tagelohn = 10 Pfennig, Referenzseite Preise und Löhne),
     Zwischenmenschliches und Zeit in **Schritten**. Zu Großes wird auf das Glaubhafte gekürzt, nicht abgelehnt. Nur ein
     herrenloser Fund braucht eine Obergrenze nach SG (Richtwert: SG 8 ½, 10 1, 12 3, 14 10, 16 30, 20 100, 24 300
     Tagelöhne). Würfe, Tod, Skills und Level, Reisezeit und Tageswechsel bleiben allein beim Code.
197. **NPCs mit Seele** (zu Entscheidung 11): Beim ersten Treffen erfindet das Modell, was der NPC will und wovor er
     Angst hat, je ein Satz, danach fest; ein Geheimnis nur bei wichtigeren NPCs. **Haltung zu dir** als harte Skala
     (feindlich, misstrauisch, neutral, gewogen, verbündet), sie ändert sich nur durch Ergebnisse und kann Proben gegen
     ihn erleichtern oder erschweren. **Gedächtnis:** ein paar Zeilen, was zwischen euch geschah, vom Modell ergänzt,
     immer im Kontext, wenn er anwesend ist.
198. **Die Welt bleibt stimmig** (zu Entscheidung 107): Fest sind Orte, Wege, Mächte, Preise und Grundcharakter (nur der
     Autor, über das Wiki). Lebendig ist ein **Orts-Gedächtnis** je Ort, höchstens etwa 5 Zeilen, vom Modell geschrieben,
     wenn ein Ergebnis den Ort sichtbar ändert, immer im Kontext am Ort, als Wahrheit. Getrennt werden **Weltfakt**
     (was stimmt) und **Wissen der Figur** (was sie gehört hat, auch Gerüchte).
199. **Kanon-Prüfung** (zu Entscheidungen 91, 104, 108): Eine Seite listet alles Weltrelevante seit der letzten Prüfung
     (NPCs, Orts-Gedächtnis, Weltfakten, Kampagnen-Seiten) mit Zeit und Zug. Je Eintrag: **Kanon** (Text änderbar),
     **nur diese Kampagne**, **verwerfen** (gilt danach auch in der Kampagne nicht mehr, ein bewusster Eingriff). „Übergabe
     erzeugen“ liefert die Kanon-Einträge als Datei; der PR läuft wie bisher. Die Synology schreibt nie ins Repo.
200. **Protokoll und Melden:** Je Modellaufruf Prompt, Antwort, Dauer und Fehler, nie Schlüssel, 30 Tage. Ein
     Melde-Knopf an jedem Zug (Fehler, unlogisch, zu hart, zu leicht, toll, dazu ein Satz). Fehler im Browser meldet die
     Seite selbst. Sitzung = Spiel zwischen Pausen über 30 Minuten. Export als Datei aus den Einstellungen; Ablage in
     einem **privaten** zweiten Repo, damit Claude über viele Sitzungen auswerten kann. Nichts davon ins öffentliche Repo.
201. **Vorschläge vom Modell** (ändert Entscheidung 190 teilweise): Aufruf B liefert bis zu 3 kurze Vorschläge zur
     Szene, je Handlung oder Rede, nur als Knöpfe, nie im Erzähltext (Regel 4 bleibt). Fest vom Code bleiben Umsehen und
     die Wege. Ein Schalter in den Einstellungen blendet die Vorschläge aus.
202. **Ablauf eines Zugs** (zu Entscheidungen 34 und 159): Zwei Aufrufe bleiben, erst rechnet der Code, dann erzählt das
     Modell. Aufruf A wird schlank, etwa 6 Absichten (erzählen, gehen, jemanden treffen oder reden, Versuch mit Einsatz
     auch gegen einen NPC, untersuchen oder sammeln, rasten), wenige Pflichtfelder, den Rest füllt der Code. Aufruf B
     bekommt die neue Freiheit (Folgen, Gedächtnis, Vorschläge). Der Rahmen-Prompt wird kürzer: Was der Code prüft,
     muss das Modell nicht als Regel lesen. Danach ein neuer Härtetest.
203. **Wiki für die Gesetze:** Orte bekommen **Wohlstand** (1 bis 5) und **Herrschaft** (Link auf eine Macht) als harte
     Felder; Orte und NPC-Typen feste Überschriften im Text (Ort: Stimmung, Leute, Konflikte; NPC-Typ: Stimme, Will,
     Fürchtet). Preise bleiben auf der Referenzseite. Entwürfe schreibt ein Agent über Übergaben, der Autor prüft per
     PR. **Gefahr** je Ort kommt später mit Gesetz 5.
204. **Reihenfolge, je Paket eine Sitzung:** (1) Protokoll und Melde-Knopf mit Export, dazu die Zeitgrenze aus 193;
     (2) Wiki-Grundlagen aus 203 mit Entwürfen; (3) Umbau des Zugs aus 196 bis 198, 201, 202, vermutlich zwei
     Sitzungen; (4) neuer Härtetest; (5) Kanon-Prüfung aus 199.

## Umsetzung Paket 1: Protokoll und Melde-Knopf

Oktober 2026, nach Entscheidung 200; die Zeitgrenze aus 193 war schon umgesetzt.

205. **Protokoll in eigener Datei** (Antwort des Autors auf die Frage nach der Schema-Version, zu Entscheidung 52):
     Protokoll, Meldungen, Fehler aus dem Browser und Exporte liegen in `protokoll.db` im Datenordner neben dem
     Spielstand, mit eigener Version (1). Sie sind kein Spielzustand. Der Spielstand bleibt bei Version 11, nichts wird
     migriert, die Figuren bleiben. Die Datei ist nur für den Server lesbar (0600). Ist sie kaputt oder unlesbar, läuft
     das Spiel ohne Protokoll weiter; die Einstellungen sagen es. Ein Fehler beim Schreiben bricht nie einen Modellaufruf
     oder Zug ab. Die Frage aus „Offen“ für Paket 3 bleibt, dort geht es um echten Spielzustand.
206. **Melde-Knopf** unter jedem Zug im Verlauf, leise rechts, auch an älteren nachgeladenen Zügen: aufklappbar, eine
     von fünf Arten (Fehler, unlogisch, zu hart, zu leicht, toll) und ein Satz dazu, freiwillig, höchstens 300 Zeichen.
     Gespeichert werden Art, Satz, Figur, Zug (seine request_id) und als Abschrift Eingabe und Erzählung, damit der
     Export für sich lesbar ist. Danach steht am Zug „Gemeldet: …“; melden lässt er sich weiter. Jedes Formular hat eine
     eigene request_id: doppelt gesendet zählt es einmal, mit anderer Meldung wird es abgelehnt. Ohne Skript ein
     Formular, das zur Spielseite zurückführt; mit Skript ohne Neuladen. Melden ändert keinen Spielstand. Einen Knopf
     gibt es nur an Wechseln; ein gescheiterter Aufruf ohne Wechsel steht ohnehin im Protokoll.
207. **Was im Protokoll steht:** Je Modellaufruf Zweck (Aufruf A `aktion`, Aufruf B `erzaehlung`), Anbieter, Modell,
     Versuch (1 oder 2), Dauer in Millisekunden, Ergebnis (ok, ungültig, Kontingent, Fehler), HTTP-Code, Fehler, Tokens,
     der ganze Prompt und die Antwort, dazu Figur und Zug. Schlüssel: Der Schlüssel wird aus jedem Text entfernt; von
     Fehlerantworten bleiben höchstens 500 Zeichen, bei 401 und 403 nur der Code, weil Anbieter dort Teile des Schlüssels
     wiederholen. Alles bleibt 30 Tage, auch Meldungen und Fehler aus dem Browser; aufgeräumt wird beim Start und mit
     jeder neuen Sitzung. **Sitzung:** Jeder Zug, Aufruf, jede Meldung und jeder Fehler aus dem Browser zählt als
     Spiel; nach einer Pause über 30 Minuten beginnt eine neue, fortlaufend nummeriert. Protokolliert wird nur die
     Weboberfläche; Kommandozeile und Härtetest schreiben keins.
208. **Fehler aus dem Browser** meldet `app.js` selbst an `/fehler`: Skriptfehler (`error`, `unhandledrejection`) und
     Verbindungsabbrüche (Zug, Melden, ältere Züge nachladen, unlesbare Antwort). Was nicht ankommt, wartet im Speicher
     des Browsers (höchstens 10, auch über ein Neuladen) und geht mit der nächsten Antwort des Servers oder beim
     Wiederverbinden hinaus. Gegen Schleifen: je Seite höchstens 20 Meldungen, der Server nimmt höchstens 100 je Stunde.
     Gespeichert werden Art, Meldung (500 Zeichen), Seite und Quelle ohne `?`-Teil, Zeile, Spalte, Stapel (2000) und der
     Browser (User-Agent, 300), ohne Steuerzeichen. Es gelten die Schutzregeln aus 174, 184 und 186: request_id, Herkunft,
     Größe, nur Formulare.
209. **Export** unter „Einstellungen“: Die Seite zeigt, was seit dem letzten Export dazukam. „Exportieren“ (Formular mit
     request_id) liefert alles seit dem letzten Export als Datei `valar-protokoll-DATUM-NR.json` (Sitzungen, Aufrufe,
     Meldungen, Fehler aus dem Browser) und merkt sich, bis wohin. Dieselbe request_id liefert dieselbe Datei; ein
     früherer Export lässt sich noch einmal laden, so weit seine Einträge noch nicht gelöscht sind. Die Datei gehört in
     ein **privates** zweites Repo; `.gitignore` hält sie aus dem öffentlichen. Bewusst in Kauf genommen (wie 183): Wer
     im Heimnetz ist, kann den Export laden; Schlüssel stehen nicht darin.

## Anbieter nicht erreichbar, Version und Ping

Oktober 2026, nach dem Update auf der Synology: Weder Gemini noch Mistral antworteten, die Seite zeigte nur einen
Fehler. Aus der Cloud-Sitzung geprüft: Gemini 3.8 Flash war überlastet (einmal HTTP 503, sonst 57 s bis zur
Zeitgrenze von 60 s), Gemini 3 Flash Preview und 2.5 Flash antworteten in 1 bis 3 s. Weil nur 429 zum nächsten
Anbieter führte, kam Mistral nie dran. Ob der Mistral-Schlüssel des Autors gilt, ließ sich von dort nicht prüfen.

210. **Überlastung führt zum nächsten Anbieter** (ändert Entscheidung 154 teilweise, vom Autor gewählt): HTTP 503
     („überlastet, nicht verfügbar“) springt wie 429 zum nächsten Anbieter; im Protokoll steht der übersprungene Aufruf
     mit Code und Anfang der Antwort. Zeitüberschreitung (Entscheidung 193) und andere Fehler beenden die Kette weiter,
     damit ein Zug nicht zweimal 60 s wartet. Die Vorlage `modelle.example.toml` bekommt nach Mistral eine Reserve
     `gemini-reserve` mit Gemini 3 Flash Preview und demselben freien Schlüssel; nicht im Härtetest geprüft.
211. **Version in den Einstellungen:** Die CI schreibt beim Veröffentlichen Commit und Bauzeit ins Abbild
     (`VALAR_VERSION`, `VALAR_BUILT`). Die Einstellungen zeigen „Build abc1234 vom …“ mit Link auf den Commit; ohne
     Build der CI steht „Entwicklung“ da. So sieht man auf der Synology, ob das Neu-Erstellen die neue Version geholt hat.
212. **Ping an den Anbieter:** Nach dem Speichern eines Schlüssels und mit dem Knopf „Prüfen“ fragt der Server die
     Modellliste ab (`GET …/models`, höchstens 10 s). Das kostet nichts, verbraucht kein freies Kontingent und zeigt:
     erreichbar, Schlüssel gilt, Modell bekannt; sonst den Grund (Schlüssel ungültig, Kontingent erschöpft, überlastet,
     Fehler, nicht erreichbar). Eine Überlastung nur des Modells zeigt die Liste nicht; die steht beim Spielen im
     Protokoll. Das Ergebnis steht unter dem Anbieter, bis der Server neu startet, und als Aufruf `ping` im Protokoll;
     Text des Anbieters zeigt die Seite nicht. Scheitert die Prüfung selbst, bleibt der Schlüssel gespeichert.

213. **Kein Guthaben führt zum nächsten Anbieter, „Prüfen“ ruft das Modell auf** (vom Autor gewählt nach dem Update:
     „Prüfen“ war grün, gespielt wurde mit Fehler 402). HTTP 402 springt wie 429 und 503 zum nächsten Anbieter (ändert
     154 weiter teilweise). Die Modellliste darf jeder gültige Schlüssel abrufen, auch ohne Guthaben; darum folgt
     auf sie ein winziger echter Aufruf (`Antworte nur mit: OK`, höchstens 5 Tokens, 20 s). Erst er zeigt fehlendes
     Guthaben (mit dem Hinweis auf einen Gemini-Schlüssel aus einem Projekt ohne Abrechnung), ein erschöpftes
     Kontingent oder ein überlastetes Modell. Er zählt ins Budget wie jeder Aufruf und unterbleibt beim Budget-Stopp;
     im Protokoll steht er als `ping-aufruf`. Scheitert schon die Liste, folgt kein Aufruf (ändert 212 teilweise).
214. **OpenRouter auch im Spielbetrieb** (vom Autor gewählt am 26.10.2026: Gemini 3.8 Flash und
     `gemini-3-flash-preview` meldeten beide 503, ein Mistral-Schlüssel fehlte). Ändert 153 Punkt 4 und 181: Die
     5,5 % Gebühr beim Aufladen werden in Kauf genommen. In der Vorlage steht OpenRouter mit Mistral Medium 3.1 hinter
     den freien Gemini-Modellen und vor Gemini Flash-Lite; die Preise enthalten die Gebühr. Bei OpenRouter verlangt
     der Client wie der Härtetest einen Unteranbieter, der `response_format` kann (`require_parameters`).

## Spieltest am PC

26.10.2026, lokal auf dem PC des Autors, 12 Züge mit einer Zufallsfigur über OpenRouter (Mistral Medium 3.1).
Gemini antwortete nicht (503 oder 60 s bis zur Zeitgrenze); Proben, Boni, Ticks, Zeit und Wege stimmten. Der
Spielleiter erzählte aber Ausgänge vor dem Wurf, notierte nach einem Fehlschlag ein Gerücht, erfand Zahlungen und
einen Weg nach „Eisenburg“, und keine der vier sprechenden Personen wurde als NPC angelegt. Vom Autor gewählt:

215. **OpenRouter auf Platz 1** (ändert 214): In der Vorlage steht OpenRouter vor den freien Gemini-Modellen, bis
     Gemini wieder verlässlich antwortet. Jeder Zug kostet damit etwa 0,3 Cent.
216. **Drinnen wird nicht gesammelt:** Ein Ort des Inhalts kann `indoors` sein (im Testgebiet Zunfthaus und Schenke).
     Dort ist nichts sammelbar und `gather` wird abgelehnt (`not_here`); was dort vorkommt, steht weiter im Kontext.
     Dazu `titles` im Inhalt: Anzeigenamen für IDs ohne Wiki-Seite (`crystal_shard` → Kristallsplitter); die IDs
     bleiben, damit alte Spielstände gelten.
217. **Aufruf B legt neue Personen an** (zu Entscheidung 11): Erzählung und „nur erzählen“ dürfen bis zu zwei `npcs`
     mitgeben (Vorlage, Name, ein Satz). Jede wird ein eigener Spielzug `introduce_npc` (`<id>/p1`, `/p2`) vor den
     Notizen: wie `meet_npc`, Werte würfelt der Code, aber ohne eigene Zeit. Eine Notiz darf die neue Person mit
     ihrem Namen als Quelle nennen; der Code setzt die NPC-ID ein. Eine unbekannte Vorlage fällt allein weg.
218. **Zufälliger Einstieg:** Nach dem Erschaffen erzählt der Spielleiter eine Eröffnungsszene am Startort, gebaut
     aus ein bis zwei zufälligen Seiten, die dort vorkommen (Ereignis, NPC-Typ, Brauch), und endet in einer Lage, die
     sofort eine Entscheidung verlangt, ohne Möglichkeiten aufzuzählen. Nur Erzählung, Notizen und neue Personen,
     keine Aktion und keine Zeit; im Wechsel steht die Eingabe `(Einstieg)`, die Seite zeigt sie nicht. Scheitert
     der Aufruf, steht die Figur trotzdem und die Spielseite bietet „Einstieg erzählen“ an, einmal je Figur.
219. **Strengere Erzählung** (zu Regeln 3, 5, 6, 8 und 9 des Rahmen-Prompts): Bei offener Probe nur der Anlauf bis
     kurz vor der Entscheidung, ohne Reaktion, Geräusch oder neue Information. Nach Fehlschlag, knapp daneben oder
     Patzer verwirft der Code alle Notizen der Erzählung. Eine Zahlung oder Gabe, die nicht im Ergebnis steht, ist
     nicht geschehen. Unbekannte Orte ohne Entfernung und Dauer. Kein Markdown; kommt doch `*…*`, zeigt die Seite es
     kursiv. Schlafen bleibt jederzeit erlaubt.
220. **Geld geben** (vom Autor gewählt, weil die Regel im Prompt allein nicht hielt: „Lorenz steckt den Groschen
     ein“, das Geld blieb gleich): neue Aktion `give_money` mit Betrag in Pfennig, Grund und wahlweise einem NPC hier.
     Der Code zieht den Betrag ab (Effekt `pay_money`) und gibt ihn dem NPC; ohne NPC geht er an niemanden Bekanntes
     (Wirt, Bettler). Mehr als die Figur hat: abgelehnt (`not_enough_money`). Kein Wurf, keine Zeit, angeboten nur mit
     Geld in der Tasche. Den Betrag nennt der Spieler; einen Preis prüft der Code erst mit dem Handel (Entscheidung 136).

## Umsetzung Paket 2: Wiki-Grundlagen

26.10.2026, nach Entscheidung 203.

221. **Felder und Überschriften:** Orte haben im Block `valar` die Felder `wealth` (Wohlstand 1 bis 5: bettelarm,
     arm, auskömmlich, wohlhabend, reich) und `ruled_by` (Herrschaft, muss auf eine Macht zeigen). Orte haben im Text
     die Überschriften `## Stimmung`, `## Leute`, `## Konflikte`, NPC-Typen `## Stimme`, `## Will`, `## Fürchtet`.
     Ein falscher Wert oder eine Herrschaft, die keine Macht ist, ist ein Fehler; fehlende Felder oder Überschriften
     sind nur Hinweise, damit ältere Seiten und Übergaben weiter laden (die CI verlangt aber ein Welt-Wiki ohne
     Hinweise). Wohlstand und Herrschaft stehen im Kontext in der Zeile „Ort“, unter „Umsehen“ und im Terminal; ein
     Unterort ohne eigene Seite nimmt die seines Orts. Die Entwürfe für alle 7 Orte und 10 NPC-Typen gliedern den
     bisherigen Text neu und ergänzen nur, was aus den verlinkten Seiten folgt; alle Orte stehen unter den Fürsten.

## Feinschliff nach Messung

222. **Der Ort laut Code steht in Aufgabe A** (Spieltest 26.10.2026): „Ich gehe hinaus nach Sturmhagen“ wurde nur
     erzählt, die Figur blieb im Zunfthaus. Ursache war der Verlauf: Der Einstieg hatte die Szene „vor dem
     Zunfthaus“ erzählt. Gemessen mit derselben protokollierten Nachricht über OpenRouter (Mistral Medium 3.1): mit
     altem und mit geändertem Rahmen-Prompt (ohne „Im Zweifel narrate“) je 0 von 3 Mal `move`; mit dem Satz „Die Figur
     ist laut Code jetzt hier: …; will sie an einen anderen Ort, wähle move“ in Aufgabe A 4 von 4. Darum bleibt der
     Rahmen-Prompt, und Aufgabe A nennt den Ort. Der Einstieg spielt ausdrücklich am Ort, drinnen, wenn er drinnen
     ist. Prompt-Änderungen werden künftig so gemessen: protokollierte Aufrufe erneut schicken, alt gegen neu.

## Vorschläge aus der Recherche

26.10.2026, vom Autor übernommen nach einer Recherche zu LLM-Spielleitern (Muster anderer Projekte und Studien).

223. **Gestörte Anbieter ruhen** (ändert 193 und 210; Muster „Circuit Breaker“): Keine Antwort (Zeitüberschreitung,
     Netz) sowie HTTP 402, 429, 502, 503 und 504 führen zum nächsten Anbieter, und der gestörte ruht 5 Minuten. Ein
     ruhender Anbieter rückt in der Kette nach hinten, fällt aber nie weg: Scheitern alle anderen, kommt er doch dran.
     Antwortet er oder ist „Prüfen“ grün, ist er sofort wieder vorn. So wartet ein Zug nicht bei jedem Zug erneut 60 s
     auf ein überlastetes Modell (Spieltest: 26 s und 60 s je Zug). Echte Fehler (401, 500) beenden den Zug weiter.
     Die Ruhe gilt im Serverprozess und ist nach einem Neustart vergessen; die Einstellungen zeigen „Ruht noch N Min.“.
224. **Nachspielen statt raten:** `python -m valar.replay` schickt protokollierte Aufrufe erneut, wahlweise alle zu
     gemeldeten Zügen (`--gemeldet`) oder nach ID, mit einem oder mehreren Rahmen-Prompts aus Dateien (`--prompt`,
     alt gegen neu) und zählt, was zurückkommt (in Aufruf A die Aktion mit Ziel). Ersetzt wird nur der Rahmen-Prompt;
     Kontext und Aufgaben bleiben wie protokolliert. Jeder Aufruf zählt ins Budget, beim Stopp geht nichts hinaus;
     Schlüssel erscheinen nie. So werden Meldungen aus dem Spiel zu Messungen, bevor ein Prompt geändert wird.
225. **Mehrere Schritte je Zug** (ändert 10 und 142 teilweise): Aufruf A antwortet weiter mit `{"action": …}`, dazu
     `then` mit höchstens 2 weiteren Schritten derselben Eingabe (ohne narrate). Der Code führt sie nacheinander als
     eigene Spielzüge `<id>/b`, `<id>/c` aus und hört bei einer Ablehnung oder offenen Probe auf; Aufruf B bekommt
     alle Schritte („Danach: …“, „Nicht mehr ausgeführt: …“) und erzählt sie zusammen. Steht vorne narrate und
     dahinter Schritte, gelten die Schritte. In `then` darf `move` jeden Ort des Inhalts nennen; ob er verbunden ist,
     prüft der Code. Wiederholung derselben request_id, nachgeholte Erzählung, Chronik und Verlauf kennen die Schritte;
     nach einem Absturz gelten die schon gespeicherten, der Rest entfällt. Gemessen über OpenRouter (je 3 Läufe,
     frischer Spielstand): Umsehen nur narrate, Hinausgehen nur move, „über Sturmhagen zum Glasgrund“ und „aus der
     Schenke zum Glasgrund“ beide Male richtig über zwei Orte; „zwei Groschen hinlegen und dann in die Schenke“ endete
     dreimal nach Geld und erstem Weg in Sturmhagen (ein Schritt fehlt, nichts falsch).
226. **Wissen pro Person: gemessen, nicht per Prompt gelöst.** Im Spieltest wusste der Wirt, dass die Figur bei der
     Stadtwache war (steht nur in ihrem Hintergrund). An derselben Nachricht je 5 Läufe: wie protokolliert 0 von 5,
     mit dem Satz „Ein NPC weiß über die Figur nur, was er sieht oder was sie ihm gesagt hat“ in Regel 7 ebenfalls
     0 von 5, mit einer Kennzeichnung am Hintergrund („kennt nur die Figur …“) 2 von 5, also schlechter. Das Leck ist
     selten, und ein Hinweis lenkt erst recht darauf; darum bleibt beides weg. Der eigentliche Weg ist das Gedächtnis
     je NPC aus Entscheidung 197 (was zwischen euch geschah, was er weiß) in Paket 3, mit neuer Schema-Version.

## Offen (eigene Sitzungen)

Jeder Punkt nennt den Schritt, den er blockiert.

- **Spielstand beim Umbau (Paket 3):** Neue Tabellen und Felder brauchen eine neue Schema-Version. Ohne Migration
  (Entscheidung 52) wären die Figuren des Autors weg. Vor Paket 3 fragen: neu anfangen oder eine kleine Migration.
- **Gesetz 5 „Die Welt handelt auch ohne dich“** und **Gefahr** je Ort (Entscheidungen 196 und 203). Nach Paket 4.

- NPC-Vorlage für Wirt oder Schankwirtin fehlt; ohne sie legen die Modelle Wirtsleute nicht als NPC an.
  *Blockiert den Kern nicht.*
- Reisen über mehrere Orte plant das Modell mit `then` (Entscheidung 225); bei drei Schritten fehlt manchmal der
  letzte. Falls das stört: Der Code sucht den Weg selbst. *Blockiert den Kern nicht.*
- Schlüssel für den Spielbetrieb (Entscheidung 181): Gemini aus einem Google-Projekt ohne Abrechnung, Mistral mit
  dem Plan „Experiment“. Der Autor legt sie an. **Blockiert das Spielen mit Spielleiter, nicht den Code.**
- Wann der Code Ereignisse mit Häufigkeit aus dem Wiki würfelt (Reise, Szene, Tageswechsel).
  *Blockiert den Kern nicht.*
- Wichtige NPCs der Startregion. *Blockiert den Kern nicht* (Testinhalt), nur den echten Weltinhalt.
- Rationen kaufen geht erst mit dem Handel; bis dahin nur der Startvorrat. *Blockiert den Kern nicht.*
- Wie der Code im Handel einen Preis des Modells prüft (Rahmen um die Referenzwerte, Entscheidung 136);
  dazu die Startausrüstung. *Blockiert den Kern nicht* (1i nutzt eine Testliste), nur den Handel.
- K.O.-Folgentabelle, Monsterarten, Mutationstabellen (Tiere und Menschen). Blockiert den Kampf, nach dem Kern.
- Zahlen für Energie: Körperladung, Kosten und Effekte der fünf Wirkungen, Sturmbrand, Entladungsrate.
  Blockiert Magie und die Begabung „Ziehen“ bei der Erschaffung, nach dem Kern.
- Materialien (MAT) aus der alten Engine prüfen: passen sie zu Valar? Blockiert Handwerk, nach dem Kern.
