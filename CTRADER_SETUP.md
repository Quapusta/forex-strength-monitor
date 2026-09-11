# cTrader setup: current status and next actions

## Current status

- A separate cTrader Demo account has been created.
- The `Forex Strength Monitor` Open API application has been submitted to
  Spotware for approval.
- No FundingPips credential or trading account is connected to this project.

## After Spotware approves the application

1. In the cTrader Open API portal, record the Client ID and Client Secret in a
   secret manager or local `.env`. Do not place them in Git or chat.
2. Add the production HTTPS OAuth callback URL to the application settings.
   The monitor will use the `accounts` scope only, which does not permit
   opening, modifying, or closing orders.
3. Open the OAuth authorization page, select the cTrader Demo account, and
   approve read-only access.
4. The application exchanges the authorization code for a refresh token.
   cTrader rotates that token whenever it is used; the worker persists the
   replacement before requesting quotes. For local development, the token is
   stored in the ignored SQLite database, so protect that file. Production must
   use an encrypted persistent database or a dedicated token-rotation secret
   service.

## Collection design

On every scheduled run, the worker will authenticate to the cTrader **Demo**
endpoint, list symbols available to the account, and subscribe only to:

`EURUSD, GBPUSD, AUDUSD, NZDUSD, USDJPY, USDCHF, USDCAD`

For each pair, it records the bid, ask, midpoint, and cTrader timestamp. The
midpoint drives the relative-strength calculation. Seven USD crosses are enough
to infer the relative ranking of USD, EUR, GBP, JPY, CHF, CAD, AUD, and NZD.

## Security boundary

The monitor must request `accounts`, never `trading`. It must not contain
order, position, or trade-management code. Rotate a token immediately if it is
ever exposed.
