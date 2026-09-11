# Конспект сесії: Forex relative-strength monitor (10.09.2026)

Це продовження проєкту, описаного в `HANDOFF_CONTEXT_UA.md`. За цю сесію
пройшли шлях від "код написаний, але жодного разу не тестувався" до
**першого успішного живого запуску з реальними даними cTrader Demo**, і
почали міграцію на PostgreSQL для хмарного розгортання.

## Як продовжити в новому діалозі

Надати новому агенту цей файл та попросити:

> Продовж роботу над Forex relative-strength monitor за контекстом у
> цьому файлі. Наступний крок: додати `psycopg2-binary` у
> `requirements.txt`, підняти PostgreSQL (локально або Cloud SQL),
> додати `DATABASE_URL` в `.env` і протестувати `python -m
> forex_strength.run` з новим storage.py.

## Зміна розташування проєкту

Папка проєкту тепер:

`D:\Program Files\Claude\Forex strength analysis`

(раніше була `D:\Program Files\GPT Codex` — файли перенесені).

## Python: чому 3.12, а не 3.14

На машині користувача стоїть Python 3.14.7, але пакет
`twisted-iocpsupport` (Windows-залежність Twisted) ще не має готового
wheel під 3.14 і намагається компілюватись із сирців, вимагаючи
Microsoft C++ Build Tools.

**Рішення:** окремо встановлено Python 3.12, і venv створено саме на
ньому:

```powershell
py -3.12 -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Обидві версії Python співіснують на машині без конфліктів.

## Безпека: ротація credentials

У попередній сесії в пам'яті проєкту Claude випадково опинився файл
`.env.example` із реальними (не placeholder) значеннями
`CTRADER_CLIENT_SECRET` і `CTRADER_REFRESH_TOKEN`. Користувач вже:

- згенерував нові `client_secret` та `refresh_token` в cTrader Open
  API Portal;
- видалив файл з пам'яті проєкту.

Старі значення більше не дійсні. **Це закрите питання**, згадується
тут лише для повноти історії.

## Знайдені та виправлені баги

### 1. Обрізаний `CTRADER_CLIENT_ID` у `.env`

Реальний Client ID у cTrader має формат `{цифри}_{буквено-цифровий
рядок}` (довжина ~56 символів), а не просто 7 цифр. При копіюванні з
порталу хвіст після `_` губився, через що OAuth token refresh падав з
помилкою `Malformed client_id parameter`.

**Виправлення:** копіювати Client ID повністю з порталу (кнопка
"View"/копіювання), перевіряти без виводу секрету в чат:

```powershell
python -c "from forex_strength.config import Settings; s = Settings.from_environment(); print(len(s.client_id), '_' in s.client_id)"
```

### 2. Неправильна назва поля protobuf у `ctrader.py`

Код очікував `response.ctidTraderAccountId` (список чисел) від
`ProtoOAGetAccountListByAccessTokenRes`, але реальне поле —
`response.ctidTraderAccount` — список об'єктів `ProtoOACtidTraderAccount`,
і вже всередині кожного є `.ctidTraderAccountId` та `.isLive`.

Перевірено напряму через встановлений пакет `ctrader-open-api`.

**Було:**

```python
elif payload_type == ProtoOAGetAccountListByAccessTokenRes().payloadType:
    response = Protobuf.extract(message)
    if not response.ctidTraderAccountId:
        self._finish(RuntimeError("No cTrader Demo accounts are authorized for this OAuth token."))
        return
    self.account_id = int(response.ctidTraderAccountId[0])
```

**Стало (застосовано в `ctrader.py`, файл на диску користувача вже
виправлений):**

```python
elif payload_type == ProtoOAGetAccountListByAccessTokenRes().payloadType:
    response = Protobuf.extract(message)
    demo_accounts = [account for account in response.ctidTraderAccount if not account.isLive]
    if not demo_accounts:
        self._finish(RuntimeError("No cTrader Demo accounts are authorized for this OAuth token."))
        return
    self.account_id = int(demo_accounts[0].ctidTraderAccountId)
```

Додатково відфільтровує `isLive == False` — гарантія, що код ніколи не
візьме live-акаунт, навіть якщо їх колись буде декілька під одним cTID.

### 3. Конфлікт версій `pyOpenSSL` / `cryptography`

Після встановлення `service_identity` (для повноцінної TLS-перевірки)
виникла помилка:

```
AttributeError: module 'lib' has no attribute 'GEN_EMAIL'
```

Це класична несумісність старого `pyOpenSSL 24.1.0` з новішою
`cryptography 50.0.1`.

**Виправлення:**

```powershell
pip install --upgrade pyOpenSSL
```

Встановилась `pyOpenSSL 26.4.0`. Pip показує попередження, що
`ctrader-open-api 0.9.2 requires pyOpenSSL==24.1.0` — **це можна
ігнорувати**, на практиці все працює, попередження про
`service_identity` теж зникло.

## Перший успішний live-запуск

```
Stored 7 quotes at 2026-09-10T12:07:15.409394+00:00
1:00:00: not enough history yet
4:00:00: not enough history yet
1 day, 0:00:00: not enough history yet
```

Через годину накопичення історії (35 рядків у базі, 5 запусків × 7
пар) з'явився перший реальний рейтинг:

```
1:00:00: USD +0.176%, JPY -0.051%, CAD -0.090%, EUR -0.103%, GBP -0.145%,
CHF -0.147%, AUD -0.309%, NZD -0.389%;
strongest synthetic pair: USDNZD (+0.565%)
```

Значення перевірені вручну — арифметика (`base − quote`) правильна.

## Рішення: інтервал збору — 15 хв, не 10

Користувач вирішив збирати дані що **15 хвилин**, а не що 10, як було
в початковому плані. Код на це не зав'язаний (`LOOKBACKS` рахує
1h/4h/24h незалежно від частоти запуску) — зміниться лише:

- Cloud Scheduler cron: `*/15 * * * *`;
- тексти в `README.md`, `CTRADER_SETUP.md`, старому
  `HANDOFF_CONTEXT_UA.md` — там написано "10 хвилин", варто оновити,
  коли будеш фіналізувати документацію.

## Міграція storage.py на PostgreSQL (зроблено в цій сесії)

Ціль — Cloud Run Job (короткоживучий контейнер без постійного диска),
тому SQLite більше не підходить для продакшну. Переписано і
**протестовано на реальній PostgreSQL 16** (ідемпотентність запису,
ротація refresh token, розрахунок сили — все збігається з SQLite
версією).

### Новий `forex_strength/storage.py` (повністю замінює старий)

```python
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import psycopg2
import psycopg2.extensions

from .market import Quote


SCHEMA = """
CREATE TABLE IF NOT EXISTS quotes (
  symbol TEXT NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL,
  price DOUBLE PRECISION NOT NULL CHECK (price > 0),
  PRIMARY KEY (symbol, observed_at)
);
CREATE INDEX IF NOT EXISTS quotes_symbol_time ON quotes (symbol, observed_at);

CREATE TABLE IF NOT EXISTS oauth_tokens (
  provider TEXT PRIMARY KEY,
  refresh_token TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);
"""


def connect(database_url: str) -> psycopg2.extensions.connection:
    """Open a PostgreSQL connection and ensure the schema exists.

    `database_url` accepts any libpq-style connection string, including:
      - a plain TCP DSN for local/Cloud SQL Auth Proxy testing, e.g.
        "postgresql://user:password@127.0.0.1:5432/forex_strength"
      - a Cloud SQL Unix-socket DSN for Cloud Run, e.g.
        "postgresql://user:password@/forex_strength?host=/cloudsql/PROJECT:REGION:INSTANCE"
    """
    connection = psycopg2.connect(database_url)
    connection.autocommit = False
    with connection.cursor() as cursor:
        cursor.execute(SCHEMA)
    connection.commit()
    return connection


def save_quotes(connection: psycopg2.extensions.connection, quotes: list[Quote]) -> None:
    with connection.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO quotes (symbol, observed_at, price) VALUES (%s, %s, %s) "
            "ON CONFLICT (symbol, observed_at) DO NOTHING",
            [(quote.symbol, quote.observed_at, quote.price) for quote in quotes],
        )
    connection.commit()


def load_refresh_token(connection: psycopg2.extensions.connection, provider: str, fallback: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT refresh_token FROM oauth_tokens WHERE provider = %s", (provider,))
        row = cursor.fetchone()
    return str(row[0]) if row else fallback


def save_refresh_token(connection: psycopg2.extensions.connection, provider: str, refresh_token: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO oauth_tokens (provider, refresh_token, updated_at) VALUES (%s, %s, %s) "
            "ON CONFLICT (provider) DO UPDATE SET refresh_token = EXCLUDED.refresh_token, updated_at = EXCLUDED.updated_at",
            (provider, refresh_token, datetime.now(timezone.utc)),
        )
    connection.commit()


def prices_near(
    connection: psycopg2.extensions.connection,
    symbols: tuple[str, ...],
    lookback: timedelta,
    now: datetime,
) -> dict[str, float]:
    """Return the latest observation at or before the requested lookback time."""
    target = (now - lookback).astimezone(timezone.utc)
    result: dict[str, float] = {}
    with connection.cursor() as cursor:
        for symbol in symbols:
            cursor.execute(
                "SELECT price FROM quotes WHERE symbol = %s AND observed_at <= %s "
                "ORDER BY observed_at DESC LIMIT 1",
                (symbol, target),
            )
            row = cursor.fetchone()
            if row:
                result[symbol] = float(row[0])
    return result
```

### Зміни в `forex_strength/config.py`

`database_path: Path` замінено на `database_url: str`; `DATABASE_URL`
тепер обов'язкова змінна середовища (як і три cTrader-змінні). Повний
`Settings`:

```python
@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    refresh_token: str
    database_url: str
    pair_symbols: tuple[str, ...]

    @classmethod
    def from_environment(cls) -> "Settings":
        load_dotenv()
        required = (
            "CTRADER_CLIENT_ID",
            "CTRADER_CLIENT_SECRET",
            "CTRADER_REFRESH_TOKEN",
            "DATABASE_URL",
        )
        missing = [name for name in required if not os.environ.get(name, "").strip()]
        if missing:
            raise RuntimeError(f"Missing configuration: {', '.join(missing)}")
        pairs = tuple(
            value.strip().upper()
            for value in os.environ.get("FOREX_PAIRS", DEFAULT_PAIRS).split(",")
            if value.strip()
        )
        if not pairs:
            raise RuntimeError("FOREX_PAIRS must contain at least one currency pair.")
        return cls(
            os.environ["CTRADER_CLIENT_ID"].strip(),
            os.environ["CTRADER_CLIENT_SECRET"].strip(),
            os.environ["CTRADER_REFRESH_TOKEN"].strip(),
            os.environ["DATABASE_URL"].strip(),
            pairs,
        )
```

(Решта файлу — `load_dotenv()` і `DEFAULT_PAIRS` — без змін.)

### Зміна в `forex_strength/run.py`

Один рядок:

```python
# було:
connection = connect(settings.database_path)
# стало:
connection = connect(settings.database_url)
```

## Що користувачу ще ТРЕБА зробити локально (не зроблено)

1. **Додати в `requirements.txt`:**
   ```
   psycopg2-binary
   ```
   (ще не додано — користувач пропустив цей крок).

2. **Підняти PostgreSQL** — варіанти:
   - локально через Docker для тесту перед хмарою:
     ```powershell
     docker run --name pg-test -e POSTGRES_PASSWORD=testpass -e POSTGRES_DB=forex_strength -p 5432:5432 -d postgres:16
     ```
   - або одразу піти в Cloud SQL (GCP) — тоді локальний крок можна
     пропустити.

3. **Додати в `.env`:**
   ```
   DATABASE_URL=postgresql://postgres:testpass@127.0.0.1:5432/forex_strength
   ```
   (значення підставити під обраний варіант підключення).

4. **Встановити залежності та протестувати:**
   ```powershell
   pip install -r requirements.txt
   python -m forex_strength.run
   ```
   Очікується той самий вивід, що й раніше на SQLite, але тепер дані
   пишуться в Postgres.

## Наступні кроки після цього (ще не почато)

1. **Dockerfile** — контейнеризація застосунку для Cloud Run Job.
2. **Cloud SQL (PostgreSQL)** — створення інстансу в GCP.
3. **Secret Manager** — `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET`,
   `DATABASE_URL` (сам `refresh_token` НЕ в Secret Manager — він і так
   зберігається/ротується в таблиці `oauth_tokens` у Postgres, це вже
   закладено в дизайн).
4. **Cloud Scheduler** — cron `*/15 * * * *`, HTTP-тригер Cloud Run Job
   з OIDC-автентифікацією сервісного акаунта.
5. **Моніторинг** — alert policy на невдале виконання job і на
   "застарілі" котирування.
6. Оновити текст документації (`README.md`, `CTRADER_SETUP.md`) під
   реальний інтервал 15 хв і хмарну архітектуру.

## Стан файлів проєкту (актуально на кінець сесії)

Папка: `D:\Program Files\Claude\Forex strength analysis`

- `forex_strength/__init__.py` — без змін.
- `forex_strength/market.py` — без змін.
- `forex_strength/config.py` — **змінено** (`database_url` замість
  `database_path`, див. вище).
- `forex_strength/storage.py` — **повністю переписано** під
  PostgreSQL, див. код вище.
- `forex_strength/ctrader.py` — **виправлено** баг з
  `ctidTraderAccount`, див. вище. Решта файлу без змін.
- `forex_strength/run.py` — **один рядок змінено** (`database_url`).
- `forex_strength/barchart.py` — історичний референс, не
  використовується, можна ігнорувати або видалити.
- `requirements.txt` — містить `ctrader-open-api`; **треба додати**
  `psycopg2-binary`.
- `.env` — заповнений робочими (ротованими) credentials локально,
  **треба додати** `DATABASE_URL`.
- `data/forex_strength.sqlite3` — стара тестова SQLite-база з першого
  успішного прогону, більше не використовується після переходу на
  Postgres, можна лишити як архів або видалити.
- venv створено на **Python 3.12** (не 3.14 — див. пояснення вище).
