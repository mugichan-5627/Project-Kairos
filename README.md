# Project Kairos

> Fund-Manager Alpha Forensics & Investor Alert System for Indian Mutual Funds.

Kairos quantifies the *portable alpha* an individual portfolio manager generated during their tenure — net of market, size, value, and momentum factor exposures — and surfaces a transition risk forecast when that manager exits the fund. Built around a Carhart 4-factor regression engine with auto-degrade to 3-factor / CAPM for tenures pre-dating the NIFTY Momentum 50 inception (Apr-2017), a factor-matched-peer Difference-in-Differences diagnostic for causal alpha attribution, a Portable Alpha Score (PAS), and an SMTP/WhatsApp alert layer.

## Architecture

```
L1  Data Truth        manager_identity · manager_tenure · scheme_lineage
L2  Market Inputs     AMFI NAV history · NSE factor proxies · RBI 91-day T-bill RFR
L3  Analytics Engine  Carhart 4-factor OLS (HAC) · DiD vs factor-matched peers · PAS · Impact Forecast
L4  Intelligence      Monitor → Evidence → Analytics → Alert agents
L5  Interfaces        Static dashboard (index.html) · REST API under /api/* · email/WhatsApp alerts
```

Backend = Python serverless handlers (one file per route under `/api`) over the bundled SQLite store at `fund_manager_tracker/fund_data.db`. Frontend = static `index.html` + Plotly. Deploys to Vercel as-is.

## Methodology one-liner

```
α_annualised = (1 + α_monthly)^12 − 1
α_monthly    = intercept of OLS(excess_fund_return ~ MKT−RF + SMB + Value + Momentum) with HAC (Newey-West) covariance.
RFR          = RBI 91-day T-bill, monthly time-varying (`seed_data/rfr_monthly_91d_tbill.csv`).
Factor data  = NSE indices via yfinance (^CRSLDX, ^NSEMDCP50, ^NSEI, NV20.NS).
Auto-degrade = drops factor legs with insufficient variance (e.g. WML before Apr-2017) and labels the
               returned model as "Carhart 4-factor" | "3-factor (no momentum)" | "CAPM 1-factor".
```

## Local run

```bash
python vercel_local_server.py        # default port 8787
# or pick any port:
python vercel_local_server.py 9090
# or via env:
KAIROS_PORT=9090 python vercel_local_server.py
```

Open `http://localhost:8787` in the browser — static dashboard + `/api/*` are served from the same origin.

> **Why not port 3000?** Port 3000 is the most-collided dev port on a workstation (Next.js, Vite, Create-React-App, Vercel CLI all default to it). Kairos uses 8787 by default so it never fights another local app for the socket. If the server fails to bind, it now prints an explicit message and exits rather than silently falling through to another process.

The bundled `fund_data.db` already contains Tier-1 manager seeds + factor data, so the UI works immediately. No environment variables are required for the read-only demo path.

### Optional API keys

Drop a `.env` file at the project root (sibling of `index.html`). Kairos auto-loads it. Either the canonical name or the `KAIROS_`-prefixed alias works:

```env
ANTHROPIC_API_KEY=sk-ant-...       # or KAIROS_ANTHROPIC_API_KEY
NVIDIA_API_KEY=nvapi-...           # or KAIROS_NVIDIA_API_KEY
TAVILY_API_KEY=tvly-...            # or KAIROS_TAVILY_API_KEY
RESEND_API_KEY=re_...              # primary email path (HTTP API, serverless-safe)
KAIROS_ALERT_RECIPIENT=you@...     # default alert/test recipient
KAIROS_ADMIN_TOKEN=...             # gates pipeline runs + outbound email endpoints
SMTP_HOST=smtp.gmail.com           # SMTP fallback (or KAIROS_SMTP_SERVER)
SMTP_USER=...                      # or KAIROS_SMTP_USER
SMTP_PASSWORD=...                  # or KAIROS_SMTP_PASSWORD
```

`/api/status` will report which agent groups went live.

## Deploy to Vercel

1. Push this folder to GitHub.
2. Import the repo in the Vercel dashboard.
3. Framework preset: **Other**. Root directory: `./`.
4. (Optional) Configure the env vars from `.env.example` in the Vercel project settings — every variable is optional and the dashboard surfaces which agent groups are live vs dormant on `/api/status`.
5. Deploy. `vercel.json` already declares the Python build and a daily cron at 03:00 UTC against `/api/cron_daily`.

## Environment variables

Every variable below is **optional**. See `.env.example`.

| Group | Vars | Behaviour when missing |
| :-- | :-- | :-- |
| LLM (Evidence) | `ANTHROPIC_API_KEY` or `NVIDIA_API_KEY` | Heuristic judging is used |
| Web evidence | `TAVILY_API_KEY` | Monitor Agent stays dormant |
| Email delivery | `RESEND_API_KEY` (primary) or `SMTP_HOST` `SMTP_USER` `SMTP_PASSWORD` | Alerts render to disk under `sent_emails/` |
| Admin auth | `KAIROS_ADMIN_TOKEN` `CRON_SECRET` | Privileged endpoints (pipeline runs, outbound email) only respond on localhost |
| WhatsApp delivery | `TWILIO_*` | WhatsApp delivery skipped |

## Production hardening pass (Jun-2026)

The following integrity fixes were applied in the latest pass — see `KAIROS_DEMO_VERIFICATION.md`.

- 10 bad-seed `manager_tenure` rows removed (wrong AMC / orphan / dividend-plan codes).
- Flat 6.5% risk-free rate replaced with the time-varying RBI monthly 91-day T-bill series 2003-present.
- `factor_data.factor_is_fallback` relabelled correctly — pre-Apr-2017 months are flagged `insufficient_momentum_history`.
- Carhart factor model gained an auto-degrade path to 3-factor / CAPM when a factor leg has insufficient variance.
- Information Ratio carry-over bug fixed (post-window IR is now NULL when observations < 24).
- API surface gained a uniform error envelope `{ error: { code, message, hint } }`.
- Alert agents now run in dry-run by default; missing keys never crash the request.

## Repository layout

```
/api                         Vercel serverless handlers
  _kairos.py                 Shared bootstrap, error envelope, sanitiser
  manager_profile.py         /api/manager_profile
  scheme_history.py          /api/scheme_history
  portfolio.py               /api/portfolio  (POST)
  investor_portfolio.py      /api/investor_portfolio  (register/list/remove)
  feed.py · alerts.py · transition.py · status.py · …
  cron_daily.py              Daily entry point (Vercel cron)
/fund_manager_tracker
  fund_data.db               Bundled SQLite store
  db_setup.py                Schema bootstrap (idempotent)
  cleanup_tier1.py           One-shot hardening cleanup
  seed_data/
    rfr_monthly_91d_tbill.csv  Time-varying RBI 91D T-bill RFR
  src/
    analytics/               Carhart, DiD, PAS, Impact Forecast, pipeline
    data/                    AMFI, NSE factors, RBI RFR, SID parser
    alerts/                  Alert + Investor Alert engines
    intelligence/            Tavily, LLM judge, claim promotion
    llm/                     Anthropic + NVIDIA router
/index.html                  Static dashboard (Plotly + plain JS)
vercel.json                  Vercel build + routing + cron
```

## Talking about Kairos

Kairos is intentionally **not** a fund screener. It is a manager-level alpha forensics engine. In a quant interview the most defensible framing is:

> *"The core is a Carhart 4-factor regression with HAC standard errors to isolate manager alpha from market, size, value, and momentum exposures. Indian factor data has no clean long-short legs, so SMB / Value / Momentum are constructed as long-only tilt factors and we label them as such — we don't claim to be running Fama-French research factors. Tenures before Apr-2017 auto-degrade to a 3-factor or CAPM specification because NIFTY Momentum 50 didn't exist before then. We then use Difference-in-Differences against factor-matched peers — not category median, which is endogenous to the transition event — to estimate causal alpha contribution. The output is a Portable Alpha Score, a Transition Impact Forecast with a bootstrapped confidence band, and an investor-facing alert."*
