# Calibration & Runtime Pulse Loss Analysis

**Scope:** KegLevel Pico (UI app) + KegLevelPicoOnly (Pico firmware)  
**Modes:** GPIO (Pi reads flow sensors) and Pico (Pico reads flow sensors, app polls API)  
**Date:** 2025-03-12

---

## Executive Summary

| Area | GPIO Mode | Pico Mode | Notes |
|------|-----------|-----------|-------|
| **Calibration pulses** | ✅ No loss | ⚠️ **Pulse loss possible** | App polls at 1.5 s; pulses before POST /calibrate/start never reach calibration |
| **Runtime pulses** | ✅ No loss | ⚠️ **Theoretical overflow** | Pico uses 8-bit `_isr_counts`; >255 pulses in 250 ms window wraps |
| **Pi GPIO when in Pico mode** | N/A | ⚠️ **Not explicitly disabled** | Pi flow pins never configured in Pico mode; residual state from prior GPIO run could persist |

---

## 1. Calibration Pulse Flow

### 1.1 GPIO Mode (Pi reads flow sensors)

**Path:** Hardware IRQ → `global_pulse_counts[]` → SensorLogic loop → `_auto_cal_session_pulses` → UI callback

- **Flow:** `count_pulse()` ISR increments `global_pulse_counts[i]` on every edge. The main loop samples `delta = global_pulse_counts[i] - last_pulse_count[i]`, updates `last_pulse_count[i]`, and adds `delta` to `_auto_cal_session_pulses`.
- **Conclusion:** No pulse loss. All pulses are counted immediately by the ISR. The loop simply diffs the cumulative counter; every pulse is captured.
- **Poll interval:** 0.5 s (`READING_INTERVAL_SECONDS`). Not a factor for loss — we read the cumulative counter.

### 1.2 Pico Mode (Pico reads flow sensors, app polls API)

**Path:** Pico HW IRQ → Pico `_isr_counts` → Pico `_cal_pulses[]` → app `GET /api/taps/<n>/calibrate` → UI callback

**Loss window:** Between pour start and app `POST /api/taps/<n>/calibrate/start`:

1. **t0:** User opens tap; Pico counts pulses → `_lifetime_pulses` and `_dispensed_liters` (`_cal_mode[i]` is False).
2. **t0..t1:** App polls `/api/state` every **1.5 s** (`POLL_INTERVAL_S`).
3. **t1:** App sees `flow_rate > 0.02` or `pouring == True`, locks to tap `i`.
4. **t2:** App `POST /api/taps/<i>/calibrate/start`. Pico sets `_cal_mode[i] = True`, `_cal_pulses[i] = 0`.
5. **t2..:** New pulses go to `_cal_pulses`. Pulses from t0..t2 stay in `_lifetime_pulses` and are never added to calibration.

**Example loss:** At 2 L/min, 5100 pulses/L ≈ 170 pulses/s. A 1.5 s gap ⇒ ~255 pulses lost.

**Root cause:** Calibration mode always uses `POLL_INTERVAL_S` (1.5 s); it never switches to `POUR_POLL_INTERVAL_S` (0.15 s) when waiting for the lock.

---

## 2. Runtime Pulse Flow

### 2.1 GPIO Mode

- Same as calibration: `global_pulse_counts` is cumulative; loop diffs it. No loss.

### 2.2 Pico Mode (Pico firmware)

**Path:** HW IRQ → `_isr_counts[]` (bytearray) → loop drains to `delta` → `_lifetime_pulses`, `_dispensed_liters`

**Risk:** `_isr_counts` is `bytearray(5)`; each element wraps at 255:

```python
# sensor.py
_isr_counts[idx] = (v + 1) & 0xFF  # Wraps at 255
```

The loop drains `_isr_counts` every `LOOP_INTERVAL_S` (250 ms). If more than 255 pulses occur in 250 ms, the excess is lost.

**Practical impact:**
- 500 mL pour, K=5100, ~30 s ⇒ ~85 pulses/s ⇒ 21 in 250 ms — safe.
- Very fast pour (3 s): ~850 pulses/s ⇒ ~212 in 250 ms — still under 255.
- High-flow case (5 L/min): ~425 pulses/s ⇒ ~106 in 250 ms — safe for typical setups.

**Conclusion:** Theoretically possible under extreme flow; unlikely in normal use. Still a structural risk.

---

## 3. Pi GPIO When in Pico Mode

**Current behavior:**
- In Pico mode, `PicoSensorLogic` is used; it never imports or uses `RPi.GPIO`.
- `SensorLogic._setup_gpios()` is never called when backend is `pico_w`.
- Pi flow sensor pins are never configured in Pico mode.

**Residual state risk:**
- If the app previously ran in GPIO mode and exited without `cleanup_gpio()` (crash, force quit), flow pins may still be configured.
- On the next run in Pico mode, we never call `GPIO.cleanup()`, so that configuration persists.
- If both Pi and Pico are wired to the same sensors (e.g., shared harness), Pi pins could affect the lines.

**Recommendation:** Add an explicit `GPIO.cleanup()` at app start when in Pico mode and `RPi.GPIO` is available, to clear any prior GPIO state.

---

## 4. Temperature Sensor

- **GPIO mode:** Pi reads DS18B20 via 1-Wire (`/sys/bus/w1/devices/`).
- **Pico mode:** Pico reads DS18B20; app gets temperature via `/api/state`.
- Only one source is used per run; no conflict.

---

## 5. Findings Summary

### Confirmed No Loss
- **GPIO calibration:** All pulses captured via cumulative `global_pulse_counts`.
- **GPIO runtime:** Same.
- **Pico runtime (normal use):** Pico is source of truth; 8-bit overflow unlikely in typical pours.

### Potential Loss
- **Pico calibration:** Pulse loss between pour start and `POST /calibrate/start` due to 1.5 s idle poll.
- **Pico firmware:** `_isr_counts` bytearray overflow (>255 pulses in 250 ms).

### Missing Practice
- **Pi GPIO in Pico mode:** Flow pins not explicitly cleared when using Pico backend.

---

## 6. Recommended Mitigations

| # | Issue | Mitigation |
|---|-------|------------|
| 1 | Pico cal pulse loss (app) | Use `POUR_POLL_INTERVAL_S` (0.15 s) when `_auto_cal_mode` and `_auto_cal_locked_tap == -1` so we detect flow and POST `calibrate/start` sooner. |
| 2 | Pico `_isr_counts` overflow | Replace `bytearray(5)` with `array.array('H', [0]*5)` or `[0]*5` (Python ints) so each tap can count >255 per loop. Requires careful ISR design to avoid heap allocation. |
| 3 | Pi GPIO in Pico mode | In `PicoSensorLogic.start_monitoring()`, if on a Pi (`RPi.GPIO` available), call `GPIO.cleanup()` before starting the poll thread. |

---

## 7. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                     GPIO MODE (sensor_logic.py)                       │
├─────────────────────────────────────────────────────────────────────┤
│  Flow sensors → Pi GPIO IRQ → global_pulse_counts (cumulative)       │
│       ↑                                                              │
│       └── Every pulse counted immediately. Loop diffs. No loss.      │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                     PICO MODE (pico_sensor_logic.py)                 │
├─────────────────────────────────────────────────────────────────────┤
│  App polls /api/state (1.5 s idle, 0.15 s when pouring)              │
│       │                                                              │
│       ├── Calibration: Uses 1.5 s even when waiting for lock → LOSS │
│       └── Runtime: Uses Pico as source; app receives dispensed vol  │
│                                                                      │
│  Pico: Flow sensors → IRQ → _isr_counts (bytearray) → _cal_pulses    │
│       │                       ↑                                       │
│       └── Wraps at 255 per 250 ms → theoretical overflow             │
└─────────────────────────────────────────────────────────────────────┘
```
