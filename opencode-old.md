# Возврат старого интерфейса opencode (desktop) и запрет обновлений

Инструкция для повторения на любом компьютере (Windows).

## Зачем

После 14.09.2026 разработчики принудительно отключили старый интерфейс
(с вертикальными вкладками) и перевели desktop-приложение на новый
«браузерный» вид. Старый интерфейс блокируется жёстко зашитой датой:

```js
const oldInterfaceSunset = new Date(2026, 8, 14); // 14 сентября 2026
```

Проверка — `Date.now() >= sunset.getTime()`. Если дата наступила, старый
интерфейс «выходит на пенсию», и никакие настройки его не вернут.

Ниже — способ обойти это: сдвинуть дату «заката» далеко в будущее прямо
в коде приложения, а также отключить автообновление, чтобы правка не
затиралась.

---

## Шаг 1. Закрыть opencode полностью

Все процессы. Иначе правки будут перезаписаны при выходе или заблокированы
занятым файлом.

Проверка в PowerShell:

```powershell
Get-Process -Name OpenCode -ErrorAction SilentlyContinue
```

Если есть процессы — закрыть приложение (и проверить в трее/диспетчере задач).

---

## Шаг 2. Исправить дату «заката» в app.asar

### Пути (версия desktop, установлена в профиль пользователя):

```
%LOCALAPPDATA%\Programs\@opencode-aidesktop\resources\app.asar
```

Раскрытый пример:
```
C:\Users\<user>\AppData\Local\Programs\@opencode-aidesktop\resources\app.asar
```

### Что менять:

```
new Date(2026, 8, 14)   →   new Date(2099, 0, 10)
```

> Важно: длина строки должна совпадать (обе по 21 символ), иначе
> «разъедется» ASAR-архив (смещения внутри файла). Не используй другую дату
> без проверки длины.

### Как сделать (Python-скрипт, без спец-инструментов):

```python
path = r'C:\Users\<user>\AppData\Local\Programs\@opencode-aidesktop\resources\app.asar'
d = open(path, 'rb').read()

old = b'new Date(2026, 8, 14)'
new = b'new Date(2099, 0, 10)'
assert len(old) == len(new)          # 21 == 21
assert d.count(old) == 1             # вхождение ровно одно

d2 = d.replace(old, new)
assert len(d2) == len(d)             # размер файла не изменился

open(path, 'wb').write(d2)
print('OK')
```

### Перед правкой — обязательно сделать бэкап:

```powershell
Copy-Item "<...>\app.asar" "<...>\app.asar.bak" -Force
```

> Откат: переименовать `app.asar.bak` обратно в `app.asar`.

---

## Шаг 3. Выключить флаги нового интерфейса в настройках

Файл:
```
%APPDATA%\ai.opencode.desktop\default.dat
```
Раскрытый пример:
```
C:\Users\<user>\AppData\Roaming\ai.opencode.desktop\default.dat
```

Внутри JSON (с экранированными кавычками `\"`) найти и поменять на `false`:

```
"newLayoutDesigns\":true        →  "newLayoutDesigns\":false
"layoutTransitionEligible\":true →  "layoutTransitionEligible\":false
```

PowerShell-правка:

```powershell
$f = "$env:APPDATA\ai.opencode.desktop\default.dat"
$t = [System.IO.File]::ReadAllText($f)
$t = $t.Replace('newLayoutDesigns\":true', 'newLayoutDesigns\":false')
$t = $t.Replace('layoutTransitionEligible\":true', 'layoutTransitionEligible\":false')
[System.IO.File]::WriteAllText($f, $t)
```

> Приложение при каждом запуске может перезаписывать эти флаги обратно в
> `true` (пока дата заката не сдвинута). Поэтому порядок важен:
> сначала патчим app.asar (шаг 2), затем флаги (шаг 3), затем запускаем.

Также проверить файл `opencode.settings` (в той же папке
`%APPDATA%\ai.opencode.desktop\`) — там должно быть `"oldLayoutEligible": true`.
Если нет — добавить/поправить.

---

## Шаг 4. Отключить автообновление (чтобы правка не затиралась)

Файл:
```
%LOCALAPPDATA%\Programs\@opencode-aidesktop\resources\app-update.yml
```

Было:
```yaml
owner: anomalyco
repo: opencode
provider: github
channel: latest
updaterCacheDirName: '@opencode-aidesktop-updater'
```

Поменять `repo` на несуществующий (autoUpdater не найдёт релизов и останется
на текущей версии):
```yaml
owner: anomalyco
repo: opencode-updates-disabled
provider: github
channel: latest
updaterCacheDirName: '@opencode-aidesktop-updater'
```

> Откат: вернуть `repo: opencode` (или файл `app-update.yml.bak`).

Перед правкой — бэкап:
```powershell
Copy-Item "<...>\app-update.yml" "<...>\app-update.yml.bak" -Force
```

---

## Шаг 5. Запустить opencode

Старый интерфейс с вертикальными вкладками должен вернуться.

---

## Итоговая таблица изменений

| Файл | Правка | Бэкап |
|---|---|---|
| `app.asar` | дата заката `2026-08-14` → `2099-01-10` | `app.asar.bak` |
| `default.dat` | `newLayoutDesigns: false`, `layoutTransitionEligible: false` | — |
| `opencode.settings` | `oldLayoutEligible: true` | — |
| `app-update.yml` | `repo` → несуществующий | `app-update.yml.bak` |

---

## Примечания

- **Обновление вручную** снесёт все правки (перезапишет `app.asar` и
  `app-update.yml`). После ручного обновления — повторить шаги 2–4.
- **Резервные копии** хранятся рядом с оригиналами (`.bak`). Для полного
  отката — вернуть все `.bak` на место.
- Это модификация чужого софта в обход решения разработчиков. Всё обратимо
  переустановкой/откатом бэкапов.
