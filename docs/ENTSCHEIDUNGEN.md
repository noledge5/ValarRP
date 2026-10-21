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

## Offen (eigene Sitzungen)

- Namen: Startregion, Bretterstadt, Dörfer, die zwei Fürsten, Kirche, Zunft, Städtebund, Stürme.
- Wichtige NPCs der Startregion.
- Gedächtnis des Modells: was es pro Zug an Zustand, Verlauf und Fakten bekommt.
- Zahlen für Energie: Körperladung, Kosten und Effekte der fünf Wirkungen, Sturmbrand, Entladungsrate.
- Mutationstabellen (Tiere und Menschen), K.O.-Folgentabelle, Monsterarten.
- Preisliste, Startausrüstung.
- Ticks für SG 24 „Legendär“ (Vorschlag: +8).
- Materialien (MAT) aus der alten Engine prüfen: passen sie zu Valar?
