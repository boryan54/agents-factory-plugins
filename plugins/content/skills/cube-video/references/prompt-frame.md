# Каркас промпта: стартовый кадр и движение

Промпт для куба собирается из блоков, а не пишется заново каждый раз. Порядок блоков
менять нельзя: модели читают начало внимательнее, поэтому запреты стоят раньше красивостей.

Всё пишется **по-английски** – так обе фотомодели держат композицию заметно точнее.
Русский остаётся в общении с заказчиком и в промпт-книге.

---

## Часть 1. Стартовый кадр (chatgpt-image-2, `9:16`, `2K`, ~10 кредитов)

### Блок БАЗА (не трогать)
```
Vertical 9:16 commercial product render, photorealistic, ultra detailed, 3D studio rendering.
EXACTLY ONE object in the frame, centred. No duplicates, no second copy, no reflections of the object.
No text, no logos, no watermark, no people, no hands.
Nothing resembling a glass case, cabinet, showcase or box frame around the scene.
```

### Блок СВЕТ (не трогать)
```
LIGHTING IS FIXED AND MUST NOT CHANGE: one single soft key light placed IN FRONT of the subject,
high above and slightly to the left, aimed backwards into the scene like a shop-window spotlight.
Because the light comes from the viewer side, the shadow falls AWAY FROM THE CAMERA, backwards and
downwards: a soft contact shadow on the floor behind the object and a faint diffuse shadow on the
back wall. No shadow is cast towards the viewer. Light position, direction, colour and intensity are locked.
```

### Блок СЦЕНА (варьируется)
Бесшовная студийная циклорама. Три проверенных варианта:
- `Seamless white-to-light-grey studio cyclorama.` – замер фона 228, объект светится, годится под яркие товары.
- `Seamless neutral light-grey studio cyclorama, slightly darker floor than wall.` – замер фона 137,
  лучший объём (тень 58%), универсальный дефолт.
- `Seamless studio cyclorama with a soft <цвет> gradient, light and airy.` – цветной фон под бренд.

Мягкий рассеянный заполняющий свет добавляется словами `soft diffused fill light, no harsh speculars`.

### Блок ОБЪЕКТ (варьируется)
Одно предложение о предмете: материал, цвет, ключевая деталь. Затем одна из двух постановок:
- парит: `floating just above a low matte podium cube at the centre, hovering a few centimetres over it`
- стоит: `standing upright on a low matte podium cube at the centre`

**Ровно одна постановка на кадр.** Композиция «парящая копия + стоящая копия» разваливается
в анимации: модель сливает их к середине клипа (проверено на телефоне).

### Если объект – фото товара заказчика
Файл загружается в `/v1/uploads`, url уходит в `image_urls`. В промпт добавляется
`the product must match the reference image exactly: same shape, same colour, same markings`.

### Если объект – человек или персонаж
Блок БАЗА остаётся, `no people, no hands` убирается, вместо него
`exactly one person, full body, centred, feet on the podium`. Тени и свет – без изменений.

---

## Часть 2. Движение (видеомодель, `image_urls` = стартовый кадр)

### Блок ДВИЖЕНИЕ (варьируется, одна фраза)
Одно непрерывное движение на весь клип, без склеек и смены плана. Что работает:
- медленный поворот вокруг вертикальной оси на 20–30 градусов;
- медленный дрейф вверх-вниз на пару сантиметров при парении;
- медленное раскрытие или разворот детали товара.

Камера всегда неподвижна: `Camera is locked, static.` Куб – это физическая витрина,
движущаяся камера ломает иллюзию предмета за стеклом.

### Блок ЗАМОК СВЕТА (не трогать)
```
CRITICAL: the lighting rig does NOT move. The key light stays in front, above and slightly left
for the whole clip. The shadow therefore keeps the SAME direction the entire time: it always falls
backwards, away from the camera, onto the floor and the back wall behind the object. The shadow must
never swing, never rotate around the object, never jump to the other side, never fade in or out.
```

### Блок ЗАПРЕТЫ (не трогать)
```
There is EXACTLY ONE object and ONE podium for the entire clip: nothing duplicates, nothing merges,
nothing morphs, no second object appears, no hands, no text, no haze, no smoke, no sparkles, no light rays.
Background stays exactly as in the first frame.
```

---

## Три запрета – откуда они взялись

1. **Слова про витрину.** `glass case`, `showcase`, `cabinet`, `display case`, `vitrine` обе фотомодели
   рисуют буквально: получается стеклянный короб внутри куба, то есть куб в кубе. Поэтому запрет
   стоит явной строкой в блоке БАЗА, а не подразумевается.
2. **Два объекта в кадре.** Красиво на статике, разваливается в движении. Один объект – всегда.
3. **Гуляющий свет.** Модель по умолчанию считает, что свет вращается вместе с объектом.
   Без блока ЗАМОК СВЕТА тень качается маятником, и куб теряет объём. Требование заказчика:
   источник стационарный, тень уходит от зрителя вглубь куба.

## Чего в кадре не бывает никогда
Чёрного фона (профессиональный контент для кубов делается на светлом), рамок и подписей,
второго объекта, дыма и дымки, световых лучей, текста, часов и цифр, отражений объекта в полу.
