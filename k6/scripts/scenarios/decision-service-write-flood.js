import http from 'k6/http';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

// MED-553: one user floods writes; the service must answer with 429s and stay ready.
// Same environment variables as decision-service-load-test.js.
const baseUrl = (__ENV.DECISION_SERVICE_URL || 'http://decision-service-dev:8080').replace(/\/$/, '');
const token = __ENV.K6_TOKEN || '';
const projectId = String(__ENV.PROJECT_ID || '1');
const duration = __ENV.DURATION || '30s';

const rateLimited = new Counter('rate_limited');

export const options = {
  scenarios: {
    write_flood: { executor: 'constant-vus', vus: Number(__ENV.VUS || 20), duration, exec: 'flood' },
    readiness: {
      executor: 'constant-arrival-rate',
      rate: 1,
      timeUnit: '1s',
      duration,
      preAllocatedVUs: 1,
      exec: 'ready',
    },
  },
  thresholds: {
    'checks{scenario:readiness}': ['rate==1'],
    rate_limited: ['count>0'],
  },
};

function headers() {
  return {
    Authorization: `Bearer ${token}`,
    'Content-Type': 'application/json',
    'x-project-id': projectId,
  };
}

export function setup() {
  if (!token) {
    throw new Error('K6_TOKEN is required');
  }
  const created = http.post(
    `${baseUrl}/api/decisions/drafts`,
    JSON.stringify({ title: 'k6 write flood: Q4 budget shift' }),
    { headers: headers() },
  );
  if (created.status !== 201) {
    throw new Error(`Could not create the draft to update (${created.status})`);
  }
  return { slug: created.json('slug') };
}

// Every VU uses the same token, so all writes come from one user.
export function flood(data) {
  const res = http.patch(
    `${baseUrl}/api/decisions/drafts/${data.slug}`,
    JSON.stringify({ title: `k6 write flood: Q4 budget shift ${__VU}-${__ITER}` }),
    { headers: headers() },
  );
  check(res, { 'write is saved or rate limited': (r) => r.status === 200 || r.status === 429 });
  if (res.status === 429) {
    rateLimited.add(1);
  }
}

export function ready() {
  check(http.get(`${baseUrl}/ready`), { 'service stays ready': (r) => r.status === 200 });
}

export function teardown(data) {
  http.del(`${baseUrl}/api/decisions/${data.slug}`, null, { headers: headers() });
}
