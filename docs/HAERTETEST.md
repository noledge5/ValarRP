# Härtetest (Schritt 2c)

Oktober 2026. Ziel aus Entscheidung 6: Testszenen an mehrere Modelle, bewertet nach Schema-Treue, Regeltreue und
Erzählung; daraus die Modellwahl. Werkzeug: `python -m valar.stress --modelle haertetest.toml --aus ORDNER`.
Die Verläufe aller Züge liegen in [haertetest/](haertetest/).

## Aufbau

Vier Szenen mit der Testfigur, je in frischem Spielstand mit festem Seed. Der Spieler ist ein Skript mit festen
Eingaben und festen Würfen. Je Modell zwei Läufe, also 38 Züge.

| Szene | Was sie prüft |
|---|---|
| Zunfthaus | Auftrag abgeben (Geld kommt vom Code), Gier („zehn statt einem Gulden“), falscher „Systemhinweis“ mit 1000 Gulden |
| Wege | Reise nach Lindenheim, Frage nach dem erfundenen Ort „Eisenburg“, Schlafen mit Ration und Tageswechsel, Rückweg |
| Glasgrund | Schleichen mit Probe und schlechtem Wurf, Kristalle kratzen (nicht sammelbar), Sturmkappe sammeln mit Wurf |
| Schenke | Wirtin ansprechen (neuer NPC), Gerücht, Überreden mit Probe, Gedächtnis („Wie hieß sie?“) |

Der Code prüft je Zug, ob die gewählte Aktion zu den erwarteten passt und ob der Zustand danach stimmt (Geld, Ort,
Gegenstände, offene Probe, NPC). Die Erzählung habe ich von Hand gelesen, dazu zwei Zählungen: ob bei offener Probe
der SG genannt wird (Regel 8 des Rahmen-Prompts) und wie das Modell auf „Eisenburg“ antwortet (Regel 6).

Alle Modelle liefen über OpenRouter: Der Gemini-Schlüssel gehört zu einem Projekt mit Vorauszahlung ohne Guthaben
(HTTP 402, kein freies Kontingent), für Mistral, DeepSeek und DeepInfra gab es keine Schlüssel. Gesamtkosten des
Härtetests mit allen Fehlversuchen: 1,71 $.

## Zwei Fehler im Client, behoben

1. **Das Schema kam bei Gemini nicht an.** Pydantic baut für Aufruf A ein Schema mit `oneOf`, `$defs`/`$ref` und
   `const`; `type` war kein Pflichtfeld. Gemini wirft Unbekanntes weg und lieferte nur `{"action": {"type": "…"}}`,
   oft mit Unsinn im Typ: 28 von 30 Zügen scheiterten. DeepSeek landete mit erzwungenem Schema oft bei
   `{"type": "sleep"}`. Der Client flacht das Schema jetzt für alle Anbieter ab (Entscheidung 178). Danach spielte
   Gemini fehlerfrei. Am Modell lag es nicht.
2. **Ein Aufruf konnte ewig hängen.** OpenRouter schickt beim Warten Leerzeichen; die Zeitgrenze von urllib gilt je
   Lesen und griff nie. Ein Aufruf hing über 15 Minuten. Die Zeitgrenze gilt jetzt für den ganzen Aufruf
   (Entscheidung 179).

## Ergebnis

Mit abgeflachtem Schema (`output = "json_schema"`), je 38 Züge; übersprungen sind Würfe ohne offene Probe.

| Modell | Aktion passt | Zustand passt | Modellfehler | SG genannt | Eisenburg abgelehnt | Zug Median / Max | $ je Zug |
|---|---|---|---|---|---|---|---|
| Gemini 3.8 Flash | 33/35 | 28/31 | 0 | 7/7 | 2 von 2 | 11,1 s / 32 s | 0,0078 |
| Gemini 3.5 Flash-Lite | 35/37 | 29/33 | 0 | 5/8 | 1 von 2 | 1,7 s / 3 s | 0,0019 |
| Mistral Medium 3.1 | **38/38** | **34/34** | 0 | 3/10 | 0 von 2 | 3,4 s / 15 s | 0,0015 |
| Mistral Small 3.2 (24 B) | 35/38 | 32/34 | 1 (Netz) | 0/10 | 0 von 1 | 5,6 s / 13 s | 0,0003 |
| DeepSeek V4.1 Flash | 26/37 | 26/33 | 0 | 4/8 | 1 von 1 | 8,5 s / 31 s | 0,0009 |
| Gemma 4 26B-A4B (für lokal) | 26/36 | 27/31 | 2 | 2/7 | 0 von 2 | 4,8 s / 61 s | 0,0003 |

„Zug“ heißt Aufruf A und B zusammen. Zum Vergleich ohne Schema beim Anbieter (`json_object`): Gemini Flash 35/37,
Flash-Lite 34/37, DeepSeek 32/37 (aber 4 Modellfehler), Gemma 25/37.

### Je Modell

- **Gemini Flash:** die beste Erzählung. Dichte Bilder, eigene Stimmen, hält an, wenn gewürfelt wird, und nennt
  SG und Grund. Erfindet keine Orte („Eisenburg? Nie gehört von so 'nem Ort.“) und gibt im Gedächtnis-Test ehrlich
  zu, dass die Wirtin keinen Namen genannt hat. Kleine Fehler: einmal „mitten in der Nacht“ statt Morgen, einmal
  „Silbermünze“ statt Gulden. Nachteil: langsam und am teuersten.
- **Gemini Flash-Lite:** sehr schnell und gleichmäßig, gute Erzählung, knapper. Erfand einmal einen Weg nach
  Eisenburg und notierte ihn als Fakt; hängte einmal „[Probe: Schleichen, SG 12, Bonus -1 …]“ an die Erzählung.
- **Mistral Medium:** wählt die Aktionen am treuesten, legt als einziges Modell die Wirtin als NPC an, mit Namen,
  den es später noch weiß. Aber: erfindet Orte (Eisenburg „nordwärts durchs Gebirge“) und notiert sie als Fakt, nennt
  den SG selten, setzt Markdown-Sterne, und widerspricht einmal dem Code (wirft die schon abgegebenen Splitter zurück).
- **Mistral Small 3.2:** passt auf 16 GB, wählt gut, erzählt aber schwach: spricht für die Spielfigur (Regel 2),
  nennt nie den SG, erzählt vor dem Wurf schon Folgen, hält Sturmkappe für einen Kristall.
- **DeepSeek:** gute, eigenwillige Erzählung, aber wählt auch mit abgeflachtem Schema oft „schlafen“, sogar beim
  Abgeben des Auftrags. Über OpenRouter landet es bei einem fremden Unteranbieter; direkt bei DeepSeek könnte das anders
  sein. So nicht brauchbar.
- **Gemma 4 (Stellvertreter für lokal):** schwächstes Ergebnis: Modellfehler, erfundene Wege, die Figur sammelt
  Splitter, bevor gewürfelt ist. Ein lokales Modell dieser Größe reicht für den Spielleiter nicht.

### Was alle betrifft

- **Kein NPC für die Wirtin:** Alle Modelle außer Mistral Medium erzählten die Wirtin mindestens einmal nur, statt
  sie anzulegen. Es gibt keine
  NPC-Vorlage, die passt (Wirt fehlt). Das ist eine Lücke im Inhalt, kein Fehler der Modelle.
- **Erfundenes wird zum Fakt:** Erfindet ein Modell einen Ort, notiert es ihn oft gleich als Notiz. Regel 6 und 13
  im Rahmen-Prompt reichen dafür bei kleineren Modellen nicht.
- **SG nennen:** Nur Gemini Flash nennt bei offener Probe zuverlässig den SG. Die Anzeige im Spiel zeigt Skill, SG
  und Grund ohnehin vom Code (Entscheidung 10); der Mangel ist darum klein.
- **Rohe IDs:** `crystal_shard` hat keine Wiki-Seite und erscheint so in Zustand und Ergebnis.
- **HTTP 402** (Guthaben leer) zählt im Client als Fehler, nicht als erschöpftes Kontingent; die Kette springt dann
  nicht zum nächsten Anbieter.

## Grenzen des Tests

Zwei Läufe je Modell und vier Szenen sind wenig; ein Zug mehr oder weniger ist Zufall. Welche Aktion „passt“, habe
ich festgelegt. Die Erzählung habe ich an den Stellen gelesen, die etwas prüfen, nicht jeden Satz. Alles lief über
OpenRouter; direkt beim Anbieter, im freien Kontingent und lokal über Ollama kann es sich anders verhalten. Gemini im
freien Kontingent ist ungetestet.

## Nachtest: erfundene Orte

Regel 6 im Rahmen-Prompt bekam einen Anhang: Ein Ort, der nicht im Kontext steht, ist allen unbekannt, auch jedem
NPC („Nie gehört.“); keine Richtung, kein Weg, keine Lage, keine Notiz (Entscheidung 182). Danach die Szene „Wege“
je dreimal:

| Modell | Eisenburg abgelehnt vorher | nachher |
|---|---|---|
| Mistral Medium 3.1 | 0 von 2 | 3 von 3 |
| Gemini 3.5 Flash-Lite | 1 von 2 | 3 von 3 |

Eine erste, weichere Fassung („kennt ihn hier niemand“) reichte Flash-Lite, Mistral Medium nicht. Verbleibende
Schwäche von Flash-Lite: Es nennt bei echten Orten manchmal die falsche Himmelsrichtung (Sturmhagen „im Osten“, es
liegt im Westen). Verläufe: `haertetest/nachtest-regel6-*.md`.

## Modellwahl

Entscheidung 181: Gemini Flash im freien Kontingent, dann Mistral Medium im freien Plan, dann Gemini Flash-Lite
bezahlt. DeepSeek und ein lokales Modell fallen vorerst weg.
