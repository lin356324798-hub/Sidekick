# Sidekick

**Ein lokaler KI-Agent, der auf deinem Android-Gerät läuft.** Eine einzige Python-Datei, keine Fremdabhängigkeiten, der Browser als Oberfläche.

Er chattet nicht nur — er **bedient dein Gerät tatsächlich**: Dateien lesen und schreiben, Befehle ausführen, Code bearbeiten, im Web surfen, dein Handy steuern, Tabellen erstellen, Bilder bearbeiten.

> `Sidekick` ist nur der Standardname ab Werk. Auf der Seite „Identität“ kannst du ihn in jeden beliebigen Namen ändern.

**Sprachen:** [English](README.en.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-TW.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | **Deutsch** | [Français](README.fr.md) | [Русский](README.ru.md) | [العربية](README.ar.md)

---

## Was ihn von anderen KI-Assistenten unterscheidet

### 1. Idiotensichere Installation — zwei Befehle, kein dritter Schritt

Keine Python-Kenntnisse nötig. Kein Umgebungs-Setup. Keine Datenbank. Termux installieren, zwei Zeilen einfügen, Browser öffnen — fertig.

```bash
pkg install -y python curl
bash install.sh
```

Kein Docker, kein Node, keine Abhängigkeitskonflikte. Er nutzt **ausschließlich die Python-Standardbibliothek**, deshalb können Upstream-Updates ihn nicht kaputtmachen — läuft er heute, läuft er auch in drei Jahren noch.

### 2. Er entwickelt sich jeden Tag selbst weiter — keine Pflege, keine Remote-Updates

Das ist das Ungewöhnlichste: **Er wird jeden Tag ein wenig besser, und niemand muss dir ein Update schicken.**

Jeden Tag überprüft er seine eigene Leistung, findet etwas Verbesserungswürdiges, setzt die Änderung um und schreibt sie in ein Protokoll. Hier echte Arbeit, die er von allein erledigt hat:

| Datum | Was er selbst geändert hat |
|---|---|
| 09-28 | „Nach Typ archivieren“-Hinweise zu `list_dir` hinzugefügt — beim Auflisten sagt er jetzt, dass Bilder in `Pictures/` und Dokumente in `Documents/` gehören |
| 09-28 | Bei falsch geschriebenen Tool-Argumenten werden nicht unterstützte Argumente still verworfen und der korrekte ähnliche Name vorgeschlagen (z. B. `cwd` → `command`) |
| 09-29 | `read_file` gibt bei Binärdateien keinen Zeichensalat mehr aus, sondern meldet Dateityp und -größe |
| 10-01 | Konsolenkarte auf feste Höhe gesetzt — Tests zeigten, dass sich der Speichern-Button beim Tab-Wechsel um 447 px verschob und Fehleingaben verursachte |

> Das sind keine Werbesprüche. Es sind Auszüge aus seinem eigenen Evolutionsprotokoll. Läuft er auf deiner Maschine, entwickelt er Verbesserungen, die **für dich relevant** sind.

**Was das bedeutet:** Dein Agent und der eines anderen werden nach einem Monat Nutzung auseinanderdriften. Lässt du ihn oft Fotoalben sortieren, entwickelt er eine stärkere Bildverarbeitung. Nutzt du ihn für Berichte, entwickelt er einen flüssigeren Dokument-Workflow. **Jedes Gerät zieht einen einzigartigen Agenten groß.**

### 3. Er repariert seine eigenen Bugs — und du kannst beim Diagnostizieren zusehen

Wenn das Programm abstürzt, hinterlässt es nicht nur einen Stacktrace. Es hat einen eingebauten **Selbstheilungsmechanismus**: Absturz erkennen → eigene Logs zum Lokalisieren lesen → Code korrigieren → verifizieren → Ergebnis protokollieren.

Ein echter Eintrag (aus `selfheal.json`):

```
10-01 14:22  Absturz erkannt   Watchdog-Log · Fingerabdruck f99407371b41
10-01 14:23  Heilung fertig    Bestätigt — Zeile 2809 ist unabhängiger
                               Log-Lesecode, und tool_wx_auto ist definiert.
                               Keine weiteren Änderungen nötig.
```

Er **kippt sogar seine eigene falsche Schlussfolgerung**: In einem anderen Selbstheilungslauf kam er zu dem Ergebnis „Das ist kein zu behebender Bug — die Datei hat sich bereits selbst geheilt“ und legte die Beweise in einer Tabelle vor. Er ändert nichts blind.

Bevor er sich selbst verändert, **legt er zuerst ein Backup an**, danach **läuft eine Syntaxprüfung**, und wenn er nicht startet, **wird automatisch zurückgerollt**. „Er repariert sich selbst“ heißt also nicht „er zerstört sich selbst“.

### 4. Er macht dort weiter, wo er aufgehört hat — lange Aufgaben überstehen Unterbrechungen

Eine halbe Stunde Arbeit und Android beendet Termux? Nach dem Neustart **nimmt er die Arbeit automatisch wieder auf**, ohne dass du alles neu erklären musst.

```bash
$ tail evolve.log
[Selbstheilung] Explizite Fortsetzungsmarkierung gefunden; läuft automatisch weiter
[Selbstheilung] Setze 20260928-034008-1058 fort (boot), Versuch 2
```

Handy-Neustart, Netzwerkwechsel, Termux vom System beendet — er merkt sich, wo er war.

---

:::tip In einem Satz

**Mühelos zu installieren, wächst mit der Nutzung, repariert sich bei Defekt selbst und macht nach Unterbrechungen weiter.**

:::

### Prüf es selbst — glaub mir nicht einfach

Sieh dir nach der Installation seine eigenen Evolutionsaufzeichnungen an:

```bash
cat ~/.termux-agent/evolve.json     # was er für sich selbst verbessert hat, und warum
cat ~/.termux-agent/selfheal.json   # welche Abstürze er fand und behob
tail ~/.termux-agent/evolve.log     # Live-Log zu Evolution und Selbstheilung
```

Diese Dateien **schreibt der Agent zur Laufzeit** — es ist kein vorab eingepacktes Marketingmaterial. Läuft er eine Weile, füllen sie sich mit Aufzeichnungen, die dir gehören.

---

## Inhalt

- [Was ihn von anderen KI-Assistenten unterscheidet](#was-ihn-von-anderen-ki-assistenten-unterscheidet)
  - [Idiotensichere Installation](#1-idiotensichere-installation--zwei-befehle-kein-dritter-schritt)
  - [Tägliche Selbstevolution](#2-er-entwickelt-sich-jeden-tag-selbst-weiter--keine-pflege-keine-remote-updates)
  - [Selbstreparatur](#3-er-repariert-seine-eigenen-bugs--und-du-kannst-beim-diagnostizieren-zusehen)
  - [Fortsetzung nach Unterbrechung](#4-er-macht-dort-weiter-wo-er-aufgehört-hat--lange-aufgaben-überstehen-unterbrechungen)
- [Was er ist](#was-er-ist)
- [Installation](#installation)
- [API-Schlüssel konfigurieren](#api-schlüssel-konfigurieren)
- [Tägliche Nutzung](#tägliche-nutzung)
- [Fähigkeitsübersicht](#fähigkeitsübersicht)
- [Werkzeugreferenz (20)](#werkzeugreferenz-20)
- [Skill-Bibliothek (36)](#skill-bibliothek-36)
- [Verzeichnisstruktur](#verzeichnisstruktur)
- [Sicherheitshinweise](#sicherheitshinweise)
- [FAQ](#faq)

---

## Was er ist

Ein KI-Assistent, der auf deinem Handy oder Tablet lebt und über eine Browser-Oberfläche genutzt wird.

**Design-Entscheidungen**

| Prinzip | Erklärung |
|---|---|
| **Keine Fremdabhängigkeiten** | Nutzt nur die Python-Standardbibliothek; `pkg install python` genügt. Keine nativen Module, die kompiliert werden müssen — Upstream-Updates können ihn daher nie kaputtmachen |
| **Lokale Speicherung** | Gespräche, Gedächtnis und Schlüssel bleiben in `~/.termux-agent/` auf deinem Gerät. Nichts wird in die Cloud hochgeladen |
| **Kann sich selbst verändern** | Er kann seinen eigenen Quellcode bearbeiten, um Funktionen hinzuzufügen, und rollt bei Fehlschlag automatisch zurück |
| **Watchdog an Bord** | Stirbt der Dienst, wird er automatisch neu gestartet — ohne systemd (das Android nicht hat) |
| **Keine Remote-Updates nötig** | Kein Server, kein Update-Push. Seine Evolution ist selbstgesteuert; neue Fähigkeiten hängen nicht davon ab, dass der Autor eine Version veröffentlicht |

---

## Installation

Voraussetzung: [Termux](https://f-droid.org/packages/com.termux/) auf dem Android-Gerät installiert (**die F-Droid-Version wird empfohlen** — Add-ons müssen dieselbe Signatur wie die Haupt-App haben).

```bash
# 1. Python installieren
pkg update && pkg install -y python curl

# 2. Aus diesem Verzeichnis den Installer ausführen
bash install.sh
```

Der Installer erledigt der Reihe nach: Umgebung prüfen → vorhandene Installation sichern → Programm und Skills kopieren → Standardkonfiguration erzeugen (**ohne einen vorhandenen API-Schlüssel zu überschreiben**) → Dienst starten und selbst testen.

---

## API-Schlüssel konfigurieren

Beim ersten Start brauchst du einen API-Schlüssel. Zwei Wege:

**Weg 1 (empfohlen)**: Öffne `http://127.0.0.1:8765/` und trage ihn unter „Einstellungen“ oben rechts ein.

**Weg 2**: über die Kommandozeile
```bash
python3 ~/.termux-agent/agent.py config
```

Standardmäßig nutzt er DeepSeek (`https://api.deepseek.com`), du kannst ihn aber auf jeden OpenAI-kompatiblen Endpunkt zeigen lassen — einschließlich eines lokalen Modells in deinem Netzwerk.

---

## Tägliche Nutzung

```bash
bash ~/.termux-agent/start.sh              # Dienst nach einem Termux-Neustart wiederherstellen
python3 ~/.termux-agent/agent.py doctor    # Umgebungsprüfung + Konnektivitätstest
python3 ~/.termux-agent/agent.py restart   # Dienst neu starten
python3 ~/.termux-agent/agent.py selfcheck # Selbstinspektion (Quellcode/Watchdog/Dienst/Backups)
```

Danach **http://127.0.0.1:8765/** im Browser öffnen.

### Direkte Nutzung über die Kommandozeile (ohne UI)

```bash
python3 ~/.termux-agent/agent.py "prüfe die Speicherbelegung"   # einmal ausführen und beenden
python3 ~/.termux-agent/agent.py -c                            # letztes Gespräch fortsetzen
python3 ~/.termux-agent/agent.py chat                          # im Terminal chatten (einfach)
```

### Autostart beim Booten (optional)

Installiere [Termux:Boot](https://f-droid.org/packages/com.termux.boot/), öffne die App einmal manuell und dann:

```bash
python3 ~/.termux-agent/agent.py autostart
```

---

## Fähigkeitsübersicht

| Kategorie | Fähigkeiten |
|---|---|
| **Systemoperationen** | Shell ausführen, Systemeinstellungen mit Shell-Rechten lesen, APKs installieren, Prozesse verwalten |
| **Dateiverarbeitung** | Dateien lesen/schreiben/bearbeiten, Batch-Patches, Verzeichnisse durchsuchen, rekursive Regex-Suche, seitenweises Lesen großer Dateien |
| **Netzwerk** | Webseiten als Text abrufen, schlüsselfreie Websuche, Streaming-Download großer Dateien |
| **Code-Hosting** | Kompletter GitHub-Workflow (Repo erstellen / clone / pull / commit / push / Code suchen) |
| **Gerätesteuerung** | Ein Huawei-Handy fernsteuern (HDC-Protokoll), auf diesem Gerät als adb shell agieren |
| **Bildverarbeitung** | Text oder Wasserzeichen entfernen, alte Fotos restaurieren, Qualität verbessern, Porträts retuschieren, Hintergründe freistellen |
| **Dokumentausgabe** | Word / Excel / PPT erzeugen, HTML in DOCX konvertieren, Vertragsvorlagen, Satzverfeinerung |
| **Aufgabenverwaltung** | To-do-Listen, paralleles Denken per Sub-Agent, dich um Entscheidungen bitten |
| **Selbstevolution** | Den eigenen Quellcode ändern, um Funktionen hinzuzufügen — mit automatischem Backup + Syntaxprüfung + Rollback bei Fehlschlag |
| **Fach-Skills** | 36 Fach-Skill-Dokumente (Schreiben, Design, Recht, Finanzen, Debugging, …) |

---

## Werkzeugreferenz (20)

### System & Dateien

| Werkzeug | Zweck | Highlights |
|---|---|---|
| `bash` | Shell-Befehle ausführen | Das Arbeitspferd: Pakete installieren, Dateien verwalten, Programme ausführen |
| `read_file` | Textdateien lesen (mit Zeilennummern) | Seitenweises Lesen großer Dateien; ein negativer `offset` liest Logs vom Ende |
| `write_file` | Eine ganze Datei schreiben | Erstellt übergeordnete Verzeichnisse automatisch |
| `edit_file` | Exakter String-Ersatz | Verlangt eine eindeutige Übereinstimmung; bei mehreren Treffern werden deren Zeilennummern gemeldet |
| `apply_patch` | Patch über mehrere Stellen / Dateien | **Atomar**: Passt eine Stelle nicht, wird nichts geschrieben — keine halb angewandten Änderungen |
| `list_dir` | Verzeichnis auflisten | Enthält Typ, Größe, Änderungszeit |
| `grep` | Rekursive Regex-Suche | Liefert `Datei:Zeile: Inhalt` |

### Netzwerk

| Werkzeug | Zweck | Hinweise |
|---|---|---|
| `fetch_url` | Seite/API als Klartext abrufen | Nur zum Lesen von Text |
| `web_search` | Websuche | **Kein API-Schlüssel nötig** |
| `download` | Streaming-Download großer Dateien | Keine Kürzung, mit Zeitlimit und automatischem Wiederholen. Gut für APKs und Installer |

### Geräte & externe Systeme

| Werkzeug | Zweck | Hinweise |
|---|---|---|
| `sysshell` | Als Shell (adb) ausführen | Höhere Rechte als eine normale App: Systemeinstellungen lesen, `dumpsys`, `getprop`, `pm/am`. **Kein Root** |
| `hdcmate` | Ein Huawei-Handy fernsteuern | Nutzt das HDC-Protokoll (nicht adb). `exec` führt einen Befehl aus / `target` merkt sich die Adresse / `test` prüft die Verbindung. Volle UI-Automatisierung möglich |
| `github` | GitHub-Repositories bedienen | Neun Aktionen: `list/repo/read/tree/clone/pull/push/create/search`. Erfordert ein Token |
| `wps` | Word/Excel/PPT erzeugen | Über einen lokalen MCP-Dienst umgesetzt, **kein Konto nötig**. Dateien landen in `~/storage/shared/WPS_AI/` |
| `imgedit` | KI-Bildverarbeitung | Fünf Operationen: `erase` Text/Wasserzeichen / `restore` alte Fotos / `enhance` Qualität / `beauty` Porträt / `matting` Freistellen |

### Zusammenarbeit & Selbstevolution

| Werkzeug | Zweck | Hinweise |
|---|---|---|
| `todo_write` | Aufgabenliste pflegen | Teilt Aufgaben mit 3+ Schritten in 2–6 Punkte, live über deinem Eingabefeld angezeigt; nach einer Wiederverbindung wird von dort fortgesetzt |
| `subagent` | Unabhängiges Denken eines Sub-Agenten | Übergibt ein isoliertes Teilproblem an einen separaten Agenten (ohne Zugriff auf lokale Dateien). Gut, um schwierige Probleme zu parallelisieren |
| `ask_user` | Dich um eine Entscheidung bitten | Öffnet ein Auswahlfenster. Nur wenn deine Entscheidung nötig ist: Geld ausgeben, Daten löschen, zwischen Ansätzen wählen |
| `selfupdate` | **Sich selbst verändern** | Automatisches Backup + Syntaxprüfung, sofortiges Zurückrollen bei Fehlschlag; bei Erfolg startet der Dienst automatisch neu |

### Archiviert

| Werkzeug | Hinweise |
|---|---|
| `wx_auto` | WeChat-Auto-Antwort-Begleiter (Bildschirmlesen + automatische Antwort; erfordert Autorisierung und Kenntnis der Gegenpartei). Drei Sicherheitsschranken: unklare Erkennungen werden nur protokolliert, bei fehlendem Senden-Button wird abgebrochen, maximal 20 Nachrichten pro Stunde |

---

## Skill-Bibliothek (36)

Skills sind **Fachmethodik-Dokumente**, die der KI übergeben werden. Vor einer relevanten Aufgabe liest er den passenden Skill und folgt den darin festgehaltenen Konventionen und Erfahrungswerten.

### Schreiben (11)

| Skill | Zweck |
|---|---|
| `general-writer` | **Allgemeiner Schreib-Fallback (L1).** Behördendokumente, Wochenberichte, Konzepte, E-Mails, Werbetexte, Essays, Neue Medien; 7-dimensionales Qualitäts-Scoring + Anpassungsmatrix für 10 Textsorten |
| `academic-paper-expert` | Wissenschaftliche Arbeiten: Strukturdesign, Literaturüberblick, Abstracts, APA/GB-T7714-Zitierregeln, akademisches Feilen |
| `tech-blog-expert` | Technische Blogs: Tutorials, Architekturanalysen, Quellcode-Analyse, Open-Source-Doku, README |
| `business-copy-expert` | Werbetexte: Markentexte, Marketing-Mails, Produktbeschreibungen, Slogans, Werberecht-Konformität (AIDA-Modell) |
| `work-report-expert` | Berufliche Berichte: Jahresabschlüsse, Leistungsbeurteilungen, Beförderungsreden, Wochen-/Monatsberichte (Pyramidenprinzip + STAR) |
| `science-writing-expert` | Wissenschaftskommunikation: Erklärungen, Tech-Reviews, ausführliche Reportagen (Feynman-Technik) |
| `poetry-prose-expert` | Lyrik und Prosa: moderne Gedichte, klassische chinesische Verse, Essays, Literaturkritik |
| `stock-research-report-expert` | **Wertpapierresearch (L2).** Branchentiefenanalysen, Einzelaktienresearch, Ereigniskommentare, Businesspläne; vier Längenstufen |
| `legal-contract-expert` | **Rechtsverträge (L2).** Entwurf und Prüfung: Vollständigkeit der Pflichtklauseln, Symmetrie von Rechten und Pflichten, Vermeidung hoher Risiken |
| `humanizer-zh` | „KI-Geruch“ entfernen (Chinesisch): erkennt und behebt anhand von Wikipedias Leitfaden zu KI-Schreibmerkmalen |
| `humanizer` | „KI-Geruch“ entfernen (Englisch) |

### Dokumentausgabe (5)

| Skill | Zweck |
|---|---|
| `doc-typeset` | **Satz und Feinschliff.** Konsumiert Design-Tokens + Inhalt und gibt poliertes HTML aus. Enthält 7 Branchenvorlagen (Verträge / wissenschaftliche Arbeiten / Behördendokumente / Geschäftsberichte / Protokolle / Research-Reports / Jahresberichte) |
| `html-to-docx` | Hochgetreue HTML→Word-Konvertierung. CSS-Variablen-Vorverarbeitung, präzises Mapping für 10+ Elementtypen, 14 CSS-Eigenschaften |
| `format-extract` | .docx in semantisches HTML umwandeln + eingebettete Bilder extrahieren (Überschriftenhierarchie, Tabellenstile, Einzüge, Farben bleiben erhalten) |
| `generate-fillable-contract-html` | Ausfüllbare HTML-Formulare für chinesische Verträge, Angebote und Vollmachten erzeugen |
| `underline-toolkit` | Lückendokumente: `create` erzeugt eine leere Vorlage / `fill` schreibt Daten in eine bestehende Vorlage (Verträge, Anträge, Deckblätter) |
| `html-review` | **HTML-Qualitätsschranke.** Führt 5-dimensionale Checks der gesetzten Ausgabe durch (Token-Konformität, strukturelle Integrität, Satzvernunft, Textsorte-Passung, maßvolle Dekoration) und schickt sie bei Nichtbestehen zur gezielten Korrektur zurück |

### Design (8)

| Skill | Zweck |
|---|---|
| `design-router` | Verteiler für Designaufgaben: entscheidet, welche Design-Familie passt, und leitet weiter |
| `design-token` | Gibt standardisierte Design-Tokens je Dokumenttyp aus und steuert alle Stilentscheidungen von doc-typeset |
| `design-variables` | Design-Variablen (Design-Tokens) an Knoteneigenschaften binden/lösen |
| `ardot-design-to-code` | Design-Mockups in Frontend-Code umwandeln oder ein Designsystem / einen Styleguide von einer Website extrahieren |
| `ardot-ui-design` | UI-Design: Webseiten, Dashboards, Landingpages, mobile Oberflächen |
| `ardot-poster` | Visuelle Poster: Plakate, Flyer, Plakatwände, Banner, Kampagnen-Keyvisuals |
| `ardot-slides` | Präsentationsdesign (keine .pptx-Dateien, sondern Design-Mockups) |
| `component-instance` | Verwaltung von Komponenteninstanzen: Instanzen erstellen/aktualisieren, Eigenschaften setzen, Varianten wechseln |
| `shared-styles` | Gemeinsame Stile binden/lösen (Textstile, Füllungen, Konturen, Effekte) |

### Debugging & Betrieb (5)

| Skill | Zweck |
|---|---|
| `termux-traps` | **Android-Termux-Umgebungsfallen**: die Tücken auf solchen Geräten und der richtige Umgang damit |
| `log-debug` | Log-Fehlerdiagnose: welche Datei bei Problemen zuerst zu prüfen ist |
| `selfupdate` | Der korrekte Selbstmodifikations-Workflow: der richtige Weg und die Grundregeln zum Bearbeiten von `agent.py` |
| `ui-debug` | Web-UI-Debugging: die UI ändern und sofort verifizieren |
| `pc-debug` | Befehle auf jenem Windows-PC ausführen und Dateien lesen/schreiben über die „PC-Debug-Brücke“ |

### Gerätevernetzung (2)

| Skill | Zweck |
|---|---|
| `hdcmate` | HDC-Handy-Debugging: Protokollhinweise, gängige HarmonyOS-Befehle, UI-Automatisierung, Stolperfallen |
| `chrome-cdp` | Chrome-Debugging und Scraping: Webdaten über das DevTools-Protokoll lesen, ohne den Bildschirm zu berühren |

### Skill-Verwaltung (4)

| Skill | Zweck |
|---|---|
| `find-skills` | Hilft dir, Skills zu entdecken und zu installieren |
| `skill-creator` | Eine Anleitung zum Erstellen neuer Skills |
| `marketplace-skill-installer` | Suchen und Installieren aus einem Skill-Marktplatz |
| `underline-toolkit` | Siehe oben unter „Dokumentausgabe“ |

---

## Verzeichnisstruktur

```
~/.termux-agent/
├── agent.py          Hauptprogramm (einzelne Datei, reine Standardbibliothek)
├── cdp.py            CDP-Client (Browser-Debugging/Scraping; genutzt von ui-debug / chrome-cdp)
├── chrome_read.py    Chrome-Webdatenleser (genutzt von chrome-cdp)
├── hdc.py            HDC-Protokollimplementierung (genutzt von hdcmate; 447 Zeilen)
├── wps.py            WPS-Dokumenterstellung
├── wps_mcp_server.py Lokaler WPS-MCP-Dienst
├── mcp_call.py       MCP-Aufrufhelfer
├── config.json       Konfiguration (enthält den API-Schlüssel, Modus 600)
├── skills/           Skill-Dokumente (36)
├── sessions/         Gesprächsaufzeichnungen
├── memory.md         Langzeitgedächtnis
├── evolve.json       Evolutionsprotokoll (was er täglich ändert und warum)
├── evolve.log        Laufzeitprotokoll der Evolution
├── selfheal.json     Selbstheilungsverlauf (Absturz erkannt → Heilung fertig)
├── selfheal.py       Modul für Selbstheilung und Fortsetzung
├── uploads/          Hochgeladene Bilder
├── outputs/          Erzeugte Artefakte
├── versions/         Quellcode-Versionsbackups (für das Selbst-Update)
├── logs/             Logs
├── tmp/              Temporäre Dateien
├── supervisor.sh     Watchdog (automatisch erzeugt)
└── start.sh          Startskript
```

---

## Sicherheitshinweise

- Die UI **lauscht nur auf `127.0.0.1`** und ist nicht im LAN erreichbar (die UI hat Rechte zur Befehlsausführung)
- `config.json` und Token-Dateien haben den Modus `600`
- Unumkehrbare Operationen — Formatieren, direktes Schreiben auf Blockgeräte, Löschen des Wurzelverzeichnisses — werden automatisch abgefangen
- Keine Root-Rechte, und private Daten anderer Apps sind nicht lesbar
- Umgebungs-Selbstprüfung: `python3 ~/.termux-agent/agent.py doctor`

---

## FAQ

**Der Dienst startet nicht?**
```bash
python3 ~/.termux-agent/agent.py selfcheck   # sehen, was er über sich selbst sagt
tail -30 ~/.termux-agent/supervisor.log      # das Watchdog-Log prüfen
```

**Entwickelt er sich wirklich selbst weiter? Was hat er geändert?**
Sieh dir `~/.termux-agent/evolve.json` an — jeder Eintrag hält fest, was geändert wurde und warum. Schnellblick per Kommandozeile:
```bash
python3 -c "import json;print('\n'.join(f'{e[\"time\"]}  {e[\"summary\"].splitlines()[0]}' for e in json.load(open('$HOME/.termux-agent/evolve.json'))['log']))"
```

**Worin unterscheidet sich Selbstevolution von einem Remote-Update?**
Er lädt keine Update-Pakete herunter, und kein Server schiebt ihm Versionen zu. Er **liest seinen eigenen Code und seine Logs und ändert sie an Ort und Stelle**. Deshalb unterscheiden sich die Fähigkeiten je Gerät — es hängt davon ab, wie du ihn nutzt.

**Was, wenn er sich beim Bearbeiten des eigenen Codes kaputtmacht?**
Der `selfupdate`-Weg **legt zuerst ein Backup an → prüft nach der Änderung die Syntax → stellt bei Fehlschlag sofort wieder her**. Selbst wenn der neue Code startet, sich aber falsch verhält, rollt der Watchdog auf die vorherige Version zurück. Der Selbstheilungsprozess selbst hinterlässt einen Eintrag (`selfheal.json`).

**Eine lange Aufgabe wurde mittendrin vom System beendet?**
Nach einem Neustart wird sie automatisch fortgesetzt; nichts muss neu erklärt werden. Im Log steht dann „setze fort“.

**Ich habe den Code geändert, aber nichts passiert?**
Neuer Code wird erst wirksam, wenn der Browser ihn einmal geladen hat — lade die Seite einfach neu.

**Wo sehe ich die Stolperfallen, in die er geraten ist?**
`skills/termux-traps.md` dokumentiert die Umgebungsfallen auf solchen Geräten.

**Wie füge ich neue Fähigkeiten hinzu?**
Nutze den `skill-creator`-Skill, um eine neue `.md` in `skills/` anzulegen, oder lass die KI sich per `selfupdate` selbst ein Werkzeug hinzufügen.

---

## Lizenz

**MIT-Lizenz** (siehe [LICENSE](LICENSE)) — frei nutzbar, veränderbar und weitergebbar, auch in kommerziellen Closed-Source-Produkten, solange der Copyright-Hinweis erhalten bleibt.

Kurz gesagt: nimm es und nutze es, wie du willst, ohne Gewähr.
