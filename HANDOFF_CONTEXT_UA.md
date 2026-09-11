# Контекст проєкту: моніторинг відносної сили валют

## Мета

Користувач — retail-трейдер, який аналізує DXY і хоче кожні 10 хвилин
отримувати довгострокову оцінку відносної сили валют та пріоритетних FX-пар.
Інструмент має працювати у хмарі, коли персональний ПК вимкнений, накопичувати
історію та показувати силу за кількома часовими вікнами.

Це аналітичний інструмент; він не має відкривати, змінювати або закривати
угоди і не є торговою рекомендацією.

## Обрана методологія

Основні валюти: `USD, EUR, GBP, JPY, CHF, CAD, AUD, NZD`.

Набір вхідних пар (незалежні USD-crosses):

`EURUSD, GBPUSD, AUDUSD, NZDUSD, USDJPY, USDCHF, USDCAD`

Для кожного lookback (план: 1 год, 4 год, 24 год) розраховується логарифмічна
зміна ціни пари. Base-валюта отримує цю зміну, quote-валюта — інверсну. Сила
кожної валюти — середнє її внесків. Синтетична сила пари =
`сила base − сила quote`.

Таким чином не потрібні всі 28 cross-пар для визначення ранжування восьми
валют. Дані зберігаються як 5-хвилинні snapshots; для розрахунку береться
midpoint `(bid + ask) / 2`.

## Чому не Barchart / TradingView / FundingPips MT5

- Barchart OnDemand trial має обмежену кількість запитів. Їхні Terms прямо
  забороняють data mining, robots та подібні інструменти збору даних, отже
  автоматизація сторінки Market Map не використовується.
- Massive/Polygon Currency Conversion зараз не входить у free plan; real-time
  починається з платного Currencies Starter.
- TradingView забороняє автоматизований збір і non-display використання
  market data, зокрема обробку alerts/webhooks як data feed. TradingView можна
  використовувати лише для ручного аналізу та звичайних персональних alerts.
- FundingPips акаунт користувача працює на MT5. FundingPips забороняє VPN/VPS
  для підключення до торгового акаунта, тому MT5 на хмарному VPS не є безпечним
  рішенням. Їхні правила EA також вимагають обережності. FundingPips не
  підключається до цього проєкту.

## Обране джерело даних: окремий cTrader Demo + cTrader Open API

Обрано окремий cTrader Demo account, не пов'язаний із FundingPips.

Причини:

- cTrader Open API наразі безкоштовний;
- він підтримує demo accounts і real-time market data;
- застосунок працює у хмарі без запущеного торгового терміналу;
- використовується OAuth scope `accounts` (read-only), ніколи `trading`;
- ліміт 50 non-historical requests/s істотно вищий за потребу раз на 10 хв.

Офіційна документація:

- https://help.ctrader.com/open-api/
- https://help.ctrader.com/open-api/account-authentication/
- https://help.ctrader.com/open-api/symbol-data/
- https://help.ctrader.com/open-api/terms-of-use/

## Поточний зовнішній статус

- Користувач створив окремий cTrader Demo account.
- Застосунок cTrader Open API `Forex Strength Monitor` був поданий і вже має
  статус **Active**.
- Користувач створив файл `D:\Program Files\GPT Codex\.env`.
- Файл існує та ігнорується Git (`.gitignore` коректний).
- На момент останньої перевірки в `.env` були правильні назви змінних, але
  значення трьох обов'язкових полів були порожні.

## Що користувач має заповнити локально

У файлі `D:\Program Files\GPT Codex\.env` потрібні:

```dotenv
CTRADER_CLIENT_ID=
CTRADER_CLIENT_SECRET=
CTRADER_REFRESH_TOKEN=
```

- `CTRADER_CLIENT_ID` і `CTRADER_CLIENT_SECRET`: розділ **Credentials**
  активного застосунку в cTrader Open API Portal.
- `CTRADER_REFRESH_TOKEN`: отримати через **Playground** у cTrader Open API
  Portal після авторизації Demo account лише зі scope `accounts`.

Ніколи не просити користувача надсилати ці значення в чат. Після їх внесення
можна перевірити тільки наявність та непорожність полів, не друкуючи значень.

`CTRADER_REDIRECT_URI` поки не потрібен для поточного Playground-тесту; він
буде потрібен під час повноцінного production OAuth callback.

## Стан файлів проєкту

Поточна папка проєкту:

`D:\Program Files\GPT Codex`

Ключові файли:

- `README.md` — загальний опис.
- `CTRADER_SETUP.md` — cTrader setup і безпека.
- `.env.example` — шаблон локальних секретів.
- `forex_strength/strength.py` — формула ранжування валют.
- `forex_strength/storage.py` — SQLite знімки та стан OAuth refresh token.
- `forex_strength/ctrader.py` — cTrader Demo snapshot collector.
- `forex_strength/run.py` — точка запуску.
- `requirements.txt` — містить офіційний пакет `ctrader-open-api`.

Код cTrader робить таке:

1. Через OAuth refresh token отримує короткоживучий access token.
2. Одразу зберігає новий refresh token у локальну SQLite БД, бо cTrader
   ротуює refresh token при використанні.
3. Підключається тільки до cTrader **Demo** Protobuf endpoint.
4. Авторизує застосунок і demo account.
5. Отримує список символів, знаходить 7 потрібних пар, підписується на
   `ProtoOASubscribeSpotsReq`.
6. Чекає bid/ask для всіх пар, записує midpoint у SQLite і розраховує ранги.

У проєкті немає торгових запитів на кшталт `NewMarketOrder`, `ClosePosition`
або `ProtoOANewOrderReq`. Статична перевірка також не знайшла Barchart key чи
торгові команди у cTrader-коді.

## Важливі технічні застереження

- Локальний refresh token зараз зберігається в SQLite, який ігнорується Git.
  Файл треба захищати. У production потрібна зашифрована постійна БД або
  сервіс безпечної ротації секретів.
- У поточній Windows-сесії команда `python`/`py` не знайдена, отже локальний
  live-тест ще не запускався. Перед тестом слід встановити Python 3.11+ і
  виконати `pip install -r requirements.txt`.
- Повний запуск у хмарі поки не розгорнуто. Цільова архітектура: scheduler
  кожні 5 хвилин + постійна PostgreSQL БД + секрети в secret manager.
  SQLite призначена тільки для локальної розробки.
- Не слід називати cTrader ціни ідентичними FundingPips MT5: Forex
  децентралізований, тому спреди й точні котирування можуть відрізнятися.
  Для аналізу відносної сили це прийнятно, але виконання угод перевіряється
  на FundingPips MT5.

## Найближчі кроки після продовження

1. Перевірити, що три cTrader-змінні в `.env` непорожні, не читаючи їх у
   відповіді.
2. Встановити Python 3.11+ на локальній машині або використати доступне
   ізольоване Python-середовище.
3. Встановити залежності:

   ```powershell
   pip install -r requirements.txt
   ```

4. Запустити один тестовий snapshot:

   ```powershell
   python -m forex_strength.run
   ```

5. Перевірити, що база містить 7 котирувань і після накопичення історії
   з'являються 1h/4h/24h rankings.
6. Після цього спроєктувати і розгорнути хмарну версію. Не розгортати дані
   або секрети до Git.

## Як продовжити в новому діалозі

Надати новому агенту цей файл та попросити:

> Продовж роботу над Forex relative-strength monitor за контекстом у
> `D:\Program Files\GPT Codex\HANDOFF_CONTEXT_UA.md`. Спочатку безпечно
> перевір наявність cTrader змінних у `.env`, не друкуючи секрети, а потім
> виконай перший live-тест cTrader Demo collector.
