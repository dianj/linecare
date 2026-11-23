# Calibration: linecare.confirm_action

Model `qwen3.8-27b`, prompt `b793c4a91b92`, 32 labelled cases, fitted temperature **T = 1.0** (2026-11-23).

| | accuracy | ECE | Brier |
|---|---|---|---|
| raw (T = 1) | 1.000 | 0.008 | 0.001 |
| calibrated, 5-fold cross-validated | 1.000 | 0.008 | 0.001 |

Risk-coverage (calibrated): the share of cases at or above each threshold and their accuracy.

| threshold | coverage | accuracy | n |
|---|---|---|---|
| 0.5 | 1.00 | 1.000 | 32 |
| 0.6 | 1.00 | 1.000 | 32 |
| 0.7 | 1.00 | 1.000 | 32 |
| 0.8 | 1.00 | 1.000 | 32 |
| 0.85 | 1.00 | 1.000 | 32 |
| 0.9 | 0.97 | 1.000 | 31 |
| 0.95 | 0.94 | 1.000 | 30 |
| 0.99 | 0.91 | 1.000 | 29 |

Cases labelled *unclear* (graded by action, not label): 6.

![Reliability diagram](linecare.confirm_action.svg)
