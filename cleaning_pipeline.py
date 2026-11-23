"""
cleaning_pipeline.py -- reusable cleaning rules for the mobile-money agent extract.

Public API
----------
audit_agent_data(txns_raw, agents) -> DataFrame   (issue, dimension, column, rows_affected)
clean_agent_data(txns, agents)     -> (clean_df, log)

Every rule is expressed as a vectorised pandas operation: no row numbers, no
manual edits. Each rule writes one line to the cleaning log so the data
governance team can trace what changed and why.
"""
import numpy as np
import pandas as pd

# --- documented business constants -----------------------------------------
TYPE_MAP = {"deposit": "Deposit", "withdrawal": "Withdrawal"}
SUSPECT_VALUE = 250_000      # repeated round value found during the audit (see notebook)
IQR_K = 1.5                  # Tukey fence multiplier
UNKNOWN_COUNTY = "Unknown"


def _iqr_fences(values, k=IQR_K):
    q1, q3 = values.quantile([0.25, 0.75])
    iqr = q3 - q1
    return q1 - k * iqr, q3 + k * iqr


def audit_agent_data(txns_raw, agents):
    """Return a data-quality audit table for the RAW extract.

    Pass the transactions read with ``dtype=str, keep_default_na=False`` so that
    blanks and the text "N/A" can be told apart. Counts are of raw rows
    (duplicates included).
    """
    t = txns_raw.copy()
    for c in t.columns:
        t[c] = t[c].fillna("").astype(str)
    rows = []

    def add(issue, dimension, column, mask_or_n):
        n = int(mask_or_n.sum()) if hasattr(mask_or_n, "sum") else int(mask_or_n)
        rows.append({"issue": issue, "dimension": dimension,
                     "column": column, "rows_affected": n})

    # Uniqueness
    add("Repeated txn_id (duplicate transactions)", "Uniqueness", "txn_id",
        t.duplicated("txn_id"))
    add("Duplicate agent_id in agent register", "Uniqueness", "agent_id (agents)",
        agents.duplicated("agent_id"))
    # Consistency
    add("txn_type has mixed case / trailing spaces (not exactly Deposit or Withdrawal)",
        "Consistency", "txn_type", ~t["txn_type"].isin(TYPE_MAP.values()))
    add("txn_date uses '/' instead of '-' (mixed formats)", "Consistency",
        "txn_date", t["txn_date"].str.contains("/", regex=False))
    known = t["agent_id"].isin(agents["agent_id"])
    add("agent_id not in agent register (orphan key)", "Consistency",
        "agent_id", ~known)
    # Completeness / Validity on amount
    blank = t["amount"].str.strip().eq("")
    num = pd.to_numeric(t["amount"], errors="coerce")
    add("amount blank (missing)", "Completeness", "amount", blank)
    add("amount is non-numeric text such as 'N/A'", "Validity", "amount",
        num.isna() & ~blank)
    add("amount <= 0", "Validity", "amount", num.le(0))
    d = pd.to_datetime(t["txn_date"].str.strip().str.replace("/", "-", regex=False),
                       format="%Y-%m-%d", errors="coerce")
    add("txn_date cannot be parsed even after normalising separators", "Validity",
        "txn_date", d.isna())
    # Accuracy
    lo, hi = _iqr_fences(num.dropna())
    add(f"amount outside IQR fences (> {hi:,.0f})", "Accuracy", "amount",
        (num < lo) | (num > hi))
    add(f"amount equals the repeated round value {SUSPECT_VALUE:,}", "Accuracy",
        "amount", num.eq(SUSPECT_VALUE))
    # Timeliness
    onboarded = pd.to_datetime(t["agent_id"].map(agents.set_index("agent_id")["onboarded"]))
    add("txn_date before the agent's onboarding date", "Timeliness", "txn_date",
        d < onboarded)
    add("txn_date in the future", "Timeliness", "txn_date", d > pd.Timestamp.today())
    return pd.DataFrame(rows)


def clean_agent_data(txns, agents):
    """Clean the agent transaction extract and merge it with the agent register.

    Parameters
    ----------
    txns : DataFrame
        Raw transactions (txn_id, agent_id, txn_type, amount, txn_date). May be
        read with pandas defaults (the text "N/A" then arrives as NaN) or as
        strings; both are handled.
    agents : DataFrame
        Agent register (agent_id, county, onboarded).

    Returns
    -------
    clean_df : DataFrame
        One row per unique txn_id with columns: txn_id, agent_id, county,
        agent_onboarded, txn_type, amount (float), txn_date (datetime64),
        amount_imputed, is_outlier, hold_for_review, agent_known.
    log : DataFrame
        One row per cleaning rule: step, dimension, rule, rows_affected, rows_after, detail.
    """
    log = []

    def note(step, dimension, rule, affected, rows_after, detail=""):
        log.append({"step": step, "dimension": dimension, "rule": rule,
                    "rows_affected": int(affected), "rows_after": int(rows_after),
                    "detail": detail})

    df = txns.copy()
    df.columns = df.columns.str.strip()
    for c in ("txn_id", "agent_id"):
        df[c] = df[c].astype(str).str.strip()
    agents = agents.copy()
    agents["agent_id"] = agents["agent_id"].astype(str).str.strip()
    if agents["agent_id"].duplicated().any():
        raise ValueError("agent register has duplicate agent_id; cannot merge safely")
    note(0, "-", "Raw input", 0, len(df), f"{len(df)} rows loaded")

    # 1 Uniqueness: txn_id is the business key. Where copies disagree (e.g. one
    # copy carries an orphan agent_id) keep the copy whose agent is in the register.
    full_dups = int(df.duplicated().sum())
    orphan = ~df["agent_id"].isin(agents["agent_id"])
    before = len(df)
    df = (df.assign(_orphan=orphan)
            .sort_values(["txn_id", "_orphan"], kind="stable")
            .drop_duplicates("txn_id", keep="first")
            .drop(columns="_orphan").reset_index(drop=True))
    removed = before - len(df)
    note(1, "Uniqueness", "Drop repeated txn_id, keeping the copy with a registered agent",
         removed, len(df),
         f"{full_dups} were exact full-row copies; {removed - full_dups} differed only in agent_id")

    # 2 Consistency: txn_type -> exactly Deposit / Withdrawal
    tt = df["txn_type"].astype(str).str.strip().str.lower().map(TYPE_MAP)
    changed = int((tt != df["txn_type"]).sum())
    unmapped = int(tt.isna().sum())
    df["txn_type"] = tt
    note(2, "Consistency", "Trim, lower-case and map txn_type to Deposit/Withdrawal",
         changed, len(df), f"{unmapped} values could not be mapped")

    # 3 Validity: txn_date -> datetime (normalise '/' to '-', strict ISO parse)
    raw_d = df["txn_date"].astype(str).str.strip()
    slash = int(raw_d.str.contains("/", regex=False).sum())
    d = pd.to_datetime(raw_d.str.replace("/", "-", regex=False), format="%Y-%m-%d",
                       errors="coerce")
    df["txn_date"] = d
    note(3, "Validity", "Normalise '/' dates and convert txn_date to datetime",
         slash, len(df), f"{int(d.isna().sum())} dates unparseable (left as NaT)")

    # 4 Validity: amount -> numeric; blanks, 'N/A', text, non-positive -> NaN
    num = pd.to_numeric(df["amount"], errors="coerce")
    nonpos = int(num.le(0).sum())
    num = num.mask(num.le(0))
    n_bad = int(num.isna().sum())
    note(4, "Validity", "Convert amount to numeric (blank / 'N/A' / non-positive -> NaN)",
         n_bad, len(df), f"{nonpos} non-positive values treated as invalid")

    # 5 Accuracy: IQR fences on observed amounts; hold out the repeated round value
    lo, hi = _iqr_fences(num.dropna())
    df["is_outlier"] = ((num < lo) | (num > hi)).fillna(False)
    df["hold_for_review"] = num.eq(SUSPECT_VALUE)
    note(5, "Accuracy", f"Flag IQR outliers (k={IQR_K}); hold out amount == {SUSPECT_VALUE:,}",
         int(df["is_outlier"].sum()), len(df),
         f"fences [{lo:,.0f}, {hi:,.0f}]; {int(df['hold_for_review'].sum())} rows held for review; "
         "rows are flagged, not deleted")

    # 6 Completeness: impute missing/invalid amount with the median of its txn_type
    #   (observed values only, held-out rows excluded) and flag the row.
    ref = num.where(~df["hold_for_review"])
    med = ref.groupby(df["txn_type"]).transform("median")
    df["amount_imputed"] = num.isna()
    df["amount"] = num.fillna(med)
    note(6, "Completeness", "Impute missing/invalid amount with per-type median; set amount_imputed",
         int(df["amount_imputed"].sum()), len(df),
         "medians: " + ", ".join(f"{k}={v:,.0f}" for k, v in ref.groupby(df['txn_type']).median().items()))

    # 7 Consistency (cross-table): left-join the register; keep orphans as Unknown
    reg = agents.rename(columns={"onboarded": "agent_onboarded"})
    reg["agent_onboarded"] = pd.to_datetime(reg["agent_onboarded"], errors="coerce")
    n_before = len(df)
    df = df.merge(reg, on="agent_id", how="left", validate="m:1")
    df["agent_known"] = df["county"].notna()
    df["county"] = df["county"].fillna(UNKNOWN_COUNTY)
    if len(df) != n_before:
        raise AssertionError("merge changed the row count")
    note(7, "Consistency", "Left-join agent register; unmatched agents get county='Unknown'",
         int((~df["agent_known"]).sum()), len(df),
         "kept (not dropped) and flagged with agent_known=False")

    cols = ["txn_id", "agent_id", "county", "agent_onboarded", "txn_type", "amount",
            "txn_date", "amount_imputed", "is_outlier", "hold_for_review", "agent_known"]
    return df[cols], pd.DataFrame(log)
