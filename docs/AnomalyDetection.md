# Anomaly detection

The Anomalies tab looks at the **latest 28 days** of one weight series and asks three different questions:

1. Has this animal stayed light for many days in a row, after the herd’s shared up-and-down days are removed? That is the **CUSUM**. It is the only path to severity **High**.
2. Is this animal’s growth rate over the month low compared with animals of the same breed, sex, mob, and treatment? That is the **peer slope**. It is severity **Medium**.
3. Did one day land in the extreme lower tail of day-to-day noise? That is the **point score**. It is severity **Low**.

“High / Medium / Low” on the sensitivity control is a different word from the severity label. Sensitivity **High** makes every test easier to fail. Severity **High** means the CUSUM crossed its threshold. The default sensitivity is **Medium**.

All of the arithmetic is in `backend/app/services/anomaly_service.py`. The tab reads a stored snapshot when it can (`backend/app/services/anomaly_snapshot.py`, `backend/app/routers/anomaly_fast.py`) and otherwise runs the same functions.

---

## What goes in

Allowed series, and only these:

| Column | Use |
|---|---|
| `weight` | Observed weight. This is the calibrated analysis. |
| `pweight` | Smoothed weight. Consecutive days are correlated, so the CUSUM flags more animals than the sensitivity level suggests, and a single-day flag means little. The API says so when this column is selected. |

Rows need a real EID, a date, and a finite weight **strictly greater than 0**. Blank, `unknown`, and `none` group labels become missing. If an animal has two rows on the same date, the weights are averaged and each group column keeps its first non-missing value.

The window ends on the latest date that has a weight in the selected period (or the latest date on the farm if no period is selected). It starts 27 days earlier, so the window is 28 dates inclusive:

\[
t \in [T - 27,\ T]
\]

Day index \(d = 1\) is the first date of that window, \(d = 28\) the last. Week index is

\[
\text{week}(d) = \left\lfloor \frac{d - 1}{7} \right\rfloor + 1
\]

so days 1–7, 8–14, 15–21, and 22–28 are weeks 1, 2, 3, and 4.

If the user has selected at least **10** animals, every score below is computed inside that selection. Otherwise the reference set is the whole farm. The peer sentence says which one was used (“all selected animals” or “whole farm”).

---

## 1. Remove the herd’s shared day

A hot day, a new ration, or a scale offset moves many animals together. That movement is taken off before any animal is judged.

For animal \(i\), let \(m_i\) be the median of its own weights inside the window. The deviation from that animal’s own level is

\[
\delta_{i,d} = W_{i,d} - m_i
\]

On day \(d\), if at least \(n_{\min} = 10\) animals have a weight, the herd effect is the median of those deviations. Otherwise it is 0:

\[
H_d =
\begin{cases}
\operatorname{median}_i \delta_{i,d} & \text{if } \#\{i : W_{i,d}\ \text{exists}\} \ge 10 \\
0 & \text{otherwise}
\end{cases}
\]

The herd-adjusted weight is

\[
A_{i,d} = W_{i,d} - H_d
\]

\(H_d > 0\) means that day’s weights sat above each animal’s own median, together. Subtracting it stops that day from looking like every animal suddenly grew. \(H_d\) is not a growth rate. It is a one-day shift relative to each animal’s window median.

The page can also show a herd note, and that note does **not** change scores. Fit a straight line to \(H_d\) against calendar day (ordinary least squares). If the largest absolute residual of \(H_d\) from that line is more than \(2\sigma\) (the noise scale from the next section), the note names that day. The idea is to tell the reader that a shared jump was removed, not to flag animals for it.

---

## 2. Noise scale \(\sigma\)

Day-to-day wobble is measured on the adjusted series, separately from the slow trend.

For each reading, take the other adjusted weights of the **same animal** whose day index is within \(\pm 3\) days (a 7-day neighbourhood, including itself). If that neighbourhood has at least **4** readings, the local level is their median, and the residual is

\[
r_{i,d} = A_{i,d} - \operatorname{median}\{ A_{i,d'} : |d' - d| \le 3 \}
\]

Otherwise \(r_{i,d}\) is missing. A residual is “this reading versus this animal’s own nearby level,” after the herd day has already been removed.

Let \(R\) be every finite residual in the reference set. If \(|R| < 200\), \(\sigma\) is undefined. Point scores and the CUSUM then do not run, because there is not enough noise data to calibrate them. Peer growth does not need \(\sigma\).

When \(|R| \ge 200\),

\[
\operatorname{MAD} = \operatorname{median}_{r \in R} \big| r - \operatorname{median}(R) \big|
\]

\[
\sigma = 1.4826 \times \operatorname{MAD}
\]

The constant \(1.4826\) is the usual normal-consistency factor: for Gaussian noise, \(1.4826 \times \operatorname{MAD}\) estimates the standard deviation. Here it is only a robust scale. Residuals are not assumed to be Gaussian for the tests below. If \(\sigma \le 0\) or is not finite, it is treated as missing.

The UI phrase “normal day-to-day variation is about \(\pm \sigma\) kg” is this number.

---

## 3. Single unusual reading (severity Low)

Standardise every residual:

\[
z_{i,d} = \frac{r_{i,d}}{\sigma}
\]

The animal’s point score is its **lowest** \(z\), and the page records the date and the residual in kilograms on that day:

\[
z_i^{\text{point}} = \min_d z_{i,d}
\]

A negative \(z\) means the reading was below the animal’s local median.

The cutoff is not a normal \(z\)-table. It is an empirical quantile of **all** \(z_{i,d}\) in the reference set (NumPy’s linear quantile). Sensitivity picks the quantile:

| Sensitivity | Quantile of \(z\) | Meaning |
|---|---:|---|
| High | 0.005 | lowest 0.5% |
| Medium | 0.001 | lowest 0.1% |
| Low | 0.0005 | lowest 0.05% |

The animal is a point anomaly when

\[
z_i^{\text{point}} < q
\]

where \(q\) is that quantile. The test is one-sided. A surprisingly heavy day does not flag.

This check can fire on a single bad scale reading. It does not require the animal to stay light afterwards. Severity is Low even when the cutoff itself is very extreme. “Low” means “this is the weakest of the three findings,” not “the reading was only a little odd.”

---

## 4. Growth versus peers (severity Medium)

For each animal, fit an ordinary least-squares line of herd-adjusted weight on the day index. With \(n\) points \((d_j, A_j)\),

\[
\bar{d} = \frac{1}{n}\sum_j d_j, \qquad
\bar{A} = \frac{1}{n}\sum_j A_j
\]

\[
\beta_i = \frac{\sum_j (d_j - \bar{d})(A_j - \bar{A})}{\sum_j (d_j - \bar{d})^2}
\]

\(\beta_i\) is kilograms per day of adjusted weight. The same fit on the raw weight \(W\) is \(\beta_i^{\text{raw}}\). Only \(\beta_i\) is tested. \(\beta_i^{\text{raw}}\) is what the sentence on the page quotes, next to the peers’ median raw slope, because that is the number a reader can compare with a weigh sheet.

The fit is used only when the animal has at least **10** readings and the first and last day indexes are at least **21** days apart. Otherwise \(\beta_i\) is missing and this test is skipped.

Peers are looked up in this order, stopping at the first group that contains at least **10** eligible animals:

1. breed, sex, mob, and treatment, all matching and all recorded
2. breed, sex, and mob
3. breed and sex
4. breed
5. the whole reference set (“whole herd”, or “all selected animals” / “whole farm”)

An animal with a missing group field cannot sit in a group that requires that field, so it falls through to a coarser group. The group that was actually used is stored as `peer_definition`.

Inside that group, let \(M\) be the median of the eligible slopes, and

\[
s_{\text{peer}} = 1.4826 \times \operatorname{median}_j |\beta_j - M|
\]

\[
z_i^{\text{peer}} = \frac{\beta_i - M}{s_{\text{peer}}}
\]

If \(s_{\text{peer}} = 0\), \(z_i^{\text{peer}}\) is missing. The animal is low-growth when \(z_i^{\text{peer}}\) is below a fixed cutoff:

| Sensitivity | Flag when |
|---|---|
| High | \(z_i^{\text{peer}} < -2.0\) |
| Medium | \(z_i^{\text{peer}} < -2.5\) |
| Low | \(z_i^{\text{peer}} < -3.0\) |

Again this is one-sided. Growing faster than the peers does not flag. The cutoff is a constant, not an empirical quantile. With a group of only 10 slopes the MAD is coarse, so a Medium flag in a small group is a weaker statement than the same flag in a large one.

---

## 5. Sustained decline (CUSUM, severity High)

The CUSUM asks whether the later part of the window is lighter than the animal’s own start, by more than noise, for long enough that a shuffle of the same weights would rarely do it.

### Baseline and monitored days

Weeks 1–2 are the baseline. Weeks 3–4 are monitored. The animal is skipped unless it has at least **7** readings in the baseline weeks and at least **3** in the monitored weeks.

The baseline level is the mean adjusted weight on the baseline readings, not the median:

\[
B_i = \operatorname{mean}\{ A_{i,d} : \text{week}(d) \le 2 \}
\]

### Standardised, capped residual

On each monitored reading,

\[
u_{i,d} = \operatorname{clip}\left( \frac{A_{i,d} - B_i}{\sigma},\ -2,\ 2 \right)
\]

\(u < 0\) means lighter than the first two weeks. The clip stops one impossible reading from dominating the sum. A single monitored day can change the CUSUM by at most \(2 - k = 1.5\) on the way up (see the recurrence below).

### The recurrence

\(k = 0.5\). This is a lower, one-sided CUSUM with a slack constant. \(S_{i,0} = 0\) before the first monitored reading, and

\[
S_{i,d} = \max\big(0,\ S_{i,d-1} - u_{i,d} - k\big)
\]

Worked cases, starting from \(S = 0\):

| Situation | \(u\) | Next \(S\) |
|---|---:|---|
| Exactly on the baseline | 0 | \(\max(0,\ -0.5) = 0\) |
| Half a sigma light | \(-0.5\) | \(\max(0,\ 0.5 - 0.5) = 0\) |
| One sigma light | \(-1\) | \(0.5\) |
| Two sigmas light (the cap) | \(-2\) | \(1.5\) |
| One sigma heavy | \(+1\) | 0, and an existing score falls by \(1.5\) |

So the score stays at 0 until the animal is **more than \(k\) sigmas** below its own first-fortnight mean. After that it accumulates. A return to baseline decays the score by \(k\) per reading. It does not reset to zero until the decay finishes. The score never goes negative.

### The run that gets reported

\(S_i^{\max}\) is the peak of \(S_{i,d}\). If the peak is 0, there is no run. Otherwise the run is the contiguous stretch of **strictly positive** \(S\) that contains the peak: walk backward and forward while \(S > 0\).

Reported with that run:

- `run_start`, `run_end`, and `run_days` (inclusive calendar days, not “number of readings”),
- shortfall in kilograms,

\[
\text{shortfall}_i = \operatorname{mean}_{\text{run}}(B_i - A_{i,d})
\]

Positive shortfall means the adjusted weight sat below the first-fortnight mean during the run. The sentence on the page uses this kilogram figure, and it adds “and it has not caught up” when `run_end` is the last date of the window.

### Where the threshold \(h\) comes from

\(h\) is not \(k\) and not a formula in \(\sigma\). It is the value \(S^{\max}\) rarely reaches when the same adjusted weights are put in a random order.

For each of 20 permutations (NumPy generator, seed `3988`, so a repeat of the same data gives the same \(h\)):

1. Inside each animal, shuffle the adjusted weights across that animal’s days. Day indexes and week labels stay put. Only the pairing of weight with day changes.
2. Recompute baseline, \(u\), and \(S\) with the same \(k\), cap, and \(\sigma\). \(\sigma\) is **not** recomputed. It stays the original noise scale.
3. Record every animal’s \(S^{\max}\) in that permutation.

That produces a list of null peaks (up to \(20 \times\) the number of animals who still have enough baseline and monitored readings after the shuffle). For a false-alarm rate \(\alpha\),

\[
h = \text{quantile}_{1-\alpha}(\text{null peaks})
\]

| Sensitivity | \(\alpha\) | \(h\) is the |
|---|---:|---|
| High | 0.05 | 95th percentile of null peaks |
| Medium | 0.02 | 98th percentile |
| Low | 0.005 | 99.5th percentile |

The animal is a sustained decline when

\[
S_i^{\max} > h
\]

Sensitivity **High** uses the smallest \(h\), so more animals cross it. Sensitivity **Low** uses the largest \(h\).

Twenty permutations is a small null, especially at \(\alpha = 0.005\). The 99.5th percentile of a short list is only a couple of the largest shuffled peaks. Treat \(h\) as a data-dependent bar that is stable for a large herd and coarse for a small one. It is recomputed whenever the score cache misses, so a different reference set (a 10-animal selection versus the whole farm) gets its own \(h\).

What the shuffle preserves, and what it destroys: each animal keeps the same multiset of adjusted weights, so an animal that is simply lighter than the herd for the whole month does not look special. The CUSUM only grows when the **light weights line up in the monitored weeks** and the heavier ones line up in the baseline weeks. A flat-but-light animal fails this test and can still fail the peer test.

---

## 6. How the three tests become one severity

Weekly change is computed and shown, and it is **not** a fourth test. It is the mean raw weight in week 4 minus the mean raw weight in week 3, and only when both weeks have at least 3 readings. The table sorts on it. It does not change `severity`.

An animal can fail more than one test. `anomaly_types` lists every failure. `severity` keeps only the strongest:

| Order | Condition | Severity | Type label |
|---|---|---|---|
| 1 | \(S^{\max} > h\) | High | Sustained decline |
| 2 | else \(z^{\text{peer}}\) below its cutoff | Medium | Low growth vs peers |
| 3 | else \(z^{\text{point}} < q\) | Low | Single unusual reading |
| 4 | else the CUSUM or the peer slope was computable | Normal | none |
| 5 | else | Not enough data | the reason states the 10-reading / 21-day rule, or 7 baseline readings, and how many readings this animal has |

“Not enough data” means neither the CUSUM nor the peer slope could be formed. A point score alone does not count as enough structure to call the animal Normal. If the point test also could not run (\(\sigma\) missing, or no residual), the animal stays in this last bin whenever CUSUM and peer are missing too.

Sort order in the table: severity (High first), then week-on-week change ascending, then \(S^{\max}\) descending, then \(z^{\text{peer}}\) ascending, then \(z^{\text{point}}\) ascending. Missing sort keys are pushed to the end of that key.

---

## Constants

All of these are module-level names in `anomaly_service.py`.

| Name | Value | Role |
|---|---:|---|
| `ANOMALY_WINDOW_DAYS` | 28 | Window length. |
| `ANOMALY_MIN_GROUP_SIZE` | 10 | Minimum animals for a herd-effect day, a peer group, and a selected-animal reference. |
| `ANOMALY_NOISE_HALF_WINDOW` | 3 | Residual neighbourhood, \(\pm 3\) days. |
| `ANOMALY_NOISE_MIN_READINGS` | 4 | Neighbourhood size required before a residual exists. |
| `ANOMALY_MIN_RESIDUALS` | 200 | Residuals required before \(\sigma\) exists. |
| `ANOMALY_GROWTH_MIN_READINGS` | 10 | Readings required for a slope. |
| `ANOMALY_GROWTH_MIN_SPAN_DAYS` | 21 | Minimum day-index span for a slope. |
| `ANOMALY_CUSUM_BASELINE_WEEKS` | 2 | Weeks 1–2 form \(B_i\). |
| `ANOMALY_CUSUM_MIN_BASELINE` | 7 | Baseline readings required. |
| `ANOMALY_CUSUM_MIN_MONITORED` | 3 | Monitored readings required. |
| `ANOMALY_CUSUM_K` | 0.5 | Slack, in units of \(\sigma\). |
| `ANOMALY_CUSUM_CAP` | 2 | Cap on \(\lvert u \rvert\). |
| `ANOMALY_CUSUM_PERMUTATIONS` | 20 | Shuffles used to build \(h\). |
| `ANOMALY_CUSUM_SEED` | 3988 | Permutation seed. |

---

## Where the result is cached

`get_anomaly_scores` keeps an in-process LRU of 20 score objects. The key includes farm, weight column, row count, latest date, the sum of the weights, whether the reference is the selection or the farm, and a hash of the reference EIDs. An upload clears this cache.

The tab’s fast path is the `anomaly_snapshots` table: one precomputed analysis per farm and weight column, read by `GET /api/anomalies/snapshot` on the light Vercel function. `GET /api/anomalies/warm` (cron, heavy function) recomputes it. Snapshot eligibility and the warm schedule are in `CRONJOB.md`.

Detail charts for one animal (`anomaly_detail_series`) plot raw weight, adjusted weight, the CUSUM path, and the threshold \(h\) for the selected sensitivity. They do not use a second formula.
