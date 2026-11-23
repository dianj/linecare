# Calibration: linecare.confirm_action

Model `gemma-4-26b-a4b-it`, prompt `b793c4a91b92`, 32 labelled cases, fitted temperature **T = 4.0157** (2026-11-23).

| | accuracy | ECE | Brier |
|---|---|---|---|
| raw (T = 1) | 0.969 | 0.031 | 0.062 |
| calibrated, 5-fold cross-validated | 0.969 | 0.020 | 0.063 |

Risk-coverage (calibrated): the share of cases at or above each threshold and their accuracy.

| threshold | coverage | accuracy | n |
|---|---|---|---|
| 0.5 | 1.00 | 0.969 | 32 |
| 0.6 | 1.00 | 0.969 | 32 |
| 0.7 | 1.00 | 0.969 | 32 |
| 0.8 | 1.00 | 0.969 | 32 |
| 0.85 | 1.00 | 0.969 | 32 |
| 0.9 | 1.00 | 0.969 | 32 |
| 0.95 | 0.97 | 0.968 | 31 |
| 0.99 | 0.81 | 0.962 | 26 |

Cases labelled *unclear* (graded by action, not label): 6.

![Reliability diagram](linecare.confirm_action.svg)
