// Read-only API acceptance, not a representative research workload.
// Source: StationController.query and StationServiceImpl.query in pinned Train Ticket.
import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';
if (!__ENV.EXPERIMENT_ID || !__ENV.TARGET_URL) throw new Error('Set unique EXPERIMENT_ID and TARGET_URL');
const attempted = new Counter('lab_attempted');
const good = new Counter('lab_good');
const slow = new Counter('lab_slow_success');
const failed = new Counter('lab_failed');
const timeout = new Counter('lab_timeout');
const rejected = new Counter('lab_seed_missing');
const unknown = new Counter('lab_unclassified_failure');
export const options = {
  scenarios: { api: { executor: 'constant-arrival-rate', rate: 2, timeUnit: '1s', duration: __ENV.SMOKE_DURATION || '90s', preAllocatedVUs: 10, maxVUs: 30, gracefulStop: '15s' } },
  thresholds: { checks: ['rate==1'], dropped_iterations: ['count==0'] },
  tags: { experiment: __ENV.EXPERIMENT_ID }
};
export default function () {
  attempted.add(1);
  let response;
  let ok = false, noSeed = false, isTimeout = false;
  const start = Date.now();
  try {
    response = http.get(__ENV.TARGET_URL.replace(/\/$/, '') + '/api/v1/stationservice/stations', {timeout: '10s', redirects: 0, tags: {name: 'station-list'}});
    isTimeout = response.error_code === 1050;
    if (response.status === 200) {
      const body = response.json();
      ok = body.status === 1 && Array.isArray(body.data) && body.data.length > 0;
      noSeed = body.status === 0 && body.msg === 'No content';
    }
  } catch (_) { /* unknown failure, not assumed capacity-related */ }
  const elapsed = Date.now() - start;
  good.add(ok && elapsed <= 200 ? 1 : 0);
  slow.add(ok && elapsed > 200 ? 1 : 0);
  failed.add(ok ? 0 : 1);
  timeout.add(isTimeout ? 1 : 0);
  rejected.add(noSeed ? 1 : 0);
  unknown.add(!ok && !noSeed && !isTimeout ? 1 : 0);
  check(response || {}, {'station list is successful and seeded': () => ok});
}
