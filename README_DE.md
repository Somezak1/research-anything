# research-anything

**Ein evidenzgeprüfter Claude-Code-Skill für Entscheidungen, die in den Produktivbetrieb gelangen können.**

[English](README.md) · [简体中文](README_CN.md) · [日本語](README_JA.md) · [한국어](README_KO.md) · [Español](README_ES.md) · [Français](README_FR.md) · [Deutsch](README_DE.md) · [Português](README_PT.md) · [Русский](README_RU.md)

> Diese Seite ist bewusst nur eine kurze v3-Zusammenfassung, damit keine veralteten übersetzten Details bestehen bleiben. Die vollständige Spezifikation, Betriebsgrenzen und Connector-Matrix stehen im aktuellen [englischen README](README.md) oder [chinesischen README](README_CN.md).

## v3 im Überblick

research-anything ist derzeit **ausschließlich für Claude Code** vorgesehen. Auch eine unklare Anfrage wird nach folgendem Ablauf untersucht:

1. Anfrage und Antworten des Benutzers werden wortgetreu gespeichert.
2. Douyin, Xiaohongshu, Zhihu, Bilibili, YouTube, GitHub, X/Twitter und das allgemeine Web werden breit sondiert.
3. Neue Kandidaten, Widersprüche, unabhängige Belege und entscheidungskritische Lücken bestimmen die adaptive Vertiefung.
4. Begriffe, Kandidaten, bestätigte Erkenntnisse und Unsicherheiten werden erklärt, bevor die wenigen entscheidungsrelevanten Fragen gestellt werden.
5. Verändern neue Einschränkungen die Antwort, folgt eine gezielte zweite Recherche.
6. Das Ergebnis erhält den Status `production-ready`, `pilot-only` oder `blocked`.

Die acht Kanäle sind eine Entdeckungsschicht und keine Vollständigkeitsgarantie. Zusätzlich sind die jeweils relevanten offiziellen Primärquellen eines Fachgebiets vorgeschrieben. Fehlen kritische Belege, gibt das System keine Produktionsempfehlung aus.

Jeder Lauf kann aus `research.db` fortgesetzt werden und exportiert unter anderem `manifest.v3.json`, `events.jsonl`, `findings.jsonl`, `claims.jsonl`, `decision.json`, `report.html` und `runbook.json`. ASR läuft nur innerhalb ausdrücklich festgelegter Zahlenlimits, mit atomarer Budgetreservierung und Idempotenz anhand des Medien-Fingerprints.

## Installation

Voraussetzungen: Claude Code, Python 3.11 oder neuer und Git.

```bash
git clone https://github.com/Somezak1/research-anything.git ~/research-anything
cd ~/research-anything
python3 scripts/install_skill.py doctor
python3 scripts/install_skill.py install
python3 scripts/install_skill.py check
```

Aktualisierung nach Prüfung einer abweichenden Installation:

```bash
git pull
python3 scripts/install_skill.py check
python3 scripts/install_skill.py install --force
python3 scripts/install_skill.py check
```

`doctor` meldet fehlende Connectoren als Fähigkeitslücken; es installiert keine Drittwerkzeuge und führt keine Anmeldung automatisch durch. Die Laufzeit ist nicht fest, und Claude, APIs, ASR, Proxys oder kommerzielle Daten können Kosten verursachen.

`MediaCrawler` ist gemäß seiner [nichtkommerziellen Lernlizenz](https://github.com/NanmiCoder/MediaCrawler/blob/main/LICENSE) ein optionaler Connector für persönliche, nichtkommerzielle Lern- und Forschungszwecke. Für kommerzielle Recherche ist er nicht voreingestellt.

Alte v2-Läufe lassen sich schreibgeschützt prüfen:

```bash
python3 scripts/audit_v2.py --out-dir /path/to/legacy/run --strict
```
