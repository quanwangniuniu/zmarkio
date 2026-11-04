import { test, expect } from '@playwright/test';
import { waitForLayoutMain } from '../navigation/navigation-helpers';

/**
 * MED-264 acceptance test: "ask about my retrospective".
 *
 * Exercises the real Agent UI -> real HTTP/SSE chat endpoint -> real tenant
 * context -> real RAG retrieval -> real persisted citations, against the
 * MED-264 RAG Eval Fixture project seeded by generate_med264_seed_sql.py /
 * run_project_rag_eval.py. Auth is the dedicated E2E user provisioned by
 * `python manage.py ensure_med264_e2e_user` (see that command's docstring),
 * never a real developer's account -- reused via the existing real-login
 * `chromium` Playwright project (storageState: e2e/.auth/user.json, produced
 * by the existing e2e/auth.setup.ts, unmodified here).
 *
 * Assertions are retrieval/citation-centric rather than exact-LLM-string
 * based, since the assistant's wording is not contractually deterministic:
 * only the presence of a non-empty answer, a Sources block, a Retrospective
 * citation, and Nov-20 deployment evidence within that citation's snippet
 * are asserted -- not a specific full answer sentence.
 *
 * No live-Gemini test-mode stubbing is introduced (explicitly out of scope
 * for this ticket): this test makes a real LLM call and accepts that cost/
 * latency in exchange for exercising the real path end to end.
 *
 * Reruns against the same persisted E2E session may already contain earlier
 * MED-264 citation messages from a prior run, so `.last()` (not `.first()`)
 * on the citations-block locator is used throughout, gated by a count
 * increase after sending -- never assert against a stale block from an
 * earlier run.
 */

const RETROSPECTIVE_QUESTION =
  "When was Aurora Retail's engineering team's session-timeout fix finally deployed?";
const NOV_20_EVIDENCE_RE = /nov(?:ember)?\.?\s*20\b/i;

test('ask about my retrospective — grounded answer with persisted Retrospective citation', async ({ page }) => {
  // Real LLM + real retrieval + the citations reveal-queue animation can
  // legitimately take a while; give this one test more room than the
  // Playwright default rather than raising it globally. 240s (not 180s):
  // confirmed via a real run where the answer text had already rendered
  // (including its "[1]" citation marker) but the citations block still
  // hadn't appeared when the 180s budget ran out mid-poll -- the intro-modal
  // dismiss retry (up to two bounded 20s waits) plus real network/LLM
  // latency left the 120s citation poll too little room inside 180s total.
  test.setTimeout(240_000);

  await page.goto('/tasks');
  await waitForLayoutMain(page);

  // Sidebar's Agent toggle has no aria-label/testid -- its accessible name
  // comes from a plain <span>AI Agent</span> text node next to an icon (see
  // Sidebar.tsx). Confirmed via a real page snapshot: the actual label is
  // "AI Agent" (not "Agent"), and the sidebar already renders full
  // text-labeled buttons by default at this viewport -- there is no
  // separately-named "Expand sidebar"/"Collapse sidebar" toggle to drive
  // (navigation-helpers.ts's expandSidebar() assumes a UI pattern that no
  // longer matches this app; not touched here, just not needed for this
  // flow).
  const agentToggle = page.getByRole('button', { name: 'AI Agent' });
  await expect(agentToggle).toBeVisible({ timeout: 15_000 });
  await agentToggle.click();

  // First-time-use "token intro" modal (OnboardingTokenIntro.tsx) covers the
  // panel and intercepts clicks on this E2E user's first-ever Agent open --
  // confirmed via a real timeout trace showing that exact dialog blocking
  // the message input. It has a stable, always-present `aria-label="Close"`
  // button (top-right X, not tied to which of its 5 slides is showing), so
  // that's used rather than Escape even though the component also supports
  // Escape. Dismissal is persisted to localStorage per-user, so a rerun
  // where it's already been seen correctly needs no dismissal. It can also
  // mount late (after the panel itself has rendered), so it is dismissed
  // reactively around the actual click it blocks rather than guessed upfront.
  // exact: true is required: a non-exact match also catches the panel's
  // "Close AI Agent panel" button (substring "Close"), which is a strict-mode
  // violation that made isVisible() throw and get silently swallowed to
  // false by the catch below, even while the dialog was genuinely blocking.
  const introCloseButton = page.getByRole('button', { name: 'Close', exact: true });
  async function dismissIntroModalIfPresent(): Promise<boolean> {
    if (!(await introCloseButton.isVisible().catch(() => false))) {
      return false;
    }
    await introCloseButton.click();
    await expect(introCloseButton).toBeHidden({ timeout: 5_000 });
    return true;
  }

  // { exact: true } is required: getByLabel('message') without it does a
  // substring/case-insensitive match, which also catches the sidebar's
  // "Messages" nav button and the "Hide message boards" toggle -- a real
  // 3-way strict-mode violation confirmed via direct reproduction, not just
  // theoretical.
  const messageInput = page.getByLabel('message', { exact: true });
  await expect(messageInput).toBeVisible({ timeout: 15_000 });

  // No stable selector exists for the assistant's own text bubble (no
  // testid/role anywhere in MessageList.tsx), so "assistant content becomes
  // non-empty" is asserted as meaningful growth in the panel's rendered
  // text between before-send and after-citations-appear, rather than
  // reading one specific element.
  const textBeforeSend = await page.locator('body').innerText();

  // Persisted E2E session may already hold citation blocks from an earlier
  // run -- record the count now so the new block can be identified by
  // count increase, not by position.
  const citationsBlockLocator = page.locator('[data-agent-block$="-citations"]');
  const citationCountBeforeSend = await citationsBlockLocator.count();

  // .fill() sets the DOM value programmatically and leaves the Send button
  // permanently disabled -- confirmed by direct reproduction (10s poll,
  // never re-enables) -- because this input's enabled state depends on
  // React state that isn't updated by a programmatic value set. Real
  // keystroke-by-keystroke input is required, matching the existing
  // convention already used for this exact reason in
  // e2e/messages/messages-helpers.ts's trySendMessage().
  // The first-time intro modal may mount after this point and intercept the
  // click; if so, dismiss it and retry the click exactly once more. A bounded
  // timeout on this first attempt is required for that retry to ever run --
  // this project has no configured actionTimeout, so an unbounded click()
  // just keeps retrying against the same blocking dialog for the entire
  // test.setTimeout(180_000) budget instead of failing fast enough to react.
  try {
    await messageInput.click({ timeout: 20_000 });
  } catch (err) {
    if (!(await dismissIntroModalIfPresent())) {
      throw err;
    }
    await messageInput.click();
  }
  await messageInput.pressSequentially(RETROSPECTIVE_QUESTION, { delay: 10 });
  const sendButton = page.getByRole('button', { name: 'Send' });
  await expect(sendButton).toBeEnabled({ timeout: 5_000 });
  await sendButton.click();

  // Citations only attach to the message after the SSE stream's "done"
  // event triggers a GET /api/agent/sessions/:id/ refetch (see
  // AgentChatPage.tsx's onDone -> refreshSession), so waiting for the count
  // to increase is the correct "stream fully complete, new citation
  // attached" signal, not just the last SSE chunk.
  await expect
    .poll(() => citationsBlockLocator.count(), { timeout: 120_000 })
    .toBeGreaterThan(citationCountBeforeSend);
  const citationsBlock = citationsBlockLocator.last();

  const textAfterCitations = await page.locator('body').innerText();
  expect(textAfterCitations.length).toBeGreaterThan(textBeforeSend.length + 50);

  await expect(citationsBlock.getByText(/^Sources \(\d+\)$/)).toBeVisible();

  const retrospectiveBadge = citationsBlock.getByText('Retrospective', { exact: false }).first();
  await expect(retrospectiveBadge).toBeVisible();

  await expect(citationsBlock).toContainText(NOV_20_EVIDENCE_RE);

  // --- Reload/reopen: assert the answer + citations are restored from
  // persisted AgentMessage.metadata via GET /api/agent/sessions/:id/, not
  // just held in in-memory SSE state. sessionStorage (holding
  // agent-session-id) survives a same-tab reload; the side panel's own
  // open/closed UI state does not, so it's re-opened explicitly. ---
  await page.reload();
  await waitForLayoutMain(page);

  const agentToggleAfterReload = page.getByRole('button', { name: 'AI Agent' });
  await expect(agentToggleAfterReload).toBeVisible({ timeout: 15_000 });
  await agentToggleAfterReload.click();

  // Reopening the panel lands on a fresh/empty "New Agent" state, not the
  // just-created session -- confirmed via a real snapshot showing the prior
  // session only as a message-board list entry (titled with the question
  // text) that must be clicked to actually load its persisted messages.
  // Prior runs against this same persisted E2E account leave earlier message
  // boards with the identical question title, so (matching this file's
  // existing .last()-over-.first() convention for exactly this
  // reruns-accumulate reason) the most-recently-created one is selected.
  const messageBoardButton = page.getByRole('button', { name: RETROSPECTIVE_QUESTION, exact: true }).last();
  await expect(messageBoardButton).toBeVisible({ timeout: 15_000 });
  await messageBoardButton.click();

  const citationsBlockLocatorAfterReload = page.locator('[data-agent-block$="-citations"]');
  await expect
    .poll(() => citationsBlockLocatorAfterReload.count(), { timeout: 30_000 })
    .toBeGreaterThan(citationCountBeforeSend);
  const citationsBlockAfterReload = citationsBlockLocatorAfterReload.last();
  await expect(citationsBlockAfterReload).toBeVisible();

  await expect(citationsBlockAfterReload.getByText(/^Sources \(\d+\)$/)).toBeVisible();
  const retrospectiveBadgeAfterReload = citationsBlockAfterReload
    .getByText('Retrospective', { exact: false })
    .first();
  await expect(retrospectiveBadgeAfterReload).toBeVisible();
  await expect(citationsBlockAfterReload).toContainText(NOV_20_EVIDENCE_RE);

  // Weak, secondary signal only (per MED-264 decision: exact LLM wording is
  // not the acceptance criterion) -- not asserted as a hard failure if the
  // model phrases the date differently, but recorded here for visibility.
  const answerMentionsNov20 = NOV_20_EVIDENCE_RE.test(textAfterCitations);
  test.info().annotations.push({
    type: 'note',
    description: `Assistant answer text matched /${NOV_20_EVIDENCE_RE.source}/i: ${answerMentionsNov20}`,
  });
});
