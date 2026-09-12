import { readStoredProject } from '../../../e2e/tasks/project-storage';

const encode = (project: unknown) => JSON.stringify({ state: { activeProject: project } });
const current = { id: 2, slug: 'current' };
const legacy = { id: 1, slug: 'legacy' };

test('prefers a complete new record', () => {
  expect(readStoredProject([encode(current), encode(legacy)])).toEqual(current);
});

test.each([null, '', '{invalid', '{}', encode(null), encode({ id: 2 }),
  encode({ slug: 'current' }), encode({ id: '2', slug: 'current' }),
  encode({ id: 0, slug: 'current' }), encode({ id: 2, slug: ' ' }),
])('falls back from missing, malformed or incomplete new data: %s', (raw) => {
  expect(readStoredProject([raw, encode(legacy)])).toEqual(legacy);
});

test('does not combine incomplete records', () => {
  expect(readStoredProject([encode({ id: 2 }), encode({ slug: 'legacy' })])).toBeNull();
});

test('can read newly hydrated data on the next poll', () => {
  expect(readStoredProject(['{}', null])).toBeNull();
  expect(readStoredProject([encode(current), null])).toEqual(current);
});
