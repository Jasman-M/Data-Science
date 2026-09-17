# ============================================================
#  Loan Default Survival Analysis
# ============================================================
#  Traditional ML asks: "Will this borrower default?" (binary)
#  Survival analysis asks: "WHEN will they default?" — a far
#  richer question for lenders: it drives provisioning, early
#  intervention, and portfolio stress-testing.
#
#  Methods used:
#    1. Kaplan-Meier estimator          — non-parametric survival curves
#    2. Log-rank test                   — statistical group comparison
#    3. Cox Proportional Hazards model  — multivariate regression
#    4. Schoenfeld residuals            — PH assumption diagnostics
#
#  Data: Synthetic loan cohort (5,000 borrowers, 24-month follow-up)
#
#  Outputs (saved to outputs/):
#    km_overall.png        — overall survival curve with CI
#    km_by_risk_group.png  — KM curves stratified by risk tier
#    km_by_education.png   — KM curves by education level
#    cox_forest.png        — Cox hazard ratio forest plot
#    schoenfeld.png        — PH assumption diagnostic plots
#    survival_report.txt   — full model summary
#
#  Author: Jasman Mander
#  Packages: survival (base R)
# ============================================================

library(survival)

dir.create("outputs", showWarnings = FALSE)

cat(strrep("=", 56), "\n")
cat("  LOAN DEFAULT SURVIVAL ANALYSIS\n")
cat(strrep("=", 56), "\n\n")


# ──────────────────────────────────────────────────────────
# 1.  GENERATE SYNTHETIC LOAN COHORT
# ──────────────────────────────────────────────────────────
set.seed(42)
n <- 5000

# Composite risk score (0–1, higher = riskier)
risk_score  <- rbeta(n, shape1 = 2, shape2 = 4)

# Education level
education   <- sample(c("Graduate","University","High School"), n,
                      replace = TRUE, prob = c(0.35, 0.47, 0.18))
education   <- factor(education, levels = c("Graduate","University","High School"))

# Credit limit — negatively correlated with risk
limit_bal   <- ifelse(risk_score < 0.3,
                 sample(c(100000,150000,200000), n, replace = TRUE),
                 ifelse(risk_score < 0.6,
                   sample(c(30000, 50000, 80000), n, replace = TRUE),
                   sample(c(10000, 20000), n, replace = TRUE)))
limit_scaled <- limit_bal / 10000

# Weibull time-to-default: higher risk → shorter time
scale_param <- exp(3.5 - 2.5*risk_score
                       - 0.2*(education == "High School")
                       + 0.1*(education == "Graduate"))
time_to_def <- pmax(rweibull(n, shape = 1.4, scale = scale_param), 1)

# Observation window: 24 months
obs_time <- pmin(time_to_def, 24)
event    <- as.integer(time_to_def <= 24)

# Risk tiers (tercile split)
risk_tier <- cut(risk_score,
                 breaks = quantile(risk_score, probs = c(0, 1/3, 2/3, 1)),
                 labels = c("Low Risk","Medium Risk","High Risk"),
                 include.lowest = TRUE)

loan_df <- data.frame(time = obs_time, event = event, risk_score = risk_score,
                      risk_tier = risk_tier, education = education,
                      limit_scaled = limit_scaled)

cat(sprintf("[Data]   %d borrowers | 24-month follow-up\n", n))
cat(sprintf("         Overall default rate:       %.1f%%\n", mean(event)*100))
cat(sprintf("         Median observation time:    %.1f months\n\n", median(obs_time)))


# ──────────────────────────────────────────────────────────
# COLOUR PALETTE (dark theme)
# ──────────────────────────────────────────────────────────
BG     <- "#0f1117"
PANEL  <- "#1a1d27"
GRID   <- "#2e3147"
TEAL   <- "#00d4aa"
ORANGE <- "#ff6b35"
RED    <- "#e74c3c"
PURPLE <- "#9b59b6"

dark_theme <- function() {
  par(bg = BG, col.axis = "white", col.lab = "white", col.main = "white",
      col.sub = "gray70", fg = "white",
      mar = c(4.5, 4.5, 3.5, 1.5))
}


# ──────────────────────────────────────────────────────────
# 2.  KAPLAN-MEIER — OVERALL
# ──────────────────────────────────────────────────────────
cat("[Analysis]  Kaplan-Meier: overall survival\n")
km_overall <- survfit(Surv(time, event) ~ 1, data = loan_df)
km_at      <- summary(km_overall, times = c(3,6,12,18,24))

cat("\n  Survival probabilities (% NOT defaulted):\n")
cat(sprintf("  Month  Survival%%\n"))
cat(paste(rep("-", 22), collapse=""), "\n")
for (i in seq_along(km_at$time))
  cat(sprintf("  %-6d  %.1f%%\n", km_at$time[i], km_at$surv[i]*100))

# Plot
png("outputs/km_overall.png", width = 800, height = 500, bg = BG)
dark_theme()
par(mar = c(4.5, 4.8, 4, 2))

# Background panel
plot(km_overall, fun = "event",            # plot cumulative incidence (1-S)
     xlab = "Months Since Loan Origination",
     ylab = "Cumulative Default Probability",
     main = "Kaplan-Meier: Cumulative Default Curve",
     conf.int = TRUE, col = TEAL, lwd = 2.2,
     conf.int.col = TEAL, conf.int.style = "bands",
     xlim = c(0,24), ylim = c(0,1),
     cex.axis = 0.95, cex.lab = 1.05, cex.main = 1.15)

grid(nx = NA, ny = 6, col = GRID, lty = 1)
abline(h = 0.5, lty = 2, col = ORANGE, lwd = 1.3)
text(1, 0.53, "50% default mark", col = ORANGE, adj = 0, cex = 0.85)

# Re-draw curve on top of grid
lines(km_overall, fun = "event", col = TEAL, lwd = 2.2,
      conf.int = TRUE, conf.int.col = adjustcolor(TEAL, alpha.f=0.25),
      conf.int.style = "bands")

mtext("Shaded band = 95% confidence interval", side = 3, line = 0.1,
      col = "gray70", cex = 0.82)
dev.off()
cat("[Plot]   Saved → outputs/km_overall.png\n")


# ──────────────────────────────────────────────────────────
# 3.  KM BY RISK TIER + LOG-RANK TEST
# ──────────────────────────────────────────────────────────
cat("\n[Analysis]  Kaplan-Meier: survival by risk tier\n")
km_risk <- survfit(Surv(time, event) ~ risk_tier, data = loan_df)
lr_risk  <- survdiff(Surv(time, event) ~ risk_tier, data = loan_df)
p_lr     <- pchisq(lr_risk$chisq, df = length(lr_risk$n)-1, lower.tail = FALSE)
cat(sprintf("  Log-rank test:  χ²=%.2f, p%s\n", lr_risk$chisq,
            ifelse(p_lr < 0.001, " < 0.001", sprintf(" = %.4f", p_lr))))

tier_cols <- c("Low Risk" = TEAL, "Medium Risk" = ORANGE, "High Risk" = RED)

png("outputs/km_by_risk_group.png", width = 860, height = 520, bg = BG)
dark_theme()
par(mar = c(4.5, 4.8, 4.5, 2))

plot(km_risk, fun = "event",
     xlab = "Months Since Loan Origination",
     ylab = "Cumulative Default Probability",
     main = "Survival Curves by Credit Risk Tier",
     col = tier_cols[levels(loan_df$risk_tier)],
     lwd = 2.2, conf.int = FALSE,
     xlim = c(0,24), ylim = c(0,1),
     cex.axis = 0.95, cex.lab = 1.05, cex.main = 1.15)

grid(nx = NA, ny = 6, col = GRID, lty = 1)
lines(km_risk, fun = "event", col = tier_cols[levels(loan_df$risk_tier)],
      lwd = 2.2, conf.int = FALSE)

legend("topleft", legend = levels(loan_df$risk_tier),
       col = tier_cols[levels(loan_df$risk_tier)],
       lwd = 2.2, bty = "n", text.col = "white", cex = 0.92)

p_label <- ifelse(p_lr < 0.001, "p < 0.001", sprintf("p = %.4f", p_lr))
legend("bottomright", legend = c(sprintf("Log-rank χ²=%.1f", lr_risk$chisq), p_label),
       bty = "n", text.col = "gray80", cex = 0.88)

mtext("High-risk borrowers default significantly earlier (p < 0.001)",
      side = 3, line = 0.1, col = "gray70", cex = 0.82)
dev.off()
cat("[Plot]   Saved → outputs/km_by_risk_group.png\n")


# ──────────────────────────────────────────────────────────
# 4.  KM BY EDUCATION LEVEL
# ──────────────────────────────────────────────────────────
cat("\n[Analysis]  Kaplan-Meier: survival by education level\n")
km_edu <- survfit(Surv(time, event) ~ education, data = loan_df)
lr_edu  <- survdiff(Surv(time, event) ~ education, data = loan_df)
p_edu   <- pchisq(lr_edu$chisq, df = length(lr_edu$n)-1, lower.tail = FALSE)
cat(sprintf("  Log-rank test:  χ²=%.2f, p%s\n", lr_edu$chisq,
            ifelse(p_edu < 0.001, " < 0.001", sprintf(" = %.4f", p_edu))))

edu_cols <- c("Graduate"="#00d4aa","University"=ORANGE,"High School"=PURPLE)

png("outputs/km_by_education.png", width = 860, height = 520, bg = BG)
dark_theme()
par(mar = c(4.5, 4.8, 4.5, 2))

plot(km_edu, fun = "event",
     xlab = "Months Since Loan Origination",
     ylab = "Cumulative Default Probability",
     main = "Survival Curves by Education Level",
     col = edu_cols[levels(loan_df$education)],
     lwd = 2.2, conf.int = FALSE,
     xlim = c(0,24), ylim = c(0,1),
     cex.axis = 0.95, cex.lab = 1.05, cex.main = 1.15)

grid(nx = NA, ny = 6, col = GRID, lty = 1)
lines(km_edu, fun = "event", col = edu_cols[levels(loan_df$education)],
      lwd = 2.2, conf.int = FALSE)

legend("topleft", legend = levels(loan_df$education),
       col = edu_cols[levels(loan_df$education)],
       lwd = 2.2, bty = "n", text.col = "white", cex = 0.92)

p_edu_label <- ifelse(p_edu < 0.001, "p < 0.001", sprintf("p = %.4f", p_edu))
legend("bottomright",
       legend = c(sprintf("Log-rank χ²=%.1f", lr_edu$chisq), p_edu_label),
       bty = "n", text.col = "gray80", cex = 0.88)
dev.off()
cat("[Plot]   Saved → outputs/km_by_education.png\n")


# ──────────────────────────────────────────────────────────
# 5.  COX PROPORTIONAL HAZARDS MODEL
# ──────────────────────────────────────────────────────────
cat("\n[Analysis]  Cox Proportional Hazards model\n")

cox_model <- coxph(
  Surv(time, event) ~ risk_score + education + limit_scaled,
  data = loan_df, ties = "efron"
)
cox_sum <- summary(cox_model)

cat("\n  Hazard Ratios (HR > 1 = higher default risk):\n")
cat(sprintf("  %-28s %7s %7s %7s  %s\n","Variable","HR","95%Lo","95%Hi","p-val"))
cat(paste(rep("-",65),collapse=""),"\n")
hr_tab <- as.data.frame(cox_sum$conf.int)
p_vals  <- cox_sum$coefficients[,"Pr(>|z|)"]
for (v in rownames(hr_tab)) {
  sig <- ifelse(p_vals[v]<0.001,"***",ifelse(p_vals[v]<0.01,"**",ifelse(p_vals[v]<0.05,"*","")))
  cat(sprintf("  %-28s %7.3f %7.3f %7.3f  %.4f %s\n",
              v, hr_tab[v,"exp(coef)"], hr_tab[v,"lower .95"], hr_tab[v,"upper .95"],
              p_vals[v], sig))
}
cat(sprintf("\n  C-index (concordance): %.4f\n", cox_sum$concordance["C"]))
cat(sprintf("  Likelihood ratio test: p = %.4g\n",  cox_sum$logtest["pvalue"]))


# ── Forest plot ───────────────────────────────────────────
var_labels <- c(
  "risk_score"             = "Risk Score",
  "educationUniversity"    = "Education:\nUniv. vs Grad.",
  "educationHigh School"   = "Education:\nHS vs Grad.",
  "limit_scaled"           = "Credit Limit\n(per $10k)"
)

png("outputs/cox_forest.png", width = 820, height = 440, bg = BG)
dark_theme()
par(mar = c(4.5, 8, 4, 8))

hrs  <- hr_tab[,"exp(coef)"]
los  <- hr_tab[,"lower .95"]
his  <- hr_tab[,"upper .95"]
nv   <- length(hrs)
ys   <- seq(nv, 1)

x_lo <- min(los) * 0.75
x_hi <- max(his) * 1.45

plot(hrs, ys, xlim = c(x_lo, x_hi), ylim = c(0.3, nv+0.7),
     pch = NA, xlab = "Hazard Ratio (HR)", ylab = "",
     main = "Cox Model: Hazard Ratios for Loan Default",
     yaxt = "n", cex.lab = 1.05, cex.main = 1.12)

grid(nx = 5, ny = NA, col = GRID, lty = 1)
abline(v = 1, lty = 2, col = "gray60", lwd = 1.5)

for (i in seq_along(hrs)) {
  y   <- ys[i]
  col <- if (hrs[i] > 1) RED else TEAL
  rect(los[i], y-0.18, his[i], y+0.18,
       col = adjustcolor(col, alpha.f=0.18), border = NA)
  lines(c(los[i], his[i]), c(y, y), col = col, lwd = 2)
  points(hrs[i], y, pch = 18, col = col, cex = 1.8)
  text(max(his)+0.12, y,
       sprintf("HR=%.2f [%.2f–%.2f]", hrs[i], los[i], his[i]),
       col = "white", cex = 0.78, adj = 0)
}

axis(2, at = ys, labels = var_labels[rownames(hr_tab)],
     las = 1, col.axis = "white", cex.axis = 0.82, tick = FALSE)

legend("bottomright",
       legend = c("HR > 1 (increases hazard)", "HR < 1 (protective)"),
       fill = c(adjustcolor(RED,0.4), adjustcolor(TEAL,0.4)),
       border = NA, bty = "n", text.col = "white", cex = 0.85)
dev.off()
cat("[Plot]   Saved → outputs/cox_forest.png\n")


# ──────────────────────────────────────────────────────────
# 6.  PH ASSUMPTION: SCHOENFELD RESIDUALS
# ──────────────────────────────────────────────────────────
cat("\n[Analysis]  Proportional Hazards assumption check\n")
ph_test <- cox.zph(cox_model)
cat("\n  Schoenfeld test results:\n")
print(ph_test$table)

png("outputs/schoenfeld.png", width = 960, height = 400, bg = BG)
dark_theme()
par(mfrow = c(1, ncol(ph_test$y)), mar = c(4, 4.5, 3.5, 1.5))

v_labels <- c("risk_score"="Risk Score",
              "educationUniversity"="Edu: Univ.",
              "educationHigh School"="Edu: HS",
              "limit_scaled"="Credit Limit")

for (j in seq_len(ncol(ph_test$y))) {
  x_t <- ph_test$x
  y_r <- ph_test$y[, j]
  vname <- colnames(ph_test$y)[j]
  plot(x_t, y_r, pch = 16, col = adjustcolor(TEAL, 0.35), cex = 0.55,
       xlab = "Time (months)", ylab = "Schoenfeld Residual",
       main = v_labels[vname], cex.main = 0.95, cex.lab = 0.9)
  grid(col = GRID, lty = 1)
  # LOESS smoother
  lo <- loess(y_r ~ x_t, span = 0.6)
  t_seq <- seq(min(x_t), max(x_t), length.out = 100)
  lines(t_seq, predict(lo, t_seq), col = ORANGE, lwd = 2)
  abline(h = 0, lty = 2, col = "gray60")
  p_ph <- ph_test$table[vname, "p"]
  legend("topright", legend = sprintf("p=%.3f", p_ph),
         bty = "n", text.col = "gray80", cex = 0.78)
}
dev.off()
cat("[Plot]   Saved → outputs/schoenfeld.png\n")


# ──────────────────────────────────────────────────────────
# 7.  SAVE TEXT REPORT
# ──────────────────────────────────────────────────────────
report <- sprintf(
"LOAN DEFAULT SURVIVAL ANALYSIS — SUMMARY REPORT
================================================
Dataset:         Synthetic cohort, n = %d, 24-month follow-up
Default rate:    %.1f%%

KAPLAN-MEIER RESULTS
─────────────────────────────────────────────────
Month   Cumulative Default%%
  3       %.1f%%
  6       %.1f%%
 12       %.1f%%
 18       %.1f%%
 24       %.1f%%

Group comparisons (log-rank test):
  By risk tier:   χ²=%.2f, p%s
  By education:   χ²=%.2f, p%s

COX PROPORTIONAL HAZARDS MODEL
─────────────────────────────────────────────────
C-index (concordance): %.4f   [higher = better discrimination]
Likelihood ratio test: p = %.4g

Hazard Ratios:
  Variable                    HR      95%% CI
  risk_score               %6.3f   [%.3f – %.3f]
  educationUniversity      %6.3f   [%.3f – %.3f]
  educationHigh School     %6.3f   [%.3f – %.3f]
  limit_scaled (per $10k)  %6.3f   [%.3f – %.3f]

Key interpretation:
  HR > 1  → increases default hazard (faster default)
  HR < 1  → protective (slower default)
  HR = 1  → no effect relative to reference group

Outputs saved to outputs/:
  km_overall.png, km_by_risk_group.png, km_by_education.png
  cox_forest.png, schoenfeld.png
",
  n, mean(event)*100,
  (1-km_at$surv[1])*100, (1-km_at$surv[2])*100, (1-km_at$surv[3])*100,
  (1-km_at$surv[4])*100, (1-km_at$surv[5])*100,
  lr_risk$chisq, ifelse(p_lr<0.001," < 0.001",sprintf(" = %.4f",p_lr)),
  lr_edu$chisq,  ifelse(p_edu<0.001," < 0.001",sprintf(" = %.4f",p_edu)),
  cox_sum$concordance["C"], cox_sum$logtest["pvalue"],
  hr_tab["risk_score","exp(coef)"],       hr_tab["risk_score","lower .95"],       hr_tab["risk_score","upper .95"],
  hr_tab["educationUniversity","exp(coef)"],    hr_tab["educationUniversity","lower .95"],    hr_tab["educationUniversity","upper .95"],
  hr_tab["educationHigh School","exp(coef)"],   hr_tab["educationHigh School","lower .95"],   hr_tab["educationHigh School","upper .95"],
  hr_tab["limit_scaled","exp(coef)"],     hr_tab["limit_scaled","lower .95"],     hr_tab["limit_scaled","upper .95"]
)

writeLines(report, "outputs/survival_report.txt")
cat("\n[Report] Saved → outputs/survival_report.txt")
cat("\n\n✓  Project 3 complete.\n")
