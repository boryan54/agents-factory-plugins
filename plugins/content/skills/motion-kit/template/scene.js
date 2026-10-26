'use strict';
// Пример сцены на движке motion-kit (8 секунд). Это стартовая точка, а не шаблон для копирования:
// под каждый ролик сцена пишется заново по раскадровке. Здесь показаны все приёмы движка:
// камера по ключам, мир со спрайтами и DOF, декор, который не перекрывает героя, заголовки
// через маску строки, вуаль, субтитры, удар на акценте, motion blur (в движке).
(function () {
  const { W, H, TL } = M;
  const C = { bg0: '#F8FAFD', bg1: '#ECF1F9', ink: '#0F1B3D', ink2: '#4A5672', blue: '#1F4FBF', blue2: '#3E6FE0', blueL: '#DCE6FB', coral: '#EC6152', green: '#2FA36B', motif: '#C9D7F4', white: '#FFFFFF' };
  const SPR = {};
  const HERO = { x: 0, y: 60, z: 2400 };

  // камера: [t, x, y, z, roll, фокус]. Длинная связная траектория, скорость меняется волнами.
  const KEYS = [
    [0.0, -260, 340, -900, -0.06, 3200],
    [2.2, -40, 120, 900, 0.025, 1500],
    [4.0, 30, 40, 1500, -0.01, 900],
    [6.0, 60, 0, 1420, 0.015, 980],
    [8.0, 120, -20, 1350, 0.0, 1050]
  ];

  // декор: фирменные скруглённые квадраты на разной глубине
  const MOTIF = []; { const r = M.rng(11); for (let i = 0; i < 70; i++) MOTIF.push({ x: (r() * 2 - 1) * 2200, y: (r() * 2 - 1) * 3000, z: -1500 + r() * 6500, s: 140 + r() * 300, rot: r() * Math.PI, spin: (r() - .5) * 0.1, fill: r() < 0.4 }); }

  function motifSprite(fill) {
    const c = M.canvas(260, 260), g = c.getContext('2d');
    M.rr(g, 18, 18, 224, 224, 48); if (fill) { g.fillStyle = 'rgba(220,230,251,0.55)'; g.fill(); }
    g.lineWidth = 9; g.strokeStyle = C.motif; g.stroke(); return c;
  }
  function cardSprite() {   // герой рисуется в 2× для чёткости на крупных планах
    const s = 2, c = M.canvas(520 * s, 340 * s), g = c.getContext('2d'); g.scale(s, s);
    g.shadowColor = 'rgba(15,27,61,0.16)'; g.shadowBlur = 30; g.shadowOffsetY = 12;
    M.rr(g, 20, 20, 480, 300, 36); g.fillStyle = C.white; g.fill(); g.shadowColor = 'transparent';
    g.fillStyle = C.blueL; M.rr(g, 52, 56, 64, 64, 16); g.fill();
    g.fillStyle = C.ink; M.setFont(g, 700, 34, M.FI); g.fillText('Задача № 128', 136, 98);
    g.fillStyle = '#E6EBF3'; M.rr(g, 52, 160, 416, 18, 9); g.fill(); M.rr(g, 52, 200, 300, 18, 9); g.fill();
    return c;
  }

  M.scene = {
    build() {
      SPR.motif = [motifSprite(false), motifSprite(true)].map(M.makeMips);
      SPR.card = M.makeMips(cardSprite());
    },
    draw(g, t) {
      const v = M.herm(KEYS, t), sh = M.shake(t, [[4.0, 10]]);
      const cam = M.cam([v[0] + sh.x, v[1] + sh.y, v[2], v[3], v[4]]);
      // фон и дальний план: точечная сетка с медленным параллаксом
      const gr = g.createLinearGradient(0, 0, 0, H); gr.addColorStop(0, C.bg0); gr.addColorStop(1, C.bg1); g.fillStyle = gr; g.fillRect(0, 0, W, H);
      g.fillStyle = 'rgba(31,79,191,0.10)'; const st = 54, ox = ((-cam.x * 0.03) % st + st) % st, oy = ((-cam.y * 0.03) % st + st) % st;
      for (let y = oy - st; y < H + st; y += st) for (let x = ox - st; x < W + st; x += st) { g.beginPath(); g.arc(x, y, 2.2, 0, 7); g.fill(); }

      // герой: карточка прилетает из глубины с перелётом и оседает; в 4.0 – смена состояния
      const u = M.eo5(M.seg(t, 0.4, 2.4)), hero = { x: HERO.x, y: HERO.y + 900 * (1 - u), z: HERO.z + 2600 * (1 - u) };
      const heroPr = M.project(hero, cam);

      const L = [];   // объекты мира с глубиной, рисуются от дальних к ближним
      for (const m of MOTIF) {
        const pr = M.project(m, cam); if (!pr || !M.clearOfHero(pr, m.s, heroPr, 300)) continue;
        L.push({ z: m.z, d: () => M.drawWorldSprite(g, SPR.motif[m.fill ? 1 : 0], m, m.s, m.rot + m.spin * t, 0.55 * M.sm(M.seg(pr.dz, 350, 900)), cam) });
      }
      L.push({ z: hero.z, d: () => {
        const sq = 1 - 0.06 * M.dsin(t - 2.4, 8, 18);
        M.drawWorldSprite(g, SPR.card, hero, 520, -0.25 * (1 - u), 1, cam, { sy: sq });
        const done = M.seg(t, 4.0, 4.35);
        if (done > 0) M.withWorld(g, { x: hero.x + 200, y: hero.y - 120, z: hero.z - 1 }, cam, 0, (q) => {
          const s = M.back(done, 2); q.scale(s, s); q.fillStyle = C.green; q.beginPath(); q.arc(0, 0, 40, 0, 7); q.fill();
          q.strokeStyle = C.white; q.lineWidth = 9; q.lineCap = 'round'; q.beginPath(); q.moveTo(-17, 0); q.lineTo(-4, 14); q.lineTo(19, -14); q.stroke();
        });
      } });
      L.sort((a, b) => b.z - a.z); for (const o of L) o.d();

      // экранный слой: вуаль, заголовки через маску строки, субтитры
      M.veil(g, 120, 600, M.sm(M.seg(t, 0.3, 0.8)));
      M.kicker(g, 'MOTION-KIT', W / 2, 300, 0.4, t, 7.4, C.blue);
      M.riseLine(g, [{ t: 'Кадр ' }, { t: 'за', c: C.blue }, { t: ' кадром' }], W / 2, 430, 100, 800, 0.6, t, { tOut: 3.6 });
      M.riseLine(g, [{ t: 'Готово', c: C.green }, { t: ' – и проверено' }], W / 2, 430, 84, 800, 4.1, t, { tOut: 7.4 });
      M.subtitles(g, t);
    }
  };
  M.zoomPulses = [[4.0, 0.7]];
  M.zoomCenter = () => 1000;
})();
