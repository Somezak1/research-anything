# research-anything

**Une skill Claude Code de recherche soumise à des portes de preuve pour les décisions destinées à la production.**

[English](README.md) · [简体中文](README_CN.md) · [日本語](README_JA.md) · [한국어](README_KO.md) · [Español](README_ES.md) · [Français](README_FR.md) · [Deutsch](README_DE.md) · [Português](README_PT.md) · [Русский](README_RU.md)

> Cette page est un résumé v3 volontairement court afin de ne pas conserver de détails traduits obsolètes. Consultez le [README anglais](README.md) ou le [README chinois](README_CN.md) pour la spécification complète, les limites opérationnelles et la matrice des connecteurs.

## Aperçu de v3

research-anything cible actuellement **Claude Code uniquement**. Il peut partir d'une demande imprécise et suit ce processus :

1. Conserver mot pour mot la demande et les réponses de l'utilisateur.
2. Explorer largement Douyin, Xiaohongshu, Zhihu, Bilibili, YouTube, GitHub, X/Twitter et le Web général.
3. Approfondir de façon adaptative selon les nouveaux candidats, les contradictions, les preuves indépendantes et les lacunes critiques.
4. Expliquer les termes, les candidats, les éléments corroborés et les incertitudes avant de poser les rares questions qui changent la décision.
5. Effectuer une seconde recherche ciblée lorsque de nouvelles contraintes modifient la réponse.
6. Publier le résultat avec le statut `production-ready`, `pilot-only` ou `blocked`.

Les huit canaux constituent une couche de découverte, pas une garantie d'exhaustivité. Chaque domaine impose aussi les sources primaires et officielles pertinentes. Si une preuve critique manque, aucune recommandation de production n'est émise.

Chaque exécution peut reprendre depuis `research.db` et exporte notamment `manifest.v3.json`, `events.jsonl`, `findings.jsonl`, `claims.jsonl`, `decision.json`, `report.html` et `runbook.json`. L'ASR ne s'exécute que dans des limites chiffrées explicites, avec réservation atomique du budget et idempotence fondée sur l'empreinte du média.

## Installation

Prérequis : Claude Code, Python 3.11 ou version ultérieure, et Git.

```bash
git clone https://github.com/Somezak1/research-anything.git ~/research-anything
cd ~/research-anything
python3 scripts/install_skill.py doctor
python3 scripts/install_skill.py install
python3 scripts/install_skill.py check
```

Pour mettre à jour après avoir vérifié une installation différente :

```bash
git pull
python3 scripts/install_skill.py check
python3 scripts/install_skill.py install --force
python3 scripts/install_skill.py check
```

`doctor` signale les connecteurs absents comme des lacunes de capacité ; il n'installe pas d'outil tiers et ne se connecte pas automatiquement. La durée n'est pas fixe et Claude, les API, l'ASR, les proxys ou les données commerciales peuvent engendrer des coûts.

`MediaCrawler` est un connecteur facultatif réservé à l'apprentissage ou à la recherche personnelle non commerciale selon sa [licence non commerciale](https://github.com/NanmiCoder/MediaCrawler/blob/main/LICENSE). Il n'est pas utilisé par défaut pour la recherche commerciale.

Les anciennes exécutions v2 peuvent être auditées sans être modifiées :

```bash
python3 scripts/audit_v2.py --out-dir /path/to/legacy/run --strict
```
