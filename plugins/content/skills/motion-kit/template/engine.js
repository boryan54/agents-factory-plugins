'use strict';
// motion-kit engine – детерминированный движок motion-ролика на Canvas 2D.
// Кадр = функция времени: M.renderFrame(t) рисует всё заново, без накопленного состояния,
// поэтому кадр одинаков после перемотки вперёд, назад и холодного старта.
// Сцена (scene.js) задаёт M.scene = { build(), draw(g, t) } и, по желанию, M.zoomPulses / M.zoomCenter(t).
(function () {
  const TL = window.TL || {};
  const W = TL.w || 1080, H = TL.h || 1920, FPS = TL.fps || 60;
  const M = window.M = { W, H, FPS, F: 1000, TL };

  // ---------- математика и кривые
  M.clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));
  M.lerp = (a, b, t) => a + (b - a) * t;
  M.seg = (t, a, b) => M.clamp((t - a) / (b - a));          // 0..1 на отрезке [a, b]
  M.sm = t => t * t * (3 - 2 * t);
  M.eio = t => t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  M.eo = t => 1 - Math.pow(1 - t, 3);
  M.eo5 = t => 1 - Math.pow(1 - t, 5);
  M.ei = t => t * t * t;
  M.back = (t, s = 1.5) => 1 + (s + 1) * Math.pow(t - 1, 3) + s * Math.pow(t - 1, 2);
  M.dsin = (d, k = 8, w = 18) => d <= 0 ? 0 : Math.exp(-k * d) * Math.cos(w * d);  // затухающий отклик после события
  M.rng = function (seed) { return function () { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; };
  M.rr = function (c, x, y, w, h, r) { r = Math.min(r, w / 2, h / 2); c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r); c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath(); };
  M.canvas = function (w, h) { const c = document.createElement('canvas'); c.width = Math.max(1, Math.round(w)); c.height = Math.max(1, Math.round(h)); return c; };

  // ---------- камера: эрмитов сплайн по ключам [t, x, y, z, roll, фокус] – непрерывная скорость на стыках
  M.herm = function (keys, t) {
    const n = keys.length;
    if (t <= keys[0][0]) return keys[0].slice(1);
    if (t >= keys[n - 1][0]) return keys[n - 1].slice(1);
    let i = 0; while (keys[i + 1][0] < t) i++;
    const k0 = keys[Math.max(0, i - 1)], k1 = keys[i], k2 = keys[i + 1], k3 = keys[Math.min(n - 1, i + 2)];
    const dt = k2[0] - k1[0], u = (t - k1[0]) / dt, u2 = u * u, u3 = u2 * u;
    const h00 = 2 * u3 - 3 * u2 + 1, h10 = u3 - 2 * u2 + u, h01 = -2 * u3 + 3 * u2, h11 = u3 - u2;
    const out = [];
    for (let j = 1; j < k1.length; j++) {
      const m1 = i === 0 ? 0 : (k2[j] - k0[j]) / (k2[0] - k0[0]);
      const m2 = i + 2 >= n ? 0 : (k3[j] - k1[j]) / (k3[0] - k1[0]);
      out.push(h00 * k1[j] + h10 * dt * m1 + h01 * k2[j] + h11 * dt * m2);
    }
    return out;
  };
  M.cam = (v) => ({ x: v[0], y: v[1], z: v[2], roll: v[3] || 0, fd: v[4] || 1000 });
  // затухающая встряска камеры на акцентах: [[время, амплитуда в мировых единицах], ...]
  M.shake = function (t, hits) {
    let x = 0, y = 0;
    for (const [ts, a] of hits) { const d = t - ts; if (d > 0 && d < 0.6) { const e = Math.exp(-9 * d); x += a * e * Math.sin(d * 70); y += a * e * Math.cos(d * 55); } }
    return { x, y };
  };

  // ---------- проекция и глубина резкости
  M.project = function (p, cam) {
    const dz = p.z - cam.z; if (dz < 40) return null;
    const s = M.F / dz, dx = (p.x - cam.x) * s, dy = (p.y - cam.y) * s;
    const c = Math.cos(cam.roll), sn = Math.sin(cam.roll);
    return { x: W / 2 + dx * c - dy * sn, y: H / 2 + dx * sn + dy * c, s, dz };
  };
  M.dof = (pr, cam, k = 13) => Math.min(28, k * Math.abs(pr.s - M.F / cam.fd));   // радиус размытия в px экрана

  // DOF без ctx.filter: ctx.filter='blur()' на каждый объект в headless Chromium стоит ~2.4 с на подкадр.
  // Поэтому спрайт заранее размывается в несколько уровней (в пониженном разрешении), а при отрисовке
  // смешиваются два соседних уровня. Скорость ~26 мс на подкадр.
  const MIP_B = [0, 2, 4, 8, 16, 32, 64];
  M.makeMips = function (spr) {
    const out = [{ b: 0, c: spr, pad: 0 }];
    for (const b of MIP_B.slice(1)) {
      const sd = M.clamp(3 / b, 0.1, 1), pad = Math.ceil(b * 2.2);
      const c = M.canvas((spr.width + 2 * pad) * sd, (spr.height + 2 * pad) * sd), g = c.getContext('2d');
      g.filter = 'blur(' + (b * sd).toFixed(2) + 'px)';
      g.drawImage(spr, pad * sd, pad * sd, spr.width * sd, spr.height * sd);
      out.push({ b, c, pad });
    }
    spr.mips = out; return spr;
  };
  // спрайт в мире: ww – ширина в мировых единицах; opt: sx, sy, oy, blur (доп. px), noDof, nearFade
  M.drawWorldSprite = function (g, spr, p, ww, rot, alpha, cam, opt = {}) {
    const pr = M.project(p, cam); if (!pr || alpha <= 0.003) return null;
    const w = ww * pr.s, h = w * spr.height / spr.width;
    if (pr.x + w < -200 || pr.x - w > W + 200 || pr.y + h < -200 || pr.y - h > H + 200) return pr;
    const bl = (opt.noDof ? 0 : M.dof(pr, cam)) + (opt.blur || 0);
    g.save(); g.translate(pr.x, pr.y); g.rotate(rot + cam.roll); g.scale(opt.sx || 1, opt.sy || 1);
    const a0 = alpha * (opt.nearFade === false ? 1 : M.sm(M.seg(pr.dz, 140, 520)));
    const k = w / spr.width, bs = M.clamp(bl / k, 0, 64), MM = spr.mips, oy = (opt.oy || 0) * pr.s;
    const put = (m, a) => { if (a <= 0.003) return; g.globalAlpha = a; const pw = (spr.width + 2 * m.pad) * k, ph = (spr.height + 2 * m.pad) * k; g.drawImage(m.c, -pw / 2, -ph / 2 + oy, pw, ph); };
    if (!MM || bs < 0.6) put({ c: spr, pad: 0 }, a0);
    else { let i = 0; while (i < MM.length - 2 && MM[i + 1].b <= bs) i++; const f = M.clamp((bs - MM[i].b) / (MM[i + 1].b - MM[i].b)); put(MM[i], a0); put(MM[i + 1], a0 * f); }
    g.restore(); return pr;
  };
  // векторное рисование в локальной системе точки мира (панели, текст на объектах)
  M.withWorld = function (g, p, cam, rot, fn, alpha = 1) {
    const pr = M.project(p, cam); if (!pr || alpha <= 0.003) return null;
    g.save(); g.translate(pr.x, pr.y); g.rotate(rot + cam.roll); g.scale(pr.s, pr.s); g.globalAlpha = alpha;
    fn(g, pr); g.restore(); return pr;
  };
  // декор перед героем гасится: возвращает false, если элемент декора закрывает героя на экране
  M.clearOfHero = function (pr, size, heroPr, heroR) {
    if (!heroPr || !pr || pr.dz >= heroPr.dz) return true;
    return Math.hypot(pr.x - heroPr.x, pr.y - heroPr.y) > (heroR * heroPr.s + size * 0.75 * pr.s) * 1.25;
  };

  // ---------- типографика
  M.FD = '"Inter Display", Inter, sans-serif';   // заголовки
  M.FI = 'Inter, sans-serif';                     // текст
  M.setFont = (g, w, size, fam = M.FD) => { g.font = w + ' ' + size + 'px ' + fam; };
  M.wrap = function (g, text, maxW) { const words = text.split(' '); const lines = []; let cur = ''; for (const w of words) { const n = cur ? cur + ' ' + w : w; if (g.measureText(n).width > maxW && cur) { lines.push(cur); cur = w; } else cur = n; } if (cur) lines.push(cur); return lines; };
  // строка из цветных частей: вход снизу из маски строки, выход вверх через ту же маску.
  // parts: [{t:'текст', c:'#цвет'}]; opt: tOut, dur, align:'left'|'center', fam, alpha, color
  M.riseLine = function (g, parts, x, y, size, weight, tIn, t, opt = {}) {
    const u = M.seg(t, tIn, tIn + (opt.dur || 0.55)); if (u <= 0) return;
    const out = opt.tOut ? M.seg(t, opt.tOut, opt.tOut + 0.4) : 0; if (out >= 1) return;
    M.setFont(g, weight, size, opt.fam || M.FD);
    let wsum = 0; const ws = parts.map(p => { const w = g.measureText(p.t).width; wsum += w; return w; });
    let cx = opt.align === 'left' ? x : x - wsum / 2;
    const lh = size * 1.25;
    g.save(); g.textAlign = 'left'; g.beginPath(); g.rect(0, y - size * 1.05, W, lh * 1.05); g.clip();
    const off = (1 - M.eo5(u)) * lh * 0.95 - M.eio(out) * lh * 1.05;
    g.globalAlpha = (opt.alpha ?? 1) * (1 - M.sm(out) * 0.85);
    parts.forEach((p, i) => { g.fillStyle = p.c || opt.color || '#0F1B3D'; g.fillText(p.t, cx, y + off); cx += ws[i]; });
    g.restore();
  };
  // надзаголовок капсом с разрядкой
  M.kicker = function (g, text, x, y, tIn, t, tOut, color, align = 'center') {
    const a = M.sm(M.seg(t, tIn, tIn + 0.35)) * (1 - M.sm(M.seg(t, tOut, tOut + 0.3))); if (a <= 0) return;
    M.setFont(g, 700, 30, M.FI); g.save(); g.globalAlpha = a; g.fillStyle = color; g.textAlign = align;
    if ('letterSpacing' in g) g.letterSpacing = '4px';
    g.fillText(text, x, y - (1 - M.eo(M.seg(t, tIn, tIn + 0.4))) * 16); g.restore();
  };
  // вуаль под верхним заголовком: 3D-объекты, проходящие за текстом, не мешают чтению
  M.veil = function (g, y0, y1, a, rgb = '248,250,253') {
    if (a <= 0) return; const vg = g.createLinearGradient(0, y0, 0, y1);
    vg.addColorStop(0, 'rgba(' + rgb + ',' + 0.95 * a + ')'); vg.addColorStop(0.7, 'rgba(' + rgb + ',' + 0.88 * a + ')'); vg.addColorStop(1, 'rgba(' + rgb + ',0)');
    g.fillStyle = vg; g.fillRect(0, 0, W, y1);
  };
  // субтитры из TL.subs: [[начало, конец, текст], ...]. Соседние не перекрываются: уход заканчивается к концу своего окна.
  M.subtitles = function (g, t, style = {}) {
    const S = Object.assign({ size: 40, lh: 54, bottom: H - 112, maxW: W - 220, bg: 'rgba(255,255,255,0.92)', color: '#0F1B3D' }, style);
    for (const [a, b, text] of (TL.subs || [])) {
      if (t < a || t > b) continue;
      const al = M.sm(M.seg(t, a, a + 0.16)) * (1 - M.sm(M.seg(t, b - 0.16, b))); if (al <= 0) continue;
      M.setFont(g, 600, S.size, M.FI); const lines = M.wrap(g, text, S.maxW);
      const bh = lines.length * S.lh + 40, by = S.bottom - bh + (1 - M.eo(M.seg(t, a, a + 0.3))) * 14;
      let bw = 0; lines.forEach(l => bw = Math.max(bw, g.measureText(l).width)); bw += 60;
      g.save(); g.globalAlpha = al; g.shadowColor = 'rgba(15,27,61,0.10)'; g.shadowBlur = 24; g.shadowOffsetY = 6;
      M.rr(g, W / 2 - bw / 2, by, bw, bh, 26); g.fillStyle = S.bg; g.fill(); g.shadowColor = 'transparent';
      g.fillStyle = S.color; g.textAlign = 'center'; lines.forEach((l, i) => g.fillText(l, W / 2, by + 54 + i * S.lh)); g.restore();
    }
  };
  // QR из матрицы window.QR (её строит build_page.py из TL.qr_url)
  M.drawQR = function (g, x, y, size, color = '#0F1B3D') {
    const Q = window.QR; if (!Q) return; const n = Q.length, cell = size / n; g.fillStyle = color;
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (Q[r][c]) g.fillRect(Math.floor(x + c * cell), Math.floor(y + r * cell), Math.ceil(cell), Math.ceil(cell));
  };

  // ---------- кадр: субкадровый motion blur + радиальные «удары» на акцентах
  M.SUBS = 5; M.SHUTTER = 0.5;            // 5 подкадров на кадр, затвор 180°
  M.zoomPulses = [];                       // [[время, сила 0..1], ...] – радиальное размытие на акцентах
  M.zoomCenter = () => H / 2;
  const cv = document.getElementById('c'); cv.width = W; cv.height = H;
  const ctx = cv.getContext('2d');
  const acc = M.canvas(W, H), actx = acc.getContext('2d');
  const sub = M.canvas(W, H), sctx = sub.getContext('2d');
  const post = M.canvas(W, H), pctx = post.getContext('2d');
  function zoomAmt(t) { let a = 0; for (const [ts, amp] of M.zoomPulses) a = Math.max(a, amp * M.sm(M.seg(t, ts - 0.08, ts)) * (1 - M.sm(M.seg(t, ts, ts + 0.32)))); return a; }
  M.renderFrame = function (t) {
    actx.globalCompositeOperation = 'source-over'; actx.globalAlpha = 1; actx.clearRect(0, 0, W, H);
    for (let k = 0; k < M.SUBS; k++) {
      const ts = t + ((k + 0.5) / M.SUBS - 0.5) * M.SHUTTER / FPS;
      sctx.setTransform(1, 0, 0, 1, 0, 0); sctx.globalAlpha = 1; sctx.filter = 'none'; sctx.globalCompositeOperation = 'source-over'; sctx.textAlign = 'left';
      M.scene.draw(sctx, Math.max(0, ts));
      actx.globalCompositeOperation = 'lighter'; actx.globalAlpha = 1 / M.SUBS; actx.drawImage(sub, 0, 0);
    }
    ctx.globalCompositeOperation = 'source-over'; ctx.globalAlpha = 1; ctx.filter = 'none'; ctx.drawImage(acc, 0, 0);
    const z = zoomAmt(t);
    if (z > 0.01) {
      pctx.clearRect(0, 0, W, H); pctx.drawImage(acc, 0, 0); const cy = M.zoomCenter(t);
      for (let i = 1; i <= 6; i++) { const s = 1 + 0.014 * i * z; ctx.globalAlpha = 0.16 * z * (1 - i / 7); ctx.drawImage(post, W / 2 - W * s / 2, cy - cy * s, W * s, H * s); }
      ctx.globalAlpha = 0.16 * z; ctx.fillStyle = '#FFFFFF'; ctx.fillRect(0, 0, W, H); ctx.globalAlpha = 1;
    }
  };
  window.renderFrame = M.renderFrame;
  // ждём DOMContentLoaded: к этому моменту scene.js уже выполнен и M.scene определён
  const domReady = new Promise(res => document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', res) : res());
  window.ready = domReady.then(async () => {
    if (!M.scene || !M.scene.draw) throw new Error('scene.js не задал M.scene.draw');
    const fonts = (TL.fonts || ['800 100px "Inter Display"', '700 40px Inter', '600 40px Inter', '500 30px Inter']);
    await Promise.all(fonts.map(f => document.fonts.load(f).catch(() => null)));
    if (M.scene.build) await M.scene.build();
    return true;
  });
  // превью в браузере: index.html?play – реальное время без звука; index.html?t=12.5 – один кадр
  const qs = new URLSearchParams(location.search);
  if (qs.has('play')) window.ready.then(() => { const t0 = performance.now(); const loop = () => { M.renderFrame(((performance.now() - t0) / 1000) % (TL.dur || 10)); requestAnimationFrame(loop); }; loop(); });
  else if (qs.has('t')) window.ready.then(() => M.renderFrame(parseFloat(qs.get('t')) || 0));
})();
