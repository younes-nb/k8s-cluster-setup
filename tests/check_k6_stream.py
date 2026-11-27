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


def snapshot(query, experiment, max_age=15):
    result = {}
    for name in COUNTERS:
        selector = name + '{experiment=' + json.dumps(experiment) + '}'
        rows = finite_rows(query(selector))
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
        if any(not -2 <= v <= max_age for _,v in ages):
            raise ValueError(name + ': stale source sample')
        result[name] = {'total': sum(v for _,v in rows),
                        'max_age_seconds': max(v for _,v in ages), 'series': len(rows)}
    return result


def verify_progress(before, after):
    deltas = {k: after[k]['total']-before[k]['total'] for k in COUNTERS}
    if any(v < 0 or not math.isfinite(v) for v in deltas.values()):
        raise ValueError('counter reset or nonfinite delta in unique experiment')
    if not (deltas[COUNTERS[0]] > 0 and deltas[COUNTERS[-1]] > 0
            and sum(deltas[k] for k in COUNTERS[1:4]) > 0):
        raise ValueError('no in-run progress in attempts, HTTP completions, or terminal outcomes')
    return deltas


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mimir', required=True)
    p.add_argument('--experiment', required=True)
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
                last = snapshot(query, a.experiment, a.max_age)
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
