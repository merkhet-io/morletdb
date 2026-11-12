"""IU4 features of arm C, its prediction, and a decision's evidence (PROTOCOL.md, Implementation)."""
import math

SIGNALS = ("force_sensor_x", "force_sensor_y", "force_sensor_z", "torque_spindle", "torque_axis_x", "torque_axis_y",
           "torque_axis_z", "position_control_deviation_axis_x", "position_control_deviation_axis_y")
CONTROLLER = SIGNALS[3:]
ARMS = {"full": SIGNALS, "controller": CONTROLLER}
VALUES = ("std", "rms", "p75", "peak_abs", "crest", "kurtosis")


def _f(x):
    return 0.0 if x is None or not math.isfinite(x) else float(x)


def feature_dict(states, arm):
    """Per signal of the arm and value: own-baseline relative deviation (ob) and history quantile (hq); per signal
    the summary score. Missing values are 0."""
    fd = {}
    for sig in ARMS[arm]:
        st = states[sig]
        hist = {v["name"]: v for v in st["values"]}
        own = {v["name"]: v for v in (st.get("own_baseline") or {}).get("values", [])}
        for name in VALUES:
            fd[f"{sig}|ob|{name}"] = _f(own.get(name, {}).get("relative_deviation"))
            fd[f"{sig}|hq|{name}"] = _f(hist.get(name, {}).get("history_quantile"))
        fd[f"{sig}|summary"] = _f((st.get("summary") or {}).get("score"))
    return fd


def predict(model, fd):
    """Logistic regression with standardised features; returns the probability and each feature's contribution."""
    z, contrib = model["intercept"], {}
    for name, coef, mu, sd in zip(model["names"], model["coef"], model["mean"], model["sd"]):
        c = coef * ((fd.get(name, 0.0) - mu) / sd if sd > 0 else 0.0)
        contrib[name] = c
        z += c
    return 1.0 / (1.0 + math.exp(-z)), contrib


def evidence(contrib, states, ex):
    """PROTOCOL H3: the run's segments, the three strongest drivers, the nearest earlier cases of the same tool, and
    labelled precedents from other tools (decision_state examples across the process)."""
    drivers = sorted(contrib.items(), key=lambda kv: -abs(kv[1]))[:3]
    top_sig = drivers[0][0].split("|")[0]
    return {"segments": [{"signal": s, "recording_id": st["segment"]["recording_id"],
                          "start_sample": st["segment"]["start_sample"], "end_sample": st["segment"]["end_sample"]}
                         for s, st in states.items()],
            "drivers": [{"feature": k, "contribution": v} for k, v in drivers],
            "nearest_same_tool": states[top_sig].get("neighbors", []),
            "precedents": ex.get("examples", []), "precedents_note": ex.get("examples_note")}
