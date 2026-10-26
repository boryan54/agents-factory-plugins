# Core Web Vitals — почему метрика красная и что чинить

Справочник к `site-craft`. Открывается на шаге 5 (пишем код так, чтобы метрики сошлись) и на шаге 6, когда Lighthouse показал красное и нужно понять, **что именно** тянет вниз. Общие требования к производительности — `standards.md`, чек-лист сдачи — `checklist.md`.

## Пороги: Google и наши

| Метрика | Что меряет | Google: хорошо | Терпимо | Плохо | **Требование site-craft** |
|---|---|---|---|---|---|
| **LCP** | загрузка | ≤ 2.5 с | 2.5–4 с | > 4 с | **< 2.0 с** |
| **INP** | отзывчивость | ≤ 200 мс | 200–500 мс | > 500 мс | **< 200 мс** |
| **CLS** | стабильность | ≤ 0.1 | 0.1–0.25 | > 0.25 | **< 0.1** |

Пороги Google — это пол, а не цель: наш LCP жёстче намеренно, чтобы был запас на реальный мобильный интернет. Google считает по **75-му перцентилю живых пользователей** (данные CrUX), а `npx lighthouse` даёт один лабораторный прогон — зелёный Lighthouse не гарантирует зелёный CrUX, но красный Lighthouse гарантирует проблему.

---

## LCP — самый большой видимый элемент

Обычно это hero-картинка, крупный текстовый блок, фоновое изображение или `<svg>`.

**1. Медленный ответ сервера (TTFB > 800 мс).** Лечится CDN, кэшированием, разумным бэкендом, рендером на краю.

**2. Ресурсы, блокирующие рендер.**
```html
<!-- ❌ блокирует отрисовку -->
<link rel="stylesheet" href="/all-styles.css">

<!-- ✅ критический CSS инлайном, остальное отложено -->
<style>/* критический CSS первого экрана */</style>
<link rel="preload" href="/styles.css" as="style"
      onload="this.onload=null;this.rel='stylesheet'">
```

**3. Картинка находится поздно.**
```html
<!-- ❌ браузер узнаёт о ней в последнюю очередь -->
<img src="/hero.jpg" alt="Hero">

<!-- ✅ предзагрузка с высоким приоритетом -->
<link rel="preload" href="/hero.webp" as="image" fetchpriority="high">
<img src="/hero.webp" alt="Hero" fetchpriority="high" width="1200" height="800">
```

**4. Контент дорисовывается джаваскриптом.**
```javascript
// ❌ текст первого экрана приезжает после гидратации
useEffect(() => {
  fetch('/api/hero-text').then(r => r.json()).then(setHeroText);
}, []);

// ✅ контент уже в HTML: SSG или серверный рендер
```
Для наших стеков это означает: контентные страницы — **статика Astro**, динамика — серверные компоненты Next.js. Данные первого экрана не грузятся с клиента.

**5. Предзагрузка следующей страницы (Speculation Rules).** У многостраничника LCP, который человек реально ощущает, чаще относится к **следующей** странице, а не к первой. Прогрев по наведению схлопывает его почти в ноль.
```html
<script type="speculationrules">
{ "prerender": [{ "where": { "href_matches": "/*" }, "eagerness": "moderate" }] }
</script>
```
`eagerness` от дешёвого к агрессивному: `conservative` (по нажатию), `moderate` (после ~200 мс наведения), `eager` (как только ссылка в зоне видимости), `immediate` (сразу при загрузке). Начинать с `moderate`.

Оговорки, без которых это вредит:
- каждый прогрев — почти полная загрузка страницы; трафик и процессор пользователя не бесплатны. Сузить `where`, исключить выход из аккаунта, оплату, корзину;
- **побочные эффекты срабатывают заранее**: Метрика, GA4 и пиксели засчитают визит в момент прогрева, а не перехода — иначе статистика заказчика поедет. Гейтить по `document.prerendering` и событию `prerenderingchange`;
- работает только в Chromium; Safari и Firefox просто игнорируют — регрессии нет.

### Чек-лист LCP
- [ ] TTFB < 800 мс
- [ ] LCP-картинка предзагружена, `fetchpriority="high"`
- [ ] LCP-картинка в AVIF/WebP и нужного размера, не «4000 px, ужатые стилями»
- [ ] Критический CSS инлайном (< 14 КБ)
- [ ] В `<head>` нет блокирующего JS
- [ ] Шрифты не задерживают текст (`font-display: swap`), self-hosted
- [ ] LCP-элемент присутствует в исходном HTML, а не рисуется скриптом
- [ ] Для многостраничника решено, нужны ли Speculation Rules

### Как найти свой LCP-элемент
```javascript
new PerformanceObserver((list) => {
  const e = list.getEntries().at(-1);
  console.log('LCP element:', e.element, 'time:', e.startTime);
}).observe({ type: 'largest-contentful-paint', buffered: true });
```

---

## INP — отклик на действия

Считается по всем взаимодействиям за визит (клики, тапы, клавиши) и показывает худшее. Складывается из трёх фаз: задержка ввода (цель < 50 мс) + обработка (< 100 мс) + отрисовка (< 50 мс).

**1. Длинные задачи держат главный поток.**
```javascript
// ❌ синхронный проход по большому массиву
items.forEach(expensiveOperation);

// ✅ кусками, с уступкой планировщику
async function processAll(items) {
  const CHUNK = 100;
  for (let i = 0; i < items.length; i += CHUNK) {
    items.slice(i, i + CHUNK).forEach(expensiveOperation);
    if ('scheduler' in window && 'yield' in scheduler) {
      await scheduler.yield();   // продолжение встаёт с повышенным приоритетом
    } else {
      await new Promise(r => setTimeout(r, 0));  // запасной путь: Safari, старый Firefox
    }
  }
}
```

**2. Тяжёлый обработчик события.** Порядок такой: сначала дешёвая визуальная реакция (класс `loading`), затем уступка планировщику — чтобы браузер успел это нарисовать, — и только потом расчёты. Аналитику отправлять последней, через `requestIdleCallback`.

**3. Сторонние скрипты.** Виджеты, чаты, карты — грузить лениво: по первому взаимодействию или по появлению в зоне видимости, `{ once: true }`.
```javascript
button.addEventListener('click', () => import('/widget.js').then(w => w.init()), { once: true });
```

**4. Лишние перерисовки в React.** Дорогие поддеревья — в `React.memo`; несрочное обновление состояния — через `useTransition`.

### Чек-лист INP
- [ ] Нет задач длиннее 50 мс на главном потоке
- [ ] Визуальная реакция на клик — мгновенная, до расчётов
- [ ] Тяжёлое отложено, аналитика — в простое
- [ ] Сторонние скрипты не блокируют взаимодействие
- [ ] Обработчики ввода с debounce, где уместно
- [ ] Тяжёлые вычисления — в Web Worker

### Как поймать медленные взаимодействия
```javascript
new PerformanceObserver((list) => {
  for (const e of list.getEntries()) {
    if (e.duration > 200) console.warn('Slow interaction:', e.name, e.duration, e.target);
  }
}).observe({ type: 'event', buffered: true, durationThreshold: 40 });
```
Порог 40 мс — тот же, что у библиотеки `web-vitals`: 16 мс срабатывает почти на всё и топит консоль. Для разбора на живых пользователях брать сборку `web-vitals/attribution` — её `onINP()` отдаёт разбивку по длинным кадрам и указывает виновный скрипт.

---

## CLS — прыгающая вёрстка

Сдвиг засчитывается, когда видимый элемент меняет положение между кадрами **без действия пользователя**.

**1. Картинки без размеров** — самая частая причина.
```html
<!-- ❌ --> <img src="photo.jpg" alt="Фото">
<!-- ✅ --> <img src="photo.jpg" alt="Фото" width="800" height="600">
<!-- ✅ --> <img src="photo.jpg" alt="Фото" style="aspect-ratio: 4/3; width: 100%">
```

**2. Встройки, iframe, реклама** — резервировать место контейнером с `min-height` или `aspect-ratio`.

**3. Контент, вставленный сверху.** Плашки, баннеры согласия, уведомления не вставлять над уже прочитанным — либо ниже зоны видимости, либо появлением через `transform`.

**4. Шрифты (FOUT).** `font-display: optional` не даёт сдвига вовсе; при `swap` — подогнать метрики запасного шрифта:
```css
@font-face {
  font-family: 'Custom';
  src: url('custom.woff2') format('woff2');
  font-display: swap;
  size-adjust: 105%; ascent-override: 95%; descent-override: 20%;
}
```

**5. Анимация геометрии.** `height`/`width`/`top` в переходах — сдвиг; `transform` и `opacity` — нет.

### Чек-лист CLS
- [ ] У всех картинок есть `width`/`height` или `aspect-ratio`
- [ ] Под видео, iframe и рекламу место зарезервировано
- [ ] Шрифты с `optional` или подогнанными метриками
- [ ] Динамический контент не вставляется над существующим
- [ ] Анимации только на `transform`/`opacity`

### Как увидеть, что прыгает
```javascript
new PerformanceObserver((list) => {
  for (const e of list.getEntries()) {
    if (!e.hadRecentInput) {
      console.log('shift:', e.value);
      e.sources?.forEach(s => console.log('  →', s.node, s.previousRect, s.currentRect));
    }
  }
}).observe({ type: 'layout-shift', buffered: true });
```

---

## Чем мерить

**Лаборатория:** DevTools → панель Performance и Lighthouse; `npx lighthouse <url> --view` (по умолчанию мобильный профиль с троттлингом 4G — это и есть наше требование); WebPageTest для водопада и плёнки загрузки.

**Живые пользователи:** отчёт Core Web Vitals в Search Console, данные CrUX, библиотека `web-vitals` с отправкой в аналитику.
```javascript
import { onLCP, onINP, onCLS } from 'web-vitals';
const send = ({ name, value, rating }) =>
  ym(COUNTER_ID, 'params', { [name]: Math.round(name === 'CLS' ? value * 1000 : value), rating });
onLCP(send); onINP(send); onCLS(send);
```

## Рецепты под наш стек

**Astro** (контентные сайты, дефолт по умолчанию)
```astro
---
import { Image } from 'astro:assets';
import hero from '../assets/hero.jpg';
---
<Image src={hero} alt="" loading="eager" fetchpriority="high" />
```
- `astro:assets` сам проставляет `width`/`height` локальным картинкам — CLS закрыт по умолчанию; удалённым URL размеры задаются руками.
- Острова интерактивности — точечно: `client:visible` или `client:idle`, а не `client:load` на всё подряд. Всё, что может остаться статикой, остаётся статикой — это и есть главный выигрыш Astro по INP.

**Next.js 15**
```jsx
import Image from 'next/image';
<Image src="/hero.jpg" alt="" priority fill />          // LCP: priority = preload + высокий приоритет
const Heavy = dynamic(() => import('./Heavy'));          // INP: тяжёлое — динамическим импортом
```
- Данные первого экрана — в серверном компоненте, без клиентского `fetch`.
- `next/image` сам резервирует место — CLS закрыт, пока не сломан ручными стилями.
- Сторонние скрипты — через `next/script` со стратегией `lazyOnload` (Метрика, пиксели) или `afterInteractive`.

**React (без фреймворка)** — `<link rel="preload" as="image" fetchpriority="high">` в `<head>`, `React.memo` и `useTransition` против лишних перерисовок, размеры у каждого `<img>`.

## Источник

Адаптация скилла `core-web-vitals` из репозитория [addyosmani/web-quality-skills](https://github.com/addyosmani/web-quality-skills/tree/main/skills/core-web-vitals), © Addy Osmani, лицензия MIT. Изменено: переведено, встроено в `site-craft` как справочник без собственных триггеров; пороги Google сведены с более жёсткими требованиями фабрики; раздел Vue/Nuxt заменён на Astro (наш стек по умолчанию); добавлены оговорки про побочные эффекты предзагрузки в Метрике и GA4; убрана ссылка на соседний скилл исходного репозитория.
