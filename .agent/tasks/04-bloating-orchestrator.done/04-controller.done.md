# Subtask 04: Controller — adaptive α + Michelot + probe

**Business Value**: Given a sensor observation and a mainnet target, the controller picks the next batch's scenario mix and byte budget so composition converges toward the target within per-verb tolerance bands.

**Dependencies**: 01 (sensor), 02 (facade for dispatch signatures), 03 (journal writer for recording state)

---

## Implementation Plan

### Files to Create

| File | Purpose |
|------|---------|
| `src/orchestrator/controller.py` | `Controller` class + `ControllerState` dataclass |
| `src/orchestrator/reference_f.py` | Load + version-stamp `REFERENCE_F-<version>.json` |
| `src/orchestrator/probe.py` | First-run probe sequence that seeds F |
| `src/orchestrator/math/michelot.py` | Pure-numpy projection onto the simplex |
| `src/orchestrator/math/adaptive_alpha.py` | Innovation update (Huber + tanh + α) |
| `src/orchestrator/target.py` | `TargetConfig` loader for `target.yaml` |
| `reference_f/REFERENCE_F-2026.04.23.json` | Per design-v3 Appendix B.1 |
| `tests/test_controller.py` | Closed-loop convergence smoke test |

### Steps

1. **`TargetConfig`**: loads `target.yaml` — fields: `composition_hash_inputs` (list of hashable files + vars), `mainnet_target` (`{accounts:0.141, storage:0.817, code:0.043}`), `target_total_bytes`, `base_address`, `revision`, `seed_hex` (legacy; unused at runtime), `qp_scenarios` (default 9-verb list).
2. **`REFERENCE_F` loader**: parses the signed JSON; exposes `per_scenario(verb) → {accounts, storage, code}`. Records `reference_f_version` for the manifest.
3. **`Controller`** state (`ControllerState`):
   - `F: dict[verb, dict[axis, float]]` — bytes-per-tx per scenario per axis
   - `sigma: dict[verb, dict[axis, float]]` — running RMS of raw innovation
   - `alpha: float` — smoothed learning rate
   - `batch_id: int`
4. **Adaptive α update** per batch (see design §2.4):
   ```
   raw_innov = observed - F_hat
   sat_innov = sigma * tanh(raw_innov / max(sigma, sigma_floor))
   sigma = 0.9 * sigma + 0.1 * abs(raw_innov)
   abs_ratio = abs(sat_innov) / max(abs(F_hat), eps)
   alpha_target = a_min + (a_max - a_min)/2 * (1 + tanh(k * (abs_ratio - c)))
   alpha = 0.7 * alpha + 0.3 * alpha_target
   F_hat = F_hat + alpha * sat_innov
   ```
   Per-axis per-verb. Defaults: `a_min=0.02`, `a_max=0.30`, `c=0.08`, `k=25`, `sigma_floor=1`, `eps=1000`.
5. **Michelot projection** (`math/michelot.py`): pure-numpy. `project_simplex(x: np.ndarray) -> np.ndarray` — sort desc; find largest k with the spec condition; apply uniform tax. O(n log n).
6. **`Controller.pick_next_batch(observation: StateObservation, target: TargetConfig) → BatchPlan`**:
   - Compute residual `r = target.mainnet_target * target.target_total_bytes - observation.{axes}`.
   - Build F matrix from `self.F` restricted to `target.qp_scenarios`.
   - Gradient descent step: `x' = x - eta * 2 * F.T @ (F @ x - r)`.
   - Project to simplex via Michelot.
   - Choose verb with highest `x*` deficit (or weighted random); compute `deadline_bytes = total_batch_bytes * x*[verb]`.
   - Return `BatchPlan(verb, deadline_bytes)`.
7. **Probe sequence** (`probe.py`): for each scenario in `qp_scenarios`: issue a 100-tx batch with budget from REFERENCE_F; observe; seed F from `observed/100`. 9 × 100 = 900 txs, ~10 s wall-clock. Sanity gate: if any probe is >3× off from REFERENCE_F (ignoring sign for `storagerefundtx`), abort with `characterization_insufficient`.
8. **Overshoot detection**: L2 norm-ratio `‖observed − commanded‖₂ / max(‖commanded‖₂, 1KB)`. If > 0.20 for 3 consecutive batches after batch 5 → raise `ControllerInstability`.

### Patterns & Hints

```python
# math/adaptive_alpha.py
import math

def update_coeff(f_hat, observed, sigma, alpha, *, a_min=0.02, a_max=0.30, c=0.08, k=25, sigma_floor=1, eps=1000):
    raw_innov = observed - f_hat
    sat = sigma * math.tanh(raw_innov / max(sigma, sigma_floor))
    sigma_new = 0.9 * sigma + 0.1 * abs(raw_innov)
    abs_ratio = abs(sat) / max(abs(f_hat), eps)
    alpha_target = a_min + (a_max - a_min) / 2 * (1 + math.tanh(k * (abs_ratio - c)))
    alpha_new = 0.7 * alpha + 0.3 * alpha_target
    f_new = f_hat + alpha_new * sat
    return f_new, sigma_new, alpha_new
```

Michelot: see `math-explained.md` §5 for the step-by-step — implement directly, don't depend on `scipy`.

---

## Testing

**Test File**: `tests/test_controller.py`, `tests/test_michelot.py`, `tests/test_adaptive_alpha.py`

| Test Name | Description | Expected |
|-----------|-------------|----------|
| `test_michelot_n1_returns_1` | Project `[0.5]` | `[1.0]` |
| `test_michelot_negative_clipped` | Project `[0.8, 0.5, -0.2, 0.4]` | `[0.567, 0.267, 0, 0.167]` (from math-explained §5 worked example) |
| `test_michelot_preserves_simplex` | Project 1000 random vectors | All satisfy `sum(x)==1, x>=0` |
| `test_alpha_rises_on_disagreement` | Apply innovation with `abs_ratio > 0.15` | `alpha_new > alpha_prev` |
| `test_alpha_falls_on_agreement` | Apply innovation with `abs_ratio < 0.03` for many batches | `alpha` trends to `a_min` |
| `test_huber_clips_outlier` | Pass `observed` 100× larger than F | Update moves F by ~α·σ, not 100× |
| `test_probe_seeds_all_9_scenarios` | Run probe against fake sensor | All 9 scenarios have non-default F |
| `test_probe_sanity_gate` | Force a scenario to return 10× REFERENCE_F | Raises `CharacterizationInsufficient` |
| `test_overshoot_triggers_after_3_consecutive` | Feed commanded/observed with `ratio=0.25` for 3 batches after batch 5 | Raises `ControllerInstability` |
| `test_closed_loop_convergence_n1` | Simulate sensor; run 200 batches with a single-verb target | Final residual norm-ratio < 0.05 |

**Faking Strategy**: use a stub `SensorClient` that returns a deterministic `StateObservation` based on cumulative "bytes committed" (track internally). Closed-loop test drives this through 200 iterations without any RPC or Nethermind.

---

## Acceptance Criteria

- [ ] `Controller.pick_next_batch` returns a `BatchPlan(verb, deadline_bytes)` in < 1 ms
- [ ] Michelot projection handles n = 1 through n = 12; always returns a valid simplex point
- [ ] Adaptive α update implements the design's §2.4 formula exactly
- [ ] Huber saturation bounds single-batch F movement at ~α · σ
- [ ] Probe sequence seeds F for all `qp_scenarios` in ≤ 10 seconds wall-clock
- [ ] Overshoot trigger fires on 3 consecutive batches with norm-ratio > 0.20 after batch 5
- [ ] Closed-loop smoke test converges on a synthetic target within 200 batches
- [ ] REFERENCE_F version is recorded in controller state for the manifest writer to pick up
