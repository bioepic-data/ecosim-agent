#!/usr/bin/env python3
"""Evaluate EcoSIM surface energy fluxes (Rn, LE, H, radiation components, ET partition) against tower data.

Writes scores.csv, monthly.csv, partition.csv, radiation.csv, closure.csv, diurnal.csv (hourly only)
and summary.json to --out. Reports diagnostics; interpretation is left to the analyst (see SKILL.md).
"""
import argparse
import glob
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from netCDF4 import Dataset

LAMBDA = 2.45e6          # latent heat of vaporization, J kg-1
SIGMA = 5.670374419e-8   # Stefan-Boltzmann constant, W m-2 K-4
SEASONS = {"All": list(range(1, 13)), "DJF": [12, 1, 2], "MAM": [3, 4, 5], "JJA": [6, 7, 8], "SON": [9, 10, 11]}
TOWER_CANDIDATES = {
    "Rn": ["NETRAD"], "LE": ["LE_F_MDS", "LE"], "H": ["H_F_MDS", "H"], "G": ["G_F_MDS", "G"],
    "SW_in": ["SW_IN_F", "SW_IN_F_MDS", "SW_IN"], "SW_out": ["SW_OUT"],
    "LW_in": ["LW_IN_F", "LW_IN_F_MDS", "LW_IN"], "LW_out": ["LW_OUT"],
}
MODEL_FIELDS = ["ECO_Radnet_col", "ECO_LE_col", "ECO_HeatS_col", "SOL_RADN_col", "LW_Sky_col", "ECO_RADSW_col",
                "ECO_SWOUT_col", "ECO_LWOUT_col", "QTransp_col", "EVAPGrnd_col", "QVegEvap_col"]


def read_model(patterns, origin):
    """Return {'daily': df, 'hourly': df} of model quantities in positive-up / positive-out conventions."""
    files = sorted({f for p in patterns for f in glob.glob(p)})
    if not files:
        raise SystemExit(f"no history files match {patterns}")
    frames = {"daily": [], "hourly": []}
    found = set()
    for path in files:
        with Dataset(path) as d:
            if "time_bounds" not in d.variables:
                continue
            b = np.asarray(d["time_bounds"][:], dtype=float)
            step = float(np.median(b[:, 1] - b[:, 0]))
            res = "hourly" if step < 0.5 else "daily"
            idx = pd.Timestamp(origin) + pd.to_timedelta(np.round(b[:, 0] * 86400), unit="s")
            cols = {}
            for v in MODEL_FIELDS:
                if v in d.variables and d[v].dimensions[0] == "time" and d[v].ndim == 2:
                    cols[v] = np.ma.filled(np.ma.asarray(d[v][:, 0], dtype=float), np.nan)
                    found.add(v)
            if cols:
                frames[res].append(pd.DataFrame(cols, index=idx))
    out = {}
    for res, fr in frames.items():
        if not fr:
            continue
        raw = pd.concat(fr).sort_index()
        raw = raw[~raw.index.duplicated(keep="first")]
        out[res] = convert_model(raw)
    return out, sorted(found)


def convert_model(r):
    """Apply EcoSIM sign conventions; derive SW_out/LW_out when the build lacks them."""
    m = pd.DataFrame(index=r.index)
    g = lambda v: r[v] if v in r else pd.Series(np.nan, index=r.index)
    m["Rn"] = g("ECO_Radnet_col")
    m["LE"] = -g("ECO_LE_col")
    m["H"] = -g("ECO_HeatS_col")
    m["SW_in"] = g("SOL_RADN_col")
    m["LW_in"] = g("LW_Sky_col")
    m["SW_abs"] = g("ECO_RADSW_col")
    m["SW_out"] = g("ECO_SWOUT_col") if "ECO_SWOUT_col" in r else m.SW_in - m.SW_abs
    m["LW_out"] = g("ECO_LWOUT_col") if "ECO_LWOUT_col" in r else m.LW_in + m.SW_abs - m.Rn
    m["T"] = -24.0 * g("QTransp_col")          # mm d-1 rate
    m["E_ground"] = -24.0 * g("EVAPGrnd_col")
    m["E_canopy"] = -24.0 * g("QVegEvap_col")
    m["ET_from_LE"] = m.LE * 86400.0 / LAMBDA
    return m


def read_tower(path, time_col=None, overrides=None):
    head = pd.read_csv(path, nrows=0).columns.tolist()
    tcol = time_col or ("TIMESTAMP_START" if "TIMESTAMP_START" in head else head[0])
    t = pd.read_csv(path, dtype={tcol: str}).replace(-9999, np.nan)
    s = t.pop(tcol).astype(str)
    t.index = pd.to_datetime(s, format="%Y%m%d%H%M") if s.str.fullmatch(r"\d{12}").all() else pd.to_datetime(s)
    out = pd.DataFrame(index=t.index)
    used = {}
    for key, cands in TOWER_CANDIDATES.items():
        col = (overrides or {}).get(key) or next((c for c in cands if c in t.columns), None)
        if col is not None and col in t.columns:
            out[key] = pd.to_numeric(t[col], errors="coerce")
            used[key] = col
    return out.sort_index(), used


def to_daily(df, min_count):
    g = df.resample("D")
    mean, cnt = g.mean(), g.count()
    return mean.where(cnt >= min_count)


def stats(m, o):
    z = pd.concat([m.rename("m"), o.rename("o")], axis=1).dropna()
    if len(z) == 0:
        return dict(n=0, obs=np.nan, model=np.nan, bias=np.nan, rmse=np.nan, r=np.nan)
    e = z.m - z.o
    r = z.m.corr(z.o) if len(z) > 2 and z.m.std() > 0 and z.o.std() > 0 else np.nan
    return dict(n=int(len(z)), obs=z.o.mean(), model=z.m.mean(), bias=e.mean(), rmse=float(np.sqrt((e ** 2).mean())), r=r)


def season_mask(idx, months):
    return np.isin(idx.month, months)


def evaluate(model, tower, start, end, peak_months, emissivity, min_hours):
    warnings = []
    use_hourly = "hourly" in model and (tower.index.to_series().diff().median() <= pd.Timedelta(hours=1))
    if use_hourly:
        mh = model["hourly"].loc[start:end]
        th = tower.loc[start:end].reindex(mh.index)
        md = to_daily(mh, min_hours)
        td = to_daily(tower.loc[start:end], min_hours)
    else:
        if "daily" in model:
            md = model["daily"].loc[start:end]
        elif "hourly" in model:
            md = to_daily(model["hourly"].loc[start:end], min_hours)
        else:
            raise SystemExit("no model data")
        tstep = tower.index.to_series().diff().median()
        td = to_daily(tower.loc[start:end], min_hours) if tstep < pd.Timedelta(days=1) else tower.loc[start:end]
        mh = th = None
    td = td.reindex(md.index)
    res = "hourly" if use_hourly else "daily"
    C_m, C_t = (mh, th) if use_hourly else (md, td)

    # forcing check
    forcing = {}
    for k in ["SW_in", "LW_in"]:
        if k in C_t and C_m[k].notna().any():
            s = stats(C_m[k], C_t[k])
            forcing[k] = dict(model=s["model"], tower=s["obs"], rel_diff=(s["model"] - s["obs"]) / s["obs"] if s["obs"] else np.nan)
            if abs(forcing[k]["rel_diff"]) > 0.05:
                warnings.append(f"incoming {k} differs from tower by {100*forcing[k]['rel_diff']:.1f}%: check forcing before judging fluxes")

    # scores at evaluation resolution
    rows = []
    for k in ["Rn", "LE", "H", "SW_in", "SW_out", "LW_in", "LW_out"]:
        if k not in C_t:
            continue
        for p, months in SEASONS.items():
            mk = season_mask(C_m.index, months)
            rows.append(dict(target=k, period=p, resolution=res, **stats(C_m[k][mk], C_t[k][mk])))

    # closure on daily data
    avail = td.Rn - td.G if "G" in td and td.G.notna().any() else td.Rn
    if not ("G" in td and td.G.notna().any()):
        warnings.append("tower G missing: closure uses Rn only (acceptable for daily or longer means)")
    turb = td.LE + td.H
    ok = turb.abs() > 20
    scale = (avail / turb).where(ok)
    td = td.assign(LE_c=td.LE * scale, H_c=td.H * scale)
    clo = []
    for p, months in SEASONS.items():
        mk = season_mask(td.index, months)
        # common support: all of Rn, LE, H (and G when used) valid in both model and tower, so the
        # identity bias(H)+bias(LE) = bias(Rn) + tower_residual - model_residual holds exactly
        cm = mk & td[["Rn", "LE", "H"]].notna().all(axis=1) & md[["Rn", "LE", "H"]].notna().all(axis=1) & avail.notna()
        tot_turb, tot_avail = turb[cm].sum(), avail[cm].sum()
        ratio = tot_turb / tot_avail if cm.any() and tot_avail else np.nan
        sl = {k: (md[k][cm] - td[k][cm]).mean() for k in ["Rn", "LE", "H"]}
        clo.append(dict(period=p, n_days=int(cm.sum()), tower_closure_ratio=ratio, tower_residual=(avail[cm] - turb[cm]).mean(),
                        model_residual=(md.Rn[cm] - md.LE[cm] - md.H[cm]).mean(), bias_Rn=sl["Rn"], bias_LE=sl["LE"],
                        bias_H=sl["H"], bias_H_plus_LE=sl["LE"] + sl["H"]))
        for k in ["LE", "H"]:
            rows.append(dict(target=k + "_closure_corrected", period=p, resolution="daily", **stats(md[k][mk], td[k + "_c"][mk])))
    scores = pd.DataFrame(rows)
    closure = pd.DataFrame(clo)

    # ET partition (daily)
    part = []
    f = 86400.0 / LAMBDA
    for p, months in SEASONS.items():
        mk = season_mask(md.index, months)
        T, Eg, Ec = md["T"][mk].mean(), md.E_ground[mk].mean(), md.E_canopy[mk].mean()
        comp = np.nansum([T, Eg, Ec])
        part.append(dict(period=p, transpiration=T, ground_evaporation=Eg, canopy_evaporation=Ec, ET_components=comp,
                         ET_model_from_LE=md.ET_from_LE[mk].mean(), ET_tower=(td.LE[mk] * f).mean(),
                         ET_tower_closure_corrected=(td.LE_c[mk] * f).mean(),
                         transpiration_fraction=T / comp if comp else np.nan))
    partition = pd.DataFrame(part)

    # radiation on common valid times at evaluation resolution
    rad = []
    need = [k for k in ["SW_in", "SW_out", "LW_in", "LW_out", "Rn"] if k in C_t]
    if set(["SW_in", "SW_out", "LW_in", "LW_out", "Rn"]).issubset(need):
        valid = C_t[need].notna().all(axis=1) & C_m[need].notna().all(axis=1)
        if use_hourly:
            day = valid & (C_t.SW_in > 20)
        else:
            day = valid
        trad = lambda lw: (lw.clip(lower=1) / (emissivity * SIGMA)) ** 0.25 - 273.15
        groups = [("season", p, season_mask(C_m.index, mo)) for p, mo in SEASONS.items()] + \
                 [("month", m_, C_m.index.month == m_) for m_ in range(1, 13)]
        for kind, label, mk in groups:
            v, dv = valid & mk, day & mk
            if v.sum() == 0:
                continue
            a_m = C_m.SW_out[dv].sum() / C_m.SW_in[dv].sum() if C_m.SW_in[dv].sum() else np.nan
            a_t = C_t.SW_out[dv].sum() / C_t.SW_in[dv].sum() if C_t.SW_in[dv].sum() else np.nan
            d = {k: (C_m[k][v] - C_t[k][v]).mean() for k in need}
            rad.append(dict(kind=kind, period=label, n=int(v.sum()), albedo_model=a_m, albedo_tower=a_t,
                            LW_out_model=C_m.LW_out[v].mean(), LW_out_tower=C_t.LW_out[v].mean(),
                            Trad_model=trad(C_m.LW_out[v]).mean(), Trad_tower=trad(C_t.LW_out[v]).mean(),
                            dRn=d["Rn"], dRn_from_SW_in=d["SW_in"], dRn_from_SW_out=-d["SW_out"],
                            dRn_from_LW_in=d["LW_in"], dRn_from_LW_out=-d["LW_out"],
                            model_budget_error=(C_m.SW_in[v] - C_m.SW_out[v] + C_m.LW_in[v] - C_m.LW_out[v] - C_m.Rn[v]).abs().max()))
        if not rad:
            warnings.append("no common valid times for all radiation components")
    else:
        warnings.append(f"tower radiation components missing ({sorted(set(['SW_in','SW_out','LW_in','LW_out','Rn'])-set(need))}): radiation decomposition skipped")
    radiation = pd.DataFrame(rad)

    # monthly means (daily data)
    mon = []
    for m_ in range(1, 13):
        mk = md.index.month == m_
        row = dict(month=m_)
        for k in ["Rn", "LE", "H", "SW_out", "LW_out"]:
            row["model_" + k] = md[k][mk].mean()
            row["tower_" + k] = td[k][mk].mean() if k in td else np.nan
        row.update(model_T=md["T"][mk].mean(), model_E_ground=md.E_ground[mk].mean(), model_E_canopy=md.E_canopy[mk].mean())
        mon.append(row)
    monthly = pd.DataFrame(mon)

    # diurnal and night LE (hourly only)
    diurnal = None
    night = None
    if use_hourly:
        pk = season_mask(mh.index, peak_months)
        cols = {}
        for k in ["Rn", "LE", "H", "LW_out"]:
            if k in th:
                both = mh[k].notna() & th[k].notna() & pk
                cols["model_" + k] = mh[k][both].groupby(mh.index[both].hour).mean()
                cols["tower_" + k] = th[k][both].groupby(th.index[both].hour).mean()
        diurnal = pd.DataFrame(cols)
        if "SW_in" in th:
            nt = (th.SW_in < 5) & mh.LE.notna() & th.LE.notna()
            night = dict(hours=int(nt.sum()), LE_model=float(mh.LE[nt].mean()), LE_tower=float(th.LE[nt].mean()),
                         peak_hours=int((nt & pk).sum()), LE_model_peak=float(mh.LE[nt & pk].mean()), LE_tower_peak=float(th.LE[nt & pk].mean()))
    return dict(resolution=res, scores=scores, closure=closure, partition=partition, radiation=radiation, monthly=monthly,
                diurnal=diurnal, night=night, forcing=forcing, warnings=warnings)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--history", action="append", required=True, help="glob of EcoSIM history files (repeatable)")
    ap.add_argument("--origin", required=True, help="history time origin (run start date), e.g. 2013-01-01")
    ap.add_argument("--tower", required=True, help="tower CSV (FLUXNET/AmeriFlux names, daily or hourly)")
    ap.add_argument("--time-col", help="tower time column (default TIMESTAMP_START or first column)")
    ap.add_argument("--map", action="append", default=[], help="override tower column, e.g. LE=LE_CORR")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--peak-months", default="6,7,8")
    ap.add_argument("--emissivity", type=float, default=0.98)
    ap.add_argument("--min-hours", type=int, default=20, help="valid hours needed to form a daily mean")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    overrides = dict(x.split("=", 1) for x in a.map)
    model, found = read_model(a.history, a.origin)
    tower, used = read_tower(a.tower, a.time_col, overrides)
    R = evaluate(model, tower, a.start, a.end, [int(x) for x in a.peak_months.split(",")], a.emissivity, a.min_hours)
    if "ECO_SWOUT_col" not in found and "ECO_RADSW_col" in found:
        R["warnings"].append("ECO_SWOUT_col absent: SW_out derived from ECO_RADSW_col, which before EcoSIM 626be305 undercounts "
                             "litter absorption; albedo is invalid where litter is exposed")
    if "ECO_LWOUT_col" not in found:
        R["warnings"].append("ECO_LWOUT_col absent: LW_out derived as LW_in + SW_abs - Rn (inherits any SW_abs error)")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for k in ["scores", "closure", "partition", "radiation", "monthly"]:
        R[k].to_csv(out / f"{k}.csv", index=False)
    if R["diurnal"] is not None:
        R["diurnal"].to_csv(out / "diurnal.csv", index_label="hour")
    pick = lambda df, t, p: df[(df.target == t) & (df.period == p)].iloc[0].to_dict() if len(df[(df.target == t) & (df.period == p)]) else None
    summary = dict(resolution=R["resolution"], model_fields_found=found, tower_columns_used=used, forcing_check=R["forcing"],
                   peak={k: pick(R["scores"], k, "JJA") for k in ["Rn", "LE", "H", "LE_closure_corrected", "H_closure_corrected"]},
                   closure=R["closure"].to_dict("records"), partition=R["partition"].to_dict("records"),
                   radiation_seasonal=R["radiation"][R["radiation"].kind == "season"].to_dict("records") if len(R["radiation"]) else [],
                   night_LE=R["night"], warnings=R["warnings"])
    (out / "summary.json").write_text(json.dumps(summary, indent=1, default=lambda x: None if pd.isna(x) else float(x)))
    print(f"wrote {out}; resolution {R['resolution']}; {len(R['warnings'])} warnings")
    for w in R["warnings"]:
        print("warning:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
