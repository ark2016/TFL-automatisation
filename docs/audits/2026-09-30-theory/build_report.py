#!/usr/bin/env python3
"""Build the offline theory-audit report from its JSON evidence files."""
from __future__ import annotations

import html
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
AUDIT = ROOT / "docs" / "audits" / "2026-09-30-theory"
OUTPUT = ROOT / "docs" / "theory_audit_2026-09-30.html"


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read {path}: {exc}") from exc


def safe_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def source_rows(data: dict) -> list[dict]:
    src = data.get("sources", {})
    if isinstance(src, list):
        return src
    if isinstance(src, dict):
        return [{"id": key, **(value if isinstance(value, dict) else {"details": value})} for key, value in src.items()]
    return []


def finding_rows(data: dict, pipeline: str) -> list[dict]:
    rows = []
    for item in data.get("assertions", []) + data.get("claims", []):
        row = dict(item)
        row["pipeline"] = pipeline
        row["status"] = row.get("status") or ("corrected" if row.get("fix") else data.get("status", "status_not_provided"))
        row["statement"] = row.get("statement", row.get("finding", "(без формулировки)"))
        row["where"] = row.get("section", row.get("location", ""))
        evidence = row.get("evidence", row.get("proof", row.get("limitation", "")))
        row["evidence_text"] = {"evidence": evidence, "regressions": row.get("regressions", [])} if row.get("regressions") else evidence
        row["mapping_text"] = row.get("mapping", row.get("prompt_code_mapping", row.get("paths", [])))
        row["correction"] = row.get("correction", row.get("fix", ""))
        rows.append(row)
    return rows


def json_script(name: str, value) -> str:
    return f'<script type="application/json" id="{name}">{safe_json(value)}</script>'


def build() -> str:
    cfl = read_json(AUDIT / "cfl_review.json", {}) or {}
    reg = read_json(AUDIT / "reg_review.json", {}) or {}
    dcfl = read_json(AUDIT / "dcfl_review.json", {}) or {}
    ll = read_json(AUDIT / "ll_review.json", {}) or {}
    sources = read_json(AUDIT / "source_followup.json", {}) or {}
    inventory = read_json(AUDIT / "inventory.json", {}) or {}
    verification = read_json(AUDIT / "verification.json")

    groups = []
    for key, label, obj in [("REG", "REG", reg), ("CFL", "CFL", cfl), ("DCFL", "DCFL", dcfl), ("LL", "LL(k)", ll)]:
        groups.append({"key": key, "label": label, "count": len(obj.get("assertions", [])) + len(obj.get("claims", []))})
    all_findings = []
    all_findings.extend(finding_rows(reg, "REG"))
    all_findings.extend(finding_rows(cfl, "CFL"))
    all_findings.extend(finding_rows(dcfl, "DCFL"))
    all_findings.extend(finding_rows(ll, "LL(k)"))
    by_id = {x.get("id"): x for x in all_findings}
    translations = {
        "CFL-FILTER-SIGNS": ("Фильтр со знаками нельзя автоматически считать регулярным", "Порог #a−#b≥0 нерегулярен: префиксы aⁿ различаются суффиксом bᵐ при n<m.", "Подсчёт разности с порогом требует неограниченной памяти; модульные условия рассматриваются отдельно."),
        "CFL-FILTER-BOOLEAN": ("Нерегулярность компонента не переносится через Boolean-операции", "F ∪ ¬F = Σ*, а F ∩ ¬F = ∅ даже если F нерегулярен.", "Проверять нужно итоговый язык всей формулы, а не помечать комбинацию по одному операнду."),
        "CFL-PARIKH-SAMPLE": ("Конечные Parikh-наблюдения не решают полную семилинейность", "Любое конечное множество векторов семилинейно; CFG S→ε|a|a⁴|a⁹ ломает старую эвристику роста по короткому префиксу.", "Наблюдаемая квадратичная динамика выборки не позволяет объявить язык не-CFL."),
        "CFL-BOUNDS": ("Порядок терминалов в отдельных правилах не доказывает ограниченность языка буквами", "S→aS|bS|ε порождает {a,b}*, хотя каждое правило локально имеет простой вид.", "Для положительного сертификата используется точный конечный DFA-реляционный fixed point."),
        "CFL-CLOSURE-LITERAL": ("Старый отрицательный свидетель фактически принадлежал языку", "Слово aabacaabac входит в язык скопированных блоков и регулярный фильтр; свидетель заменён на aabacabac.", "Задачу опровергает конкретное слово, проверенное относительно построенного оракула."),
        "CFL-CFG-FILTER-GUARD": ("Регулярный фильтр не спасает некорректный CFG-сертификат", "Некорректное начальное состояние или символ слева от правила раньше могло пройти как доказательство принадлежности CFL.", "Сначала проверяются сам CFG и его start/LHS-контракты, затем строится регулярный фильтр; добавлены отдельные регрессии."),
        "REG-01": ("Исправлено: конечный поиск накачки даёт только ограниченные свидетельства", "Регулярное однословное множество {a¹⁰} может не выявить проблему на малом p; у языка, исключающего длину 4, разбиение может пройти i=0,2, но провалиться при i=3.", "Неизвестные проверки оставляют trust well_formed; отрицательный вывод требует точного свидетельства."),
        "REG-02": ("Исправлено: рост классов Нероуда по конечной глубине не решает регулярность", "Стабилизация на конечной глубине не исключает различимые продолжения за пределами найденных слов.", "Эвристика возвращает scoped plausible diagnostics со статусом well_formed."),
        "DCFL-002": ("Лемма Ю сверялась по статье, пересказывающей результат, а не по оригиналу 1989 года", "Старые course witnesses не опровергали полную дизъюнкцию альтернатив накачки.", "Yamakami 2021, лемма 3.2, PDF p.7 прочитана; статья Yu полностью недоступна в этой проверке."),
        "DCFL-006": ("Опровержение накачки должно охватывать каждое разрешённое разбиение", "Провал одной выбранной декомпозиции не отрицает существование другой; отдельно приведено универсальное marked-continuation доказательство.", "Локальное доказательство, не независимая сверка полного текста Yu."),
        "DCFL-008": ("Для критерия через классы Нероуда нужно проверять все классы", "Одна бесконечная различимая семья не означает, что каждый класс конечен.", "Тезис взят из учебных слайдов по Шаллиту; исходная монография не прочитана, дополнение локальное."),
        "LL-001": ("LL(k) — свойство грамматики, а не только данного представления языка", "Таблица конфликтует у выбранной грамматики — это ещё не доказательство, что у языка нет другой LL(k)-грамматики.", "Определение 8.7 Нихольта прочитано и визуально сверено на указанных страницах."),
        "LL-011": ("Исправлена граница общей префиксной формы в доказательстве для LL(k)", "Старое усиленное неравенство ломается на LL(1): S→a¹⁰⁰A, A→b|c, если брать короткий общий префикс.", "Используется форма wδ с ℓ−k < |w| ≤ ℓ; аргумент и контрпример записаны в LL review."),
        "LL-022": ("Преобразование устранения левой рекурсии должно сохранять пустой язык", "S→Sa задаёт ∅, а прежнее преобразование ошибочно создавало a*; nullable-префиксы могут скрывать рекурсию.", "Условия сохранения языка и скрытая левая рекурсия получили отдельные исправления и контрпримеры."),
        "LL-024": ("Пропуск слова в выборке или неизвестный ответ оракула не доказывают неэквивалентность", "S→A³⁰, A→ε и S→ε задают один язык, хотя лимит sentential-form поиска давал false.", "Отрицательный вывод допускается только при точном отрицательном ответе; успешная выборка остаётся ограниченной."),
    }
    highlights = []
    for key, (title, counterexample, source_note) in translations.items():
        row = by_id.get(key)
        if row:
            highlights.append({"id": key, "pipeline": row["pipeline"], "status": row["status"], "finding": title, "counterexample": counterexample, "affected_paths": row.get("mapping_text") or row.get("where") or "см. полную запись ниже", "source": source_note})
    source_cards = sources.get("original_source_followup", [])

    if verification:
        verification_state = "Final verification data are present in verification.json."
        ver_json = verification
    else:
        verification_state = "Финальный сводный файл verification.json пока отсутствует; итоговые тесты и проверки здесь не заявляются."
        ver_json = None

    payload = {
        "groups": groups,
        "findings": all_findings,
        "highlights": highlights,
        "sourceCards": source_cards,
        "sourceRows": {"REG": source_rows(reg), "CFL": source_rows(cfl), "DCFL": source_rows(dcfl), "LL(k)": source_rows(ll)},
        "verification": ver_json,
        "auditBoundaries": sources.get("global_boundaries", []),
        "inventory": inventory,
    }

    template = r'''<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light">
<link rel="icon" href="data:,">
<title>Теоретический аудит TFL — 30 сентября 2026</title>
<style>
:root{--bg:#f3f4ef;--paper:#fffefa;--ink:#202d27;--muted:#59665e;--line:#d5ddd5;--green:#236347;--green-soft:#e8f1e9;--gold:#845b16;--gold-soft:#f6efd9;--red:#8a3a36;--red-soft:#f8eae7;--blue:#315d78;--blue-soft:#e9f1f5;--shadow:0 10px 30px #2031280d}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font:17px/1.65 'Segoe UI',Arial,sans-serif}main{max-width:1120px;margin:auto;padding:0 26px;overflow-wrap:anywhere}header{padding:56px 0 30px}h1,h2,h3{font-family:Cambria,Georgia,serif;line-height:1.12;letter-spacing:-.025em}h1{font-size:clamp(37px,6vw,65px);max-width:900px;margin:18px 0}h2{font-size:clamp(27px,4vw,38px);margin:0 0 18px}h3{font-size:22px;margin:5px 0 10px}p{margin:8px 0 16px}.eyebrow,.section-label{font-size:12px;text-transform:uppercase;letter-spacing:.14em;font-weight:700;color:var(--green)}.lead{max-width:900px;font-size:clamp(18px,2.5vw,22px);line-height:1.5}.muted,.caption{color:var(--muted);font-size:14px}a{color:var(--green);text-underline-offset:3px}a:hover{text-decoration-thickness:2px}nav{display:flex;flex-wrap:wrap;gap:8px 22px;padding:15px 0;border-block:1px solid var(--line);font-size:14px}section{padding:43px 0;border-bottom:1px solid var(--line)}.notice{padding:17px 20px;background:var(--gold-soft);border-left:4px solid var(--gold);border-radius:0 7px 7px 0}.notice strong{color:#573b0e}.cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:24px 0}.card,.source-card,.highlight,.finding{background:var(--paper);border:1px solid var(--line);border-radius:9px;box-shadow:var(--shadow)}.card{padding:18px}.card strong{display:block;font:700 32px/1.1 Cambria,Georgia,serif;color:var(--green)}.card small{color:var(--muted)}.pipeline-strip{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:21px 0}.pipeline-strip div{padding:12px 14px;border-top:3px solid var(--green);background:var(--paper)}.pipeline-strip b{display:block}.pipeline-strip span{font-size:13px;color:var(--muted)}.architecture{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}.architecture article{padding:18px;background:var(--paper);border:1px solid var(--line);border-radius:8px}.architecture .num{color:var(--green);font-size:12px;font-weight:700;letter-spacing:.1em}.tag{display:inline-block;margin:2px 4px 2px 0;padding:3px 8px;border-radius:99px;background:var(--green-soft);color:var(--green);font-size:12px;font-weight:700}.tag.gold{background:var(--gold-soft);color:var(--gold)}.tag.red{background:var(--red-soft);color:var(--red)}.tag.blue{background:var(--blue-soft);color:var(--blue)}.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:18px}.source-card,.highlight{padding:19px;margin:14px 0}.highlight{border-left:4px solid var(--green)}.highlight .meta{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px}.highlight .body{color:var(--muted)}details{margin:12px 0;border-top:1px solid var(--line);padding-top:10px}summary{cursor:pointer;font-weight:650}summary:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid #a67b28;outline-offset:3px}.source-list{padding-left:20px}.source-list li{margin:5px 0}.controlbar{display:grid;grid-template-columns:minmax(220px,1fr) 180px 180px auto;gap:10px;margin:20px 0}.controlbar input,.controlbar select,.controlbar button{min-width:0;width:100%;font:inherit;font-size:15px;color:var(--ink);background:var(--paper);border:1px solid #aab7ad;border-radius:6px;padding:10px 12px}.controlbar button{cursor:pointer;background:var(--green-soft);font-weight:650}.resultline{font-size:14px;color:var(--muted);margin:6px 0 15px}.finding{padding:17px;margin:11px 0;box-shadow:none}.finding[hidden]{display:none}.finding-head{display:flex;align-items:flex-start;gap:10px;flex-wrap:wrap}.finding-id{font:700 13px Consolas,monospace;color:var(--blue)}.finding h3{font:600 18px/1.4 'Segoe UI',Arial,sans-serif;letter-spacing:0;margin:4px 0 10px}.finding .facts{display:grid;grid-template-columns:1fr 1fr;gap:8px 18px;font-size:14px}.finding .facts b{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}.finding details{font-size:14px}.finding code,code{font: .9em/1.5 Consolas,monospace;background:#edf1ea;padding:2px 5px;border-radius:4px;overflow-wrap:anywhere}.empty{display:none;padding:20px;color:var(--muted)}.source-row{padding:10px 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.source-row:last-child{border:0}.source-row a{overflow-wrap:anywhere}footer{padding:30px 0 50px;color:var(--muted);font-size:14px}.links{display:flex;flex-wrap:wrap;gap:10px 22px}.source-level{font-size:13px;color:var(--muted)}.phase-title{font-size:16px;font-weight:700}.verification{background:var(--blue-soft);border-left:4px solid var(--blue);padding:17px 20px;border-radius:0 7px 7px 0}
.verify-cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:17px 0}.verify-card{background:var(--paper);border:1px solid var(--line);border-radius:8px;padding:14px}.verify-card span,.verify-card small{display:block;color:var(--muted);font-size:13px}.verify-card strong{display:block;font:700 29px/1.2 Cambria,Georgia,serif;color:var(--green);margin:4px 0}.lean-scope{margin:17px 0;padding:16px 18px;background:#f5f1e5;border-left:4px solid var(--gold)}.rich-list{margin:6px 0;padding-left:23px}.rich-list li{margin:4px 0}.rich-dl{display:grid;grid-template-columns:minmax(110px,190px) 1fr;gap:5px 12px;margin:8px 0}.rich-dl dt{font-weight:650;color:var(--muted);overflow-wrap:anywhere}.rich-dl dd{margin:0;overflow-wrap:anywhere}.evidence-block{margin:8px 0}.table-wrap{display:block;width:100%;max-width:100%;min-width:0;overflow-x:auto;overscroll-behavior-x:contain}.verification details{max-width:100%;overflow:hidden}table{border-collapse:collapse;min-width:520px;width:100%;font-size:14px}th,td{padding:9px;text-align:left;vertical-align:top;border-bottom:1px solid var(--line)}th{font-size:12px;color:var(--muted)}
.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
@media(max-width:760px){main{padding:0 18px}header{padding-top:38px}.cards,.verify-cards{grid-template-columns:1fr 1fr}.pipeline-strip{grid-template-columns:1fr 1fr}.architecture,.grid-2{grid-template-columns:1fr}.controlbar{grid-template-columns:1fr 1fr}.controlbar input{grid-column:1/-1}.finding .facts{grid-template-columns:1fr}.finding{padding:14px}.source-card,.highlight{padding:15px}section{padding:34px 0}}
@media(max-width:420px){.cards,.verify-cards{grid-template-columns:1fr 1fr;gap:8px}.card,.verify-card{padding:13px}.card strong,.verify-card strong{font-size:25px}.pipeline-strip{grid-template-columns:1fr}.controlbar{grid-template-columns:1fr}.controlbar input{grid-column:auto}body{font-size:16px}.rich-dl{grid-template-columns:1fr;gap:0}.rich-dl dd{margin-bottom:7px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
</style>
</head>
<body><main>
<header><div class="eyebrow">TFL · проверка теоретических контрактов · 30.09.2026</div><h1>Что именно проверено в теории TFL</h1><p class="lead">Отчёт связывает математические утверждения о REG, CFL, DCFL и LL(k) с источниками, локальными доказательствами и местами применения в коде. Уровни доказательств различаются: наличие ссылки само по себе не доказывает корректность реализации или будущего ответа языковой модели.</p><div class="notice"><strong>Граница вывода.</strong> Этот аудит не гарантирует правильность каждого утверждения, всех старых отчётов или любого будущего ответа. Он фиксирует проверенные формулировки и контрпримеры в выбранных контрактах, отмечает недоступные первоисточники и отделяет математику от реализации.</div>
<div class="cards" id="metrics"></div>
<nav aria-label="Навигация по отчёту"><a href="#method">Как читать статусы</a><a href="#architecture">Путь проверки</a><a href="#highlights">Главные находки</a><a href="#sources">Источники</a><a href="#all">Все утверждения</a><a href="#verification">Проверка кода</a></nav>
</header>

<section id="method"><div class="section-label">Как читать отчёт</div><h2>Четыре разных вида свидетельств</h2><div class="architecture">
<article><div class="num">A · ИСТОЧНИК</div><h3>Формулировка теоремы</h3><p>Статус «source_checked» означает, что отмеченное утверждение сверяли с указанным текстом и локатором. Источник может быть первичным, учебным или библиотекой формальных доказательств; это показано отдельно.</p></article>
<article><div class="num">B · ЛОКАЛЬНОЕ ДОКАЗАТЕЛЬСТВО</div><h3>Аргумент для примера</h3><p>«proved_locally» означает, что в аудите приведено математическое рассуждение или конструкция. Это не машинная формализация и не утверждение о корректности всех путей программы.</p></article>
<article><div class="num">C · ОГРАНИЧЕННЫЙ ЭКСПЕРИМЕНТ</div><h3>Конечная проверка</h3><p>Тесты, перечисление грамматик, выборка слов и оракулы проверяют только заданную область. Отсутствие найденного контрпримера в конечном поиске не доказывает универсальную теорему.</p></article>
</div><p class="caption">Статус описывает конкретную строку в аудиторском JSON, а не общий уровень доверия ко всему пайплайну.</p></section>

<section id="architecture"><div class="section-label">Схема ответственности</div><h2>Теория проходит несколько разных проверок</h2><p>Порядок нужен, чтобы не смешивать доказательство математического факта с тестированием кода и с генерацией отчёта.</p>
<div class="pipeline-strip"><div><b>1. Теория</b><span>Определения, леммы, гипотезы</span></div><div><b>2. Промпты и код</b><span>Как контракт используется</span></div><div><b>3. Тесты и конечные поиски</b><span>Регрессии и ограниченные случаи</span></div><div><b>4. Lean</b><span>Отдельная формализация конкретной теоремы</span></div></div>
<div class="grid-2"><article><h3>Что может подтвердить источник</h3><p>Содержание утверждения в конкретной книге, статье, слайдах или формальной библиотеке при указанном локаторе. Если прочитан только abstract или библиографическая запись, точное содержание теоремы не подтверждено.</p></article><article><h3>Что требует отдельной проверки</h3><p>Перевод задачи в IR, обработка пустого слова, кода оракула, правильность статусов, полнота тестов, сетевые/LLM ответы и соответствие реализации доказанному контракту.</p></article></div></section>

<section id="highlights"><div class="section-label">Главные находки</div><h2>Где аудит изменил или ограничил выводы</h2><p>Карточки ниже собраны из source_followup.json: они выделяют условия теорем, контрпримеры и пределы источников, а не заменяют полный журнал.</p><div id="highlight-list"></div></section>

<section id="sources"><div class="section-label">Первичные и вторичные источники</div><h2>Что удалось прочитать</h2><p>Страница издателя может подтвердить библиографию или abstract, но этого недостаточно для точной формулировки теоремы. В карточках явно записано, читался ли полный текст.</p><div id="primary-sources"></div><h3>Источники по направлениям</h3><div id="source-matrix"></div><details><summary>Границы источниковой проверки</summary><ul id="boundaries" class="source-list"></ul></details></section>

<section id="all"><div class="section-label">Полный журнал</div><h2>Утверждения и контракты</h2><p>Записи сгруппированы по направлению. Ищи по формулировке, файлу, источнику или статусу. Строки сохраняют исходные ID и статус из аудиторских JSON.</p><div class="controlbar"><label class="sr-only" for="search">Поиск</label><input id="search" type="search" placeholder="Поиск по тексту, пути, ID…" autocomplete="off"><label class="sr-only" for="pipeline">Направление</label><select id="pipeline"><option value="">Все направления</option></select><label class="sr-only" for="status">Статус</label><select id="status"><option value="">Все статусы</option></select><button id="clear" type="button">Сбросить</button></div><div id="resultline" class="resultline" aria-live="polite"></div><div id="finding-list" aria-live="polite"></div><div id="empty" class="empty">По этим условиям записей нет.</div></section>

<section id="verification"><div class="section-label">Пределы машинной проверки</div><h2>Тесты не являются доказательствами теорем</h2><div class="verification" id="verification-state"></div><p>Финальные количества проверок не подставляются из промежуточных JSON, потому что их статус может измениться после общего прогона. Если появится <code>verification.json</code>, генератор добавит его сводку; до этого результат явно помечен как pending.</p><details><summary>Что всё равно остаётся за пределами этого отчёта</summary><ul><li>Доказательство всех математических фактов во всём репозитории.</li><li>Формальная проверка каждого локального доказательства в Lean или Coq.</li><li>Полная эквивалентность кода математической спецификации.</li><li>Гарантия корректности всех существующих и будущих LLM-ответов.</li><li>Неограниченное доказательство по результатам конечных тестов или перебора.</li></ul></details></section>

<footer><p class="section-label">Связанные материалы</p><div class="links"><a href="THEORY_REFERENCE.md">Справочник формулировок и источников</a><a href="THEORY.md">Технические доказательства и примеры</a><a href="audit_fixes_2026-09-30.html">Предыдущий отчёт об исправлениях</a><a href="architecture_explorer.html">Интерактивный обзор архитектуры</a></div><p>Эта страница работает офлайн: внешние скрипты, шрифты, библиотеки и сетевые запросы не используются. Для обновления данных запусти <code>python docs/audits/2026-09-30-theory/build_report.py</code> из корня репозитория.</p></footer>
</main>
__DATA_SCRIPTS__
<script>
(()=>{
const data=JSON.parse(document.getElementById('report-data').textContent);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const text=v=>Array.isArray(v)?v.map(text).join('; '):typeof v==='object'&&v!==null?JSON.stringify(v):String(v??'');
const fmt=(v,depth=0)=>{if(v===null||v===undefined)return '';if(Array.isArray(v))return `<ul class="rich-list">${v.map(x=>`<li>${fmt(x,depth+1)}</li>`).join('')}</ul>`;if(typeof v==='object'){if(depth>3)return `<code>${esc(JSON.stringify(v))}</code>`;return `<dl class="rich-dl">${Object.entries(v).map(([k,x])=>`<dt>${esc(k)}</dt><dd>${fmt(x,depth+1)}</dd>`).join('')}</dl>`}return esc(v)};
const labelStatus=s=>({source_checked:'Источник сверён',proved_locally:'Доказано локально',corrected:'Исправлено',unresolved:'Не разрешено',bounded_pass:'Только ограниченная проверка',bounded_check_only:'Только конечная проверка',status_not_provided:'Статус не указан',source_checked_with_scope_limit:'Источник сверён с оговоркой',source_checked_secondary:'Сверён по вторичному источнику',secondary_source_checked_original_unread:'Вторичный источник; оригинал не прочитан',metadata_only_full_text_unread:'Только метаданные; полный текст не прочитан',abstract_only_full_text_unread:'Прочитан abstract; полный текст недоступен',unresolved_source_gap:'Пробел в источниках',local_proof_corrected:'Локальный аргумент исправлен',locally_proved:'Доказано локально',bounded_or_unknown:'Ограничено или неизвестно',inventory_flag_needs_review:'Требует проверки по inventory',source_checked_definition:'Определение сверено',source_checked_with_local_construction:'Источник + локальная конструкция',secondary_source_checked_plus_local_completion:'Вторичный источник + локальное дополнение',local_proofs:'Локальные доказательства'}[s]||s||'Статус не указан');
const klass=s=>/unresolved|gap|unread|unknown|needs_review/.test(s)?'red':/bounded|metadata|abstract/.test(s)?'gold':/secondary/.test(s)?'blue':'';
document.getElementById('metrics').innerHTML=data.groups.map(g=>`<div class="card"><strong>${esc(g.count)}</strong><span>${esc(g.label)} · записей в подробном журнале</span></div>`).join('')+`<div class="card"><strong>${esc(data.findings.length)}</strong><span>Всего записей с утверждениями в текущих JSON</span></div>`;
document.getElementById('highlight-list').innerHTML=data.highlights.map(x=>`<article class="highlight"><div class="meta"><span class="tag ${klass(x.status)}">${esc(labelStatus(x.status))}</span><span class="tag">${esc(x.id)}</span></div><h3>${esc(x.finding)}</h3><div class="body">${x.counterexample?`<p><b>Контрпример или граница:</b> ${esc(x.counterexample)}</p>`:''}<p><b>Затронуто:</b> ${esc(text(x.affected_paths))}</p><p><b>Источник:</b> ${esc(x.source)}</p></div></article>`).join('');
document.getElementById('primary-sources').innerHTML=data.sourceCards.map(x=>`<article class="source-card"><div class="tag ${klass(x.status)}">${esc(labelStatus(x.status))}</div><h3>${esc(x.work)}</h3><p><a href="${esc(x.url)}">${esc(x.url)}</a></p><p><b>Локатор:</b> ${esc(x.locator)}</p><p><b>Что прочитано:</b> ${esc(x.read)}</p><p><b>Осталось не проверено:</b> ${esc(x.gap)}</p><div class="source-level">Уровень источника: ${esc(x.source_level)}</div></article>`).join('');
const sm=document.getElementById('source-matrix');sm.innerHTML=Object.entries(data.sourceRows).map(([group,rows])=>`<details><summary>${esc(group)} · ${rows.length} записей источников</summary><div>${rows.map(x=>`<div class="source-row"><b>${esc(x.id||x.title||'Источник')}</b> ${x.url?`<a href="${esc(x.url)}">${esc(x.url)}</a>`:''}<div>${esc(x.read||x.actually_read||x.type||x.status||x.limitation||'')}</div>${x.not_read?`<div class="muted"><b>Не прочитано:</b> ${esc(x.not_read)}</div>`:''}${x.limitation?`<div class="muted"><b>Ограничение:</b> ${esc(x.limitation)}</div>`:''}</div>`).join('')}</div></details>`).join('');
document.getElementById('boundaries').innerHTML=data.auditBoundaries.map(x=>`<li>${esc(x)}</li>`).join('');
const pipe=document.getElementById('pipeline'),stat=document.getElementById('status');
[...new Set(data.findings.map(x=>x.pipeline))].forEach(p=>{let o=document.createElement('option');o.value=p;o.textContent=p;pipe.append(o)});
[...new Set(data.findings.map(x=>x.status))].sort().forEach(s=>{let o=document.createElement('option');o.value=s;o.textContent=labelStatus(s);stat.append(o)});
const renderFinding=x=>{let allText=text([x.id,x.pipeline,x.status,x.statement,x.where,x.hypotheses,x.evidence_text,x.issue,x.mapping_text,x.sources,x.counterexample,x.correction,x.limitation]);return `<article class="finding" data-pipeline="${esc(x.pipeline)}" data-status="${esc(x.status)}" data-search="${esc(allText.toLowerCase())}"><div class="finding-head"><span class="finding-id">${esc(x.id||'без ID')}</span><span class="tag">${esc(x.pipeline)}</span><span class="tag ${klass(x.status)}">${esc(labelStatus(x.status))}</span></div><h3>${esc(x.statement)}</h3><div class="facts">${x.where?`<div><b>Раздел / расположение</b>${esc(x.where)}</div>`:''}${x.hypotheses?`<div><b>Условия</b>${esc(x.hypotheses)}</div>`:''}${x.issue?`<div><b>Проблема / контрпример</b>${esc(x.issue)}</div>`:''}${x.counterexample?`<div><b>Контрпример</b>${esc(x.counterexample)}</div>`:''}</div><details><summary>Доказательства и соответствие</summary>${x.evidence_text?`<div class="evidence-block"><b>Свидетельство</b>${fmt(x.evidence_text)}</div>`:''}${x.correction?`<p><b>Исправление:</b> ${fmt(x.correction)}</p>`:''}${x.limitation?`<p><b>Ограничение:</b> ${fmt(x.limitation)}</p>`:''}${x.sources?`<p><b>Источники:</b> ${fmt(x.sources)}</p>`:''}${x.mapping_text?`<p><b>Соответствие в репозитории:</b> ${fmt(x.mapping_text)}</p>`:''}</details></article>`};
document.getElementById('finding-list').innerHTML=data.findings.map(renderFinding).join('');
const search=document.getElementById('search'),list=[...document.querySelectorAll('.finding')],empty=document.getElementById('empty'),line=document.getElementById('resultline');
function filter(){let q=search.value.trim().toLowerCase(),p=pipe.value,s=stat.value,n=0;list.forEach(el=>{let show=(!p||el.dataset.pipeline===p)&&(!s||el.dataset.status===s)&&(!q||el.dataset.search.includes(q));el.hidden=!show;if(show)n++});empty.style.display=n?'none':'block';line.textContent=`Показано ${n} из ${list.length} записей.`}
search.addEventListener('input',filter);pipe.addEventListener('change',filter);stat.addEventListener('change',filter);document.getElementById('clear').addEventListener('click',()=>{search.value='';pipe.value='';stat.value='';filter();search.focus()});filter();
const v=document.getElementById('verification-state');
if(data.verification){const ver=data.verification, runs=ver.runs||[], core=runs.find(x=>/core/i.test(x.name||'')),ui=runs.find(x=>/ui/i.test(x.name||'')),lean=ver.lean||{};const card=(title,value,detail)=>`<article class="verify-card"><span>${esc(title)}</span><strong>${esc(value)}</strong><small>${esc(detail||'')}</small></article>`;let cards=`<div class="verify-cards">${core?card('Core · пройдено',core.passed??'—',`${core.skipped??0} пропусков · ${core.xfailed??0} ожидаемых отказов · ${core.passed_subtests??0} под-проверок · ${core.seconds??'—'} с`):card('Core','не указан','Нет записи core в verification.json')}${ui?card('UI · пройдено',ui.passed??'—',`${ui.failures??0} ошибок · ${ui.skipped??0} пропусков · ${ui.seconds??'—'} с`):card('UI','не указан','Нет записи ui в verification.json')}${card('Lean · статус',lean.status||'не указан',`${(lean.axioms||[]).length} стандартных аксиом · ${lean.elapsed??'—'} с`)}</div>`;let runRows=runs.map(r=>`<tr><th>${esc(r.name)}</th><td>${esc(r.passed??'—')}${r.passed_subtests?` <span class="muted">(+${esc(r.passed_subtests)} под-проверок)</span>`:''}</td><td>${esc(r.skipped??0)}</td><td>${esc(r.xfailed??0)}</td><td>${esc((Number(r.failures)||0)+(Number(r.errors)||0))}</td><td>${esc(r.seconds??'—')} с</td></tr>`).join('');let details=`<details><summary>Команды, исключения и причины пропуска</summary><div class="table-wrap"><table><thead><tr><th>Набор</th><th>Passed</th><th>Skipped</th><th>Xfailed</th><th>Failures + errors</th><th>Время</th></tr></thead><tbody>${runRows}</tbody></table></div>${runs.map(r=>`<details><summary>${esc(r.name)} · детали</summary>${r.command?`<p><b>Команда:</b> <code>${esc(r.command)}</code></p>`:''}${r.skip_reasons?.length?`<p><b>Причины пропусков:</b></p>${fmt(r.skip_reasons)}`:''}${r.failures?.length?`<p><b>Failure details:</b>${fmt(r.failures)}</p>`:''}${r.errors?.length?`<p><b>Error details:</b>${fmt(r.errors)}</p>`:''}</details>`).join('')}</details>`;let leanBlock=`<div class="lean-scope"><b>Что доказывает Lean здесь.</b> Ticket 50 формализует конкретный язык слов <i>w₁ b w₂ w₃</i> с равными длинами <i>w₁,w₂,w₃</i>, выделенным блоком из одной буквы <i>b</i> (другие <i>b</i> в слове допускаются) и двоичным <i>w₂</i>, которое имеет чётную длину и чередуется, начиная с <i>a</i>; последнее условие равносильно <i>w₂∈(ab)*</i>. Результат применим к этой формулировке языка. Отдельный Lean-чек не формализует все алгоритмы Python или все выводы по LL(k).<p>${esc(lean.scope||'')}</p><p>Аксиомы: ${esc((lean.axioms||[]).join(', ')||'не указаны')}. SHA-256: <code>${esc(lean.sha256||'не указан')}</code>. Свидетельство: <code>${esc(lean.evidence_file||'не указано')}</code>.</p></div>`;let envNote=ver.environment_retry?.ui_sandbox?.reason?`<p class="muted"><b>UI retry:</b> ${esc(ver.environment_retry.ui_sandbox.reason)} Чистый повторный прогон выше прошёл; два sandbox-отказа не остаются в итоговом результате.</p>`:'';let limits=`<details><summary>Ограничения и дополнительные проверки</summary>${fmt(ver.limits||[])}${ver.additional_checks?`<p><b>Дополнительные конечные проверки:</b>${fmt(ver.additional_checks)}</p>`:''}${ver.environment_retry?`<p><b>Детали повтора в среде с нужными правами:</b>${fmt(ver.environment_retry)}</p>`:''}</details>`;v.innerHTML=`<b>Итоговый verification.json загружен.</b> Ниже показаны прогоны и их пределы. Пропуски и ожидаемые xfail не считаются прошедшими проверками.<p>Ветка: <code>${esc(ver.branch||'не указана')}</code> · база: <code>${esc(ver.base_commit||'не указана')}</code> · платные API-вызовы: ${esc(ver.paid_api_calls??'не указано')}.</p>${cards}${envNote}${leanBlock}${details}${limits}`}else v.textContent='__VERIFICATION_STATE__';
})();
</script></body></html>'''
    scripts = json_script("report-data", payload)
    return template.replace("__DATA_SCRIPTS__", scripts).replace("__VERIFICATION_STATE__", html.escape(verification_state))


if __name__ == "__main__":
    OUTPUT.write_text(build(), encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")
