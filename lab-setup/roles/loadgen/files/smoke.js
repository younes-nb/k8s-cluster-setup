import http from 'k6/http';
import { Counter } from 'k6/metrics';
import { check } from 'k6';
const attempted = new Counter('lab_attempted');
const good = new Counter('lab_good');
const failed = new Counter('lab_failed');
const slow = new Counter('lab_slow_success');
const timeout = new Counter('lab_timeout');
if (!__ENV.EXPERIMENT_ID || !__ENV.TARGET_URL) { throw new Error('Set a unique EXPERIMENT_ID and TARGET_URL before running'); }
export const options = { scenarios: { smoke: { executor: 'constant-arrival-rate', rate: 5, timeUnit: '1s', duration: '60s', preAllocatedVUs: 10, maxVUs: 30 } }, thresholds: { checks: ['rate==1'], dropped_iterations: ['count==0'] }, tags: { experiment: __ENV.EXPERIMENT_ID } };
export default function () {
  attempted.add(1);
  const r = http.get(__ENV.TARGET_URL + '/', {timeout: '10s', redirects: 0});
  const ok = r.status === 200;
  check(r, {'gateway HTTP 200': () => ok});
  good.add(ok && r.timings.duration <= 200 ? 1 : 0);
  slow.add(ok && r.timings.duration > 200 ? 1 : 0);
  failed.add(ok ? 0 : 1);
  timeout.add(r.error_code === 1050 ? 1 : 0);
}

