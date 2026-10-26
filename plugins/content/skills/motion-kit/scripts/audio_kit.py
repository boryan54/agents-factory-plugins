"""audio_kit – синтез музыки и звукового дизайна кодом, без сервисов и сэмплов.

Импорт в audio.py проекта:
    import sys; sys.path.insert(0, r"<путь к motion-kit>/scripts"); from audio_kit import *
    K = Kit(dur=50.0)              # шина 48 кГц стерео с запасом на хвосты
    K.place(K.music, K.piano(72, 1.0, 0.25), t0=16.0, pan=0.1)
    ...
    mix = K.master(music, sfx, vo)  # громкость под телефон, лимитер, обрезка по длительности
    K.write("audio/mix.wav", mix)

Всё детерминировано (seed). Инструменты: piano, pad, bass, kick, clap, hat, bell; SFX: whoosh, tick, pop,
thud, rustle, beep, sweep. Обработка: reverb, sidechain, duck_under_voice, master (LUFS + лимитер).
Зависимости: numpy, scipy; pyloudnorm – по желанию (иначе громкость по RMS-оценке).
"""
import json, pathlib
import numpy as np
from scipy.signal import butter, sosfilt, fftconvolve, resample_poly
from scipy.io import wavfile
from scipy.ndimage import maximum_filter1d, minimum_filter1d

SR = 48000

def mtof(m): return 440.0 * 2 ** ((m - 69) / 12)


class Kit:
    def __init__(self, dur, sr=SR, seed=7, tail=3.0):
        self.sr, self.dur = sr, dur
        self.N = int(sr * (dur + tail))
        self.rng = np.random.default_rng(seed)
        self.music, self.drums, self.sfx, self.vo = self.bus(), self.bus(), self.bus(), self.bus()

    # ---------- шина и размещение
    def bus(self): return np.zeros((self.N, 2))
    def tt(self, d): return np.arange(int(d * self.sr)) / self.sr
    def noise(self, d): return self.rng.standard_normal(int(d * self.sr))
    def place(self, b, x, t0, gain=1.0, pan=0.0):
        i0 = int(round(t0 * self.sr))
        if i0 >= self.N or i0 < 0: return
        x = x[: self.N - i0] * gain
        l, r = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
        b[i0:i0 + len(x), 0] += x * l * 1.414; b[i0:i0 + len(x), 1] += x * r * 1.414

    # ---------- фильтры и огибающие
    def bp(self, x, lo, hi, order=2): return sosfilt(butter(order, [lo, hi], btype="band", fs=self.sr, output="sos"), x)
    def lp(self, x, f, order=2): return sosfilt(butter(order, f, btype="low", fs=self.sr, output="sos"), x)
    def hp(self, x, f, order=2): return sosfilt(butter(order, f, btype="high", fs=self.sr, output="sos"), x)
    def fade(self, x, a=0.003, r=0.01):
        na, nr = max(1, int(a * self.sr)), max(1, int(r * self.sr)); x = x.copy()
        x[:na] *= np.linspace(0, 1, na); x[-nr:] *= np.linspace(1, 0, nr); return x

    # ---------- инструменты
    def piano(self, m, dur=1.6, vel=0.5):
        f = mtof(m); t = self.tt(dur + 0.4); y = np.zeros_like(t)
        for k in range(1, 9):
            fk = k * f * np.sqrt(1 + 0.0004 * k * k)
            if fk > 16000: break
            y += np.sin(2 * np.pi * fk * t + k) * (1 / k ** 1.4) * np.exp(-t * (0.9 + 0.55 * k) * (f / 400) ** 0.3)
        y *= np.minimum(1, t / 0.004); y *= np.where(t > dur, np.exp(-(t - dur) * 12), 1)
        y += self.lp(self.rng.standard_normal(len(t)), 3000) * np.exp(-t * 60) * 0.03
        return self.fade(y * vel, 0.002, 0.05)
    def pad(self, notes, dur, vel=0.12, cut=1400, att=0.5, rel=0.9):
        t = self.tt(dur + rel); y = np.zeros_like(t)
        for m in notes:
            for det in (-0.07, 0.0, 0.07):
                f = mtof(m + det); ph = self.rng.random(); y += 2 * ((f * t + ph) % 1.0) - 1
        y = self.lp(self.lp(y, cut), cut * 1.2) / (len(notes) * 3) * 2.2
        env = np.minimum(1, t / att) * np.where(t > dur, np.exp(-(t - dur) / (rel / 3)), 1)
        return y * env * vel
    def bass(self, m, dur, vel=0.35):
        f = mtof(m); t = self.tt(dur + 0.08)
        y = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t) + 0.08 * np.sin(6 * np.pi * f * t)
        env = np.minimum(1, t / 0.006) * np.exp(-t * 1.5) * np.where(t > dur, np.exp(-(t - dur) * 60), 1)
        return y * env * vel
    def kick(self, vel=0.8, low=45):
        t = self.tt(0.5); f = low + 75 * np.exp(-t * 28); ph = 2 * np.pi * np.cumsum(f) / self.sr
        y = np.sin(ph) * np.exp(-t * 7.5) + self.hp(self.rng.standard_normal(len(t)), 3000) * np.exp(-t * 300) * 0.25
        return self.fade(y * vel, 0.0005, 0.02)
    def clap(self, vel=0.3):
        t = self.tt(0.3); n = self.bp(self.rng.standard_normal(len(t)), 900, 4500)
        env = sum(np.exp(-np.maximum(0, t - d) * 90) * (t >= d) for d in (0, 0.011, 0.022)) + 0.5 * np.exp(-t * 14)
        return self.fade(n * env * vel * 0.5)
    def hat(self, vel=0.12, d=0.045):
        t = self.tt(0.15); return self.fade(self.hp(self.rng.standard_normal(len(t)), 7500, 3) * np.exp(-t / d) * vel)
    def bell(self, f=1318.5, dur=2.5, vel=0.25):
        t = self.tt(dur); y = np.zeros_like(t)
        for r, a, d in ((1, 1, 1.4), (2.76, 0.45, 2.2), (5.4, 0.25, 3.5), (8.93, 0.12, 5)):
            y += a * np.sin(2 * np.pi * f * r * t) * np.exp(-t * d)
        return self.fade(y * vel, 0.001, 0.05)

    # ---------- звуковой дизайн
    def whoosh(self, d, f0, f1, vel=0.3, shape="up"):
        """shape: up – нарастает к концу (райзер), mid – пролёт, down – удар и хвост"""
        t = self.tt(d); x = self.noise(d); nseg = 24; y = np.zeros_like(t); L = len(t) // nseg + 1
        for i in range(nseg):
            u = i / (nseg - 1); fc = f0 * (f1 / f0) ** u; s0 = i * L
            s = self.bp(x, max(60, fc * 0.6), min(18000, fc * 1.6))[s0:s0 + L]; y[s0:s0 + len(s)] += s
        env = np.sin(np.pi * np.clip(t / d, 0, 1)) ** (1.6 if shape == "mid" else 1.0)
        if shape == "up": env = (t / d) ** 2.2 * np.exp(-np.maximum(0, t - d * 0.92) * 40)
        if shape == "down": env = np.exp(-t / (d * 0.35)) * np.minimum(1, t / 0.02)
        return self.fade(self.lp(y, 12000) * env * vel, 0.005, 0.03)
    def tick(self, f=2400, vel=0.12, d=0.012):
        t = self.tt(0.05); return self.fade(np.sin(2 * np.pi * f * t) * np.exp(-t / d) * vel, 0.0003, 0.005)
    def pop(self, f0=500, f1=900, vel=0.2):
        t = self.tt(0.12); f = f0 + (f1 - f0) * np.minimum(1, t / 0.05)
        return self.fade(np.sin(2 * np.pi * np.cumsum(f) / self.sr) * np.exp(-t * 35) * vel, 0.001, 0.01)
    def thud(self, vel=0.6):
        t = self.tt(0.6); f = 55 + 60 * np.exp(-t * 20); y = np.sin(2 * np.pi * np.cumsum(f) / self.sr) * np.exp(-t * 7)
        y += self.lp(self.noise(0.6), 900) * np.exp(-t * 28) * 0.6
        return self.fade(y * vel, 0.0005, 0.03)
    def rustle(self, d=0.25, vel=0.12):
        t = self.tt(d); x = self.bp(self.noise(d), 1200, 7000); env = np.zeros_like(t)
        for _ in range(max(1, int(d * 40))):
            c = self.rng.random() * d; env += np.exp(-np.abs(t - c) * (60 + self.rng.random() * 120)) * self.rng.random()
        return self.fade(x * env * vel / max(1e-6, env.max()), 0.004, 0.03)
    def beep(self, fs=(1760, 2349), vel=0.12):
        return np.concatenate([self.fade(np.sin(2 * np.pi * f * self.tt(0.07)) * vel, 0.002, 0.01) for f in fs])
    def sweep(self, f0=900, f1=2300, d=0.4, vel=0.07):
        t = self.tt(d); f = f0 + (f1 - f0) * t / d
        return self.fade(np.sin(2 * np.pi * np.cumsum(f) / self.sr) * np.exp(-t * 9) * vel)

    # ---------- обработка
    def reverb(self, x, length=2.2, decay=3.2, amount=0.55):
        ir_t = self.tt(length)
        ir = np.stack([self.lp(self.rng.standard_normal(len(ir_t)) * np.exp(-ir_t * decay), 6000) for _ in range(2)], 1)
        ir /= np.abs(ir).sum(0) ** 0.5 * 9
        return x + np.stack([fftconvolve(x[:, c], ir[:, c])[: self.N] for c in range(2)], 1) * amount
    def sidechain(self, x, hits, depth=0.45, rel=0.09):
        """приседание шины под удары (времена в секундах)"""
        g = np.ones(self.N); L = int(0.3 * self.sr); seg = 1 - depth * np.exp(-np.arange(L) / self.sr / rel)
        for h in hits:
            i0 = int(h * self.sr); n = max(0, min(L, self.N - i0)); g[i0:i0 + n] = np.minimum(g[i0:i0 + n], seg[:n])
        return x * g[:, None]
    def duck_under_voice(self, music, vo, depth=0.55):
        """музыка уходит вниз, пока звучит голос"""
        env = np.abs(vo).max(1); k = int(0.25 * self.sr)
        env = np.convolve(env, np.ones(k) / k, "same")
        if env.max() <= 0: return music
        g = 1 - depth * np.clip(env / (env.max() * 0.15 + 1e-9), 0, 1)
        return music * g[:, None]
    def load_vo(self, timeline, audio_dir, gain=0.5):
        """кладёт audio/vo_<id>.wav на шину голоса по времени из timeline['vo']; возвращает число фраз"""
        n = 0
        for v in timeline.get("vo", []):
            f = pathlib.Path(audio_dir) / f"vo_{v['id']}.wav"
            if not f.exists(): continue
            sr, x = wavfile.read(f); x = x.astype(np.float64); x = x.mean(1) if x.ndim > 1 else x
            x /= (np.abs(x).max() + 1e-9)
            if sr != self.sr: x = resample_poly(x, self.sr, sr)
            self.place(self.vo, x, v["t"], gain); n += 1
        return n
    @staticmethod
    def norm(x, peak=0.9): return x * (peak / (np.abs(x).max() + 1e-9))
    def limit(self, x, ceil_db=-1.2, rel=0.08):
        c = 10 ** (ceil_db / 20); look = int(0.005 * self.sr)
        pk = maximum_filter1d(np.abs(x).max(1), size=2 * look + 1)
        g = minimum_filter1d(np.minimum(1, c / (pk + 1e-9)), size=2 * look + 1)
        a = np.exp(-1 / (rel * self.sr)); out = np.empty_like(g); cur = 1.0
        for i, v in enumerate(g):
            cur = v if v < cur else a * cur + (1 - a) * v; out[i] = cur
        return x * out[:, None]
    def loudness(self, x):
        try:
            import pyloudnorm as pyln
            return pyln.Meter(self.sr).integrated_loudness(x)
        except ImportError:   # грубая оценка без K-взвешивания
            return 20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-9) - 0.7
    def master(self, music, sfx, vo=None, sfx_gain=0.85, lufs=-15.0, ceil_db=-1.2, fade_out=1.2):
        """сведение: музыка уходит под голос, громкость под телефон (-15 LUFS), лимитер, обрезка по dur"""
        music = self.norm(music, 0.5); sfx = self.norm(sfx, 0.42) if np.abs(sfx).max() > 0 else sfx
        if vo is not None and np.abs(vo).max() > 0: music = self.duck_under_voice(music, vo)
        mix = music + sfx * sfx_gain + (vo if vo is not None else 0)
        L = int(self.dur * self.sr); mix = mix[:L].copy()
        fo = int(fade_out * self.sr); mix[-fo:] *= np.linspace(1, 0, fo)[:, None] ** 1.5
        n0 = int(0.01 * self.sr); mix[:n0] *= np.linspace(0, 1, n0)[:, None]
        for _ in range(3):
            mix *= 10 ** ((lufs - self.loudness(mix)) / 20); mix = self.limit(mix, ceil_db)
        return mix
    def write(self, path, x):
        pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
        wavfile.write(path, self.sr, (np.clip(x, -1, 1) * 32767).astype(np.int16))
