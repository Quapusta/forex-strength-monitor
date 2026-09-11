# Forex relative-strength monitor

Cloud-ready worker which records Forex quotes every 5 minutes and calculates a
relative-strength score for USD, EUR, GBP, JPY, CHF, CAD, AUD and NZD. It is
designed to run as a scheduled job, so it continues operating when a personal
computer is off.

## What it measures

For each selected lookback (1 hour, 4 hours, 24 hours) the worker calculates
the log return of every pair. A currency receives that return when it is the
base currency and the inverse return when it is the quote currency. Its score
is the average across its available pairs. Pair opportunity is then simply:

`base currency strength − quote currency strength`

This gives a transparent long-term strength ranking rather than a single
snapshot from a market-map page. It is an analytical aid, not a trading signal
or investment recommendation.

## Data source

The selected source is **cTrader Open API** using a separate cTrader Demo
account. The monitor uses read-only OAuth access and never sends trading
requests. See [CTRADER_SETUP.md](CTRADER_SETUP.md) for account setup and the
approval workflow.

The original Barchart prototype is retained only as a historical reference;
it must not be used for live collection.

## First live run

1. Wait for the cTrader application to be approved.
2. Complete the one-time, read-only OAuth authorization in a browser.
3. Add cTrader credentials only to `.env` locally or to the cloud secret
   manager in production.
4. Use Python 3.11 or newer and install dependencies with
   `pip install -r requirements.txt`.

## Cloud production design

Use a scheduled Cloud Run Job (or equivalent) every 5 minutes during the
Forex trading week. Store production data in PostgreSQL, not in the container
filesystem. Put cTrader OAuth credentials in the cloud secret manager. Configure
alerts for a failed job and for stale quotes.

Google Cloud Run + Cloud Scheduler is the recommended deployment because it
does not depend on the desktop computer.
