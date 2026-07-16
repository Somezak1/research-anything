# research-anything

**Навык Claude Code с доказательными шлюзами для решений, которые могут попасть в промышленную эксплуатацию.**

[English](README.md) · [简体中文](README_CN.md) · [日本語](README_JA.md) · [한국어](README_KO.md) · [Español](README_ES.md) · [Français](README_FR.md) · [Deutsch](README_DE.md) · [Português](README_PT.md) · [Русский](README_RU.md)

> Это краткое описание v3, чтобы в переводе не сохранялись устаревшие детали. Полная спецификация, эксплуатационные ограничения и таблица коннекторов находятся в актуальном [README на английском](README.md) и [README на китайском](README_CN.md).

## Обзор v3

research-anything сейчас предназначен **только для Claude Code**. Он может начать с неопределённого запроса и выполняет следующие этапы:

1. Дословно сохраняет запрос и ответы пользователя.
2. Проводит широкий поиск в Douyin, Xiaohongshu, Zhihu, Bilibili, YouTube, GitHub, X/Twitter и обычном Интернете.
3. Адаптивно углубляет поиск вслед за новыми кандидатами, противоречиями, независимыми доказательствами и критическими пробелами.
4. Сначала объясняет термины, кандидатов, подтверждённые выводы и неопределённости, а затем задаёт только вопросы, способные изменить решение.
5. Выполняет целевой второй поиск, если новые ограничения меняют ответ.
6. Выпускает результат со статусом `production-ready`, `pilot-only` или `blocked`.

Восемь каналов — это слой обнаружения, а не гарантия полноты. Для каждой предметной области обязательны соответствующие официальные первичные источники. При нехватке критических доказательств система не выдаёт рекомендацию для промышленного использования.

Каждый запуск можно продолжить из `research.db`; среди экспортируемых файлов — `manifest.v3.json`, `events.jsonl`, `findings.jsonl`, `claims.jsonl`, `decision.json`, `report.html` и `runbook.json`. ASR запускается только в явно заданных числовых пределах, с атомарным резервированием бюджета и идемпотентностью по отпечатку медиафайла.

## Установка

Требования: Claude Code, Python 3.11 или новее и Git.

```bash
git clone https://github.com/Somezak1/research-anything.git ~/research-anything
cd ~/research-anything
python3 scripts/install_skill.py doctor
python3 scripts/install_skill.py install
python3 scripts/install_skill.py check
```

Обновление после проверки отличающейся установленной копии:

```bash
git pull
python3 scripts/install_skill.py check
python3 scripts/install_skill.py install --force
python3 scripts/install_skill.py check
```

`doctor` сообщает об отсутствующих коннекторах как о пробелах возможностей; он не устанавливает сторонние инструменты и не выполняет вход автоматически. Время исследования не фиксировано, а Claude, API, ASR, прокси и коммерческие данные могут требовать оплаты.

`MediaCrawler` — необязательный коннектор для личного некоммерческого обучения и исследований согласно его [некоммерческой учебной лицензии](https://github.com/NanmiCoder/MediaCrawler/blob/main/LICENSE). Он не используется по умолчанию для коммерческих исследований.

Старые запуски v2 можно проверить без изменения файлов:

```bash
python3 scripts/audit_v2.py --out-dir /path/to/legacy/run --strict
```
