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

## Offen (eigene Sitzungen)

Jeder Punkt nennt den Schritt, den er blockiert.

- Gedächtnis des Modells: genaue Zahlen für Budget, Verlauf und Zusammenfassung (Rahmen: Entscheidungen 106, 107,
  137 bis 143); ob eine Modellantwort
  Aktion und weiche Einträge in einem Spielzug bündelt. **Blockiert 2b.**
- Rahmen-Prompt (Entscheidung 35). **Blockiert 2b.**
- Monatsbudget der Kostenbremse, Anbieter-Konto und Schlüssel. **Blockiert 2a (Budget) und 2c.**
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
