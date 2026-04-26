# pvc_app/pvc.py

import json
import math
from io import BytesIO
from datetime import date

import pandas as pd
from dateutil.relativedelta import relativedelta
from flask import (
    Blueprint,
    request,
    redirect,
    url_for,
    flash,
    render_template,
    send_file,
    jsonify,
)
from flask_login import login_required, current_user

from . import db
from .models import Item, ItemIndex, PVCResult, TenderMaster, TenderVendor  # [file:1]

GST_FACTOR = 1.18

pvc_bp = Blueprint("pvc", __name__)


# ───────────────────────────────────
# Generic helpers
# ───────────────────────────────────

def safe_float(x):
    try:
        return float(str(x or 0).replace(",", "").strip())
    except Exception:
        return 0.0  # [file:1]


def safe_round(x, n=2):
    try:
        return round(float(x), n)
    except Exception:
        return None  # [file:1]


def to_month_start(d):
    if not d:
        return pd.NaT
    ts = pd.to_datetime(d, errors="coerce")
    if pd.isna(ts):
        return pd.NaT
    return pd.Timestamp(ts.year, ts.month, 1)  # [file:1]


def previous_month(d, n=1):
    d = to_month_start(d)
    return pd.NaT if pd.isna(d) else d - relativedelta(months=n)  # [file:1]


# ───────────────────────────────────
# Index access (IEEMA + IGBT)
# ───────────────────────────────────

def get_item_index_df(item):
    rows = (
        ItemIndex.query
        .filter_by(item_id=item.id)
        .order_by(ItemIndex.month.asc())
        .all()
    )
    if not rows:
        return pd.DataFrame()

    data = []
    for r in rows:
        row = {"date": pd.Timestamp(r.month.year, r.month.month, 1)}
        try:
            idx = json.loads(r.indices_json or "{}")
        except Exception:
            idx = {}
        row.update(idx)
        data.append(row)

    df = pd.DataFrame(data)
    if df.empty:
        return df
    return df.set_index("date").sort_index()  # [file:1]


def ieema_row(df, dt, previous=False):
    """Latest index row on or before target month."""
    if df is None or df.empty:
        return None
    target = previous_month(dt) if previous else to_month_start(dt)
    if pd.isna(target):
        return None
    eligible = df[df.index <= target]
    return eligible.iloc[-1] if not eligible.empty else None  # [file:1]


def pvc_percent(base_date, current_date, idx_df, weights):
    base = ieema_row(idx_df, base_date, previous=False)
    curr = ieema_row(idx_df, current_date, previous=True)
    if base is None or curr is None:
        return 0.0

    total = 0.0
    for key, weight in weights.items():
        b = base.get(key)
        c = curr.get(key)
        if b not in (None, 0) and c is not None:
            total += float(weight) * (float(c) - float(b)) / float(b)
    return total  # [file:1]


def pvc_percent_detailed(base_date, current_date, idx_df, scenario, weights):
    base = ieema_row(idx_df, base_date, previous=False)
    curr = ieema_row(idx_df, current_date, previous=True)
    if base is None or curr is None:
        return None

    row = {
        "scenario": scenario,
        "basemonth": base.name.isoformat() if hasattr(base.name, "isoformat") else str(base.name),
        "currentmonth": curr.name.isoformat() if hasattr(curr.name, "isoformat") else str(curr.name),
        "pvcpercent": 0.0,
    }
    total = 0.0
    for key, weight in weights.items():
        b = base.get(key)
        c = curr.get(key)
        contrib = None
        if b not in (None, 0) and c is not None:
            contrib = round(float(weight) * (float(c) - float(b)) / float(b), 4)
            total += contrib
        row[f"{key}_base"] = safe_round(b, 2) if b is not None else None
        row[f"{key}_current"] = safe_round(c, 2) if c is not None else None
        row[f"{key}_weight"] = weight
        row[f"{key}_contributionpct"] = contrib
    row["pvcpercent"] = round(total, 4)
    return row  # [file:1]


# ───────────────────────────────────
# IGBT-specific helpers
# ───────────────────────────────────

def igbt_index_values(idx_df, base_month, ref_month, currency):
    """
    Base: all indices from base_month.
    Current: C/AL/FE/IM/W/D from ref_month - 1, ER from ref_month - 3.
    """
    currency = (currency or "EUR").upper().strip()
    ercol = f"ER_{currency}"

    base_row = ieema_row(idx_df, base_month, previous=False)
    row_1m = ieema_row(idx_df, previous_month(ref_month, 1), previous=False)
    row_3m = ieema_row(idx_df, previous_month(ref_month, 3), previous=False)
    if base_row is None or row_1m is None or row_3m is None:
        raise ValueError("IGBT indices missing for some required months.")  # [file:1]

    def get_row(row, col):
        v = row.get(col)
        if v is None:
            raise ValueError(f"Index column {col} missing")
        return float(v)

    baseidx = {
        "C": get_row(base_row, "C"),
        "AL": get_row(base_row, "AL"),
        "FE": get_row(base_row, "FE"),
        "IM": get_row(base_row, "IM"),
        "W": get_row(base_row, "W"),
        "D": get_row(base_row, "D"),
        "ER": get_row(base_row, ercol),
    }
    curridx = {
        "C": get_row(row_1m, "C"),
        "AL": get_row(row_1m, "AL"),
        "FE": get_row(row_1m, "FE"),
        "IM": get_row(row_1m, "IM"),
        "W": get_row(row_1m, "W"),
        "D": get_row(row_1m, "D"),
        "ER": get_row(row_3m, ercol),
    }
    return baseidx, curridx  # [file:1]


def igbt_p1_indigenous(baseidx, curridx, weights):
    """
    P1 indigenous component PVC.
    Original RDSO IGBT pattern (simplified notation).
    """
    Fc = float(weights.get("FIXED", 16))
    wC = float(weights.get("C", 26))
    wAL = float(weights.get("AL", 13))
    wFE = float(weights.get("FE", 18))
    wIM = float(weights.get("IM", 9))
    wW = float(weights.get("W", 18))

    indigenous_value = 100.0
    factor = (
        Fc
        + wC * curridx["C"] / baseidx["C"]
        + wAL * curridx["AL"] / baseidx["AL"]
        + wFE * curridx["FE"] / baseidx["FE"]
        + wIM * curridx["IM"] / baseidx["IM"]
        + wW * curridx["W"] / baseidx["W"]
    )
    return 100.0 * factor - indigenous_value  # [file:1]


def igbt_p2_cif(cif_value, baseidx, curridx):
    """P2 imported CIF component PVC."""
    cif_value = float(cif_value or 0.0)
    if abs(cif_value) < 1e-6:
        return 0.0
    return cif_value * (
        100.0 * curridx["ER"] / baseidx["ER"]
        + 100.0 * (curridx["D"] - baseidx["D"]) / baseidx["D"]
    ) / 100.0  # [file:1]


def igbt_vendor_scenario(rate, vendor, base_month, ref_month, idx_df, weights, scenario_label):
    """
    Compute P1/P2 and total PVC for one vendor in one scenario.
    Returns (summary_dict, indexdetails_list, total_pvc).
    """
    currency = (vendor.currency or "EUR").upper()
    baseidx, curridx = igbt_index_values(idx_df, base_month, ref_month, currency)
    currmonth = ref_month

    cif = safe_float(vendor.cif)
    baseduty = baseidx["D"]
    indigenous_portion = rate - cif - baseduty

    p1 = igbt_p1_indigenous(baseidx, curridx, weights)
    p2 = igbt_p2_cif(cif, baseidx, curridx)
    total_pvc = p1 + p2  # [file:1]

    summary = {
        "scenario": scenario_label,
        "vendor": vendor.vendor_name,
        "pono": vendor.po_no,
        "currency": currency,
        "rate": safe_round(rate),
        "cif": safe_round(cif),
        "baseduty": safe_round(baseduty),
        "indigenous": safe_round(indigenous_portion),
        "p1": safe_round(p1),
        "p2": safe_round(p2),
        "totalpvc": safe_round(total_pvc),
        "basemonth": str(base_month.date()) if hasattr(base_month, "date") else str(base_month),
        "currentmonth": str(currmonth.date()) if hasattr(currmonth, "date") else str(currmonth),
    }

    # Index-wise vendor details (used in result.html IGBT tabs)
    indexdetails = []
    for col, label in [
        ("C", "Copper Index"),
        ("AL", "Aluminium Index"),
        ("FE", "Iron/Steel Index"),
        ("IM", "Import Machine Index"),
        ("W", "Labour/Wage Index"),
        ("D", "Import Duty"),
        (f"ER_{currency}", f"Exchange Rate {currency}"),
    ]:
        base_val = baseidx.get(col if col != f"ER_{currency}" else "ER")
        curr_val = curridx.get(col if col != f"ER_{currency}" else "ER")
        indexdetails.append(
            {
                "scenario": scenario_label,
                "vendor": vendor.vendor_name,
                "parameter": col,
                "label": label,
                "basemonth": summary["basemonth"],
                "basevalue": safe_round(base_val, 4),
                "currentmonth": summary["currentmonth"],
                "currentvalue": safe_round(curr_val, 4),
            }
        )

    return summary, indexdetails, total_pvc  # [file:1]


def calculate_igbt_propulsion(item, data, tender_id, idx_df, weights):
    """
    Full IGBT Propulsion System PVC calculation.
    Scenarios A2, B2, C1, D1; minimum vendor PVC per scenario; LD; fair price.
    """
    tender = TenderMaster.query.get_or_404(int(tender_id))
    vendors = (
        TenderVendor.query
        .filter_by(tender_id=tender.id)
        .order_by(TenderVendor.id.asc())
        .all()
    )
    if not vendors:
        raise ValueError("No vendor rows configured for the selected tender.")  # [file:1]

    # Merge form data with tender defaults
    basic_rate = safe_float(data.get("basicrate") or tender.basicrate)
    pvc_base_date = data.get("pvcbasedate") or tender.pvcbasedate
    lower_rate = safe_float(data.get("lowerrate") or tender.lowerrate or 0)
    lower_base = data.get("lowerbasicdate") or tender.lowerratebasedate
    freight_per_unit = safe_float(data.get("freightrateperunit") or tender.freightrateperunit or 0)
    lower_freight = safe_float(data.get("lowerfreight") or tender.lowerfreight or 0)
    quantity = safe_float(data.get("quantity") or 1)

    cal_date = data.get("caldate")
    origdp = data.get("origdp")
    refixeddp = data.get("refixeddp")
    extendeddp = data.get("extendeddp")
    supply_date = data.get("supdate")
    rate_applied = (data.get("rateapplied") or "").strip().lower()

    scheduleddp = extendeddp or refixeddp or origdp  # [file:1]

    calts = pd.to_datetime(cal_date, errors="coerce")
    schedts = pd.to_datetime(scheduleddp, errors="coerce")
    supplyts = pd.to_datetime(supply_date, errors="coerce")

    basemonth = to_month_start(pvc_base_date)
    lowerbase_month = to_month_start(lower_base) if lower_base else None
    calmonth = to_month_start(cal_date)
    schedmonth = to_month_start(scheduleddp)

    lowerrate_applicable = lower_rate > 0 and lowerbase_month is not None

    freight_total = freight_per_unit * quantity
    lowerfreight_total = lower_freight * quantity

    # LD
    delaydays = 0
    ldweeks = 0
    ldratepct = 0.0
    ldapplicable = False
    duets = schedts
    if pd.notna(duets) and pd.notna(supplyts) and supplyts > duets:
        delaydays = int((supplyts - duets).days)
        ldweeks = math.ceil(delaydays / 7) if delaydays > 0 else 0
        ldratepct = min(ldweeks * 0.5, 10.0)
        ldapplicable = True  # [file:1]

    all_vendor_summaries = []
    all_index_details = []

    def run_scenario(label, rate, base_m, ref_m):
        if not base_m or pd.isna(base_m):
            return 0.0
        if not ref_m or pd.isna(ref_m):
            return 0.0

        sc_summaries = []
        sc_indices = []
        for v in vendors:
            try:
                s, idxrows, totalpvc = igbt_vendor_scenario(
                    rate, v, base_m, ref_m, idx_df, weights, label
                )
                sc_summaries.append(s)
                sc_indices.extend(idxrows)
            except ValueError:
                continue
        all_vendor_summaries.extend(sc_summaries)
        all_index_details.extend(sc_indices)
        if not sc_summaries:
            return 0.0
        return min(s["totalpvc"] for s in sc_summaries)  # [file:1]

    pvca2 = run_scenario("A2", basic_rate, basemonth, calmonth)
    pvcb2 = run_scenario("B2", basic_rate, basemonth, schedmonth)
    pvcc1 = None
    pvcd1 = None
    if lowerrate_applicable:
        pvcc1 = run_scenario("C1", lower_rate, lowerbase_month, calmonth)
        pvcd1 = run_scenario("D1", lower_rate, lowerbase_month, schedmonth)

    def with_freight_gst(baser, pvcamt, qty, frt):
        return baser * qty * (1 + pvcamt / 100.0) + frt * GST_FACTOR  # [file:1]

    pvcactual = with_freight_gst(basic_rate, pvca2, quantity, freight_total)
    pvccontractual = with_freight_gst(basic_rate, pvcb2, quantity, freight_total)
    loweractual = (
        with_freight_gst(lower_rate, pvcc1 or 0, quantity, lowerfreight_total)
        if lowerrate_applicable
        else None
    )
    lowercontractual = (
        with_freight_gst(lower_rate, pvcd1 or 0, quantity, lowerfreight_total)
        if lowerrate_applicable
        else None
    )

    ldamtactual = max(pvcactual, 0) * (ldratepct / 100.0) if ldapplicable else 0.0
    ldamtcontractual = max(pvccontractual, 0) * (ldratepct / 100.0) if ldapplicable else 0.0

    pvcactuallessld = pvcactual - ldamtactual
    pvccontractuallessld = pvccontractual - ldamtcontractual
    loweractuallessld = loweractual - ldamtactual if loweractual is not None else None
    lowercontractuallessld = lowercontractual - ldamtcontractual if lowercontractual is not None else None  # [file:1]

    # Scenario selection (A2/B2/C1/D1)
    candidates = {}
    if rate_applied == "supply before due date":
        candidates = {"A2": pvcactual}
    elif rate_applied == "supply after due date":
        candidates = {"A2": pvcactuallessld, "B2": pvccontractuallessld}
    elif rate_applied == "lower rate applicable":
        candidates = {
            "A2": pvcactual,
            "B2": pvccontractual,
            "C1": loweractual,
            "D1": lowercontractual,
        }
    elif rate_applied == "lower rate and ld comparison":
        candidates = {
            "A2": pvcactuallessld,
            "B2": pvccontractuallessld,
            "C1": loweractual,
            "D1": lowercontractual,
        }
    elif rate_applied == "lower rate with ld in further extension":
        candidates = {
            "A2": pvcactuallessld,
            "B2": pvccontractuallessld,
            "C1": loweractuallessld,
            "D1": lowercontractuallessld,
        }
    else:
        candidates = {"A2": pvcactual, "B2": pvccontractual}
        if lowerrate_applicable:
            candidates["C1"] = loweractual
            candidates["D1"] = lowercontractual

    candidates = {k: v for k, v in candidates.items() if v is not None}
    selected = min(candidates, key=candidates.get) if candidates else "A2"
    fairprice = candidates.get(selected, pvcactual)  # [file:1]

    scenarioamounts = {
        "A2": safe_round(pvcactuallessld),
        "B2": safe_round(pvccontractuallessld),
    }
    if lowerrate_applicable:
        scenarioamounts["C1"] = safe_round(loweractual)
        scenarioamounts["D1"] = safe_round(lowercontractual)

    scenariodetails = []
    ieema_scenarios = [
        ("A2", pvc_base_date, cal_date),
        ("B2", pvc_base_date, scheduleddp),
    ]
    if lowerrate_applicable:
        ieema_scenarios.append(("C1", lower_base, cal_date))
        ieema_scenarios.append(("D1", lower_base, scheduleddp))
    for sc, bd, cd in ieema_scenarios:
        if bd and cd:
            det = pvc_percent_detailed(bd, cd, idx_df, sc, weights)
            if det:
                scenariodetails.append(det)  # [file:1]

    return {
        "pvcactual": safe_round(pvcactual),
        "pvccontractual": safe_round(pvccontractual),
        "loweractual": safe_round(loweractual) if lowerrate_applicable else None,
        "lowercontractual": safe_round(lowercontractual) if lowerrate_applicable else None,
        "delaydays": delaydays,
        "ldweeks": ldweeks,
        "ldratepct": safe_round(ldratepct),
        "ldapplicable": ldapplicable,
        "ldamtactual": safe_round(ldamtactual),
        "ldamtcontractual": safe_round(ldamtcontractual),
        "pvcactuallessldnew": safe_round(pvcactuallessld),
        "pvccontractuallessldnew": safe_round(pvccontractuallessld),
        "loweractuallessld": safe_round(loweractuallessld) if loweractuallessld is not None else None,
        "lowercontractuallessld": safe_round(lowercontractuallessld) if lowercontractuallessld is not None else None,
        "fairpricenew": safe_round(fairprice),
        "selectedscenarionew": selected,
        "pvcperseta2": safe_round(pvca2),
        "pvcpersetb2": safe_round(pvcb2),
        "pvcpersetc1": safe_round(pvcc1) if pvcc1 is not None else None,
        "pvcpersetd1": safe_round(pvcd1) if pvcd1 is not None else None,
        "scenarioamounts": scenarioamounts,
        "scenariodetails": scenariodetails,
        "igbtvendordetails": all_vendor_summaries,
        "igbtindexdetails": all_index_details,
        "tenderno": tender.tender_no,
        "pono": vendors[0].po_no if vendors else None,
    }  # [file:1]


# ───────────────────────────────────
# IEEMA single‑record helper
# ───────────────────────────────────

def calc_single_record(data, idx_df, weights):
    pvc_base_date = data.get("pvcbasedate")
    cal_date = data.get("caldate")
    origdp = data.get("origdp")
    refixeddp = data.get("refixeddp")
    extendeddp = data.get("extendeddp")
    scheduled_date = extendeddp or refixeddp or origdp
    supply_date = data.get("supdate")
    lower_base_date = data.get("lowerbasicdate")

    qty = safe_float(data.get("quantity"))
    basic_rate = safe_float(data.get("basicrate"))
    freight_per_unit = safe_float(data.get("freightrateperunit"))
    lower_rate = safe_float(data.get("lowerrate"))
    lower_freight = safe_float(data.get("lowerfreight"))

    freight_total = freight_per_unit * qty
    lowerfreight_total = lower_freight * qty  # [file:1]

    pct_a2 = pvc_percent(pvc_base_date, cal_date, idx_df, weights)
    pct_b2 = pvc_percent(pvc_base_date, scheduled_date, idx_df, weights)
    pct_c1 = pvc_percent(lower_base_date, cal_date, idx_df, weights) if lower_base_date else None
    pct_d1 = pvc_percent(lower_base_date, scheduled_date, idx_df, weights) if lower_base_date else None  # [file:1]

    baseamt = basic_rate * qty
    loweramt = lower_rate * qty

    pvcactual = baseamt * (1 + pct_a2 / 100.0) + freight_total * GST_FACTOR
    pvccontractual = baseamt * (1 + pct_b2 / 100.0) + freight_total * GST_FACTOR
    loweractual = (
        loweramt * (1 + (pct_c1 or 0) / 100.0) + lowerfreight_total * GST_FACTOR
        if lower_rate
        else None
    )
    lowercontractual = (
        loweramt * (1 + (pct_d1 or 0) / 100.0) + lowerfreight_total * GST_FACTOR
        if lower_rate
        else None
    )  # [file:1]

    delaydays = 0
    ldweeks = 0
    ldratepct = 0.0
    ldapplicable = False
    duets = pd.to_datetime(scheduled_date, errors="coerce")
    supplyts = pd.to_datetime(supply_date, errors="coerce")
    if pd.notna(duets) and pd.notna(supplyts) and supplyts > duets:
        delaydays = int((supplyts - duets).days)
        ldweeks = math.ceil(delaydays / 7) if delaydays > 0 else 0
        ldratepct = min(ldweeks * 0.5, 10.0)
        ldapplicable = True  # [file:1]

    ldamtactual = pvcactual * (ldratepct / 100.0) if ldapplicable else 0.0
    ldamtcontractual = pvccontractual * (ldratepct / 100.0) if ldapplicable else 0.0
    pvcactuallessld = pvcactual - ldamtactual
    pvccontractuallessld = pvccontractual - ldamtcontractual
    loweractuallessld = loweractual - ldamtactual if loweractual is not None else None
    lowercontractuallessld = lowercontractual - ldamtcontractual if lowercontractual is not None else None  # [file:1]

    rate_applied = (data.get("rateapplied") or "").strip().lower()
    candidates = {}
    if rate_applied == "supply before due date":
        candidates = {"A2": pvcactual}
    elif rate_applied == "supply after due date":
        candidates = {"A2": pvcactuallessld, "B2": pvccontractuallessld}
    elif rate_applied == "lower rate applicable":
        candidates = {
            "A2": pvcactual,
            "B2": pvccontractual,
            "C1": loweractual,
            "D1": lowercontractual,
        }
    elif rate_applied == "lower rate and ld comparison":
        candidates = {
            "A2": pvcactuallessld,
            "B2": pvccontractuallessld,
            "C1": loweractual,
            "D1": lowercontractual,
        }
    elif rate_applied == "lower rate with ld in further extension":
        candidates = {
            "A2": pvcactuallessld,
            "B2": pvccontractuallessld,
            "C1": loweractuallessld,
            "D1": lowercontractuallessld,
        }
    else:
        candidates = {"A2": pvcactual, "B2": pvccontractual}
        if lower_rate:
            candidates["C1"] = loweractual
            candidates["D1"] = lowercontractual

    candidates = {k: v for k, v in candidates.items() if v is not None}
    selected = min(candidates, key=candidates.get) if candidates else "A2"
    fairprice = candidates.get(selected, pvcactual)  # [file:1]

    scenarioamounts = {
        "A2": safe_round(pvcactuallessld),
        "B2": safe_round(pvccontractuallessld),
    }
    if lower_rate:
        scenarioamounts["C1"] = safe_round(loweractual)
        scenarioamounts["D1"] = safe_round(lowercontractual)

    scenariodetails = []
    for sc, bd, cd in [
        ("A2", pvc_base_date, cal_date),
        ("B2", pvc_base_date, scheduled_date),
        ("C1", lower_base_date, cal_date),
        ("D1", lower_base_date, scheduled_date),
    ]:
        if bd and cd:
            det = pvc_percent_detailed(bd, cd, idx_df, sc, weights)
            if det:
                scenariodetails.append(det)  # [file:1]

    return {
        "pvcactual": safe_round(pvcactual),
        "pvccontractual": safe_round(pvccontractual),
        "loweractual": safe_round(loweractual) if loweractual is not None else None,
        "lowercontractual": safe_round(lowercontractual) if lowercontractual is not None else None,
        "delaydays": delaydays,
        "ldweeks": ldweeks,
        "ldratepct": safe_round(ldratepct),
        "ldapplicable": ldapplicable,
        "ldamtactual": safe_round(ldamtactual),
        "ldamtcontractual": safe_round(ldamtcontractual),
        "pvcactuallessldnew": safe_round(pvcactuallessld),
        "pvccontractuallessldnew": safe_round(pvccontractuallessld),
        "loweractuallessld": safe_round(loweractuallessld) if loweractuallessld is not None else None,
        "lowercontractuallessld": safe_round(lowercontractuallessld) if lowercontractuallessld is not None else None,
        "fairpricenew": safe_round(fairprice),
        "selectedscenarionew": selected,
        "pvcperseta2": safe_round(baseamt * pct_a2 / 100.0),
        "pvcpersetb2": safe_round(baseamt * pct_b2 / 100.0),
        "pvcpersetc1": safe_round(loweramt * (pct_c1 or 0) / 100.0) if lower_rate else None,
        "pvcpersetd1": safe_round(loweramt * (pct_d1 or 0) / 100.0) if lower_rate else None,
        "scenarioamounts": scenarioamounts,
        "scenariodetails": scenariodetails,
        "igbtvendordetails": [],
        "igbtindexdetails": [],
        "tenderno": None,
        "pono": None,
    }  # [file:1]


def calculate_for_item(item, data, idx_df, weights):
    code = (item.pvc_formula_code or "").replace(" ", "").upper()
    if code == "IGBTPROPULSIONSYSTEM":
        tender_id = data.get("tenderid")
        if not tender_id:
            raise ValueError("Tender is required for IGBT Propulsion System.")
        return calculate_igbt_propulsion(item, data, tender_id, idx_df, weights)
    return calc_single_record(data, idx_df, weights)  # [file:1]


# ───────────────────────────────────
# PVC input + result wrapper
# ───────────────────────────────────

class PVCInput:
    _KNOWN = {
        "itemid", "item_id", "basicrate", "quantity", "freightrateperunit",
        "pvcbasedate", "origdp", "refixeddp", "extendeddp", "caldate", "supdate",
        "lowerrate", "lowerfreight", "lowerbasicdate", "rateapplied",
        "tenderid", "tender_id",
    }

    def __init__(self, form):
        self.user_id = None
        self.username = None
        self.item_id = int(form.get("itemid") or form.get("item_id") or 0)
        self.basic_rate = safe_float(form.get("basicrate"))
        self.quantity = safe_float(form.get("quantity"))
        self.freight_rate_per_unit = safe_float(form.get("freightrateperunit"))
        self.pvc_base_date = form.get("pvcbasedate")
        self.original_dp = form.get("origdp")
        self.refixed_dp = form.get("refixeddp")
        self.extended_dp = form.get("extendeddp")
        self.cal_date = form.get("caldate")
        self.supply_date = form.get("supdate")
        self.lower_rate = safe_float(form.get("lowerrate"))
        self.lower_freight = safe_float(form.get("lowerfreight"))
        self.lower_basic_date = form.get("lowerbasicdate")
        self.rate_applied = form.get("rateapplied")
        self.tender_id = form.get("tenderid") or form.get("tender_id")
        self.extra_data = {
            k: form.get(k)
            for k in form.keys()
            if k not in self._KNOWN
        }  # [file:1]

    def to_dict(self):
        d = {
            "basicrate": self.basic_rate,
            "quantity": self.quantity,
            "freightrateperunit": self.freight_rate_per_unit,
            "pvcbasedate": self.pvc_base_date,
            "origdp": self.original_dp,
            "refixeddp": self.refixed_dp,
            "extendeddp": self.extended_dp,
            "caldate": self.cal_date,
            "supdate": self.supply_date,
            "lowerrate": self.lower_rate,
            "lowerfreight": self.lower_freight,
            "lowerbasicdate": self.lower_basic_date,
            "rateapplied": self.rate_applied,
            "tenderid": self.tender_id,
        }
        d.update(self.extra_data)
        return d  # [file:1]


class ResultObj:
    def __init__(self, payload):
        self.data = payload
        self.scenarioamounts = payload.get("scenarioamounts", {})
        self.scenariodetails = payload.get("scenariodetails", [])
        self.igbtvendordetails = payload.get("igbtvendordetails", [])
        self.igbtindexdetails = payload.get("igbtindexdetails", [])  # [file:1]


# ───────────────────────────────────
# Routes: calculate, view, Excel, tender JSON
# ───────────────────────────────────

@pvc_bp.route("/calculate", methods=["POST"])
@login_required
def calculate():
    itemid = request.form.get("itemid") or request.form.get("item_id")
    if not itemid:
        flash("Please select an item.", "danger")
        return redirect(url_for("main.index"))  # [file:15]

    item = Item.query.get_or_404(int(itemid))
    idx_df = get_item_index_df(item)
    if idx_df.empty:
        flash("Indices not configured for this item. Please add in Admin → Item Indices.", "danger")
        return redirect(url_for("main.index"))  # [file:15]

    try:
        weights = json.loads(item.weights_json or "{}")
    except Exception:
        weights = {}

    pvc_input = PVCInput(request.form)
    pvc_input.user_id = current_user.id
    pvc_input.username = current_user.username
    data = pvc_input.to_dict()

    try:
        result_row = calculate_for_item(item, data, idx_df, weights)
    except ValueError as e:
        flash(str(e), "danger")
        return redirect(url_for("main.index"))
    except Exception:
        flash("Calculation failed. Please check your inputs and index data.", "danger")
        return redirect(url_for("main.index"))  # [file:1]

    payload = {
        "pvcactual": result_row.get("pvcactual", 0.0),
        "pvccontractual": result_row.get("pvccontractual", 0.0),
        "loweractual": result_row.get("loweractual"),
        "lowercontractual": result_row.get("lowercontractual"),
        "ldamtactual": result_row.get("ldamtactual", 0.0),
        "ldamtcontractual": result_row.get("ldamtcontractual", 0.0),
        "fairprice": result_row.get("fairpricenew", 0.0),
        "pvcactuallessldnew": result_row.get("pvcactuallessldnew"),
        "pvccontractuallessldnew": result_row.get("pvccontractuallessldnew"),
        "loweractuallessld": result_row.get("loweractuallessld"),
        "lowercontractuallessld": result_row.get("lowercontractuallessld"),
        "delaydays": result_row.get("delaydays", 0),
        "ldweeks": result_row.get("ldweeks", 0),
        "ldratepct": result_row.get("ldratepct", 0.0),
        "ldapplicable": result_row.get("ldapplicable", False),
        "selectedscenario": result_row.get("selectedscenarionew"),
        "pvcperseta2": result_row.get("pvcperseta2"),
        "pvcpersetb2": result_row.get("pvcpersetb2"),
        "pvcpersetc1": result_row.get("pvcpersetc1"),
        "pvcpersetd1": result_row.get("pvcpersetd1"),
        "tenderno": result_row.get("tenderno"),
        "pono": result_row.get("pono"),
        "scenarioamounts": result_row.get("scenarioamounts", {}),
        "scenariodetails": result_row.get("scenariodetails", []),
        "igbtvendordetails": result_row.get("igbtvendordetails", []),
        "igbtindexdetails": result_row.get("igbtindexdetails", []),
    }  # [file:1]

    calc = PVCResult(
        user_id=current_user.id,
        username=current_user.username,
        item_id=item.id,
        basicrate=data.get("basicrate", 0),
        quantity=data.get("quantity", 0),
        freightrateperunit=data.get("freightrateperunit", 0),
        pvcbasedate=data.get("pvcbasedate"),
        origdp=data.get("origdp"),
        refixeddp=data.get("refixeddp"),
        extendeddp=data.get("extendeddp"),
        caldate=data.get("caldate"),
        supdate=data.get("supdate"),
        rateapplied=data.get("rateapplied"),
        pvcactual=payload["pvcactual"],
        pvccontractual=payload["pvccontractual"],
        loweractual=payload["loweractual"],
        lowercontractual=payload["lowercontractual"],
        ldamtactual=payload["ldamtactual"],
        ldamtcontractual=payload["ldamtcontractual"],
        fairprice=payload["fairprice"],
        selectedscenario=payload["selectedscenario"],
        pvcactuallessldnew=payload["pvcactuallessldnew"],
        pvccontractuallessldnew=payload["pvccontractuallessldnew"],
        loweractuallessld=payload["loweractuallessld"],
        lowercontractuallessld=payload["lowercontractuallessld"],
        delaydays=payload["delaydays"],
        ldweeksnew=payload["ldweeks"],
        ldratepctnew=payload["ldratepct"],
        ldapplicable=payload["ldapplicable"],
        pvcperseta2=payload["pvcperseta2"],
        pvcpersetb2=payload["pvcpersetb2"],
        pvcpersetc1=payload["pvcpersetc1"],
        pvcpersetd1=payload["pvcpersetd1"],
        tenderno=payload["tenderno"],
        pono=payload["pono"],
        scenarioamounts_json=json.dumps(payload["scenarioamounts"]),
        scenariodetails_json=json.dumps(payload["scenariodetails"]),
        igbt_vendor_details_json=json.dumps(payload["igbtvendordetails"]),
    )
    db.session.add(calc)
    db.session.commit()  # [file:1]

    result = ResultObj(payload)
    return render_template(
        "result.html",
        item=item.name,
        itemobj=item,
        data=data,
        result=result,
        calcid=calc.id,
    )  # [file:16]


@pvc_bp.route("/calc/<int:calcid>")
@login_required
def view_calc(calcid):
    calc = PVCResult.query.filter_by(id=calcid, user_id=current_user.id).first_or_404()
    item = calc.item

    data = {
        "basicrate": calc.basicrate,
        "quantity": calc.quantity,
        "freightrateperunit": calc.freightrateperunit,
        "pvcbasedate": calc.pvcbasedate,
        "origdp": calc.origdp,
        "refixeddp": calc.refixeddp,
        "extendeddp": calc.extendeddp,
        "caldate": calc.caldate,
        "supdate": calc.supdate,
        "rateapplied": calc.rateapplied,
        "lowerrate": calc.loweractual,
        "lowerfreight": 0,
        "lowerbasicdate": None,
    }

    payload = {
        "pvcactual": calc.pvcactual,
        "pvccontractual": calc.pvccontractual,
        "loweractual": calc.loweractual,
        "lowercontractual": calc.lowercontractual,
        "ldamtactual": calc.ldamtactual,
        "ldamtcontractual": calc.ldamtcontractual,
        "fairprice": calc.fairprice,
        "selectedscenario": calc.selectedscenario,
        "ldapplicable": calc.ldapplicable,
        "pvcactuallessldnew": calc.pvcactuallessldnew,
        "pvccontractuallessldnew": calc.pvccontractuallessldnew,
        "loweractuallessld": calc.loweractuallessld,
        "lowercontractuallessld": calc.lowercontractuallessld,
        "delaydays": calc.delaydays,
        "ldweeks": calc.ldweeksnew,
        "ldratepct": calc.ldratepctnew,
        "pvcperseta2": calc.pvcperseta2,
        "pvcpersetb2": calc.pvcpersetb2,
        "pvcpersetc1": calc.pvcpersetc1,
        "pvcpersetd1": calc.pvcpersetd1,
        "tenderno": calc.tenderno,
        "pono": calc.pono,
        "scenarioamounts": json.loads(calc.scenarioamounts_json or "{}"),
        "scenariodetails": json.loads(calc.scenariodetails_json or "[]"),
        "igbtvendordetails": json.loads(calc.igbt_vendor_details_json or "[]"),
        "igbtindexdetails": [],
    }  # [file:1]

    result = ResultObj(payload)
    return render_template(
        "result.html",
        item=item.name if item else "",
        itemobj=item,
        data=data,
        result=result,
        calcid=calc.id,
    )  # [file:16]


@pvc_bp.route("/calc/<int:calcid>/excel")
@login_required
def export_calc_excel(calcid):
    calc = PVCResult.query.filter_by(id=calcid, user_id=current_user.id).first_or_404()
    item = calc.item

    maindata = {
        "Calc ID": calc.id,
        "User": calc.username,
        "Item": item.name if item else "",
        "Basic Rate": calc.basicrate,
        "Quantity": calc.quantity,
        "FreightUnit": calc.freightrateperunit,
        "PVC Base Date": calc.pvcbasedate,
        "Original DP": calc.origdp,
        "Refixed DP": calc.refixeddp,
        "Extended DP": calc.extendeddp,
        "Call Date": calc.caldate,
        "Supply Date": calc.supdate,
        "Rate Applied": calc.rateapplied,
        "PVC Actual": calc.pvcactual,
        "PVC Contractual": calc.pvccontractual,
        "Lower Actual": calc.loweractual,
        "Lower Contractual": calc.lowercontractual,
        "LD Days": calc.delaydays,
        "LD Weeks": calc.ldweeksnew,
        "LD Rate": calc.ldratepctnew,
        "LD Amt Actual": calc.ldamtactual,
        "LD Amt Contractual": calc.ldamtcontractual,
        "PVC Actual-LD": calc.pvcactuallessldnew,
        "PVC Contractual-LD": calc.pvccontractuallessldnew,
        "Lower Actual-LD": calc.loweractuallessld,
        "Lower Contr-LD": calc.lowercontractuallessld,
        "Fair Price": calc.fairprice,
        "Selected Scenario": calc.selectedscenario,
        "Tender No": calc.tenderno,
        "PO No": calc.pono,
    }  # [file:1]

    sadata = []
    for sc, amount in (json.loads(calc.scenarioamounts_json or "{}") or {}).items():
        sadata.append({"Scenario": sc, "Amount": amount})

    sddata = []
    for det in json.loads(calc.scenariodetails_json or "[]"):
        row = {
            "Scenario": det.get("scenario"),
            "Base Month": det.get("basemonth"),
            "Current Month": det.get("currentmonth"),
            "PVC": det.get("pvcpercent"),
        }
        for k, v in det.items():
            if k not in ("scenario", "basemonth", "currentmonth", "pvcpercent"):
                row[k] = v
        sddata.append(row)

    igbtvendors = json.loads(calc.igbt_vendor_details_json or "[]")

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        pd.DataFrame([maindata]).to_excel(writer, index=False, sheet_name="PVC Result")
        if sadata:
            pd.DataFrame(sadata).to_excel(writer, index=False, sheet_name="Scenario Amounts")
        if sddata:
            pd.DataFrame(sddata).to_excel(writer, index=False, sheet_name="Index Details")
        if igbtvendors:
            pd.DataFrame(igbtvendors).to_excel(writer, index=False, sheet_name="IGBT Vendors")

    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name=f"PVC_Calc_{calc.id}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )  # [file:1]


@pvc_bp.route("/gettender/<int:tenderid>")
@login_required
def get_tender(tenderid):
    tender = TenderMaster.query.get_or_404(tenderid)
    return jsonify(
        basicrate=tender.basicrate,
        freightrateperunit=tender.freightrateperunit,
        pvcbasedate=tender.pvcbasedate,
        lowerrate=tender.lowerrate,
        lowerfreight=tender.lowerfreight,
        lowerbasicdate=tender.lowerratebasedate,
        itemid=tender.item_id,
        tenderno=tender.tender_no,
    )  # [file:1]