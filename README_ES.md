# research-anything

**Investigación con puertas de evidencia para decisiones que pueden llegar a producción, integrada en Claude Code.**

[English](README.md) · [简体中文](README_CN.md) · [日本語](README_JA.md) · [한국어](README_KO.md) · [Español](README_ES.md) · [Français](README_FR.md) · [Deutsch](README_DE.md) · [Português](README_PT.md) · [Русский](README_RU.md)

> Esta página es un resumen breve de v3 para evitar conservar detalles traducidos obsoletos. Consulta el [README en inglés](README.md) o el [README en chino](README_CN.md) para ver la especificación completa, los límites operativos y la matriz de conectores.

## Resumen de v3

research-anything está dirigido actualmente **solo a Claude Code**. Puede partir de una petición imprecisa y sigue este proceso:

1. Conserva literalmente la petición y las respuestas del usuario.
2. Hace una exploración amplia de Douyin, Xiaohongshu, Zhihu, Bilibili, YouTube, GitHub, X/Twitter y la web general.
3. Profundiza de forma adaptativa según nuevos candidatos, contradicciones, evidencia independiente y lagunas críticas.
4. Explica términos, candidatos, corroboraciones e incertidumbres antes de formular las pocas preguntas que cambiarían la decisión.
5. Realiza una segunda investigación dirigida cuando las nuevas restricciones cambian la respuesta.
6. Publica el resultado como `production-ready`, `pilot-only` o `blocked`.

Los ocho canales son una capa de descubrimiento, no una garantía de cobertura completa. Cada dominio también exige sus fuentes primarias y oficiales pertinentes. Si falta evidencia crítica, el sistema no emite una recomendación para producción.

Cada ejecución puede reanudarse desde `research.db` y exporta, entre otros, `manifest.v3.json`, `events.jsonl`, `findings.jsonl`, `claims.jsonl`, `decision.json`, `report.html` y `runbook.json`. El ASR solo se ejecuta dentro de límites numéricos explícitos, con reserva atómica de presupuesto e idempotencia basada en la huella del medio.

## Instalación

Requisitos: Claude Code, Python 3.11 o posterior y Git.

```bash
git clone https://github.com/Somezak1/research-anything.git ~/research-anything
cd ~/research-anything
python3 scripts/install_skill.py doctor
python3 scripts/install_skill.py install
python3 scripts/install_skill.py check
```

Para actualizar después de revisar una instalación distinta:

```bash
git pull
python3 scripts/install_skill.py check
python3 scripts/install_skill.py install --force
python3 scripts/install_skill.py check
```

`doctor` informa de los conectores ausentes como brechas de capacidad; no instala herramientas de terceros ni inicia sesiones automáticamente. La duración no es fija y Claude, las API, el ASR, los proxys o los datos comerciales pueden generar costes.

`MediaCrawler` es un conector opcional para aprendizaje o investigación personal no comercial conforme a su [licencia no comercial](https://github.com/NanmiCoder/MediaCrawler/blob/main/LICENSE). No es el conector predeterminado para investigación comercial.

Las ejecuciones antiguas de v2 pueden auditarse sin modificarlas:

```bash
python3 scripts/audit_v2.py --out-dir /path/to/legacy/run --strict
```
