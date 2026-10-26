#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборщик MD: meta.json + cues.json (+ опционально спикеры) -> готовый файл расшифровки.

Только stdlib. Скрипт делает механику: шапку, оглавление по главам, склейку коротких
реплик в читаемые абзацы с таймкодами. Саммари и тезисы скрипт НЕ выдумывает —
он оставляет плашку-заглушку, которую агент заменяет своим текстом после чтения.

Запуск:
    python build_md.py --meta <slug>.meta.json --cues <slug>.cues.json
                       --out output/transcripts/2026-08-16-imya.md
                       [--engine "субтитры ВК"] [--speakers speakers.json]

--speakers: [{"start": сек, "end": сек, "speaker": "SPEAKER_00"}, ...] от WhisperX.
            Без него в файл пишется честное «разделения по спикерам нет».
"""
import argparse
import json
import os
import re
import sys

PLACEHOLDER = (
    "<!-- САММАРИ: заменить этот блок. Прочитай расшифровку ниже и напиши\n"
    "     3–5 предложений о чём видео + 5–9 тезисов списком. Ничего не добавляй\n"
    "     от себя: только то, что реально сказано в тексте. -->\n"
    "_Саммари не заполнено._"
)


# Врезка о качестве автосубтитров ВК: термины и имена собственные там регулярно
# искажаются. Ставится автоматически на уровне 0, конкретные примеры дописывает агент.
SOURCE_NOTE = (
    "> **О качестве источника.** Текст снят с автоматических субтитров ВК: термины,\n"
    "> имена собственные и отчества местами искажены, пунктуация расставлена машиной.\n"
    "> Смысл восстанавливается по контексту, но дословное цитирование – только после\n"
    "> сверки с видео.\n"
    "> <!-- АГЕНТ: при чтении выпиши сюда 3–5 конкретных искажений вида\n"
    ">      «услышано» вместо «правильно», если они встретились; иначе строку удали. -->"
)

DASH = "–"          # правило фабрики: длинное тире в сдаваемых текстах запрещено


def hms(sec):
    sec = int(sec or 0)
    return "%02d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)


def plural(n, one, few, many):
    """Согласование числительных: 1 абзац / 2 абзаца / 5 абзацев."""
    n = abs(int(n))
    if n % 100 // 10 == 1:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def human_date(yyyymmdd):
    s = str(yyyymmdd or "")
    return "%s-%s-%s" % (s[:4], s[4:6], s[6:8]) if len(s) == 8 and s.isdigit() else DASH


def speaker_at(spans, t):
    for s in spans:
        if s["start"] <= t <= s["end"]:
            return s.get("speaker")
    return None


SENT_END = re.compile(r"[.!?…]['\"»)]?$")
CLAUSE_END = re.compile(r"[,;:]['\"»)]?$")     # запятая — законная точка отката


def _clause_cut(buf, floor):
    """Куда резать сверхдлинный период: ближайшая назад запятая, но не раньше floor.

    Возвращает длину куска. Если запятых в окне нет — рубим по счётчику, как раньше.
    """
    for i in range(len(buf) - 1, floor - 1, -1):
        if CLAUSE_END.search(buf[i][0]):
            return i + 1
    return len(buf)


def paragraphs(cues, spans=None, soft=70, hard=160, pause=2.5, stats=None):
    """Склейка коротких реплик (в среднем 5–8 слов) в читаемые абзацы.

    Работаем не репликами, а предложениями: реплика почти никогда не кончается
    на точке, и разрыв по реплике режет фразу пополам. Поэтому сначала собираем
    поток слов с временами, режем его на предложения, потом набираем предложения
    в абзац до soft слов. Разрыв раньше срока: смена спикера или пауза >= pause.

    Страховка hard — на случай, когда точек нет вовсе (у ВК периоды до 266 слов).
    Срабатывает она НЕ по счётчику слов: точка разреза откатывается назад
    к ближайшей запятой, иначе разрыв приходится на середину придаточного.
    stats (если передан) наполняется счётчиками принудительных разрывов.
    """
    st = stats if stats is not None else {}
    st.setdefault("hard_breaks", 0)         # всего принудительных разрывов
    st.setdefault("hard_at_clause", 0)      # из них уведённых на запятую
    st.setdefault("longest_period", 0)      # самый длинный период без точки, слов

    words = []                      # (слово, время, спикер, пауза_перед)
    prev_end = None
    for c in cues:
        gap = 0.0 if prev_end is None else max(0.0, c["start"] - prev_end)
        spk = speaker_at(spans, c["start"]) if spans else None
        for i, w in enumerate(c["text"].split()):
            words.append((w, c["start"], spk, gap if i == 0 else 0.0))
        prev_end = c["end"]

    sentences, buf = [], []         # предложение = (слова, время, спикер, пауза)
    run = 0                         # длина текущего периода без точки
    for w in words:
        buf.append(w)
        run += 1
        if SENT_END.search(w[0]):
            st["longest_period"] = max(st["longest_period"], run)
            run = 0
            sentences.append(buf)
            buf = []
        elif len(buf) >= hard:
            k = _clause_cut(buf, hard // 2)
            st["hard_breaks"] += 1
            if CLAUSE_END.search(buf[k - 1][0]):
                st["hard_at_clause"] += 1
            sentences.append(buf[:k])
            buf = buf[k:]
    if buf:
        st["longest_period"] = max(st["longest_period"], run)
        sentences.append(buf)

    out, para = [], []
    for s in sentences:
        head = s[0]
        brk = bool(para) and (
            (spans and head[2] != para[0][0][2])
            or (head[3] >= pause and sum(len(x) for x in para) >= 20)
        )
        if brk:
            out.append(_para(para))
            para = []
        para.append(s)
        if sum(len(x) for x in para) >= soft:
            out.append(_para(para))
            para = []
    if para:
        out.append(_para(para))
    return out


def _para(sents):
    flat = [w for s in sents for w in s]
    return {"start": flat[0][1], "speaker": flat[0][2],
            "text": " ".join(w[0] for w in flat)}


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", required=True)
    ap.add_argument("--cues", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--engine")
    ap.add_argument("--speakers")
    ap.add_argument("--date", help="дата расшифровки ГГГГ-ММ-ДД (по умолчанию сегодня)")
    a = ap.parse_args()

    meta = json.load(open(a.meta, encoding="utf-8"))
    cues = json.load(open(a.cues, encoding="utf-8"))
    spans = json.load(open(a.speakers, encoding="utf-8")) if a.speakers else None
    engine = a.engine or meta.get("engine") or "не указано"
    today = a.date
    if not today:
        import datetime
        today = datetime.date.today().isoformat()

    stats = {}
    paras = paragraphs(cues, spans, stats=stats)
    words = sum(len(p["text"].split()) for p in paras)
    dur = meta.get("duration") or (cues[-1]["end"] if cues else 0)

    L = []
    L.append("# %s\n" % (meta.get("title") or "Расшифровка видео"))
    L.append("| | |")
    L.append("|---|---|")
    L.append("| Канал / автор | %s |" % (meta.get("uploader") or DASH))
    L.append("| Длительность | %s |" % hms(dur))
    L.append("| Дата загрузки | %s |" % human_date(meta.get("upload_date")))
    L.append("| Ссылка | %s |" % (meta.get("url") or DASH))
    L.append("| Расшифровано | %s |" % today)
    L.append("| Чем распознано | **%s** |" % engine)
    L.append("| Объём | %d %s, %d %s |" % (
        words, plural(words, "слово", "слова", "слов"),
        len(paras), plural(len(paras), "абзац", "абзаца", "абзацев")))
    L.append("")
    if spans:
        names = sorted({s.get("speaker") for s in spans if s.get("speaker")})
        L.append("> Спикеров размечено: %d (%s). Диаризация автоматическая – "
                 "проверь границы реплик в спорных местах.\n" % (len(names), ", ".join(names)))
    else:
        L.append("> **Разделения по спикерам нет** – источник его не даёт. "
                 "Кто именно говорит, по этому файлу определить нельзя.\n")

    L.append("## Кратко\n")
    L.append(PLACEHOLDER + "\n")

    chapters = meta.get("chapters") or []
    if chapters:
        L.append("## Главы\n")
        for ch in chapters:
            L.append("- `%s` %s" % (hms(ch.get("start_time")), ch.get("title") or DASH))
        L.append("")

    if "субтитр" in engine.lower():          # уровень 0 — врезка о качестве обязательна
        L.append(SOURCE_NOTE + "\n")

    L.append("## Расшифровка\n")
    ch_idx = 0
    for p in paras:
        while ch_idx < len(chapters) and p["start"] >= (chapters[ch_idx].get("start_time") or 0):
            L.append("### %s\n" % (chapters[ch_idx].get("title") or hms(p["start"])))
            ch_idx += 1
        head = "`%s`" % hms(p["start"])
        if p.get("speaker"):
            head += " **%s:**" % p["speaker"]
        L.append("%s %s\n" % (head, p["text"]))

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    body = "\n".join(L).rstrip() + "\n"
    open(a.out, "w", encoding="utf-8").write(body)
    size = len(body.encode("utf-8"))
    print("OK  %s – %d %s, %d %s, %d байт (%.0f КБ), движок: %s" % (
        a.out,
        words, plural(words, "слово", "слова", "слов"),
        len(paras), plural(len(paras), "абзац", "абзаца", "абзацев"),
        size, size / 1024.0, engine))
    print("    принудительных разрывов сверхдлинных периодов: %d (из них по запятой %d), "
          "самый длинный период без точки: %d %s" % (
              stats["hard_breaks"], stats["hard_at_clause"], stats["longest_period"],
              plural(stats["longest_period"], "слово", "слова", "слов")))
    print("    не забудь заменить блок «Кратко» – сейчас там заглушка")


if __name__ == "__main__":
    main()
