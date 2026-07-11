# Project Kairos

Project Kairos is a Streamlit system for Indian mutual fund manager transition intelligence and fund alpha attribution.

It uses cache-first public data loaders, a SQLite warehouse, factor attribution, peer-relative DiD analysis, manager scorecards, and a multi-page Streamlit app.

## Quick Start

```powershell
cd fund_manager_tracker
python db_setup.py
python -m streamlit run app.py
```

For data refresh:

```powershell
python data_pipeline.py --incremental
```

For first-run bootstrapping:

```powershell
python bootstrap.py --max-schemes 100
```

The app is designed to degrade gracefully when optional sources such as SIDs, holdings PDFs, or ValueResearch pages are unavailable.
