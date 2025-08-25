# MarketPulse — Macro & Markets Dashboard (Streamlit, single file)
# --------------------------------------------------------------
# Features
# - Pulls macro indicators from the *public* World Bank API (no key required).
# - Indicators included: GDP growth (annual, %), Inflation (annual CPI, %), Unemployment (%, modeled ILO).
# - Compare multiple countries, filter by years, see KPIs & deltas, correlations, and export CSV.
# - Clean Plotly charts + lightweight caching.
#
# How to run
# 1) pip install streamlit pandas requests plotly
# 2) streamlit run marketpulse.py
# 3) Open the local URL from Streamlit output.
#
# Notes
# - World Bank series are annual; if you need higher frequency (monthly), extend the sources (e.g., OECD, ECB, FRED).
# - Add more indicators by extending INDICATORS dict below.

import io
import math
import time
import json
import typing as t

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

# --------------------------- Config ---------------------------------
st.set_page_config(
    page_title="MarketPulse — Macro Dashboard",
    page_icon="📊",
    layout="wide",
)

COUNTRIES = {
    # ISO-3 codes used by World Bank
    "United States": "USA",
    "Euro Area": "EMU",  # or EUU for European Union aggregate
    "United Kingdom": "GBR",
    "Germany": "DEU",
    "France": "FRA",
    "Italy": "ITA",
    "Spain": "ESP",
    "Japan": "JPN",
    "China": "CHN",
    "India": "IND",
    "Brazil": "BRA",
    "Canada": "CAN",
    "Australia": "AUS",
}

INDICATORS = {
    # World Bank indicator code : (label, units)
    "NY.GDP.MKTP.KD.ZG": ("GDP growth", "% (annual)"),
    "FP.CPI.TOTL.ZG": ("Inflation, CPI", "% (annual)"),
    "SL.UEM.TOTL.ZS": ("Unemployment", "% of labor force"),
}

DEFAULT_COUNTRIES = ["United States", "Euro Area", "United Kingdom"]
DEFAULT_INDICATORS = ["NY.GDP.MKTP.KD.ZG", "FP.CPI.TOTL.ZG", "SL.UEM.TOTL.ZS"]

WB_BASE = "https://api.worldbank.org/v2"

# --------------------------- Data layer -----------------------------
@st.cache_data(show_spinner=False)
def wb_fetch_series(country_iso3: str, indicator: str) -> pd.DataFrame:
    """Fetch one World Bank indicator series for a country.
    Returns columns: country, countryiso3code, indicator, date (year as int), value (float)
    """
    url = f"{WB_BASE}/country/{country_iso3}/indicator/{indicator}?format=json&per_page=20000"
    all_rows = []
    page = 1
    while True:
        resp = requests.get(url + f"&page={page}", timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"WB error {resp.status_code}: {resp.text[:200]}")
        payload = resp.json()
        if not isinstance(payload, list) or len(payload) < 2:
            break
        meta, rows = payload
        if not rows:
            break
        all_rows.extend(rows)
        # paging
        if meta.get("page") >= meta.get("pages", 1):
            break
        page += 1
        time.sleep(0.05)  # be nice to the API

    if not all_rows:
        return pd.DataFrame(columns=["country", "countryiso3code", "indicator", "date", "value"])  

    df = pd.DataFrame.from_records(
        [
            {
                "country": r.get("country", {}).get("value"),
                "countryiso3code": r.get("countryiso3code"),
                "indicator": r.get("indicator", {}).get("id"),
                "date": int(r.get("date")) if r.get("date") and str(r.get("date")).isdigit() else None,
                "value": r.get("value"),
            }
            for r in all_rows
        ]
    )
    df = df.dropna(subset=["date"]).sort_values("date")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    return df

@st.cache_data(show_spinner=False)
def wb_fetch_multi(countries_iso3: list[str], indicators: list[str]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for c in countries_iso3:
        for ind in indicators:
            try:
                frames.append(wb_fetch_series(c, ind))
            except Exception as e:
                st.warning(f"Failed to load {ind} for {c}: {e}")
    if not frames:
        return pd.DataFrame(columns=["country","countryiso3code","indicator","date","value"]) 
    df = pd.concat(frames, ignore_index=True)
    return df

# --------------------------- UI helpers -----------------------------
def kpi_delta(current: float | None, previous: float | None) -> tuple[str, str, str]:
    if current is None or previous is None or (isinstance(previous, float) and math.isnan(previous)):
        return ("—", "", "secondary")
    delta = current - previous
    sign = "▲" if delta >= 0 else "▼"
    color = "green" if delta >= 0 else "red"
    return (f"{current:.2f}", f"{sign} {abs(delta):.2f}", color)

# --------------------------- Sidebar --------------------------------
st.sidebar.header("📊 MarketPulse")
st.sidebar.caption("Public World Bank data. Annual frequency.")

countries = st.sidebar.multiselect(
    "Countries",
    options=list(COUNTRIES.keys()),
    default=DEFAULT_COUNTRIES,
)

indicators = st.sidebar.multiselect(
    "Indicators",
    options=[f"{k} — {INDICATORS[k][0]}" for k in INDICATORS.keys()],
    default=[f"{k} — {INDICATORS[k][0]}" for k in DEFAULT_INDICATORS],
)

# parse indicator codes back
indicator_codes = [opt.split(" — ")[0] for opt in indicators]

min_year, max_year = st.sidebar.slider("Year range", 1960, 2025, (2000, 2024))

norm = st.sidebar.checkbox("Normalize series (z-score within each series)", value=False)

st.sidebar.markdown("---")
show_raw = st.sidebar.checkbox("Show raw data table", value=False)

# --------------------------- Data loading ----------------------------
with st.spinner("Loading data from World Bank…"):
    iso3 = [COUNTRIES[c] for c in countries]
    data = wb_fetch_multi(iso3, indicator_codes)

if data.empty:
    st.error("No data loaded. Try different countries/indicators.")
    st.stop()

# merge readable indicator names
name_map = {k: INDICATORS[k][0] for k in INDICATORS}
data["indicator_name"] = data["indicator"].map(name_map)
# filter by year
mask = (data["date"] >= min_year) & (data["date"] <= max_year)
data = data.loc[mask].copy()

# --------------------------- KPIs -----------------------------------
st.markdown("# MarketPulse — Macro Dashboard")
st.caption("Annual macro indicators from World Bank. Choose countries & years in the sidebar.")

kpi_cols = st.columns(len(indicator_codes))
for i, ind in enumerate(indicator_codes):
    label, units = INDICATORS[ind]
    col = kpi_cols[i]
    # latest by country: show first country selection
    primary_country = countries[0] if countries else list(COUNTRIES.keys())[0]
    iso = COUNTRIES.get(primary_country)
    dfc = data[(data["countryiso3code"]==iso) & (data["indicator"]==ind)].sort_values("date")
    current = dfc["value"].dropna().iloc[-1] if not dfc.empty else None
    previous = dfc["value"].dropna().iloc[-2] if len(dfc.dropna(subset=["value"]))>=2 else None
    val, delta, color = kpi_delta(current, previous)
    with col:
        st.metric(f"{label} — {primary_country}", val + f" {units}" if val != "—" else val, delta)

# --------------------------- Chart ----------------------------------
# Prepare tidy frame per indicator
if norm:
    # z-score per (country, indicator)
    data["value_norm"] = data.groupby(["countryiso3code","indicator"])['value'].transform(
        lambda s: (s - s.mean()) / (s.std() if s.std() not in (0, None) else 1)
    )

# Tabs per indicator for clarity
all_tabs = st.tabs([INDICATORS[i][0] for i in indicator_codes])
for tab, ind in zip(all_tabs, indicator_codes):
    with tab:
        label, units = INDICATORS[ind]
        df = data[data["indicator"]==ind].copy()
        ycol = "value_norm" if norm else "value"
        ylab = f"{label} ({units})" + (" — normalized" if norm else "")
        fig = px.line(
            df,
            x="date", y=ycol, color="country", markers=True,
            title=f"{label} — {units}",
            labels={"date":"Year", ycol: ylab, "country":"Country"},
        )
        fig.update_layout(height=450, legend_title_text="Country", margin=dict(l=10,r=10,t=50,b=10))
        st.plotly_chart(fig, use_container_width=True)

# --------------------------- Correlation ----------------------------
st.markdown("## Cross-series correlation (wide pivot)")
# Create wide table: columns as Country:Indicator, rows as years, values as selected ycol
wide_parts = []
for ind in indicator_codes:
    df = data[data["indicator"]==ind].copy()
    ycol = "value_norm" if norm else "value"
    wide = df.pivot_table(index="date", columns=["country","indicator_name"], values=ycol)
    wide_parts.append(wide)

if wide_parts:
    wide_all = pd.concat(wide_parts, axis=1)
    corr = wide_all.corr().round(2)
    st.dataframe(corr)
else:
    st.info("Not enough data for correlation table.")

# --------------------------- Raw & Export ----------------------------
if show_raw:
    st.markdown("## Raw data")
    st.dataframe(data.sort_values(["indicator","country","date"]))

csv_buf = io.StringIO()
data.sort_values(["indicator","country","date"]).to_csv(csv_buf, index=False)
st.download_button(
    label="Download CSV",
    data=csv_buf.getvalue(),
    file_name="marketpulse_export.csv",
    mime="text/csv",
)

st.caption("Data source: World Bank Open Data API. Built with Streamlit.")
