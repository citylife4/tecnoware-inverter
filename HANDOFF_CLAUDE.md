# Claude handoff — review fixes and battery safety follow-up

Prepared 2026-09-16. Repository: `/home/greenv/dev/tecnoware-inverter`.
Base HEAD: `3370a15` (watchlist), following `6a805a7` (review fixes).
The follow-up described below is **uncommitted and not deployed**. No live
inverter commands, service restarts, or Telegram messages were used to test it.

## What the user requested

Review the project beyond the review prompt, fix what can be fixed, recheck
the previous work, close the remaining battery-window topics, and provide
this handoff. The user highlighted the lack of regression tests in the
committed review pass. That caveat was valid.

## Committed review pass: context

The earlier pass changed these areas (already in `6a805a7`):

- Battery-window hand-back ownership is recorded and persisted **before**
  requesting POP02. Entry is refused if this persistence fails. Only an
  acknowledged POP00 releases the obligation. QMOD=L is not evidence that
  POP00 is configured: program 12 can transfer to line with POP02 still set.
- Priority-file write failures no longer hide a hardware ACK from the
  issuing controller. They are logged, deduplicated and exposed in status.
- Command validation rejects framing/control characters, and priority
  parameters are checked explicitly.
- Grid-charge loses its cached PCP belief when stale data releases override
  ownership; this forces a real write when it reacquires ownership.
- The standalone charge scheduler invalidates its persistent cache before
  an uncertain write. Battery-test revert retries survive logging failures,
  and unsuccessful reversion produces a nonzero exit status.
- Schedule/grid-charge configuration changes serialize conflict checking
  with mutation. Startup rejects conflicting exclusive automations before
  starting the serial service.
- USB watchdog recovery attempts persist across polling/process restarts;
  healthy operation or an explicit operator reset rearms the budget.
- The dashboard preserves extra pump windows and edited fields, and reports
  stale/network failures. Daily energy reporting excludes invalid/stale
  samples, splits export at zero crossings, and avoids bridging line-mode
  intervals in battery-energy accounting.

This list describes the committed changes; it is not a claim that this
follow-up exhaustively re-audited every file or verified live behavior.

## Follow-up changes in the working tree

### Exception recovery and shutdown

`webapp/battery_window.py`:

- Added the missing `sys` import from the unfinished exception-handler patch.
- Unexpected background tick errors now appear in stderr/journal and the
  API (`loop_error`, error `last_run`). They latch recovery and attempt POP00
  immediately, using a path independent of the failed telemetry decision.
- A failed/unacknowledged emergency hand-back retains ownership and is
  retried before executing the ordinary decision path again.
- Shutdown attempts the same hand-back. A successful ACK releases the
  persisted obligation; a lost reply preserves it for retry/restart.
- A stopped controller cannot re-enter battery mode through a late API tick
  or config update. Repeated successful stop calls do not resend POP00.
- Shutdown waits up to five seconds for the background thread and up to five
  seconds for the controller lock. Failure to obtain the lock is logged;
  the obligation is retained. This does **not** put a deadline around a
  blocked serial/service call once hand-back starts.

### Single-reading hardware override

A QMOD=L observation with battery voltage **above the configured floor**
now reports `hardware_override_pending`. Three consecutive controller
observations are required before latching `hardware_override` and writing
POP00. A battery-mode or unreadable-mode observation resets the count.
Forced ticks do not reassert POP02 during pending confirmation.

The debounce is deliberately narrow: low or unreadable battery voltage and
non-line fault modes still cause immediate hand-back. Window closure, pump
blocks, disable, and the other utility-first interlocks retain priority.
The observation count follows controller ticks; this is not a guaranteed
three-minute timer or a count of distinct telemetry IDs.

Four existing hardware-override tests were updated to reach the confirmed
third observation. Their original latch/dwell assertions remain. New tests
separately cover transients, broken consecutive sequences, sustained
transfers, and the immediate low/unknown-voltage path.

### Missing POP drift alert

The controller now exposes `pop_drift_stuck` as a persisted boolean after
the existing corrective-write budget is exhausted. Unknown QMOD does not
clear it. It clears after the controller believes POP00 and observes L.
An ACK alone does not clear it.

`usb_watchdog.py` now checks `/api/battery-window` on a healthy-link poll,
with the existing authenticated local API and a five-second request timeout.
It uses the existing `Notifier` under key `battery_pop_drift`:

- Standing faults are deduplicated.
- Failed delivery is retried by subsequent watchdog checks.
- Confirmed recovery produces a recovery message.
- Missing/invalid/unreachable responses do not announce recovery.
- A POP fault does not initiate USB reset or service restart.

Notifications are pt-PT. The notifier may also send an initial healthy-state
message when it first observes confirmed line operation, consistent with
its existing state-change semantics.

## Tests and evidence

New permanent files:

- `test_battery_safety.py`: 15 tests for pre-write persistence, disk-failure
  refusal, lost replies/restarts, QMOD=L during disable, loop failures,
  emergency retry, shutdown/read-only/lock timeout, override debounce, and
  persisted drift alerts/recovery.
- `test_review_regressions.py`: six tests for ACK visibility under disk
  failure followed by a low-battery write, watchdog budget/reset, simultaneous
  exclusive-enable requests, alert delivery/retry/deduplication/recovery,
  failed alert API reads, and watchdog alert integration without USB reset.

Run from the repository root:

```sh
python3 -B -m unittest discover -q
git diff --check
```

Result: **336 tests pass** (315 existing plus 21 new); diff whitespace check
passes. Hardware, notification HTTP and watchdog actions are mocked in the
new tests; persistent state is confined to temporary directories.

Old-code checks used `git show REV:webapp/battery_window.py`, executed in the
module namespace in a separate Python process, then rebound the test's
BatteryWindow class. The working tree was not rolled back:

- The first 11 battery-safety tests all failed against `42df1db`, with no
  harness errors; five failed against committed HEAD, demonstrating the
  additional exception/shutdown gaps.
- The new transient-transfer, third-observation, and drift-alert tests fail
  against committed HEAD and pass with the working-tree implementation.

**Correction to an earlier Codex claim:** “extended review probes passing”
was not properly established. Running the temporary probe suite correctly
revealed outdated assumptions: expecting OSError after an ACK, placing a
barrier inside the now-serialized critical section, and reusing a watchdog
state path. Those probes are not the evidence for this handoff. Permanent
tests above exercise the intended outcomes with isolated state.

## Operational limits and next action

Review the uncommitted diff, then commit/deploy through the project's normal
workflow. Both the web service and watchdog need the updated code loaded to
activate both halves of alerting. No runtime configuration was changed here.

Successful live Telegram delivery remains unverified. It requires a running
watchdog, valid notification settings and network access. Alert latency is
the watchdog interval (default five minutes) plus request/delivery time.

POP00 is a request for utility-first, not a guarantee of mains availability
or physical transfer. Process kill/power loss bypasses graceful shutdown;
a wedged serial transport can prevent a hand-back. Persistence supports
retry after restart but cannot make an unresponsive device obey. The new
drift flag uses existing best-effort runtime persistence; disk failure can
prevent that diagnostic flag surviving a crash. Battery-entry ownership
persistence remains strict.

The underlying serial stalls and the installation's full-battery export
limitation remain open system concerns in WATCHLIST.md/NOTES.md. These fixes
do not resolve either underlying cause.
