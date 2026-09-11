# Конспект сесії (продовження): Forex relative-strength monitor (10.09.2026, частина 2)

Це продовження `HANDOFF_SESSION_2026-09-10.md` (перша частина цього ж дня) та
`HANDOFF_CONTEXT_UA.md`. За цю частину сесії: локальний перехід на PostgreSQL
завершено й підтверджено живим запуском, ухвалено фінальне рішення по
інтервалу збору, і підготовлено `Dockerfile` для Cloud Run.

## Як продовжити в новому діалозі

Надати новому агенту цей файл (і за потреби `HANDOFF_SESSION_2026-09-10.md`,
`HANDOFF_CONTEXT_UA.md`, `CTRADER_SETUP.md`, `README.md`) та попросити:

> Продовж роботу над Forex relative-strength monitor за контекстом у цьому
> файлі. Наступний крок: з'ясувати стан GCP-проєкту користувача (проєкт є чи
> ні, чи встановлено gcloud CLI), і почати розгортання Cloud Run Job —
> Artifact Registry, Cloud SQL, Secret Manager, Cloud Scheduler.

## Зроблено в цій частині сесії

### 1. PostgreSQL піднято локально напряму (без Docker)

Спроба використати Docker Desktop провалилась: `Virtualization support not
detected` (апаратна віртуалізація вимкнена на цій машині). **Рішення:**
Docker Desktop видалено, PostgreSQL встановлено напряму з офіційного
інсталятора (`postgresql.org`), версія **18** (не 16, як у початковому плані
з Docker — це нормально, сумісно).

Місце встановлення на диску користувача:

`D:\Program Files\PostgreSQL\bin\psql.exe`

(без підпапки з номером версії — саме так виявилось у цій інсталяції).

**Важливо для майбутнього хмарного кроку:** оскільки апаратна віртуалізація
недоступна на цій машині, **локальна збірка Docker-образу неможлива**
(`docker build` теж вимагає Docker Desktop). Образ доведеться збирати через
**Cloud Build** (`gcloud builds submit`), а не локально.

### 2. Забутий/невірний пароль `postgres` — скинуто

Стандартна процедура: тимчасово `scram-sha-256` → `trust` у
`D:\Program Files\PostgreSQL\data\pg_hba.conf` для рядків `127.0.0.1/32` та
`::1/128`, `Restart-Service postgresql-x64-18` (з PowerShell **від
адміністратора** — звичайний користувач не має прав на `Restart-Service`),
`ALTER USER postgres PASSWORD '...'` без пароля, потім **обов'язково**
повернено `trust` назад на `scram-sha-256` і ще раз `Restart-Service`.

Це закрите питання, новий пароль встановлено й перевірено успішним
`CREATE DATABASE`.

### 3. База `forex_strength` створена, `DATABASE_URL` в `.env`

```
DATABASE_URL=postgresql://postgres:<пароль>@127.0.0.1:5432/forex_strength
```

Пароль користувача ніколи не виводився в чат.

### 4. Перший живий запуск на PostgreSQL — успішний

```
Stored 7 quotes at 2026-09-10T21:20:26.914912+00:00
1:00:00: NZD +0.050%, CAD +0.042%, CHF +0.026%, GBP +0.008%, EUR +0.002%,
JPY -0.003%, AUD -0.004%, USD -0.017%; strongest synthetic pair: NZDUSD (+0.067%)
4:00:00: USD +0.077%, EUR -0.039%, NZD -0.041%, GBP -0.051%, CAD -0.068%,
CHF -0.068%, AUD -0.072%, JPY -0.202%; strongest synthetic pair: USDJPY (+0.279%)
1 day, 0:00:00: not enough history yet
```

Новий `storage.py` (PostgreSQL-версія з попередньої частини сесії) працює
коректно в парі з `config.py` і `run.py` без подальших правок коду.

**Відоме, неблокуюче попередження**, ще не усунуте:
```
UserWarning: You do not have a working installation of the service_identity
module: 'cannot import name 'asn1' from 'cryptography.hazmat'...
```
Той самий конфлікт `pyOpenSSL`/`cryptography`, що й у першій частині сесії.
Не заважає роботі. Виправлення (не застосовано, опційно):
```powershell
pip install --upgrade pyOpenSSL
```

### 5. Фінальне рішення по інтервалу збору: **5 хвилин**

Користувач передумав двічі (10 хв → 15 хв → **5 хв остаточно**). Причина: хоче
візуалізувати тренд (1h/4h/24h) лінійним графіком, і густіші 5-хвилинні
знімки дають точнішу картину зростання/спадання на короткому вікні (1h).

Код змін не потребує (`LOOKBACKS` рахує 1h/4h/24h незалежно від частоти
запуску). **Дано інструкції користувачу** оновити вручну (стан застосування
на диску користувача **не підтверджено** — перевірити на початку наступної
сесії):

- `README.md`: "every 10 minutes" → "every 5 minutes" (двічі в тексті: опис
  worker і секція Cloud production design).
- `HANDOFF_CONTEXT_UA.md`: "10-хвилинні snapshots" → "5-хвилинні snapshots";
  "scheduler кожні 10 хвилин" → "кожні 5 хвилин".
- `CTRADER_SETUP.md`: явних згадок числа хвилин не містить, правок не
  потребує.
- **Майбутній Cloud Scheduler cron:** `*/5 * * * *` (не `*/15 * * * *`, як
  фігурувало в першій частині сесії).

### 6. `Dockerfile` створено і розміщено в корені проєкту

Розміщено як окремий файл (не в підпапці) в:

`D:\Program Files\Claude\Forex strength analysis\Dockerfile`

Вміст:

```dockerfile
# syntax=docker/dockerfile:1

FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libssl-dev \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY forex_strength ./forex_strength

RUN useradd --create-home --uid 1000 worker
USER worker

CMD ["python", "-m", "forex_strength.run"]
```

Базовий образ — `python:3.12-slim`, той самий мажорний Python, що й у
локальному venv (3.12, не 3.14 — див. першу частину сесії щодо
`twisted-iocpsupport`). На Linux ця Windows-специфічна залежність не
збирається, тож у контейнері проблем очікувати не варто, але **це ще не
перевірено практичним запуском**, оскільки локальний `docker build`
недоступний (див. пункт 1).

## Що користувачу ще ТРЕБА зробити локально (не зроблено)

1. **Підтвердити**, що правки документації (5 хв) з пункту 5 вище дійсно
   внесені в `README.md` і `HANDOFF_CONTEXT_UA.md` на диску.
2. Нічого більше локально не потребує коду — решта роботи переходить у хмару.

## Наступні кроки (ще не почато) — розгортання в GCP

Порядок узгоджено, конкретні деталі (назва проєкту, регіон тощо) ще не
обговорено з користувачем:

1. **З'ясувати стан GCP:** чи є вже проєкт, чи встановлено `gcloud` CLI.
   (Це питання було задано користувачу наприкінці сесії, відповідь ще не
   отримана — почати звідси.)
2. **Artifact Registry:** створити Docker-репозиторій для образу.
3. **Збірка образу через Cloud Build** (НЕ локальний `docker build` —
   віртуалізація недоступна на машині користувача):
   ```powershell
   gcloud builds submit --tag REGION-docker.pkg.dev/PROJECT_ID/REPO/forex-strength
   ```
4. **Cloud SQL (PostgreSQL)** — створити керований інстанс у GCP. Локальна
   Postgres-база (крок 1-4 вище) була лише для розробницького тесту; для
   продакшну потрібен окремий Cloud SQL інстанс, під'єднання або через Cloud
   SQL Auth Proxy (локально), або через Unix-socket DSN у Cloud Run:
   ```
   postgresql://user:password@/forex_strength?host=/cloudsql/PROJECT:REGION:INSTANCE
   ```
   (формат вже підтримується в `storage.py`, коментар у `connect()`).
5. **Secret Manager:** зберегти `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET`,
   `DATABASE_URL` (Cloud SQL-версію, не локальний `127.0.0.1`). Сам
   `refresh_token` НЕ в Secret Manager — зберігається й ротується в таблиці
   `oauth_tokens` у Postgres, це вже закладено в дизайн (`storage.py`).
6. **Cloud Run Job:** створити job з побудованого образу, підключити секрети
   як змінні середовища, підключити Cloud SQL instance.
7. **Cloud Scheduler:** cron **`*/5 * * * *`** (оновлено з `*/15`, див. пункт
   5 вище), HTTP-тригер Cloud Run Job з OIDC-автентифікацією сервісного
   акаунта.
8. **Моніторинг:** alert policy на невдале виконання job і на "застарілі"
   котирування.
9. Після повного розгортання — фінально звірити, що вся документація
   (`README.md`, `CTRADER_SETUP.md`, `HANDOFF_CONTEXT_UA.md`) відображає
   реальну хмарну архітектуру й інтервал 5 хв.

## Стан файлів проєкту (актуально на кінець цієї частини сесії)

Папка: `D:\Program Files\Claude\Forex strength analysis`

- `forex_strength/*.py` — без змін відносно кінця першої частини сесії
  (config.py/storage.py/run.py на PostgreSQL, ctrader.py з виправленим
  `ctidTraderAccount`).
- `requirements.txt` — містить `ctrader-open-api` і **тепер також**
  `psycopg2-binary` (додано на початку цієї частини сесії).
- `.env` — містить робочі cTrader credentials і **тепер також**
  `DATABASE_URL` з локальним Postgres-підключенням (`127.0.0.1:5432`).
- `Dockerfile` — **новий файл**, у корені проєкту, готовий, але ще не
  перевірений збіркою (через відсутність локальної віртуалізації; перевірка
  відбудеться через Cloud Build).
- Docker Desktop — **видалено** з машини користувача (віртуалізація
  недоступна на апаратному рівні).
- Локальний PostgreSQL 18 — встановлено напряму (не Docker), служба
  `postgresql-x64-18`, база `forex_strength` створена й підтверджена
  успішним запуском.
- `data/forex_strength.sqlite3` — стара SQLite-база, як і раніше, архівна,
  не використовується.
