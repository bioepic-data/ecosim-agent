"""Behavioral tests for energy_eval.py with synthetic EcoSIM history files and tower CSVs."""
from pathlib import Path
import importlib.util
import json
import tempfile
import unittest

import netCDF4 as nc
import numpy as np
import pandas as pd

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/energy_eval.py"
spec = importlib.util.spec_from_file_location("energy_eval", SCRIPT)
ee = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ee)


def write_history(path, start_day, nt, step, fields):
    """fields: name -> array (nt,) in EcoSIM output convention."""
    with nc.Dataset(path, "w") as d:
        d.createDimension("time", None)
        d.createDimension("column", 1)
        d.createDimension("hist_interval", 2)
        tb = d.createVariable("time_bounds", "f8", ("time", "hist_interval"))
        t0 = start_day + np.arange(nt) * step
        tb[:] = np.column_stack([t0, t0 + step])
        for k, v in fields.items():
            x = d.createVariable(k, "f4", ("time", "column"))
            x[:] = np.asarray(v, float)[:, None]


class EnergyEvalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def daily_case(self, with_out_fields=True):
        n = 365
        days = pd.date_range("2017-01-01", periods=n)
        season = np.clip(np.sin(2 * np.pi * (np.asarray(days.dayofyear) - 80) / 365), 0, None)
        sw_in = 100 + 200 * season
        lw_in = 300 + 60 * season
        # tower: albedo 0.2, LW_out 380+70s, Rn from components; turbulent fluxes close to 80% of Rn
        t_swout = 0.2 * sw_in
        t_lwout = 380 + 70 * season
        t_rn = sw_in - t_swout + lw_in - t_lwout
        t_le, t_h = 0.6 * 0.8 * t_rn, 0.4 * 0.8 * t_rn
        # model: same albedo, LW_out 10 W m-2 lower -> Rn +10; closes its balance; H too high
        m_swabs = 0.8 * sw_in
        m_lwout = t_lwout - 10
        m_rn = m_swabs + lw_in - m_lwout
        m_le, m_h = 0.5 * m_rn, 0.5 * m_rn
        fields = {"ECO_Radnet_col": m_rn, "ECO_LE_col": -m_le, "ECO_HeatS_col": -m_h, "SOL_RADN_col": sw_in,
                  "LW_Sky_col": lw_in, "ECO_RADSW_col": m_swabs,
                  "QTransp_col": -np.full(n, 2.0) / 24, "EVAPGrnd_col": -np.full(n, 1.0) / 24, "QVegEvap_col": -np.full(n, 0.0)}
        if with_out_fields:
            fields["ECO_SWOUT_col"] = sw_in - m_swabs
            fields["ECO_LWOUT_col"] = m_lwout
        write_history(self.dir / "case.ecosim.h0.2017-01-01-00000.nc", 0.0, n, 1.0, fields)
        tw = pd.DataFrame({"TIMESTAMP_START": days.strftime("%Y%m%d0000"), "NETRAD": t_rn, "LE_F_MDS": t_le, "H_F_MDS": t_h,
                           "SW_IN_F": sw_in, "SW_OUT": t_swout, "LW_IN_F": lw_in, "LW_OUT": t_lwout})
        tw.loc[5, "LE_F_MDS"] = -9999
        tw.to_csv(self.dir / "tower.csv", index=False)
        return m_rn, t_rn

    def run_cli(self, *extra):
        out = self.dir / "out"
        rc = ee.main(["--history", str(self.dir / "case.ecosim.h0.*.nc"), "--origin", "2017-01-01", "--tower", str(self.dir / "tower.csv"),
                      "--out", str(out), *extra])
        self.assertEqual(rc, 0)
        return out

    def test_signs_and_scores(self):
        self.daily_case()
        out = self.run_cli()
        s = pd.read_csv(out / "scores.csv").set_index(["target", "period"])
        self.assertAlmostEqual(s.loc[("Rn", "All"), "bias"], 10.0, places=3)
        self.assertGreater(s.loc[("H", "All"), "bias"], 0)          # model H positive-up and too high
        self.assertEqual(int(s.loc[("LE", "All"), "n"]), 364)       # -9999 masked

    def test_closure_and_h_plus_le(self):
        m_rn, t_rn = self.daily_case()
        out = self.run_cli()
        c = pd.read_csv(out / "closure.csv").set_index("period")
        self.assertAlmostEqual(c.loc["All", "tower_closure_ratio"], 0.8, places=4)
        self.assertAlmostEqual(c.loc["All", "model_residual"], 0.0, places=3)
        # H+LE bias = Rn bias + tower residual
        self.assertAlmostEqual(c.loc["All", "bias_H_plus_LE"], c.loc["All", "bias_Rn"] + c.loc["All", "tower_residual"], places=2)
        s = pd.read_csv(out / "scores.csv").set_index(["target", "period"])
        # closure-corrected LE+H biases sum to the Rn bias
        tot = s.loc[("LE_closure_corrected", "All"), "bias"] + s.loc[("H_closure_corrected", "All"), "bias"]
        self.assertAlmostEqual(tot, 10.0, delta=0.5)

    def test_partition_and_radiation(self):
        self.daily_case()
        out = self.run_cli()
        p = pd.read_csv(out / "partition.csv").set_index("period")
        self.assertAlmostEqual(p.loc["All", "transpiration"], 2.0, places=4)
        self.assertAlmostEqual(p.loc["All", "ground_evaporation"], 1.0, places=4)
        self.assertAlmostEqual(p.loc["All", "transpiration_fraction"], 2 / 3, places=4)
        r = pd.read_csv(out / "radiation.csv")
        a = r[(r.kind == "season") & (r.period == "All")].iloc[0]
        self.assertAlmostEqual(a.albedo_model, 0.2, places=4)
        self.assertAlmostEqual(a.albedo_tower, 0.2, places=4)
        self.assertAlmostEqual(a.dRn_from_LW_out, 10.0, places=3)
        self.assertAlmostEqual(a.dRn, a.dRn_from_SW_in + a.dRn_from_SW_out + a.dRn_from_LW_in + a.dRn_from_LW_out, places=3)
        self.assertLess(a.Trad_model, a.Trad_tower)
        self.assertLess(a.model_budget_error, 1e-3)

    def test_derived_outgoing_fields_warn(self):
        self.daily_case(with_out_fields=False)
        out = self.run_cli()
        summ = json.loads((out / "summary.json").read_text())
        self.assertTrue(any("ECO_SWOUT_col absent" in w for w in summ["warnings"]))
        r = pd.read_csv(out / "radiation.csv")
        self.assertAlmostEqual(r[(r.kind == "season") & (r.period == "All")].iloc[0].albedo_model, 0.2, places=4)

    def test_hourly_diurnal_and_night(self):
        n = 24 * 60
        idx = pd.date_range("2017-06-01", periods=n, freq="h")
        sun = np.round(np.clip(np.sin(np.pi * (np.asarray(idx.hour) - 6) / 12), 0, None), 9)
        sw = 800 * sun
        lw_in = np.full(n, 350.0)
        t_lwout = 420 + 60 * sun
        t_rn = sw * 0.8 + lw_in - t_lwout
        t_le = np.where(sw > 0, 0.6 * t_rn, 8.0)
        t_h = np.where(sw > 0, 0.2 * t_rn, -20.0)
        m_le = np.where(sw > 0, 0.5 * t_rn, 25.0)
        m_h = t_rn - m_le
        write_history(self.dir / "case.ecosim.h1.2017-06-01-00000.nc", 151.0, n, 1 / 24,
                      {"ECO_Radnet_col": t_rn, "ECO_LE_col": -m_le, "ECO_HeatS_col": -m_h, "SOL_RADN_col": sw, "LW_Sky_col": lw_in,
                       "ECO_RADSW_col": 0.8 * sw, "ECO_SWOUT_col": 0.2 * sw, "ECO_LWOUT_col": t_lwout,
                       "QTransp_col": -np.where(sw > 0, 0.3, 0.0), "EVAPGrnd_col": -np.full(n, 0.05), "QVegEvap_col": np.zeros(n)})
        pd.DataFrame({"TIMESTAMP_START": idx.strftime("%Y%m%d%H%M"), "NETRAD": t_rn, "LE_F_MDS": t_le, "H_F_MDS": t_h, "SW_IN_F": sw,
                      "SW_OUT": 0.2 * sw, "LW_IN_F": lw_in, "LW_OUT": t_lwout}).to_csv(self.dir / "tower.csv", index=False)
        out = self.dir / "out"
        ee.main(["--history", str(self.dir / "case.ecosim.h1.*.nc"), "--origin", "2017-01-01", "--tower", str(self.dir / "tower.csv"), "--out", str(out)])
        summ = json.loads((out / "summary.json").read_text())
        self.assertEqual(summ["resolution"], "hourly")
        self.assertAlmostEqual(summ["night_LE"]["LE_model"], 25.0, places=3)
        self.assertAlmostEqual(summ["night_LE"]["LE_tower"], 8.0, places=3)
        d = pd.read_csv(out / "diurnal.csv", index_col=0)
        self.assertEqual(len(d), 24)
        self.assertAlmostEqual(d.loc[12, "model_LW_out"], d.loc[12, "tower_LW_out"], places=3)


if __name__ == "__main__":
    unittest.main()
