# research-anything

**Uma skill do Claude Code com barreiras de evidência para decisões que podem chegar à produção.**

[English](README.md) · [简体中文](README_CN.md) · [日本語](README_JA.md) · [한국어](README_KO.md) · [Español](README_ES.md) · [Français](README_FR.md) · [Deutsch](README_DE.md) · [Português](README_PT.md) · [Русский](README_RU.md)

> Esta página é um resumo curto da v3 para evitar manter detalhes traduzidos desatualizados. Consulte o [README em inglês](README.md) ou o [README em chinês](README_CN.md) para ver a especificação completa, os limites operacionais e a matriz de conectores.

## Visão geral da v3

research-anything atualmente se destina **somente ao Claude Code**. Ele pode partir de um pedido vago e segue este processo:

1. Preserva literalmente o pedido e as respostas do usuário.
2. Faz uma sondagem ampla no Douyin, Xiaohongshu, Zhihu, Bilibili, YouTube, GitHub, X/Twitter e na web geral.
3. Aprofunda a pesquisa de forma adaptativa conforme novos candidatos, contradições, evidências independentes e lacunas críticas.
4. Explica termos, candidatos, achados corroborados e incertezas antes de fazer as poucas perguntas que mudariam a decisão.
5. Executa uma segunda pesquisa direcionada quando novas restrições alteram a resposta.
6. Publica o resultado como `production-ready`, `pilot-only` ou `blocked`.

Os oito canais são uma camada de descoberta, não uma garantia de cobertura completa. Cada domínio também exige suas fontes primárias e oficiais relevantes. Se faltar evidência crítica, o sistema não emite uma recomendação para produção.

Cada execução pode ser retomada a partir de `research.db` e exporta, entre outros, `manifest.v3.json`, `events.jsonl`, `findings.jsonl`, `claims.jsonl`, `decision.json`, `report.html` e `runbook.json`. O ASR só é executado dentro de limites numéricos explícitos, com reserva atômica de orçamento e idempotência baseada na impressão digital da mídia.

## Instalação

Requisitos: Claude Code, Python 3.11 ou posterior e Git.

```bash
git clone https://github.com/Somezak1/research-anything.git ~/research-anything
cd ~/research-anything
python3 scripts/install_skill.py doctor
python3 scripts/install_skill.py install
python3 scripts/install_skill.py check
```

Para atualizar após verificar uma instalação diferente:

```bash
git pull
python3 scripts/install_skill.py check
python3 scripts/install_skill.py install --force
python3 scripts/install_skill.py check
```

`doctor` informa conectores ausentes como lacunas de capacidade; ele não instala ferramentas de terceiros nem faz login automaticamente. A duração não é fixa, e Claude, APIs, ASR, proxies ou dados comerciais podem gerar custos.

`MediaCrawler` é um conector opcional para aprendizado ou pesquisa pessoal não comercial, conforme sua [licença não comercial](https://github.com/NanmiCoder/MediaCrawler/blob/main/LICENSE). Ele não é o padrão para pesquisa comercial.

Execuções antigas da v2 podem ser auditadas sem alteração:

```bash
python3 scripts/audit_v2.py --out-dir /path/to/legacy/run --strict
```
