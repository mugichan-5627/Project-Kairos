"""Seed curated (web-verified) qualitative profiles for marquee managers and
derive quantitative style rows for everyone else.

Curated summaries are grounded in the cited public sources (interviews,
AMC documents, fund-house profiles) — researched Jul-2026. Derived rows are
produced purely from Kairos's own Carhart attribution results.

Run:  python seed_qualitative.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.analytics.manager_assessment import refresh_derived_assessments
from src.utils.db import get_connection, read_sql

CURATED: list[dict] = [
    {
        "name": "Sankaran Naren",
        "style_label": "Contrarian value, multi-cap",
        "aggression": "balanced",
        "style_summary": (
            "CIO of ICICI Prudential AMC; a contrarian value investor who buys neglected "
            "sectors and themes before consensus, blending top-down macro views with "
            "bottom-up stock picking. Describes value investing as 'the marriage of a "
            "contrarian streak and a calculator'."
        ),
        "investment_approach": "Counter-cyclical entries into out-of-favour sectors; sticks to conviction against market direction.",
        "transition_note": "His funds often position against the prevailing style; a successor typically normalizes the portfolio toward benchmark, so expect tracking-error compression after his exit.",
        "sources": [
            "https://www.morningstar.in/posts/41572/naren-2.aspx",
            "https://www.morningstar.in/posts/75181/sankaran-naren-11-brilliant-lessons-learnt-over-3-decades.aspx",
            "https://www.icicidirect.com/mutual-funds/fund-manager/sankaran-naren/294",
        ],
    },
    {
        "name": "Vinit Sambre",
        "style_label": "Quality small/mid-cap, buy-and-hold",
        "aggression": "balanced",
        "style_summary": (
            "Head of Equity at DSP; specializes in small/mid-caps with a high-quality, "
            "low-churn buy-and-hold framework. Scrutinizes 5-10 years of management "
            "capital-allocation history; prefers reasonable valuations or businesses in "
            "low-to-mid cycle with visible recovery."
        ),
        "investment_approach": "Bottom-up quality at reasonable valuations; low portfolio turnover.",
        "transition_note": "Low-churn quality portfolios degrade slowly — his departure risk shows up over 2-4 quarters as holdings rotate, not immediately.",
        "sources": [
            "https://www.dspim.com/assets/documents/dsp-small-cap-amp-dsp-mid-cap-funds---investment-framework.pdf",
            "https://www.valueresearchonline.com/stories/221900/interview-vinit-sambre-dsp-mutual-fund-dsp-midcap-fund/",
        ],
    },
    {
        "name": "R Srinivasan",
        "style_label": "Concentrated quality (QARP)",
        "aggression": "balanced",
        "style_summary": (
            "CIO-Equity at SBI MF; runs 'Quality at a Reasonable Price' with concentrated "
            "25-30 stock portfolios, sustainable competitive advantage and capital "
            "efficiency as the filters. Buffett-inspired, conviction-led and bottom-up."
        ),
        "investment_approach": "Few high-quality stocks, held for the medium-to-long term.",
        "transition_note": "Concentrated portfolios are manager-specific; his exit historically warrants a closer watch than diversified funds because single-name conviction does not transfer.",
        "sources": [
            "https://www.altportfunds.com/fund-managers/r-srinivasan/",
            "https://www.businesstoday.in/magazine/special/story/best-mutual-fund-manager-r-srinivasan-sbi-mutual-fund-40666-2013-05-21",
        ],
    },
    {
        "name": "Rajeev Thakkar",
        "style_label": "Value, buy-and-hold, cash-patient",
        "aggression": "conservative",
        "style_summary": (
            "CIO of PPFAS (Parag Parikh Flexi Cap); explicit value-investing franchise — "
            "management quality, balance-sheet strength, sensible valuation. Cash is a "
            "residual of valuation discipline, not a market-timing call; famously low churn."
        ),
        "investment_approach": "Buy businesses, not tickers; hold cash when valuations don't justify deployment.",
        "transition_note": "PPFAS is process-driven with team continuity (co-managers per sleeve); single-manager departure risk is lower than at star-manager funds.",
        "sources": [
            "https://amc.ppfas.com/schemes/parag-parikh-flexi-cap-fund/",
            "https://www.valueresearchonline.com/stories/201609/interview-with-rajeev-thakkar-cio-and-director-of-ppfas-amc/",
        ],
    },
    {
        "name": "Neelesh Surana",
        "style_label": "GARP (growth at reasonable price)",
        "aggression": "balanced",
        "style_summary": (
            "CIO at Mirae Asset India since 2008; built the equity franchise on GARP — "
            "quality growth businesses bought with valuation discipline. Over three "
            "decades of research and portfolio management experience."
        ),
        "investment_approach": "Growth at a reasonable price; diversified quality-growth portfolios.",
        "transition_note": "GARP portfolios sit near style-neutral; succession risk is moderate and mostly about stock-selection quality rather than style shift.",
        "sources": [
            "https://www.valueresearchonline.com/stories/54565/interview-with-neelesh-surana-cio-at-mirae-asset-investment-managers/",
        ],
    },
    {
        "name": "Prashant Jain",
        "style_label": "Disciplined value, cycle-aware",
        "aggression": "balanced",
        "style_summary": (
            "Ran HDFC equity funds for ~3 decades; HDFC Prudence/Balanced Advantage "
            "compounded ~17.9% CAGR (1994-2022) vs Sensex ~9.6%. Value discipline across "
            "cycles — refused to own quality at unreasonable prices, navigated the tech "
            "boom, old-economy runs and rate cycles."
        ),
        "investment_approach": "Underlying value wins eventually; do not participate in overpriced quality.",
        "transition_note": "His 2022 exit from HDFC AMC is the canonical Indian star-manager transition: funds repositioned gradually and tracking error fell as portfolios normalized.",
        "sources": [
            "https://freefincal.com/prashant-jain-report-card-how-consistently-did-he-beat-the-market/",
            "https://finshots.in/markets/investing-lessons-from-hdfc-prashant-jain/",
        ],
    },
    {
        "name": "Chirag Setalvad",
        "style_label": "Bottom-up small/mid-cap",
        "aggression": "balanced",
        "style_summary": (
            "Head of Equities at HDFC MF; manages HDFC Mid-Cap Opportunities with 20+ "
            "years of fund-management and research experience, focused on the small- and "
            "mid-cap space with a patient bottom-up approach."
        ),
        "investment_approach": "Bottom-up selection in mid/small caps; long holding periods.",
        "transition_note": "Mid-cap franchises depend on the manager's coverage network; his moves historically matter more than large-cap fund changes.",
        "sources": [
            "https://files.hdfcfund.com/s3fs-public/inline-files/Mr.%20Chirag%20Setalvad_1.pdf",
            "https://www.icicidirect.com/mutual-funds/fund-manager/chirag-setalvad/343",
        ],
    },
    {
        "name": "Shreyash Devalkar",
        "style_label": "Quality growth, relative-growth screen",
        "aggression": "balanced",
        "style_summary": (
            "Head-Equity at Axis MF (joined 2016); runs the flagship Bluechip/Midcap/"
            "Growth Opportunities books on a quality-first framework, emphasizing "
            "'relative growth' — owning the faster grower within each sector."
        ),
        "investment_approach": "Quality + relative earnings growth; benchmark-aware portfolios.",
        "transition_note": "Axis's house style is quality-growth; manager changes within the house historically shift portfolios less than cross-house moves.",
        "sources": [
            "https://www.valueresearchonline.com/stories/53954/we-have-been-on-the-side-of-quality-and-relative-growth-for-most-of-our-funds/",
            "https://www.valueresearchonline.com/stories/222070/axis-mf-shreyash-devalkar-interview/",
        ],
    },
    {
        "name": "Taher Badshah",
        "style_label": "Growth core with contrarian sleeve",
        "aggression": "balanced",
        "style_summary": (
            "CIO at Invesco MF, ~3 decades in Indian equities; oversees growth-oriented "
            "flexi/small-cap strategies while personally associated with the Invesco "
            "India Contra Fund — buying challenged businesses with intact competitive "
            "advantages before the turn."
        ),
        "investment_approach": "Growth portfolios plus a disciplined contrarian/value sleeve.",
        "transition_note": "Contra-style funds are conviction products; a successor usually de-risks the contrarian bets first.",
        "sources": [
            "https://www.valueresearchonline.com/stories/222226/taher-badshah-invesco-interview/",
            "https://www.forbesindia.com/article/take-one-big-story-of-the-day/market-may-not-find-it-easy-to-climb-unless-earnings-growth-surprises-materially-invesco-mfs-cio-taher-badshah/92901/1",
        ],
    },
    {
        "name": "Mahesh Patil",
        "style_label": "GARP, large-cap oriented",
        "aggression": "conservative",
        "style_summary": (
            "Long-time CIO at Aditya Birla Sun Life AMC (stepped down Jan-2026 after 21 "
            "years); growth-at-a-reasonable-price approach — businesses growing faster "
            "than their industry, bought with valuation awareness."
        ),
        "investment_approach": "GARP with large-cap emphasis; diversified, benchmark-aware.",
        "transition_note": "His Jan-2026 step-down is a live transition in this dataset: ABSL elevated Harish Krishnan to CIO-Equity; watch ABSL flagship funds for style continuity.",
        "sources": [
            "https://mutualfund.adityabirlacapital.com/-/media/knowledgecenter/pdf/market-mastery_mahesh-patil.pdf",
            "https://www.outlookmoney.com/invest/mutual-funds/mahesh-patil-steps-down-from-aditya-birla-sun-life-amc-harish-krishnan-elevated-to-cio-equity",
        ],
    },
    {
        "name": "Jinesh Gopani",
        "style_label": "Concentrated quality growth",
        "aggression": "balanced",
        "style_summary": (
            "Former Head of Equities at Axis MF; known for concentrated, low-churn, "
            "high-growth high-quality portfolios (Axis Long Term Equity, Focused 25), "
            "holding through rough patches rather than rotating."
        ),
        "investment_approach": "Concentrated quality-growth, very low turnover.",
        "transition_note": "Quality-growth concentration means his portfolios carried large single-name bets; successor transitions at his former funds saw meaningful repositioning.",
        "sources": [
            "https://www.valueresearchonline.com/stories/51685/anticipate-good-times-for-quality-and-growth-stocks/",
            "https://eightytwentyinvestor.com/2020/06/07/interview-with-jinesh-gopani-head-of-equities-axis-mutual-fund/",
        ],
    },
    {
        "name": "Sailesh Raj Bhan",
        "style_label": "Valuation-disciplined growth",
        "aggression": "balanced",
        "style_summary": (
            "President & CIO at Nippon India MF, 27+ years in Indian equities; one "
            "constant principle across cycles — don't overpay for growth. Reputation for "
            "respecting valuations and staying the course through drawdowns."
        ),
        "investment_approach": "Growth with strict valuation discipline; multi-cap flexibility.",
        "transition_note": "House CIO with deep bench; single-fund manager changes at Nippon India matter more than his personal book.",
        "sources": [
            "https://www.valueresearchonline.com/stories/228412/sailesh-raj-bhan-nippon-india-interview-investing-discipline-valuations/",
            "https://www.valueresearchonline.com/stories/54140/cio-equity-of-india-s-fourth-largest-fund-house-explains-the-turnaround-in-his-large-cap-fund/",
        ],
    },
    {
        "name": "Gopal Agrawal",
        "style_label": "Absolute-value multi-cap, downside-aware",
        "aggression": "conservative",
        "style_summary": (
            "Senior Fund Manager - Equity at HDFC AMC (joined May-2022; earlier SBI MF, "
            "DSP, Tata AMC, Mirae); manages HDFC Balanced Advantage. Builds on the fund's "
            "value tradition with a focus on absolute value, downside protection and "
            "consistent absolute returns — explicitly avoids chasing momentum."
        ),
        "investment_approach": "Absolute value across cycles; low-churn, downside-protection-first balanced-advantage framework.",
        "transition_note": "Took over HDFC BAF's equity book after Prashant Jain's 2022 exit — this dataset's flagship transition; his tenure is the post-window in the fund's DiD analysis.",
        "sources": [
            "https://www.valueresearchonline.com/stories/225548/hdfc-balanced-advantage-fund-70-per-cent-plus-returns-gopal-agrawal-interview/",
            "https://www.dezerv.in/mutual-funds/fund-manager/gopal-agrawal/",
        ],
    },
    {
        "name": "Kenneth Andrade",
        "style_label": "Mid-cap cycles, market-leader hunting",
        "aggression": "aggressive",
        "style_summary": (
            "Founder-CIO of Old Bridge Capital (formerly IDFC MF), 25+ years in Indian "
            "equities; earned the 'midcap mogul' tag building IDFC Premier Equity by "
            "buying tomorrow's sector leaders early in their capex/earnings cycle."
        ),
        "investment_approach": "Own future market leaders through their growth cycle; sector-concentrated bets.",
        "transition_note": "Cycle-timed concentrated portfolios are highly manager-specific; his historical fund exits saw material strategy resets.",
        "sources": [
            "https://www.valueresearchonline.com/stories/53882/mid-cap-mogul-kenneth-andrade-set-to-launch-first-mutual-fund-on-jan-17/",
            "https://www.altportfunds.com/fund-managers/kenneth-andrade/",
        ],
    },
]


def apply_curated() -> int:
    identities = read_sql("SELECT manager_id, canonical_name FROM manager_identity")
    aliases = read_sql("SELECT manager_id, alias_name FROM manager_alias")
    lookup: dict[str, int] = {}
    for _, r in identities.iterrows():
        lookup[str(r["canonical_name"]).lower()] = int(r["manager_id"])
    for _, r in aliases.iterrows():
        lookup.setdefault(str(r["alias_name"]).lower(), int(r["manager_id"]))

    applied = 0
    for profile in CURATED:
        manager_id = lookup.get(profile["name"].lower())
        if manager_id is None:
            print(f"  SKIP (not in identity table): {profile['name']}")
            continue
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO manager_qualitative
                    (manager_id, canonical_name, style_label, aggression, style_summary,
                     investment_approach, transition_note, curated, sources_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, datetime('now'))
                ON CONFLICT(manager_id) DO UPDATE SET
                    style_label=excluded.style_label,
                    aggression=excluded.aggression,
                    style_summary=excluded.style_summary,
                    investment_approach=excluded.investment_approach,
                    transition_note=excluded.transition_note,
                    curated=1,
                    sources_json=excluded.sources_json,
                    updated_at=datetime('now')
                """,
                (
                    manager_id,
                    profile["name"],
                    profile["style_label"],
                    profile["aggression"],
                    profile["style_summary"],
                    profile["investment_approach"],
                    profile["transition_note"],
                    json.dumps(profile["sources"]),
                ),
            )
        applied += 1
        print(f"  curated: {profile['name']} (manager_id={manager_id})")
    return applied


def main() -> None:
    from db_setup import initialize_database

    initialize_database()
    print("== derived assessments (all managers with attribution) ==")
    derived = refresh_derived_assessments()
    print(f"derived rows: {derived}")
    print("== curated profiles ==")
    curated = apply_curated()
    print(f"curated rows: {curated}")


if __name__ == "__main__":
    main()
