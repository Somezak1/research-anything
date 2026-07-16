# research-anything

**本番判断のための、証拠ゲート付き Claude Code リサーチスキル。**

[English](README.md) · [简体中文](README_CN.md) · [日本語](README_JA.md) · [한국어](README_KO.md) · [Español](README_ES.md) · [Français](README_FR.md) · [Deutsch](README_DE.md) · [Português](README_PT.md) · [Русский](README_RU.md)

> この日本語版は、古い詳細を残さないための簡潔な v3 概要です。完全な仕様、運用上の制約、コネクター表については、最新の [English README](README.md) または [中文 README](README_CN.md) を参照してください。

## v3 の概要

research-anything は現在 **Claude Code 専用**です。曖昧な依頼から開始し、次の順序で調査します。

1. ユーザーの発言を一字一句そのまま記録する。
2. Douyin、小紅書、Zhihu、Bilibili、YouTube、GitHub、X/Twitter、一般 Web を広く探査する。
3. 新しい候補、矛盾、独立した証拠、重要な証拠不足に応じて深掘りする。
4. 用語・候補・確定事項・不明点を説明してから、判断を変える質問だけを行う。
5. 新しい制約で答えが変わる場合は、対象を絞った二次調査を行う。
6. 最終結果を `production-ready`、`pilot-only`、`blocked` のいずれかに判定する。

8 チャンネルは発見用の探査層であり、完全性の保証ではありません。技術、旅行、政策など、各分野で必要な公式・一次情報も必須です。重大な証拠が不足していれば、本番推奨は出しません。

各実行は `research.db` から再開でき、`manifest.v3.json`、`events.jsonl`、`findings.jsonl`、`claims.jsonl`、`decision.json`、`report.html`、`runbook.json` などを出力します。ASR は明示的な数値上限の範囲でのみ、原子的な予算予約とメディア指紋による重複防止を使って実行されます。

## インストール

要件: Claude Code、Python 3.11 以上、Git。

```bash
git clone https://github.com/Somezak1/research-anything.git ~/research-anything
cd ~/research-anything
python3 scripts/install_skill.py doctor
python3 scripts/install_skill.py install
python3 scripts/install_skill.py check
```

既存の異なるインストールを確認後に更新する場合:

```bash
git pull
python3 scripts/install_skill.py check
python3 scripts/install_skill.py install --force
python3 scripts/install_skill.py check
```

`doctor` は利用できないコネクターを capability gap として報告し、ログインや第三者ツールのインストールを自動実行しません。調査時間は固定ではなく、Claude、API、ASR、プロキシ、商用データなどに費用が発生する場合があります。

`MediaCrawler` は上流の [非商用学習ライセンス](https://github.com/NanmiCoder/MediaCrawler/blob/main/LICENSE)により、個人の非商用学習・調査向けの任意コネクターです。商用調査の既定値ではありません。

旧 v2 実行は書き換えずに監査できます:

```bash
python3 scripts/audit_v2.py --out-dir /path/to/legacy/run --strict
```
