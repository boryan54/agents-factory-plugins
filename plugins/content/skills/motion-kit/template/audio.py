"""Звук примера (8 с): музыка, звуковой дизайн, голос, мастер. Под каждый ролик пишется заново
по карте акцентов раскадровки. Запуск из папки проекта: python audio.py"""
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent
KIT = json.loads((ROOT / "kit.json").read_text(encoding="utf-8"))["scripts"]   # путь к scripts/ motion-kit
sys.path.insert(0, KIT)
from audio_kit import Kit   # noqa: E402

TL = json.loads((ROOT / "timeline.json").read_text(encoding="utf-8"))
K = Kit(TL["dur"])
BEAT = 60 / 120

# музыка: Фа мажор, 120 BPM; до акцента 4.0 – прозрачно, после – пульс
for t0, ch, cut in ((0, [53, 57, 60, 64], 900), (2, [50, 53, 57, 60], 1100), (4, [53, 57, 60, 67], 2200), (6, [46, 50, 53, 57], 2000)):
    K.place(K.music, K.pad(ch, 2.0, 0.12, cut, att=0.6 if t0 < 4 else 0.2, rel=1.4 if t0 == 6 else 0.5), t0)
    up = [m + 12 for m in ch]
    for i, b in enumerate([x * BEAT / 2 for x in range(8)]):
        if t0 < 4 and i % 2: continue
        K.place(K.music, K.piano(up[(i * 3) % 4], 0.5, 0.15), t0 + b, pan=-0.3 + 0.1 * (i % 6))
hits = []
for b in [2.4 + i * BEAT for i in range(int((8 - 2.4) / BEAT))]:
    if b >= 3.5 and b < 4.0: continue        # пауза перед акцентом
    K.place(K.drums, K.kick(0.6 if b >= 4 else 0.35), b); hits.append(b)
    if b >= 4: K.place(K.drums, K.hat(0.08), b + BEAT / 2, pan=0.3)
K.place(K.music, K.whoosh(1.4, 300, 6000, 0.2, "up"), 2.6)
K.place(K.drums, K.kick(0.9, 38), 4.0); hits.append(4.0)
music = K.sidechain(K.music, hits) + K.drums
music = K.reverb(music, amount=0.4)

# звуковой дизайн: каждое событие сцены получает свой звук
K.place(K.sfx, K.whoosh(1.6, 400, 3000, 0.16, "mid"), 0.5)      # прилёт карточки
K.place(K.sfx, K.thud(0.3), 2.4); K.place(K.sfx, K.pop(520, 760, 0.1), 2.42)   # посадка
K.place(K.sfx, K.bell(1760, 2.5, 0.16), 4.0); K.place(K.sfx, K.thud(0.6), 4.0)  # «готово»
K.place(K.sfx, K.pop(700, 1000, 0.12), 4.05)
sfx = K.reverb(K.sfx, amount=0.25)

n_vo = K.load_vo(TL, ROOT / "audio")
mix = K.master(music, sfx, K.vo if n_vo else None)
K.write(ROOT / "audio" / "mix.wav", mix)
print("audio/mix.wav готов,", f"голос: {n_vo} фраз" if n_vo else "без голоса")
