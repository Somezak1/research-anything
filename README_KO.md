# research-anything

**실제 운영 결정을 위한 증거 게이트 기반 Claude Code 리서치 스킬입니다.**

[English](README.md) · [简体中文](README_CN.md) · [日本語](README_JA.md) · [한국어](README_KO.md) · [Español](README_ES.md) · [Français](README_FR.md) · [Deutsch](README_DE.md) · [Português](README_PT.md) · [Русский](README_RU.md)

> 이 문서는 오래된 세부 정보를 남기지 않기 위한 간결한 v3 요약입니다. 전체 사양, 운영 제약 및 커넥터 표는 최신 [English README](README.md) 또는 [中文 README](README_CN.md)를 확인하세요.

## v3 개요

research-anything은 현재 **Claude Code 전용**입니다. 모호한 요청도 다음 과정으로 조사합니다.

1. 사용자의 요청과 답변을 원문 그대로 기록합니다.
2. Douyin, Xiaohongshu, Zhihu, Bilibili, YouTube, GitHub, X/Twitter 및 일반 웹을 폭넓게 탐색합니다.
3. 새로운 후보, 모순, 독립 증거 및 의사 결정에 중요한 증거 공백에 따라 적응형으로 심화 조사합니다.
4. 용어, 후보군, 교차 검증된 사실과 불확실성을 먼저 설명한 뒤 선택을 바꾸는 질문만 합니다.
5. 새 제약이 결과를 바꾸면 표적 2차 조사를 수행합니다.
6. 결과를 `production-ready`, `pilot-only`, `blocked` 중 하나로 판정합니다.

8개 채널은 발견을 위한 탐색층이지 완전성을 보장하지 않습니다. 기술, 여행, 정책 등 각 도메인에 필요한 공식 1차 출처도 반드시 조사합니다. 핵심 증거가 부족하면 운영 권고를 내리지 않습니다.

각 실행은 `research.db`에서 재개할 수 있으며 `manifest.v3.json`, `events.jsonl`, `findings.jsonl`, `claims.jsonl`, `decision.json`, `report.html`, `runbook.json` 등을 내보냅니다. ASR은 명시된 수치 한도 안에서만 원자적 예산 예약과 미디어 지문 기반 중복 방지를 거쳐 실행됩니다.

## 설치

요구 사항: Claude Code, Python 3.11 이상, Git.

```bash
git clone https://github.com/Somezak1/research-anything.git ~/research-anything
cd ~/research-anything
python3 scripts/install_skill.py doctor
python3 scripts/install_skill.py install
python3 scripts/install_skill.py check
```

기존 설치와 차이를 확인한 뒤 업데이트하려면:

```bash
git pull
python3 scripts/install_skill.py check
python3 scripts/install_skill.py install --force
python3 scripts/install_skill.py check
```

`doctor`는 사용할 수 없는 커넥터를 capability gap으로 보고하며 로그인이나 타사 도구 설치를 자동으로 수행하지 않습니다. 조사 시간은 고정되어 있지 않으며 Claude, API, ASR, 프록시 및 상용 데이터 사용에 비용이 발생할 수 있습니다.

`MediaCrawler`는 상위 프로젝트의 [비상업적 학습 라이선스](https://github.com/NanmiCoder/MediaCrawler/blob/main/LICENSE)에 따라 개인 비상업 학습/조사용 선택 커넥터입니다. 상업적 조사에서는 기본값이 아닙니다.

기존 v2 실행은 수정하지 않고 감사할 수 있습니다:

```bash
python3 scripts/audit_v2.py --out-dir /path/to/legacy/run --strict
```
