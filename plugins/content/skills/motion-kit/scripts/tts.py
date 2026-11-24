"""Закадровый голос по фразам из timeline.json: через BobGate (gemini-3.8-flash-tts у Google), иначе Genosai.

    python tts.py <папка ролика> [--voice Charon] [--style Newscaster] [--pace Natural] [--ids 2,4]

Ключ: сначала BobGate – переменная BOBGATE_API_KEY или файл .secrets/bobgate.env (BOBGATE_API_KEY=..., адрес
BOBGATE_URL, по умолчанию https://bobgate.kupryakov.ru); если его нет – Genosai: GENOSAI_API_KEY или
.secrets/genosai.env. Файлы ищутся вверх по дереву от папки ролика, от текущей папки, от этого скрипта и в
~/.secrets. Ключ никогда не печатается.
Результат: audio/vo_<id>.wav (тишина по краям срезана), audio/vo_durations.json и предупреждение,
если фраза не помещается до начала следующей. Цена: BobGate – доли рубля за фразу; Genosai – 1.5 кредита
за 100 символов, минимум 5 за фразу.
Голоса: муж. Charon, Puck, Algieba, Orus, Fenrir, Sadachbia; жен. Kore, Sulafat, Leda, Aoede.
Стили: Vocal Smile, Newscaster, Empathetic, Promo/Hype, Deadpan, Whisper."""
import argparse, io, json, os, pathlib, re, subprocess, sys, time
import numpy as np
from scipy.io import wavfile

API = "https://api.genosai.io"
BOBGATE = "https://bobgate.kupryakov.ru"
MODEL = "gemini-3.1-flash-tts"

def _from_files(starts, fname, var):
    dirs = []
    for s in starts:
        d = pathlib.Path(s).resolve()
        dirs += [d, *d.parents]
    for p in [*dirs, pathlib.Path.home()]:
        f = p / ".secrets" / fname
        if f.is_file():
            m = re.search(rf"^{var}\s*=\s*['\"]?([^\s'\"]+)", f.read_text(encoding="utf-8"), re.M)
            if m: return m.group(1)
    return None

def find_key(starts):
    """BobGate, если есть его ключ (адрес и модель меняются на BobGate), иначе Genosai."""
    global API, MODEL
    k = os.environ.get("BOBGATE_API_KEY") or _from_files(starts, "bobgate.env", "BOBGATE_API_KEY")
    if k:
        API = (os.environ.get("BOBGATE_URL") or _from_files(starts, "bobgate.env", "BOBGATE_URL") or BOBGATE).rstrip("/")
        MODEL = "gemini-3.8-flash-tts"
        return k
    return os.environ.get("GENOSAI_API_KEY") or _from_files(starts, "genosai.env", "GENOSAI_API_KEY")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root"); ap.add_argument("--voice", default="Charon"); ap.add_argument("--style", default="Newscaster")
    ap.add_argument("--pace", default="Natural"); ap.add_argument("--ids", default="")
    a = ap.parse_args()
    import requests
    root = pathlib.Path(a.root).resolve()
    key = find_key([root, pathlib.Path.cwd(), pathlib.Path(__file__).parent])
    if not key: sys.exit("нет ключа: положи .secrets/bobgate.env (или genosai.env) в проект, фабрику или ~/.secrets "
                         "(в чат ключ не присылать)")
    H = {"Authorization": f"Bearer {key}"}
    tl = json.loads((root / "timeline.json").read_text(encoding="utf-8"))
    out = root / "audio"; out.mkdir(exist_ok=True)
    only = {int(x) for x in a.ids.split(",") if x.strip()}
    durs_f = out / "vo_durations.json"; durs = json.loads(durs_f.read_text(encoding="utf-8")) if durs_f.exists() else {}
    total = 0.0
    for v in tl["vo"]:
        if only and v["id"] not in only: continue
        body = {"model": MODEL, "input": {"text": v["text"], "voice": a.voice, "style": a.style, "pace": a.pace}}
        r = requests.post(f"{API}/v1/createTask", headers={**H, "Content-Type": "application/json"},
                          data=json.dumps(body, ensure_ascii=False).encode("utf-8"), timeout=30)
        if r.status_code != 200: sys.exit(f"фраза {v['id']}: HTTP {r.status_code} (401 – ключ/хост, 402 – нет кредитов/лимита) {r.text[:200]}")
        tid = r.json()["data"]["taskId"]; d = {}
        for _ in range(150):
            d = requests.get(f"{API}/v1/taskInfo", headers=H, params={"taskId": tid}, timeout=30).json()["data"]
            if d.get("status") in ("succeeded", "failed"): break
            time.sleep(2)
        if d.get("status") != "succeeded": sys.exit(f"фраза {v['id']}: генерация не удалась ({d.get('status')} {d.get('message') or ''})")
        total += float(d.get("cost") or 0)
        raw = requests.get(d["result"]["media_urls"][0], timeout=60).content
        try:
            sr, x = wavfile.read(io.BytesIO(raw))
        except Exception:   # пришёл не wav – перекодируем ffmpeg
            src = out / f"vo_{v['id']}.src"; src.write_bytes(raw); tmp = out / f"vo_{v['id']}.tmp.wav"
            subprocess.run([os.environ.get("MOTION_FFMPEG", "ffmpeg"), "-y", "-loglevel", "error", "-i", str(src), "-ac", "1", "-ar", "48000", str(tmp)], check=True)
            sr, x = wavfile.read(tmp); src.unlink(); tmp.unlink()
        x = x.astype(np.float64); x = x.mean(1) if x.ndim > 1 else x
        idx = np.where(np.abs(x) > np.abs(x).max() * 0.02)[0]
        x = x[max(0, idx[0] - int(0.03 * sr)): idx[-1] + int(0.12 * sr)]
        wavfile.write(out / f"vo_{v['id']}.wav", sr, (x / np.abs(x).max() * 0.9 * 32767).astype(np.int16))
        durs[str(v["id"])] = round(len(x) / sr, 2)
        print(f"  фраза {v['id']}: {durs[str(v['id'])]:.2f} с")
    durs_f.write_text(json.dumps(durs, ensure_ascii=False, indent=1), encoding="utf-8")
    vo = tl["vo"]
    for i, v in enumerate(vo):
        end = vo[i + 1]["t"] if i + 1 < len(vo) else tl["dur"] - 1.0
        d = durs.get(str(v["id"]))
        if d and v["t"] + d > end - 0.2:
            print(f"  ВНИМАНИЕ: фраза {v['id']} ({d:.1f} с) не помещается до {end:.1f} с – сдвинь тайминги или сократи текст")
    via = "BobGate, рублей" if "bobgate" in API else "Genosai, кредитов"
    print(f"списано ({via}): {total:.2f}; модель {MODEL}, голос {a.voice}, стиль {a.style}")

if __name__ == "__main__":
    main()
