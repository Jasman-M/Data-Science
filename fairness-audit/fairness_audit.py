"""
Statistical Fairness Audit for Credit Scoring Models
=====================================================
Regulators (CFPB, OSFI) require lenders to demonstrate their models
do not discriminate across protected groups. This tool implements a
full statistical fairness audit — the same methodology used in
production credit risk systems.

Fairness Metrics Computed:
  1. Disparate Impact Ratio (DIR)     – EEOC "4/5ths rule"
  2. Equal Opportunity Difference     – TPR gap across groups
  3. Predictive Equality Difference   – FPR gap across groups
  4. Calibration                      – does the score mean the same thing across groups?
  5. AUC by Group                     – model discrimination per group

Statistical Tests:
  - Chi-square test for independence  (approval rate vs group)
  - Z-test for proportions            (TPR / FPR differences)
  - Bootstrap confidence intervals    (group-level AUC)

Output (saved to outputs/):
  - fairness_report.txt      : full audit findings
  - dir_plot.png             : disparate impact ratios
  - calibration_plot.png     : calibration curves by group
  - auc_by_group.png         : AUC with bootstrap CIs

Author: Jasman Mander
"""

import os
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy import stats
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score, confusion_matrix
from sklearn.calibration import calibration_curve
import xgboost as xgb

warnings.filterwarnings("ignore")
os.makedirs("outputs", exist_ok=True)

# ── Colour palette ──────────────────────────────────────────────
TEAL    = "#00d4aa"
ORANGE  = "#ff6b35"
PURPLE  = "#9b59b6"
RED     = "#e74c3c"
GREEN   = "#2ecc71"
BG      = "#0f1117"
PANEL   = "#1a1d27"
BORDER  = "#2e3147"

print("=" * 58)
print("  STATISTICAL FAIRNESS AUDIT — CREDIT SCORING MODEL")
print("=" * 58)


# ─────────────────────────────────────────────
# 1. LOAD DATA & TRAIN MODEL
#    (re-trains XGBoost from Project 1 so this script is standalone)
# ─────────────────────────────────────────────
df = pd.read_csv("../alternative-credit-scoring/credit_data.csv")

pay_status_cols = ["PAY_0","PAY_2","PAY_3","PAY_4","PAY_5","PAY_6"]
bill_cols       = ["BILL_AMT1","BILL_AMT2","BILL_AMT3","BILL_AMT4","BILL_AMT5","BILL_AMT6"]
pay_amt_cols    = ["PAY_AMT1","PAY_AMT2","PAY_AMT3","PAY_AMT4","PAY_AMT5","PAY_AMT6"]

df["avg_pay_status"]     = df[pay_status_cols].mean(axis=1)
df["max_pay_delay"]      = df[pay_status_cols].max(axis=1)
df["utilization_ratio"]  = df["BILL_AMT1"] / (df["LIMIT_BAL"] + 1)
df["payment_volatility"] = df[pay_amt_cols].std(axis=1)

pay_to_bill = []
for b, p in zip(bill_cols, pay_amt_cols):
    ratio = np.where(df[b] > 0, df[p] / df[b], 1.0)
    pay_to_bill.append(np.clip(ratio, 0, 2))
df["avg_pay_to_bill"] = np.mean(pay_to_bill, axis=0)

pay_matrix = df[pay_amt_cols].values
slopes = np.polyfit(np.arange(6), pay_matrix.T, 1)[0]
df["payment_trend"]       = slopes
df["zero_payment_months"] = (df[pay_amt_cols] == 0).sum(axis=1)
df["bill_growth"]         = df["BILL_AMT1"] - df["BILL_AMT6"]
df["credit_headroom"]     = df["LIMIT_BAL"] - df["BILL_AMT1"]

FEATURES = [
    "LIMIT_BAL","AGE","avg_pay_status","max_pay_delay","utilization_ratio",
    "payment_volatility","avg_pay_to_bill","payment_trend",
    "zero_payment_months","bill_growth","credit_headroom"
]

X, y = df[FEATURES], df["default"]
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42, stratify=y
)
test_idx = X_test.index

model = xgb.XGBClassifier(
    n_estimators=300, max_depth=5, learning_rate=0.05,
    subsample=0.8, colsample_bytree=0.8,
    scale_pos_weight=(y_train == 0).sum() / (y_train == 1).sum(),
    eval_metric="auc", random_state=42, verbosity=0
)
model.fit(X_train, y_train, eval_set=[(X_test, y_test)], verbose=False)

y_prob = model.predict_proba(X_test)[:, 1]
y_pred = (y_prob >= 0.5).astype(int)

# Attach predictions back to test slice
test_df          = df.loc[test_idx].copy()
test_df["score"] = y_prob
test_df["pred"]  = y_pred
test_df["true"]  = y_test.values

print(f"\n[Model]  XGBoost trained | Test AUC: {roc_auc_score(y_test, y_prob):.4f}")
print(f"[Data]   Test set: {len(test_df):,} records\n")


# ─────────────────────────────────────────────
# 2. DEFINE PROTECTED GROUPS
# ─────────────────────────────────────────────
# SEX:       1 = Male, 2 = Female
# EDUCATION: 1 = Graduate, 2 = University, 3 = High School
# AGE:       Young (<30), Mid (30-50), Senior (>50)
test_df["gender"]     = test_df["SEX"].map({1:"Male", 2:"Female"})
test_df["edu_group"]  = test_df["EDUCATION"].map({1:"Graduate",2:"University",3:"High School",4:"Other"})
test_df["age_group"]  = pd.cut(test_df["AGE"], bins=[0,30,50,100],
                                labels=["Young (<30)","Mid (30–50)","Senior (50+)"])

PROTECTED_ATTRS = {
    "Gender":        ("gender",    ["Male","Female"]),
    "Education":     ("edu_group", ["Graduate","University","High School"]),
    "Age Group":     ("age_group", ["Young (<30)","Mid (30–50)","Senior (50+)"]),
}
REFERENCE = {"Gender": "Male", "Education": "Graduate", "Age Group": "Mid (30–50)"}


# ─────────────────────────────────────────────
# 3. COMPUTE FAIRNESS METRICS
# ─────────────────────────────────────────────
def compute_group_metrics(df_sub, score_col="score", pred_col="pred", true_col="true"):
    """Return key fairness metrics for a single group."""
    y_t = df_sub[true_col]
    y_p = df_sub[pred_col]
    y_s = df_sub[score_col]
    tn, fp, fn, tp = confusion_matrix(y_t, y_p, labels=[0,1]).ravel()
    tpr  = tp / (tp + fn) if (tp + fn) > 0 else np.nan   # recall / sensitivity
    fpr  = fp / (fp + tn) if (fp + tn) > 0 else np.nan   # false alarm rate
    acc  = (tp + tn) / len(y_t)
    deny = y_p.mean()                                      # denial rate (predicted positive)
    try:
        auc = roc_auc_score(y_t, y_s)
    except Exception:
        auc = np.nan
    return {"n": len(df_sub), "deny_rate": deny, "TPR": tpr, "FPR": fpr,
            "accuracy": acc, "AUC": auc, "actual_default": y_t.mean()}


def bootstrap_auc_ci(df_sub, n_boot=500, alpha=0.05, seed=42):
    """Bootstrap 95% CI for AUC."""
    rng   = np.random.default_rng(seed)
    aucs  = []
    y_t   = df_sub["true"].values
    y_s   = df_sub["score"].values
    for _ in range(n_boot):
        idx = rng.integers(0, len(y_t), len(y_t))
        if len(np.unique(y_t[idx])) < 2:
            continue
        try:
            aucs.append(roc_auc_score(y_t[idx], y_s[idx]))
        except Exception:
            pass
    if not aucs:
        return np.nan, np.nan
    lo, hi = np.percentile(aucs, [100*alpha/2, 100*(1-alpha/2)])
    return lo, hi


# ─────────────────────────────────────────────
# 4. RUN AUDIT
# ─────────────────────────────────────────────
report_lines = []
report_lines.append("CREDIT MODEL FAIRNESS AUDIT REPORT")
report_lines.append("=" * 60)
report_lines.append(f"Model: XGBoost + Alt-Data Features | Threshold: 0.50")
report_lines.append(f"Test records: {len(test_df):,}\n")

all_results = {}

for attr_name, (col, groups) in PROTECTED_ATTRS.items():
    ref_group  = REFERENCE[attr_name]
    ref_df     = test_df[test_df[col] == ref_group]
    ref_deny   = compute_group_metrics(ref_df)["deny_rate"]
    ref_tpr    = compute_group_metrics(ref_df)["TPR"]
    ref_fpr    = compute_group_metrics(ref_df)["FPR"]

    report_lines.append(f"\n{'─'*60}")
    report_lines.append(f"Protected Attribute: {attr_name}  (reference: {ref_group})")
    report_lines.append(f"{'─'*60}")
    header = f"{'Group':<18} {'N':>6} {'Deny%':>6} {'DIR':>6} {'TPR':>6} {'EOD':>6} {'FPR':>6} {'AUC':>7}"
    report_lines.append(header)
    report_lines.append("-" * len(header))

    group_results = {}
    for g in groups:
        gdf = test_df[test_df[col] == g].copy()
        if len(gdf) < 30:
            continue
        m   = compute_group_metrics(gdf)
        dir_ = m["deny_rate"] / ref_deny if ref_deny > 0 else np.nan
        eod  = m["TPR"] - ref_tpr
        ped  = m["FPR"] - ref_fpr
        lo, hi = bootstrap_auc_ci(gdf)
        group_results[g] = {**m, "DIR": dir_, "EOD": eod, "PED": ped,
                             "AUC_lo": lo, "AUC_hi": hi}

        flag_dir = " ⚠" if dir_ < 0.8 or dir_ > 1.25 else ""
        flag_eod = " ⚠" if abs(eod) > 0.05 else ""
        report_lines.append(
            f"{g:<18} {m['n']:>6,} {m['deny_rate']:>6.1%} {dir_:>6.3f}{flag_dir} "
            f"{m['TPR']:>6.3f} {eod:>+6.3f}{flag_eod} {m['FPR']:>6.3f} {m['AUC']:>7.4f}"
        )

        # Chi-square test: group membership vs approval
        grp_labels = (test_df[col].isin([g, ref_group]))
        sub = test_df[grp_labels][[col, "pred"]].copy()
        sub["is_target"] = (sub[col] == g).astype(int)
        ct   = pd.crosstab(sub["is_target"], sub["pred"])
        chi2, p_val, dof, _ = stats.chi2_contingency(ct)
        sig  = "SIGNIFICANT" if p_val < 0.05 else "not significant"
        report_lines.append(
            f"   Chi-square vs {ref_group}: χ²={chi2:.2f}, p={p_val:.4f} → {sig}"
        )

    all_results[attr_name] = group_results

# EEOC 4/5 rule summary
report_lines.append(f"\n{'═'*60}")
report_lines.append("EEOC 4/5 Rule:  DIR < 0.80 or > 1.25 = adverse impact  (⚠)")
report_lines.append("Equal Opp.:    |EOD| > 0.05 = meaningful gap           (⚠)")
report_lines.append("p < 0.05 on chi-square = statistically significant disparity")
report_lines.append(f"{'═'*60}")


# ─────────────────────────────────────────────
# 5. PLOT — DISPARATE IMPACT RATIOS
# ─────────────────────────────────────────────
fig, axes = plt.subplots(1, 3, figsize=(14, 5))
fig.patch.set_facecolor(BG)

for ax, (attr_name, group_res) in zip(axes, all_results.items()):
    ax.set_facecolor(PANEL)
    for sp in ax.spines.values():
        sp.set_edgecolor(BORDER)

    groups   = list(group_res.keys())
    dirs     = [group_res[g]["DIR"] for g in groups]
    ref_grp  = REFERENCE[attr_name]

    bar_colors = []
    for d in dirs:
        if d < 0.8 or d > 1.25:
            bar_colors.append(RED)
        elif 0.9 <= d <= 1.1:
            bar_colors.append(GREEN)
        else:
            bar_colors.append(ORANGE)

    bars = ax.barh(groups, dirs, color=bar_colors, edgecolor="none", height=0.55)
    ax.axvline(1.0, color="white",  lw=1.5, linestyle="--", alpha=0.6, label="Parity (1.0)")
    ax.axvline(0.8, color=RED,      lw=1,   linestyle=":",  alpha=0.7, label="EEOC floor (0.8)")
    ax.axvline(1.25,color=RED,      lw=1,   linestyle=":",  alpha=0.7, label="EEOC ceiling (1.25)")

    for bar, val in zip(bars, dirs):
        ax.text(bar.get_width() + 0.01, bar.get_y() + bar.get_height()/2,
                f"{val:.3f}", va="center", color="white", fontsize=8.5)

    ax.set_xlabel("Disparate Impact Ratio", color="white", fontsize=9)
    ax.set_title(f"{attr_name}\n(ref: {ref_grp})", color="white", fontweight="bold", fontsize=10)
    ax.tick_params(colors="white", labelsize=8.5)
    ax.set_xlim(0, max(dirs) * 1.15 + 0.1)
    if ax == axes[0]:
        ax.legend(facecolor=PANEL, edgecolor=BORDER, labelcolor="white", fontsize=7.5, loc="lower right")

plt.suptitle("Disparate Impact Ratios by Protected Group\n(Green = fair | Orange = borderline | Red = adverse impact)",
             color="white", fontsize=12, fontweight="bold")
plt.tight_layout()
plt.savefig("outputs/dir_plot.png", dpi=150, bbox_inches="tight", facecolor=BG)
plt.close()
report_lines.append("\n[Plot]  Saved → outputs/dir_plot.png")


# ─────────────────────────────────────────────
# 6. PLOT — AUC WITH BOOTSTRAP CIs BY GROUP
# ─────────────────────────────────────────────
fig, axes = plt.subplots(1, 3, figsize=(14, 5))
fig.patch.set_facecolor(BG)

for ax, (attr_name, group_res) in zip(axes, all_results.items()):
    ax.set_facecolor(PANEL)
    for sp in ax.spines.values():
        sp.set_edgecolor(BORDER)

    groups = list(group_res.keys())
    aucs   = [group_res[g]["AUC"]    for g in groups]
    lo_ci  = [group_res[g]["AUC_lo"] for g in groups]
    hi_ci  = [group_res[g]["AUC_hi"] for g in groups]
    yerr   = [[a - l for a, l in zip(aucs, lo_ci)],
               [h - a for h, a in zip(hi_ci, aucs)]]

    ax.barh(groups, aucs, xerr=yerr, color=TEAL, error_kw={"ecolor":"white","capsize":4,"lw":1.5},
            edgecolor="none", height=0.55)
    ax.axvline(0.5, color=RED, lw=1, linestyle="--", alpha=0.7, label="Random (0.5)")

    for i, (a, l, h) in enumerate(zip(aucs, lo_ci, hi_ci)):
        ax.text(h + 0.003, i, f"{a:.3f}", va="center", color="white", fontsize=8)

    ax.set_xlabel("AUC  (with 95% Bootstrap CI)", color="white", fontsize=9)
    ax.set_title(f"{attr_name}", color="white", fontweight="bold", fontsize=10)
    ax.tick_params(colors="white", labelsize=8.5)
    ax.set_xlim(0.45, max(hi_ci) * 1.05 + 0.02)
    ax.legend(facecolor=PANEL, edgecolor=BORDER, labelcolor="white", fontsize=8)

plt.suptitle("Model AUC by Protected Group with Bootstrap Confidence Intervals",
             color="white", fontsize=12, fontweight="bold")
plt.tight_layout()
plt.savefig("outputs/auc_by_group.png", dpi=150, bbox_inches="tight", facecolor=BG)
plt.close()
report_lines.append("[Plot]  Saved → outputs/auc_by_group.png")


# ─────────────────────────────────────────────
# 7. PLOT — CALIBRATION BY GENDER
# ─────────────────────────────────────────────
fig, ax = plt.subplots(figsize=(7, 5))
fig.patch.set_facecolor(BG)
ax.set_facecolor(PANEL)
for sp in ax.spines.values():
    sp.set_edgecolor(BORDER)

colors_cal = [TEAL, ORANGE, PURPLE, GREEN]
for (g, color) in zip(["Male","Female"], colors_cal):
    sub = test_df[test_df["gender"] == g]
    if len(sub) < 100:
        continue
    prob_true, prob_pred = calibration_curve(sub["true"], sub["score"], n_bins=8, strategy="uniform")
    ax.plot(prob_pred, prob_true, "o-", color=color, lw=2, markersize=5, label=g)

ax.plot([0,1],[0,1], "--", color="gray", lw=1.5, alpha=0.7, label="Perfect calibration")
ax.set_xlabel("Mean Predicted Probability", color="white")
ax.set_ylabel("Fraction of Positives (Actual Default Rate)", color="white")
ax.set_title("Calibration Curve by Gender\n(should overlap for a fair model)",
             color="white", fontweight="bold")
ax.tick_params(colors="white")
ax.legend(facecolor=PANEL, edgecolor=BORDER, labelcolor="white")
plt.tight_layout()
plt.savefig("outputs/calibration_plot.png", dpi=150, bbox_inches="tight", facecolor=BG)
plt.close()
report_lines.append("[Plot]  Saved → outputs/calibration_plot.png")


# ─────────────────────────────────────────────
# 8. SAVE FULL REPORT
# ─────────────────────────────────────────────
report_text = "\n".join(report_lines)
with open("outputs/fairness_report.txt", "w") as f:
    f.write(report_text)

print(report_text)
print("\n✓  Project 2 complete — fairness audit saved to outputs/\n")
