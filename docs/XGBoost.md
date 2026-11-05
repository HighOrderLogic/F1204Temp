# Prediction (XGBoost)

The Prediction tab shows a **182-day** forecast of each animal’s **FinalPweight**, starting the day after that animal’s last recorded weight. One model serves every farm and every animal. The page does not train anything. It reads rows that were written into the `forecasts` table the last time that farm’s data was uploaded or an admin refreshed forecasts.

Code:

| Piece | File |
|---|---|
| Feature construction and the day-by-day loop | `ml/forecasting.py` |
| Running the trees without the XGBoost package | `ml/tree_model.py` |
| Load a farm, forecast, replace that farm’s rows | `backend/app/services/forecast_service.py` |
| Prediction API | `backend/app/routers/forecasts.py` |
| Page | `frontend/js/pages/prediction.js` |
| Shipped model card | `ml/models/model_meta.json` |
| Shipped trees | `ml/models/xgb_weight.json` (what the server loads) and `ml/models/xgb_weight.ubj` |

The training script named in `ml/forecasting.py` (`ml/train_xgb_recursive.py`) is not in this tree. What is deployed is the model card and the tree file.

---

## What one forecast step is

Each day the animal’s next weight is

\[
w_{t+1} = w_t + \mathrm{clip}\big(b(w_t) + s_t \cdot e_t\big)
\]

- \(w_t\) is the latest weight in the 14-day window (a real record on day 0, a forecast after that).
- \(b(w_t)\) is the farm’s typical daily gain at that weight (kg/day). See [Baseline](#baseline).
- \(e_t\) is the model’s predicted **excess** over that baseline (kg/day).
- \(s_t\) fades the model out. See [Fade](#fade).
- `clip` holds the daily change inside the farm’s own 1st–99th percentile of observed day-to-day changes. If the farm has no consecutive-day pairs at all, the clip is \((-3, 3)\) kg/day.

The new weight is appended to the window, the oldest day drops off, and the step repeats 182 times. Column `horizon` 1 is the first future day.

---

## Baseline

The baseline is computed from the **farm being forecast**, not frozen from training. A model trained on one farm can be applied to another whose cattle grow at a different rate.

Take every pair of weights on consecutive calendar days. The change is tomorrow’s weight minus today’s, and the band is today’s weight.

The shipped mode is `capped` (`BASELINE_MODE` in `ml/forecasting.py`, and `baseline_mode` in the model card):

1. Split those changes into 50 kg bands. The centre of band \(k\) is \((k + 0.5) \times 50\) kg.
2. Drop a band with fewer than 30 changes. Neighbours fill the gap by interpolation.
3. The band’s typical gain is its **median** daily change.
4. Cap every band at the **farm-wide median** daily change. A band may be slower than the farm median. It is never faster.

\(b(w)\) is linear interpolation of those capped medians between band centres, and flat outside the lightest and heaviest centre. Heavier cattle therefore gain less, and the six-month curve bends instead of running as a straight line.

Two other modes exist for backtests only:

| Mode | Gain curve |
|---|---|
| `curve` | Band medians, with no farm-wide cap. |
| `flat` | One farm-wide median for every weight. |

---

## Features

The model sees the last `WINDOW = 14` calendar days, oldest first. A missing day is `NaN`. XGBoost sends a missing value down the tree’s default branch (`default_left` in the JSON). The server does the same in `TreeModel.inplace_predict`.

Let \(d\) be the 13 day-to-day changes inside the window, and let \(e = d - b(w_{\text{latest}})\). Every change in the window is compared with the baseline at the **current** weight, not at the weight on that older day.

| Feature | Meaning |
|---|---|
| `weight` | Latest weight in the window. |
| `n_days` | How many of the 14 days are present. |
| `e1` … `e7` | Excess gain on each of the last 7 day-to-day steps (`e1` is the most recent). |
| `mean_e3`, `mean_e7`, `mean_e13` | Mean excess over the last 3, last 7, and all 13 changes. All-NaN means stay NaN. |
| `std_d7`, `std_d13` | Standard deviation of the raw daily changes over the last 7 and all 13. NaN when fewer than 2 changes exist. |

Breed is stored on the animal row and is not a model feature.

---

## Fade

Without a fade, forecast weights feed back into the features and the error runs away. On the 58-day backtest, the same capped model with no fade has MAE **39.9 kg**. With the fade it is **12.8 kg**.

For step index \(s = 0, 1, \ldots\) (day 1 ahead is \(s = 0\)):

\[
s_t = \max\left(0,\ 1 - \frac{s}{\text{fade\_days}}\right)
\]

`fade_days` comes from `model_meta.json`. The shipped model uses **20**. So:

- Day 1 ahead uses the full excess.
- Day 10 ahead uses half of it.
- Day 21 onward uses \(s_t = 0\): the animal follows \(b(w)\) only, still clipped.

The Prediction page stores this number as `validated_days` and draws the tested portion separately. Days 1–20 are inside the range the backtest could check. After that the line is the farm’s typical gain, not a checked model forecast. A retrained model with a larger `fade_days` brings its own tested range. The page does not have the number hardcoded.

An animal is labelled `full` when all 14 days before its last record exist, and `short` otherwise. The model still forecasts short histories. The label is only for the dropdown (`short history`).

---

## How the trees are evaluated

Training used XGBoost 2.1.4, objective `reg:absoluteerror` (minimise absolute error; the prediction is `base_score` plus the sum of the leaf values). The server does not import XGBoost. `ml/tree_model.py` walks the JSON trees with NumPy:

\[
\hat{e} = \text{base\_score} + \sum_{\text{trees}} \text{leaf}(x)
\]

That sum is \(e_t\) in the update above. `backend/tests/unit/forecasting/test_tree_model.py` checks that this matches XGBoost on the committed model.

Shipped hyperparameters, from `model_meta.json`:

| Setting | Value |
|---|---|
| `model_version` | `xgb-recursive-v2` |
| Target | `finalpweight` |
| Trained | 2026-10-20, on `wow_COMP3988_2026.xlsx`, 376 animals, 2026-06-16 to 2026-09-21 |
| Trees | 633 |
| `max_depth` | 4 |
| `learning_rate` | 0.05 |
| `subsample`, `colsample_bytree` | 0.8 |
| `min_child_weight` | 20 |
| `reg_lambda` | 5.0 |
| `tree_method` | `hist` |
| `random_state` | 42 |

The training-set growth curve in the model card is a record of that fit. Live forecasts recompute the curve from the farm in the database.

---

## Backtest of the shipped model

Figures below are copied from `model_meta.json` for the method the dashboard uses: **XGB with a 14-day window, capped baseline, fade on**. “Naive” carries the last observed daily change forward.

| Check | Animals | Points | This model MAE | Naive MAE | Baseline-only MAE |
|---|---:|---:|---:|---:|---:|
| 5–20 days ahead, steps 1–4 | 344 | 16,142 | 4.89 kg | 9.88 kg | 6.63 kg |
| 20 days ahead, step 1 | 344 | 6,579 | 5.62 kg | 11.58 kg | 7.42 kg |
| 58 days ahead | 373 | 20,551 | 12.81 kg | 54.90 kg | 13.31 kg |

At 58 days the capped model is close to the capped baseline alone (13.31 kg). Almost all of that horizon is past day 20, where the fade has already handed the path to the baseline. The same horizon with the fade removed is 39.9 kg MAE. The fade is what keeps the long forecast from drifting. It also means a six-month chart is mostly the farm’s typical gain curve, not 182 days of tested model output.

---

## When rows are written

`forecast_service.refresh_farm` loads every `finalpweight` for the farm, runs `forecast_farm`, and **replaces** that farm’s rows in `forecasts` (target `finalpweight`). The first write creates the table (`FORECASTS_DDL` in `backend/app/db.py`).

That refresh runs:

- after a successful admin CSV upload (skip it with `forecast=false` on the upload),
- on `POST /api/admin/farms/{farm_id}/forecasts/refresh`,
- from `python scripts/forecast_job.py --farm KF` (add `--dry-run` to preview),
- from `python scripts/admin.py forecast --farm KF --token <admin JWT>`.

Each stored row is `farm_id`, `eid`, `date`, `pred` (kg, rounded to 2 decimals), `horizon`, `tier` (`full` or `short`), `history_days`, `data_through`, `model_version`, `validated_days`, `generated_at`.

`GET /api/forecasts/animals` lists animals that have rows. `GET /api/forecasts/series` returns one animal’s path. Non-admins send the masked id. The server resolves it. Admins send the real EID.

If the farm has no FinalPweight values, the refresh returns `status: no_data` and leaves any previous rows as they were only when forecasting itself raises before the delete. A successful refresh deletes then inserts, so a farm that forecasts to an empty result would clear the table. An empty input raises `NoForecastData` before that delete.

---

## Replacing the model

1. Retrain with the same feature code in `ml/forecasting.py`. The dashboard and the trainer must share `make_features` and `recursive_forecast`.
2. Export JSON for the server: load the `.ubj` booster and `save_model` to `ml/models/xgb_weight.json`.
3. Update `ml/models/model_meta.json`, including `fade_days` set to how far the new backtest actually checked.
4. Redeploy, then refresh each farm. Old rows keep the old `validated_days` until that refresh.
