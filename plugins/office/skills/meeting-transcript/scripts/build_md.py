#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборщик протокола встречи: meta.json + turns.json -> MD-файл.

Только stdlib. Скрипт делает механику: шапку, список участников, расшифровку по репликам
с таймкодами. Смысловые разделы (кратко, договорённости, задачи, открытые вопросы)
скрипт НЕ выдумывает: оставляет заглушки, которые агент заполняет после чтения.

Запуск:
    python build_md.py --meta .work/<slug>.meta.json --turns .work/<slug>.turns.json
        --out output/meetings/2026-11-01-vstrecha.md
        [--title "Старт рабочей группы"] [--date 2026-11-15]
        [--names "S1=Олег,S2=Марина"]   кто есть кто, если установлено
        [--fix "00:00:49=S1"]          перевесить реплику на другой голос по таймкоду начала;
                                       только когда по смыслу очевидно, каждая правка
                                       попадает в шапку списком

Без --meta (расшифровка пришла из облачного сервиса и приведена к turns.json):
        --source <имя записи> --engine "transcribe.so (облако)" [--diarization "..."]
Счётчики (реплики, слова, покрытие, голоса) всегда считаются по turns.json.
"""
import argparse
import json
import os
import sys


def hms(sec):
    sec = int(sec or 0)
    return "%02d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)


def plural(n, one, few, many):
    n = abs(int(n))
    if n % 100 // 10 == 1:
        return many
    last = n % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def stub(what):
    return "<!-- АГЕНТ: %s -->\n_Не заполнено._" % what


SECTIONS = [
    ("Кратко", "3–5 предложений: о чём встреча и чем закончилась. Только то, что прозвучало."),
    ("Ход встречи по темам", "темы в том порядке, как шёл разговор: подзаголовок «### [ЧЧ:ММ:СС–ЧЧ:ММ:СС] "
     "Тема», под ним 2–4 строки: кто что предлагал, на чём сошлись или разошлись."),
    ("Договорённости", "списком: о чём договорились окончательно. У каждого пункта таймкод "
     "[ЧЧ:ММ:СС]. Предложение без согласия второй стороны – не договорённость, ему место "
     "в открытых вопросах."),
    ("Задачи", "таблица | Что сделать | Кто | Срок | Таймкод |. Кто и срок – только если "
     "прозвучали; не прозвучали – пиши «не названо», не додумывай."),
    ("Открытые вопросы", "списком: что осталось нерешённым или отложено, с таймкодом."),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--engine", default="")
    ap.add_argument("--diarization", default="")
    ap.add_argument("--language", default="ru")
    ap.add_argument("--turns", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--date", default="", help="дата встречи ГГГГ-ММ-ДД, если известна")
    ap.add_argument("--names", default="", help="S1=Имя,S2=Имя")
    ap.add_argument("--fix", default="", help="ЧЧ:ММ:СС=S1,... перевесить реплику на другой голос")
    a = ap.parse_args()

    turns = json.load(open(a.turns, encoding="utf-8"))
    if not turns:
        sys.exit("turns.json пустой: собирать нечего")
    fixes = []
    for pair in filter(None, [p.strip() for p in a.fix.split(",")]):
        tc, _, spk = pair.partition("=")
        hit = [t for t in turns if hms(t["start"]) == tc.strip()]
        if not hit:
            sys.exit("--fix: нет реплики, начинающейся в %s" % tc)
        for t in hit:
            fixes.append("[%s] с %s на %s" % (tc.strip(), t["speaker"], spk.strip()))
            t["speaker"] = spk.strip()
    if fixes:   # склеить соседние реплики одного голоса после правки
        merged = []
        for t in turns:
            if merged and merged[-1]["speaker"] == t["speaker"]:
                merged[-1]["text"] += " " + t["text"]
                merged[-1]["end"] = t["end"]
            else:
                merged.append(dict(t))
        turns = merged
    meta = json.load(open(a.meta, encoding="utf-8")) if a.meta else {}
    if not meta and not (a.source and a.engine):
        sys.exit("Нужен --meta или пара --source и --engine")
    for key, val in (("source", a.source), ("engine", a.engine),
                     ("diarization", a.diarization), ("language", a.language)):
        if val and (key != "language" or not meta.get(key)):
            meta[key] = val
    spk_set = {t.get("speaker") for t in turns if t.get("speaker")}
    meta["turns"] = len(turns)
    meta["words"] = sum(len(t["text"].split()) for t in turns)
    meta["coverage"] = "%s … %s" % (hms(turns[0]["start"]), hms(turns[-1]["end"]))
    meta["speakers_found"] = len(spk_set) if len(spk_set) > 1 or meta.get("speakers_found") else None
    meta.setdefault("duration", hms(turns[-1]["end"]) + " (по последней реплике)")
    meta.setdefault("diarization", "есть" if meta["speakers_found"] else "нет")
    meta.setdefault("language", "ru")
    names = {}
    for pair in filter(None, [p.strip() for p in a.names.split(",")]):
        k, _, v = pair.partition("=")
        names[k.strip()] = v.strip()

    # Статистика по голосам: сколько говорил каждый
    stat = {}
    for t in turns:
        s = stat.setdefault(t["speaker"], {"sec": 0.0, "turns": 0, "first": t["start"]})
        s["sec"] += t["end"] - t["start"]
        s["turns"] += 1
    total = sum(s["sec"] for s in stat.values()) or 1

    title = a.title or os.path.splitext(meta["source"])[0]
    L = ["# Протокол встречи «%s»" % title, ""]
    L.append("- **Дата встречи:** %s" % (
        a.date or "не указана; дата в имени файла – дата расшифровки"))
    L.append("- **Запись:** `%s`, %s" % (meta["source"], meta["duration"]))
    L.append("- **Распознано:** %s, язык `%s`" % (meta["engine"], meta["language"]))
    L.append("- **Разделение на голоса:** %s" % meta["diarization"])
    L.append("- **Объём:** %d %s, %d %s, покрытие %s" % (
        meta["turns"], plural(meta["turns"], "реплика", "реплики", "реплик"),
        meta["words"], plural(meta["words"], "слово", "слова", "слов"), meta["coverage"]))
    if fixes:
        L.append("- **Разметка голосов поправлена вручную:** " + "; ".join(fixes))
    L.append("")
    L.append("## Участники")
    L.append("")
    if meta.get("speakers_found") is None:
        L.append("Разделения на голоса нет: реплики ниже идут одним потоком.")
    else:
        for spk in sorted(stat, key=lambda k: stat[k]["first"]):
            s = stat[spk]
            who = names.get(spk, "не установлено, кто это")
            L.append("- **%s** – %s: %d %s, %d\u00a0%% времени речи, впервые в %s" % (
                spk, who, s["turns"], plural(s["turns"], "реплика", "реплики", "реплик"),
                round(100 * s["sec"] / total), hms(s["first"])))
    L.append("")
    L.append("> **О точности.** Текст распознан машиной: имена, фамилии, названия и цифры "
             "сверять с записью по таймкоду, прежде чем цитировать. Разделение на голоса тоже "
             "машинное: короткие вставки в одно-два слова на стыке реплик могут быть "
             "приписаны не тому собеседнику.")
    L.append("> <!-- АГЕНТ: впиши сюда 2–5 замеченных искажений вида «услышано» вместо "
             "«правильно» и спорные места разметки; не нашёл – удали эту строку. -->")
    L.append("")
    for head, what in SECTIONS:
        L.append("## " + head)
        L.append("")
        L.append(stub(what))
        L.append("")
    L.append("## Расшифровка")
    L.append("")
    for t in turns:
        who = names.get(t["speaker"], t["speaker"])
        L.append("**[%s] %s:** %s" % (hms(t["start"]), who, t["text"]))
        L.append("")

    text = "\n".join(L).rstrip() + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(text)
    print("Готово: %s" % a.out)
    print("Байт: %d · реплик: %d · слов: %d · голосов: %s" % (
        len(text.encode("utf-8")), meta["turns"], meta["words"], meta.get("speakers_found")))
    left = sum(1 for h, _ in SECTIONS)
    print("Заглушек для заполнения: %d (+ строка о точности)" % left)


if __name__ == "__main__":
    sys.exit(main())
