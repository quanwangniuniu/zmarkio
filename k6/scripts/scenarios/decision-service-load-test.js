import http from 'k6/http';
import { check } from 'k6';

const baseUrl = (__ENV.DECISION_SERVICE_URL || 'http://decision-service-dev:8080').replace(/\/$/, '');
const token = __ENV.K6_TOKEN || '';
const projectId = String(__ENV.PROJECT_ID || '1');
const seedCount = Number(__ENV.SEED_DECISIONS || 30);
const targetVus = Number(__ENV.VUS || 200);
const rampDuration = __ENV.RAMP || '2m';
const holdDuration = __ENV.HOLD || '1m';

export const options = {
  scenarios: {
    read_decisions: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: rampDuration, target: targetVus },
        { duration: holdDuration, target: targetVus },
        { duration: '15s', target: 0 },
      ],
      gracefulRampDown: '15s',
    },
  },
  thresholds: {
    http_req_failed: ['rate<0.01'],
    'http_req_duration{name:list}': ['p(95)<1000'],
    'http_req_duration{name:detail}': ['p(95)<1000'],
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
  const slugs = [];
  for (let i = 0; i < seedCount; i += 1) {
    const response = http.post(
      `${baseUrl}/api/decisions/drafts`,
      JSON.stringify({ title: `k6 load test ${i + 1}` }),
      { headers: headers(), tags: { name: 'seed' } },
    );
    if (response.status === 201) {
      slugs.push(response.json('slug'));
    }
  }
  if (slugs.length === 0) {
    throw new Error('No seed decisions could be created');
  }
  return { slugs };
}

export default function (data) {
  const list = http.get(`${baseUrl}/api/decisions?project_id=${projectId}`, {
    headers: headers(),
    tags: { name: 'list' },
  });
  check(list, { 'list returns 200': (r) => r.status === 200 });

  const slug = data.slugs[Math.floor(Math.random() * data.slugs.length)];
  const detail = http.get(`${baseUrl}/api/decisions/${slug}`, {
    headers: headers(),
    tags: { name: 'detail' },
  });
  check(detail, { 'detail returns 200': (r) => r.status === 200 });
}

export function teardown(data) {
  for (const slug of data.slugs) {
    http.del(`${baseUrl}/api/decisions/${slug}`, null, { headers: headers(), tags: { name: 'cleanup' } });
  }
}
