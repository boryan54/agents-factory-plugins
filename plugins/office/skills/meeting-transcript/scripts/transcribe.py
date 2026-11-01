#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Запись встречи (видео или аудио) -> реплики по голосам с таймкодами.

Три шага, всё локально, без облачных сервисов и без токенов:
  1. ffmpeg вынимает звук: 16 кГц, моно, WAV;
  2. faster-whisper распознаёт речь с отметками времени по словам;
  3. sherpa-onnx делит запись на голоса (сегментация pyannote 3.0 + эмбеддинги
     голоса), каждое слово получает голос по наибольшему пересечению.

На выходе в --out-dir:
  <slug>.turns.json  [{"start": сек, "end": сек, "speaker": "S1", "text": "..."}]
  <slug>.meta.json   файл, длительность, чем распознано, сколько голосов и слов, время работы

Имена голосам скрипт НЕ даёт: только S1, S2... в порядке первого появления.
Кто есть кто, решает агент по содержанию разговора или заказчик.

Запуск:
    python transcribe.py <запись.mp4|m4a|mp3|wav> --out-dir .work --slug 2026-11-01-vstrecha
        [--speakers 2]          число собеседников, если известно (сильно повышает точность)
        [--model auto]          auto: large-v3-turbo на видеокарте, small на процессоре
        [--language ru]
        [--no-diarize]          без разделения на голоса
        [--models-dir ...]      где лежат модели разделения голосов (скачаются сами)

Зависимости: ffmpeg, faster-whisper, sherpa-onnx, numpy. Модели разделения голосов
(~47 МБ) скачиваются с GitHub-релизов sherpa-onnx при первом запуске.
"""
import argparse
import json
import os
import subprocess
import sys
import tarfile
import time
import urllib.request
import wave

SEG_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
           "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2")
EMB_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
           "speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx")
EMB_NAME = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def hms(sec):
    sec = int(sec or 0)
    return "%02d:%02d:%02d" % (sec // 3600, sec % 3600 // 60, sec % 60)


def extract_wav(src, dst):
    cmd = ["ffmpeg", "-loglevel", "error", "-y", "-i", src, "-vn",
           "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", dst]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("ffmpeg не смог вынуть звук:\n" + r.stderr.strip())
    with wave.open(dst) as w:
        return w.getnframes() / float(w.getframerate())


def read_samples(wav):
    import numpy as np
    with wave.open(wav) as w:
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def has_cuda():
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def asr(wav, model_name, language):
    from faster_whisper import WhisperModel
    cuda = has_cuda()
    if model_name == "auto":
        model_name = "large-v3-turbo" if cuda else "small"
    device = "cuda" if cuda else "cpu"
    compute = "float16" if cuda else "int8"
    log("Распознаю: faster-whisper %s, %s" % (model_name, device))
    model = WhisperModel(model_name, device=device, compute_type=compute)
    # Звук подаём массивом, а не путём: так faster-whisper не зависит от версии PyAV
    segments, info = model.transcribe(
        read_samples(wav), language=language, word_timestamps=True, vad_filter=True,
        beam_size=5, condition_on_previous_text=False)
    words = []
    for seg in segments:
        for w in (seg.words or []):
            t = w.word
            if t.strip():
                words.append({"start": float(w.start), "end": float(w.end), "text": t})
        log("  %s  %s" % (hms(seg.start), seg.text.strip()[:70]))
    return words, "faster-whisper %s (%s)" % (model_name, device)


def ensure_models(models_dir):
    os.makedirs(models_dir, exist_ok=True)
    seg_dir = os.path.join(models_dir, "sherpa-onnx-pyannote-segmentation-3-0")
    seg = os.path.join(seg_dir, "model.onnx")
    emb = os.path.join(models_dir, EMB_NAME)
    if not os.path.exists(seg):
        log("Скачиваю модель сегментации (~7 МБ)...")
        arc = os.path.join(models_dir, "seg.tar.bz2")
        urllib.request.urlretrieve(SEG_URL, arc)
        with tarfile.open(arc) as t:
            t.extractall(models_dir)
        os.remove(arc)
    if not os.path.exists(emb):
        log("Скачиваю модель голоса (~40 МБ)...")
        urllib.request.urlretrieve(EMB_URL, emb)
    return seg, emb


def diarize(wav, models_dir, speakers, threshold):
    import numpy as np
    import sherpa_onnx
    seg, emb = ensure_models(models_dir)
    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=seg)),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=emb),
        clustering=sherpa_onnx.FastClusteringConfig(
            num_clusters=speakers if speakers else -1, threshold=threshold),
        min_duration_on=0.3, min_duration_off=0.5)
    if not cfg.validate():
        sys.exit("Неверная конфигурация разделения голосов: проверь модели в " + models_dir)
    sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)
    samples = read_samples(wav)
    log("Делю на голоса%s..." % (" (%d)" % speakers if speakers else ""))
    res = sd.process(samples).sort_by_start_time()
    return [{"start": r.start, "end": r.end, "spk": r.speaker} for r in res]


def label_words(words, spans):
    """Каждому слову – голос с наибольшим пересечением; нет пересечения – ближайший."""
    if not spans:
        for w in words:
            w["spk"] = 0
        return
    for w in words:
        best, best_ov = None, 0.0
        for s in spans:
            ov = min(w["end"], s["end"]) - max(w["start"], s["start"])
            if ov > best_ov:
                best, best_ov = s["spk"], ov
        if best is None:
            mid = (w["start"] + w["end"]) / 2
            best = min(spans, key=lambda s: min(abs(mid - s["start"]), abs(mid - s["end"])))["spk"]
        w["spk"] = best


def smooth(words, min_words=2):
    """Одиночные слова чужого голоса внутри реплики – почти всегда ошибка границы."""
    n = len(words)
    i = 0
    while i < n:
        j = i
        while j + 1 < n and words[j + 1]["spk"] == words[i]["spk"]:
            j += 1
        run = j - i + 1
        if run < min_words and 0 < i and j < n - 1 and words[i - 1]["spk"] == words[j + 1]["spk"]:
            for k in range(i, j + 1):
                words[k]["spk"] = words[i - 1]["spk"]
        i = j + 1


def to_turns(words, gap=1.5):
    order, turns = {}, []
    for w in words:
        if w["spk"] not in order:
            order[w["spk"]] = "S%d" % (len(order) + 1)
        spk = order[w["spk"]]
        if turns and turns[-1]["speaker"] == spk and w["start"] - turns[-1]["end"] <= gap:
            turns[-1]["text"] += w["text"]
            turns[-1]["end"] = w["end"]
        else:
            turns.append({"start": round(w["start"], 2), "end": w["end"],
                          "speaker": spk, "text": w["text"]})
    for t in turns:
        t["text"] = " ".join(t["text"].split())
        t["end"] = round(t["end"], 2)
    return turns, len(order)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("--out-dir", default=".work")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--speakers", type=int, default=0)
    ap.add_argument("--model", default="auto")
    ap.add_argument("--language", default="ru")
    ap.add_argument("--no-diarize", action="store_true")
    ap.add_argument("--threshold", type=float, default=0.9,
                    help="порог склейки голосов, если число собеседников не задано")
    ap.add_argument("--models-dir", default=os.path.join(
        os.path.expanduser("~"), ".cache", "meeting-transcript"))
    a = ap.parse_args()

    if not os.path.exists(a.src):
        sys.exit("Нет файла: " + a.src)
    os.makedirs(a.out_dir, exist_ok=True)
    t0 = time.time()
    wav = os.path.join(a.out_dir, a.slug + ".wav")
    dur = extract_wav(a.src, wav)
    log("Длительность записи: %s" % hms(dur))

    words, engine = asr(wav, a.model, a.language)
    t_asr = time.time() - t0
    if not words:
        sys.exit("Речь не распознана: файл пустой, тишина или не тот язык (--language).")

    if a.no_diarize:
        spans, diar = [], "не делалось (--no-diarize)"
    else:
        spans = diarize(wav, a.models_dir, a.speakers, a.threshold)
        diar = "sherpa-onnx: pyannote-segmentation-3.0 + eres2net" + (
            ", собеседников задано: %d" % a.speakers if a.speakers else ", число голосов – автоматически")
    label_words(words, spans)
    smooth(words)
    turns, n_spk = to_turns(words)

    n_words = sum(len(t["text"].split()) for t in turns)
    meta = {
        "source": os.path.basename(a.src),
        "duration_sec": round(dur, 1),
        "duration": hms(dur),
        "engine": engine,
        "language": a.language,
        "diarization": diar,
        "speakers_found": n_spk if spans else None,
        "turns": len(turns),
        "words": n_words,
        "coverage": "%s … %s" % (hms(turns[0]["start"]), hms(turns[-1]["end"])),
        "work_sec": {"asr": round(t_asr, 1), "total": round(time.time() - t0, 1)},
    }
    base = os.path.join(a.out_dir, a.slug)
    with open(base + ".turns.json", "w", encoding="utf-8") as f:
        json.dump(turns, f, ensure_ascii=False, indent=1)
    with open(base + ".meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    os.remove(wav)

    print("Готово: %s.turns.json" % base)
    print("Длительность %s · покрытие %s · реплик %d · слов %d · голосов %s" % (
        meta["duration"], meta["coverage"], meta["turns"], n_words, meta["speakers_found"]))
    print("Распознано: %s · разделение: %s · работа %s" % (
        engine, diar, hms(meta["work_sec"]["total"])))


if __name__ == "__main__":
    main()
