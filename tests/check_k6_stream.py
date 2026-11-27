#!/usr/bin/env python3
"""Prove raw k6 counters arrive DURING a unique run, not at teardown.
Run after warm-up and finish before k6 stops. Keep original k6 JSON output.
No aliases, synthesized zeroes, or refreshed timestamps are accepted.
"""
import argparse
import json
import math
import pathlib
import time
from check_live_metrics import make_query, finite_rows

COUNTERS = ('k6_lab_attempted_total', 'k6_lab_good_total',
            'k6_lab_slow_success_total', 'k6_lab_failed_total',
            'k6_lab_timeout_total', 'k6_http_reqs_total')
# Cumulative event counters legitimately have no load series at 0 events.
EVENT_COUNTERS = {'k6_lab_slow_success_total', 'k6_lab_failed_total',
                  'k6_lab_timeout_total'}


def snapshot(query, experiment, max_age=15, scenario="load"):
    result = {}
    for name in COUNTERS:
        # The k6 setup() phase leaves a stale ::setup series; the measurement is
        # the load scenario only.
        selector = name + '{experiment=' + json.dumps(experiment) + ',scenario=' + json.dumps(scenario) + '}'
        raw = query(selector)
        if not raw and name in EVENT_COUNTERS:
            # Sparse event streams may not have emitted yet. This is unknown,
            # not a measured zero or a fresh sample. Keep it visible in evidence.
            result[name] = {'total': None, 'max_age_seconds': None, 'series': 0}
            continue
        rows = finite_rows(raw)
        ages = finite_rows(query('time()-timestamp(' + selector + ')'))
        # Values and ages must refer to the same raw series. Never use the
        # API result evaluation timestamp as the source timestamp.
        def identity(labels):
            return tuple(sorted((k,v) for k,v in labels.items() if k != '__name__'))
        keys = {identity(m) for m,v in rows}
        if (keys != {identity(m) for m,v in ages} or len(keys) != len(rows)
                or len(keys) != len(ages)):
            raise ValueError(name + ': inconsistent or duplicate identities')
        if any(m.get('experiment') != experiment or v < 0 for m,v in rows):
            raise ValueError(name + ': wrong experiment or negative count')
        # Continuous counters must be fresh; cumulative event counters
        # (slow/failed/timeout) increment rarely, so a present-but-older sample
        # is valid. Their liveness is proven by the continuous counters.
        if name not in EVENT_COUNTERS and any(not -2 <= v <= max_age for _,v in ages):
            raise ValueError(name + ': stale source sample')
        result[name] = {'total': sum(v for _,v in rows),
                        'max_age_seconds': max(v for _,v in ages), 'series': len(rows)}
    return result


def verify_progress(before, after):
    deltas = {}
    for k in COUNTERS:
        old, new = before[k]['total'], after[k]['total']
        if old is not None and new is None:
            raise ValueError(k + ': observed counter disappeared during run')
        deltas[k] = None if old is None or new is None else new-old
    if any(v < 0 or not math.isfinite(v) for v in deltas.values() if v is not None):
        raise ValueError('counter reset or nonfinite delta in unique experiment')
    if not (deltas[COUNTERS[0]] is not None and deltas[COUNTERS[0]] > 0
            and deltas[COUNTERS[-1]] is not None and deltas[COUNTERS[-1]] > 0
            and any(deltas[k] is not None and deltas[k] > 0 for k in COUNTERS[1:4])):
        raise ValueError('no in-run progress in attempts, HTTP completions, or terminal outcomes')
    return deltas


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mimir', required=True)
    p.add_argument('--experiment', required=True)
    p.add_argument('--scenario', default='load', help='k6 measurement scenario')
    p.add_argument('--duration', type=float, default=40)
    p.add_argument('--interval', type=float, default=2)
    p.add_argument('--max-age', type=float, default=15)
    p.add_argument('--evidence', type=pathlib.Path, required=True)
    a = p.parse_args()
    if not (0<a.max_age<=15 and 30<=a.duration and 0<a.interval<=5):
        p.error('require at least 30s observation, polling <=5s, max age <=15s')
    a.evidence.parent.mkdir(parents=True, exist_ok=True)
    query = make_query(a.mimir)
    started = time.monotonic()
    first = last = None
    count = 0
    # Exclusive creation avoids mixing evidence for different experiments.
    with a.evidence.open('x') as f:
        while True:
            now = time.monotonic()
            try:
                last = snapshot(query, a.experiment, a.max_age, a.scenario)
                if first is None:
                    first = last
                f.write(json.dumps({'epoch': time.time(), 'samples': last})+'\n')
                f.flush()
                count += 1
            except Exception as e:
                f.write(json.dumps({'epoch': time.time(), 'error': str(e)})+'\n')
                f.flush()
                raise
            if time.monotonic()-started >= a.duration:
                break
            time.sleep(max(0, a.interval-(time.monotonic()-now)))
        if count < 3:
            raise ValueError('insufficient observations')
        print('PASS raw counters during workload:', json.dumps(verify_progress(first,last)), 'polls:',count)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
