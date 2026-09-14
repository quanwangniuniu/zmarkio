-- MED-264 eval-fixture seed SQL -- GENERATED FILE, DO NOT HAND-EDIT.
-- Source of truth: /app/rag/tests/fixtures/project_rag_eval_dataset.json (dataset_version='2.1.0').
-- Regenerate with: python manage.py generate_med264_seed_sql
-- Verify in sync with: python manage.py generate_med264_seed_sql --check
--
-- Usage (target_schema is REQUIRED, no default -- never relies on the
-- caller's existing search_path, to avoid silently seeding the wrong tenant):
--   psql <connection args> -v target_schema=org_rag_eval_harness -f med264_seed.sql
--
-- Portable: project/user rows are resolved at execution time by stable natural
-- keys (organization name 'RAG Eval Harness' + project name 'MED-264 RAG Eval Fixture'; user
-- email 'rag-eval-harness-bot@rag-eval-harness.internal'), never by the generating machine's local numeric ids.
--
-- Idempotent + collision-safe: every row is ON CONFLICT (id) DO NOTHING followed by a
-- verification block. Re-running against a schema that already has these exact rows is
-- a safe no-op; an id collision with unrelated data aborts the whole transaction (no
-- partial seed is ever left behind).
-- 120 row(s).

BEGIN;

-- psql's :'var' / :"var" substitution is a client-side text pass over
-- top-level statements; whether it also reaches inside a $$-quoted DO
-- body is not something to assume. So the schema name is captured ONCE
-- here (top-level statement, substitution definitely applies) into a
-- transaction-local setting, and every DO block below reads it back via
-- plain current_setting() -- no psql variable syntax inside any $$ body.
SELECT set_config('med264.target_schema', :'target_schema', true);

-- Preflight: fail fast with a clear MED-264-specific error instead of a
-- confusing downstream FK/NOT NULL failure mid-seed.
DO $$
BEGIN
  IF to_regnamespace(current_setting('med264.target_schema')) IS NULL THEN
    RAISE EXCEPTION 'MED-264 seed preflight: target schema "%" does not exist', current_setting('med264.target_schema');
  END IF;
END $$;

SET LOCAL search_path TO :"target_schema", public;

DO $$
DECLARE
  project_count int;
  user_count int;
BEGIN
  SELECT count(*) INTO project_count FROM core_project WHERE name = 'MED-264 RAG Eval Fixture';
  IF project_count != 1 THEN
    RAISE EXCEPTION 'MED-264 seed preflight: expected exactly 1 eval Project (name=%) in schema %, found %',
      'MED-264 RAG Eval Fixture', current_setting('med264.target_schema'), project_count;
  END IF;

  SELECT count(*) INTO user_count FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal';
  IF user_count != 1 THEN
    RAISE EXCEPTION 'MED-264 seed preflight: expected exactly 1 eval User (email=%) in public, found %',
      'rag-eval-harness-bot@rag-eval-harness.internal', user_count;
  END IF;
END $$;

INSERT INTO "meetings_meetingtypedefinition" ("id", "project_id", "slug", "label") VALUES (-1102370946, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'planning', 'Planning')
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingtypedefinition" WHERE "id" = -1102370946 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "slug" = 'planning') THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingtypedefinition is occupied by unrelated data (expected %)', -1102370946, 'MeetingTypeDefinition:planning';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1771777911, 'weekly-standup-sept-2-2026', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Weekly Standup — Sept 2, 2026', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1771777911 AND "slug" = 'weekly-standup-sept-2-2026' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Weekly Standup — Sept 2, 2026' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1771777911, 'Meeting:meeting-001';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1854878059, -1771777911, 'Weekly Standup — Sept 2, 2026
Attendees: Priya, Marcus, Dev.
Priya: Finished the homepage banner mockups, sent to client for review.
Marcus: Blocked on stock photography licensing for the Aurora Retail winter sale page.
Dev: QA pass on the new email template starts tomorrow.
No blockers reported for the sprint otherwise.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1854878059 AND "meeting_id" = -1771777911) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1854878059, 'MeetingDocument:meeting-001';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1840908246, 'kickoff-aurora-retail-q4-campaign', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Kickoff: Aurora Retail Q4 Campaign', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1840908246 AND "slug" = 'kickoff-aurora-retail-q4-campaign' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Kickoff: Aurora Retail Q4 Campaign' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1840908246, 'Meeting:meeting-002';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1110515164, -1840908246, 'Kickoff: Aurora Retail Q4 Campaign — Sept 3, 2026
Attendees: Priya (Creative Lead), Marcus (Strategy), Jade (Account Manager), client stakeholders Lena Ortiz and Sam Petrov from Aurora Retail.
Jade opened by confirming the campaign budget of $85,000 for Q4, covering paid social, email, and landing page production.
Lena confirmed the primary goal is driving holiday gift-card sales, with a target of 4,000 gift cards sold by December 24.
Marcus proposed a three-phase rollout: teaser (Oct 1-15), main push (Nov 1-30), and last-chance reminders (Dec 15-24).
Action item: Priya to deliver first creative concepts by Sept 20.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1110515164 AND "meeting_id" = -1840908246) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1110515164, 'MeetingDocument:meeting-002';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1581148036, 'q3-marketing-sync-launch-timeline', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Q3 Marketing Sync — Launch Timeline', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1581148036 AND "slug" = 'q3-marketing-sync-launch-timeline' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Q3 Marketing Sync — Launch Timeline' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1581148036, 'Meeting:meeting-003';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1589397403, -1581148036, 'Q3 Marketing Sync — Launch Timeline — Aug 12, 2026
Attendees: Priya, Marcus, Jade.
Marcus confirmed the Aurora Retail Q3 loyalty-program landing page will launch on October 15, 2026.
The launch will coincide with the loyalty program''s public announcement email.
Jade noted the client wants a soft-launch email to VIP customers three days earlier, on October 12.
Priya flagged that the hero image is still pending final approval from the client''s legal team.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1589397403 AND "meeting_id" = -1581148036) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1589397403, 'MeetingDocument:meeting-003';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1604877319, 'q3-sales-sync-pipeline-review', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Q3 Sales Sync — Pipeline Review', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1604877319 AND "slug" = 'q3-sales-sync-pipeline-review' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Q3 Sales Sync — Pipeline Review' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1604877319, 'Meeting:meeting-004';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1623858324, -1604877319, 'Q3 Sales Sync — Pipeline Review — Aug 14, 2026
Attendees: Dev, Jade, Sales team.
The team reviewed the Q3 pipeline and discussed the sales-enablement deck launch, which Dev confirmed will go live internally on November 1, 2026.
Jade noted this is separate from the client-facing Aurora Retail loyalty launch and should not be referenced in external materials.
Dev will circulate the finished deck to the sales team the week before launch.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1623858324 AND "meeting_id" = -1604877319) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1623858324, 'MeetingDocument:meeting-004';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1353494817, 'quarterly-business-review-full-transcript', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Quarterly Business Review — Full Transcript', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1353494817 AND "slug" = 'quarterly-business-review-full-transcript' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Quarterly Business Review — Full Transcript' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1353494817, 'Meeting:meeting-005';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1473941208, -1353494817, 'Quarterly Business Review — Full Transcript — Sept 25, 2026
Attendees: Jade (Account Manager), Priya (Creative Lead), Marcus (Strategy), Dev (Engineering), and agency leadership: Corinne Blake (Managing Director).

Budget Review
Corinne opened with the Q3 budget summary: total agency spend was $412,000 against a planned $430,000, a 4% underspend driven mainly by delayed vendor invoices from the print supplier. Marcus noted that the Aurora Retail account alone accounted for $138,000 of that spend, primarily creative production and paid media.

Headcount
Corinne announced that the team will add one new mid-level copywriter in Q4, with an anticipated start date of November 2. Priya will lead the hiring process and expects to have a shortlist by October 10.

Campaign Performance
Marcus presented Q3 campaign results: the Aurora Retail loyalty-program launch generated 5,600 sign-ups against a goal of 4,000, a 40% overperformance. Click-through rate on the launch email was 6.2%, well above the agency''s 3.5% benchmark. However, the winter-sale teaser campaign underperformed, generating only 1,100 landing-page visits against a goal of 3,000.

Risks
Dev flagged a recurring risk: the checkout flow on the Aurora Retail site has intermittent failures under high traffic, which caused a partial outage during the loyalty-program launch on October 15. Dev is coordinating with Aurora Retail''s engineering team on a fix before the Q4 holiday push. Corinne asked that this risk be tracked explicitly in the next retrospective.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1473941208 AND "meeting_id" = -1353494817) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1473941208, 'MeetingDocument:meeting-005';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1856231474, 'client-check-in-aurora-retail', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Client Check-in — Aurora Retail', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1856231474 AND "slug" = 'client-check-in-aurora-retail' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Client Check-in — Aurora Retail' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1856231474, 'Meeting:meeting-006';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1371443709, -1856231474, 'Client Check-in — Aurora Retail — Sept 30, 2026
Attendees: Jade, Lena Ortiz (Aurora Retail).
Lena approved the final homepage banner concept, Option B (blue gradient with gift-card imagery).
Jade confirmed the winter sale landing page copy is on track for review by Oct 5.
No new action items.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1371443709 AND "meeting_id" = -1856231474) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1371443709, 'MeetingDocument:meeting-006';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1524985860, 'creative-review-homepage-banner-concepts', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Creative Review — Homepage Banner Concepts', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1524985860 AND "slug" = 'creative-review-homepage-banner-concepts' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Creative Review — Homepage Banner Concepts' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1524985860, 'Meeting:meeting-007';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1749581010, -1524985860, 'Creative Review — Homepage Banner Concepts — Sept 18, 2026
Attendees: Priya, Marcus, Jade, freelance designer Theo Nkemelu.
Theo presented three homepage banner concepts: Option A (warm red/orange holiday theme), Option B (blue gradient with gift-card imagery), and Option C (minimalist white with gold accents).
Priya recommended Option B as the strongest fit for Aurora Retail''s brand guidelines.
Marcus asked Theo to prepare a mobile-responsive version of Option B for the next review.
Jade will present all three options to the client on Sept 22.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1749581010 AND "meeting_id" = -1524985860) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1749581010, 'MeetingDocument:meeting-007';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1681588191, 'vendor-call-print-supplier', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Vendor Call — Print Supplier', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1681588191 AND "slug" = 'vendor-call-print-supplier' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Vendor Call — Print Supplier' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1681588191, 'Meeting:meeting-008';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1585510187, -1681588191, 'Vendor Call — Print Supplier — Sept 8, 2026
Attendees: Marcus, print supplier rep Alan Cho.
Alan confirmed purchase order PO-88214 for the holiday gift-card inserts has shipped, with delivery expected Sept 14.
Marcus asked Alan to send tracking details once available.
Unit cost was confirmed at $0.42 per insert for the run of 20,000 units.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1585510187 AND "meeting_id" = -1681588191) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1585510187, 'MeetingDocument:meeting-008';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1329059990, 'retrospective-planning-meeting', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Retrospective Planning Meeting', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1329059990 AND "slug" = 'retrospective-planning-meeting' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Retrospective Planning Meeting' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1329059990, 'Meeting:meeting-009';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1216298757, -1329059990, 'Retrospective Planning Meeting — Sept 10, 2026
Attendees: Dev, Priya, Jade.
The team agreed to hold sprint retrospectives every two weeks going forward, alternating between a written retro doc and a live session.
Dev proposed adding a standing agenda item for tracking recurring bugs across sprints, specifically citing the checkout flow issue as a candidate.
Jade will own scheduling the retro calendar invites starting Sprint 15.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1216298757 AND "meeting_id" = -1329059990) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1216298757, 'MeetingDocument:meeting-009';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1905614721, 'all-hands-fy26-planning', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'All-Hands: FY26 Planning', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1905614721 AND "slug" = 'all-hands-fy26-planning' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'All-Hands: FY26 Planning' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1905614721, 'Meeting:meeting-010';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1683978274, -1905614721, 'All-Hands: FY26 Planning — Oct 1, 2026
Attendees: Full agency team, led by Corinne Blake (Managing Director).

Agency Goals
Corinne outlined three company-wide goals for FY26: grow revenue by 18%, launch a formal RAG-assisted internal knowledge tool for account teams, and reduce average campaign turnaround time from 21 days to 14 days.

New Client Wins
Corinne announced two new client wins closing in October: a mid-size home goods brand, Willow & Pine, and a regional credit union, Northbridge Financial. Onboarding for Willow & Pine begins Oct 20; Northbridge Financial begins Nov 3.

Tooling Investment
Dev presented the roadmap for the internal RAG-assisted knowledge tool, which will let account teams ask natural-language questions across meeting notes, Notion drafts, and retrospectives instead of searching manually. Dev estimated a beta rollout to the Aurora Retail account team by end of Q4.

Org Changes
Corinne confirmed Priya will be promoted to Head of Creative effective Nov 1, taking on hiring and creative-direction responsibilities across all accounts. Marcus will take on an additional strategy lead role for the two new client accounts.

Q&A
In the Q&A, a team member asked about pricing changes for FY26; Corinne said pricing details would be shared in a separate finance meeting in November and were not finalized as of this all-hands.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1683978274 AND "meeting_id" = -1905614721) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1683978274, 'MeetingDocument:meeting-010';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1944187447, '11-jade-manager', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), '1:1 — Jade & Manager', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1944187447 AND "slug" = '11-jade-manager' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = '1:1 — Jade & Manager' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1944187447, 'Meeting:meeting-011';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1904159373, -1944187447, '1:1 — Jade & Manager — Sept 12, 2026
Attendees: Jade, Corinne.
Jade raised concerns about bandwidth on the Aurora Retail account given the upcoming winter sale push.
Corinne agreed to loop in a second account coordinator starting Oct 1 to help balance the workload.
No other topics discussed.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1904159373 AND "meeting_id" = -1944187447) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1904159373, 'MeetingDocument:meeting-011';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1821612789, 'post-launch-debrief-nova-campaign', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Post-Launch Debrief — Nova Campaign', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1821612789 AND "slug" = 'post-launch-debrief-nova-campaign' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Post-Launch Debrief — Nova Campaign' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1821612789, 'Meeting:meeting-012';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1880676240, -1821612789, 'Post-Launch Debrief — Nova Campaign — Sept 5, 2026
Attendees: Priya, Marcus, Dev, Jade.
The team reviewed the Project Nova product-launch campaign, which ran Aug 1-31.
Marcus reported the campaign generated 12,400 website visits and 890 email sign-ups, against goals of 10,000 visits and 750 sign-ups.
Dev noted the landing page load time averaged 2.1 seconds, within the 3-second target.
Priya flagged that the paid social creative underperformed relative to organic posts and recommended revisiting the ad creative strategy for the next campaign.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1880676240 AND "meeting_id" = -1821612789) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1880676240, 'MeetingDocument:meeting-012';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1952811683, 'blog-draft-5-ways-to-prep-for-holiday-shopping', 'Blog Draft: 5 Ways to Prep for Holiday Shopping', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1952811683 AND "slug" = 'blog-draft-5-ways-to-prep-for-holiday-shopping' AND "title" = 'Blog Draft: 5 Ways to Prep for Holiday Shopping' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1952811683, 'Draft:notion-001';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1969438629, -1952811683, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1969438629 AND "draft_id" = -1952811683 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1969438629, 'DraftProjectLink:notion-001';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1582970635, -1952811683, 'text', '{"text": "Blog Draft: 5 Ways to Prep for Holiday Shopping\nStatus: In review\nAuthor: Priya\n\nIntro: The holidays sneak up fast, and shoppers who plan ahead save both money and stress. Here are five practical ways to get ready for the season.\n\n1. Set a gift budget early and track it.\n2. Make a list of recipients before you start browsing.\n3. Sign up for loyalty programs to unlock early-access deals \u2014 Aurora Retail''s loyalty program, for example, gives members first access to sales before the public.\n4. Compare shipping deadlines across retailers.\n5. Keep gift receipts organized for easy returns.\n\nCTA: Join the Aurora Retail loyalty program today."}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1582970635 AND "draft_id" = -1952811683) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1582970635, 'ContentBlock:notion-001';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1578818069, 'email-draft-v1-welcome-series-step-1', 'Email Draft v1: Welcome Series — Step 1', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1578818069 AND "slug" = 'email-draft-v1-welcome-series-step-1' AND "title" = 'Email Draft v1: Welcome Series — Step 1' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1578818069, 'Draft:notion-002';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1957742132, -1578818069, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1957742132 AND "draft_id" = -1578818069 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1957742132, 'DraftProjectLink:notion-002';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1142838796, -1578818069, 'text', '{"text": "Email Draft v1: Welcome Series \u2014 Step 1\nStatus: Draft \u2014 needs revision\nSubject line: Welcome to Aurora Retail!\n\nBody: Thanks for joining Aurora Retail. As a welcome gift, enjoy 10% off your first order with code WELCOME10.\n\nNote from Priya: Marketing flagged that 10% is too low compared to competitor welcome offers; revised version should use 15%. Do not send this version \u2014 see v2."}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1142838796 AND "draft_id" = -1578818069) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1142838796, 'ContentBlock:notion-002';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1592565401, 'social-caption-bank-october', 'Social Caption Bank — October', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1592565401 AND "slug" = 'social-caption-bank-october' AND "title" = 'Social Caption Bank — October' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1592565401, 'Draft:notion-003';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1270269083, -1592565401, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1270269083 AND "draft_id" = -1592565401 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1270269083, 'DraftProjectLink:notion-003';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1803519789, -1592565401, 'text', '{"text": "Social Caption Bank \u2014 October\nStatus: Approved\n\n1. \"Cozy season, cozy savings. \ud83c\udf42\" \u2014 Instagram, Oct 3\n2. \"Your holiday list called \u2014 it wants Aurora Retail.\" \u2014 Instagram, Oct 10\n3. \"First dibs go to loyalty members. Join free today.\" \u2014 Instagram, Oct 17"}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1803519789 AND "draft_id" = -1592565401) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1803519789, 'ContentBlock:notion-003';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1810138572, 'landing-page-copy-aurora-retail-winter-sale', 'Landing Page Copy: Aurora Retail Winter Sale', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1810138572 AND "slug" = 'landing-page-copy-aurora-retail-winter-sale' AND "title" = 'Landing Page Copy: Aurora Retail Winter Sale' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1810138572, 'Draft:notion-004';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1731630070, -1810138572, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1731630070 AND "draft_id" = -1810138572 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1731630070, 'DraftProjectLink:notion-004';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1877142385, -1810138572, 'text', '{"text": "Landing Page Copy: Aurora Retail Winter Sale\nStatus: Client review\nAuthor: Priya, edited by Marcus\n\nHero Section\nHeadline: \"Winter Savings Start Here\"\nSubhead: \"Up to 40% off sitewide, plus free shipping on orders over $50.\"\nCTA button: \"Shop the Sale\"\n\nLoyalty Callout Section\nHeadline: \"Loyalty Members Get First Access\"\nBody: Loyalty members can shop the winter sale three days early, starting November 12, before it opens to the public on November 15. Sign up is free and takes less than a minute.\n\nProduct Grid Intro\nHeadline: \"Top Picks for Gift-Giving\"\nBody: From cozy home goods to statement accessories, these are the picks our stylists say will make the best gifts this season.\n\nFAQ Section\nQ: When does the sale end?\nA: The winter sale runs through December 24, while supplies last.\nQ: Can I combine the sale discount with a loyalty coupon?\nA: Yes, loyalty coupons can be stacked with the sitewide winter sale discount, up to a maximum combined discount of 50%.\nQ: Is the sale available in-store?\nA: This promotion is online-only; in-store pricing may differ.\n\nFooter CTA\nHeadline: \"Don''t Miss Out\"\nBody: Join the Aurora Retail loyalty program now to unlock early access before November 12."}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1877142385 AND "draft_id" = -1810138572) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1877142385, 'ContentBlock:notion-004';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1639556164, 'email-draft-v2-welcome-series-step-1-revised', 'Email Draft v2: Welcome Series — Step 1 (Revised)', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1639556164 AND "slug" = 'email-draft-v2-welcome-series-step-1-revised' AND "title" = 'Email Draft v2: Welcome Series — Step 1 (Revised)' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1639556164, 'Draft:notion-005';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1472897246, -1639556164, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1472897246 AND "draft_id" = -1639556164 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1472897246, 'DraftProjectLink:notion-005';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1679639233, -1639556164, 'text', '{"text": "Email Draft v2: Welcome Series \u2014 Step 1 (Revised)\nStatus: Approved for send\nSubject line: Welcome to Aurora Retail \u2014 here''s 15% off\n\nBody: Thanks for joining Aurora Retail. As a welcome gift, enjoy 15% off your first order with code WELCOME15.\n\nNote from Priya: Updated per Marketing''s feedback to be competitive with industry-standard welcome offers. This is the version to send starting Oct 1."}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1679639233 AND "draft_id" = -1639556164) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1679639233, 'ContentBlock:notion-005';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1959981006, 'press-release-snippet-product-launch', 'Press Release Snippet — Product Launch', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1959981006 AND "slug" = 'press-release-snippet-product-launch' AND "title" = 'Press Release Snippet — Product Launch' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1959981006, 'Draft:notion-006';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1940893152, -1959981006, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1940893152 AND "draft_id" = -1959981006 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1940893152, 'DraftProjectLink:notion-006';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1960772647, -1959981006, 'text', '{"text": "Press Release Snippet \u2014 Product Launch\nStatus: Draft\n\n\"Aurora Retail today announced the launch of its redesigned loyalty program, giving members early access to seasonal sales and exclusive gift-card bonuses,\" said Lena Ortiz, VP of Marketing at Aurora Retail. The program launched October 15, 2026."}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1960772647 AND "draft_id" = -1959981006) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1960772647, 'ContentBlock:notion-006';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1422865561, 'case-study-draft-aurora-retail-results', 'Case Study Draft: Aurora Retail Results', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1422865561 AND "slug" = 'case-study-draft-aurora-retail-results' AND "title" = 'Case Study Draft: Aurora Retail Results' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1422865561, 'Draft:notion-007';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1607478088, -1422865561, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1607478088 AND "draft_id" = -1422865561 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1607478088, 'DraftProjectLink:notion-007';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1126145186, -1422865561, 'text', '{"text": "Case Study Draft: Aurora Retail Results\nStatus: In progress\nAuthor: Marcus\n\nOverview: This case study covers the Q3 loyalty-program launch for Aurora Retail.\n\nResults: The loyalty-program launch generated 5,600 sign-ups in the first month against a goal of 4,000 \u2014 a 40% overperformance. Email click-through rate hit 6.2%, compared to the agency''s 3.5% benchmark average.\n\nClient quote (pending approval): \"The team''s execution on the loyalty launch exceeded every benchmark we set.\" \u2014 Lena Ortiz, VP of Marketing, Aurora Retail."}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1126145186 AND "draft_id" = -1422865561) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1126145186, 'ContentBlock:notion-007';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1313114950, 'ad-copy-variations-meta-feed', 'Ad Copy Variations — Meta Feed', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1313114950 AND "slug" = 'ad-copy-variations-meta-feed' AND "title" = 'Ad Copy Variations — Meta Feed' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1313114950, 'Draft:notion-008';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1129306507, -1313114950, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1129306507 AND "draft_id" = -1313114950 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1129306507, 'DraftProjectLink:notion-008';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1939273094, -1313114950, 'text', '{"text": "Ad Copy Variations \u2014 Meta Feed\nStatus: Approved\n\nVariant A: \"40% off winter must-haves. Loyalty members shop first.\"\nVariant B: \"Your gift list, sorted. Up to 40% off sitewide.\"\nVariant C: \"Early access starts Nov 12 for loyalty members.\""}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1939273094 AND "draft_id" = -1313114950) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1939273094, 'ContentBlock:notion-008';
  END IF;
END $$;

INSERT INTO "retrospective_retrospectivetask" ("id", "campaign_id", "status", "scheduled_at", "decision", "confidence_level", "primary_assumption", "key_risk_ignore", "outcome_compared_to_expectation", "biggest_wrong_assumption", "would_make_same_decision_again", "started_at", "completed_at", "report_url", "report_generated_at", "reviewed_by_id", "reviewed_at", "created_at", "updated_at", "created_by_id") VALUES ('5f7f5a07-a19b-57ac-89aa-355c5939fe1b', (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'scheduled', now(), 'Sprint 14 Retrospective', 3, 'Sprint 14 Retrospective — Sept 1, 2026
Participants: Dev, Priya, Marcus, Jade.

What went well: The homepage banner concepts were delivered a day ahead of schedule. Client feedback turnaround was faster than usual, at under 24 hours.

What didn''t go well: Two email templates had to be rebuilt after a last-minute brand-guideline change wasn''t communicated to the design team in time.

Action items: Jade to create a shared brand-guideline changelog so design and copy teams are notified automatically.', '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, now(), now(), (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'))
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "retrospective_retrospectivetask" WHERE "id" = '5f7f5a07-a19b-57ac-89aa-355c5939fe1b' AND "campaign_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "decision" = 'Sprint 14 Retrospective' AND "created_by_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in retrospective_retrospectivetask is occupied by unrelated data (expected %)', '5f7f5a07-a19b-57ac-89aa-355c5939fe1b', 'RetrospectiveTask:retro-001';
  END IF;
END $$;

INSERT INTO "retrospective_retrospectivetask" ("id", "campaign_id", "status", "scheduled_at", "decision", "confidence_level", "primary_assumption", "key_risk_ignore", "outcome_compared_to_expectation", "biggest_wrong_assumption", "would_make_same_decision_again", "started_at", "completed_at", "report_url", "report_generated_at", "reviewed_by_id", "reviewed_at", "created_at", "updated_at", "created_by_id") VALUES ('49ff0eae-f941-54db-a763-dafc28f605d2', (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'scheduled', now(), 'Sprint 15 Retrospective — Checkout Flow Bug', 3, 'Sprint 15 Retrospective — Checkout Flow Bug — Sept 15, 2026
Participants: Dev, Jade, Marcus.

What went well: The loyalty-program landing page shipped on time for the October 15 launch.

What didn''t go well: Dev identified an intermittent checkout flow failure under high traffic, first observed during load testing on Sept 13. Root cause was traced to a session-timeout misconfiguration on Aurora Retail''s checkout server, not on the agency''s landing page.

Action items: Dev to share findings with Aurora Retail''s engineering team and follow up before the winter sale launch.', '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, now(), now(), (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'))
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "retrospective_retrospectivetask" WHERE "id" = '49ff0eae-f941-54db-a763-dafc28f605d2' AND "campaign_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "decision" = 'Sprint 15 Retrospective — Checkout Flow Bug' AND "created_by_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in retrospective_retrospectivetask is occupied by unrelated data (expected %)', '49ff0eae-f941-54db-a763-dafc28f605d2', 'RetrospectiveTask:retro-002';
  END IF;
END $$;

INSERT INTO "retrospective_retrospectivetask" ("id", "campaign_id", "status", "scheduled_at", "decision", "confidence_level", "primary_assumption", "key_risk_ignore", "outcome_compared_to_expectation", "biggest_wrong_assumption", "would_make_same_decision_again", "started_at", "completed_at", "report_url", "report_generated_at", "reviewed_by_id", "reviewed_at", "created_at", "updated_at", "created_by_id") VALUES ('6ce8487a-eeec-5a5d-a1c7-56056b2f8369', (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'scheduled', now(), 'Retro: Nova Launch Week', 3, 'Retro: Nova Launch Week — Sept 6, 2026
Participants: Priya, Marcus, Dev, Jade.

Went well: Landing page load time stayed under the 3-second target throughout launch week.
Didn''t go well: Paid social creative underperformed organic posts; team agreed to revisit ad creative strategy next campaign.', '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, now(), now(), (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'))
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "retrospective_retrospectivetask" WHERE "id" = '6ce8487a-eeec-5a5d-a1c7-56056b2f8369' AND "campaign_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "decision" = 'Retro: Nova Launch Week' AND "created_by_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in retrospective_retrospectivetask is occupied by unrelated data (expected %)', '6ce8487a-eeec-5a5d-a1c7-56056b2f8369', 'RetrospectiveTask:retro-003';
  END IF;
END $$;

INSERT INTO "retrospective_retrospectivetask" ("id", "campaign_id", "status", "scheduled_at", "decision", "confidence_level", "primary_assumption", "key_risk_ignore", "outcome_compared_to_expectation", "biggest_wrong_assumption", "would_make_same_decision_again", "started_at", "completed_at", "report_url", "report_generated_at", "reviewed_by_id", "reviewed_at", "created_at", "updated_at", "created_by_id") VALUES ('84037f8a-3e26-5edf-9db1-42750bd21a66', (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'scheduled', now(), 'Quarterly Retrospective — Q3 Wrap-up', 3, 'Quarterly Retrospective — Q3 Wrap-up — Sept 28, 2026
Participants: Full account team plus Corinne Blake.

What went well
The Aurora Retail loyalty-program launch overperformed its sign-up goal by 40%, reaching 5,600 sign-ups against a target of 4,000. The team also successfully shifted to a two-week retrospective cadence, which surfaced the checkout flow issue earlier than it would have been caught otherwise.

What didn''t go well
The winter-sale teaser campaign underperformed, generating only 1,100 landing-page visits against a goal of 3,000 — the team attributed this to a delayed creative-approval cycle that pushed the teaser''s launch back by six days. Print vendor delays also caused a minor budget underspend that complicated Q3 financial reporting.

Recurring themes
This is the second consecutive quarter where a brand-guideline change wasn''t communicated to the full team in time, causing rework. The team also flagged that the checkout flow issue identified in Sprint 15 recurred again in a later sprint and was not fully resolved before the winter sale.

Action items
1. Formalize the brand-guideline changelog process (owner: Jade).
2. Escalate the checkout flow issue to Aurora Retail''s engineering leadership directly rather than routing through the account team (owner: Dev).
3. Build creative-approval buffer time into future campaign timelines (owner: Priya).', '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, now(), now(), (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'))
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "retrospective_retrospectivetask" WHERE "id" = '84037f8a-3e26-5edf-9db1-42750bd21a66' AND "campaign_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "decision" = 'Quarterly Retrospective — Q3 Wrap-up' AND "created_by_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in retrospective_retrospectivetask is occupied by unrelated data (expected %)', '84037f8a-3e26-5edf-9db1-42750bd21a66', 'RetrospectiveTask:retro-004';
  END IF;
END $$;

INSERT INTO "retrospective_retrospectivetask" ("id", "campaign_id", "status", "scheduled_at", "decision", "confidence_level", "primary_assumption", "key_risk_ignore", "outcome_compared_to_expectation", "biggest_wrong_assumption", "would_make_same_decision_again", "started_at", "completed_at", "report_url", "report_generated_at", "reviewed_by_id", "reviewed_at", "created_at", "updated_at", "created_by_id") VALUES ('71f492ef-4fd2-55ae-94b5-0badd0b6afa5', (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'scheduled', now(), 'Sprint 18 Retrospective — Checkout Flow Regression', 3, 'Sprint 18 Retrospective — Checkout Flow Regression — Oct 27, 2026
Participants: Dev, Jade.

What went well: The Willow & Pine onboarding kicked off smoothly with no blockers.

What didn''t go well: The checkout flow issue from Sprint 15 recurred during a traffic spike on Oct 24. Dev confirmed Aurora Retail''s engineering team had not yet deployed the session-timeout fix discussed in Sprint 15.

Action items: Dev to escalate directly to Aurora Retail''s engineering lead rather than the account team, given the fix has been pending for six weeks.', '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, now(), now(), (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'))
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "retrospective_retrospectivetask" WHERE "id" = '71f492ef-4fd2-55ae-94b5-0badd0b6afa5' AND "campaign_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "decision" = 'Sprint 18 Retrospective — Checkout Flow Regression' AND "created_by_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in retrospective_retrospectivetask is occupied by unrelated data (expected %)', '71f492ef-4fd2-55ae-94b5-0badd0b6afa5', 'RetrospectiveTask:retro-005';
  END IF;
END $$;

INSERT INTO "retrospective_retrospectivetask" ("id", "campaign_id", "status", "scheduled_at", "decision", "confidence_level", "primary_assumption", "key_risk_ignore", "outcome_compared_to_expectation", "biggest_wrong_assumption", "would_make_same_decision_again", "started_at", "completed_at", "report_url", "report_generated_at", "reviewed_by_id", "reviewed_at", "created_at", "updated_at", "created_by_id") VALUES ('c79902e0-deca-599c-bfa6-282f6326e1a3', (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'scheduled', now(), 'Retro: Client Onboarding Process', 3, 'Retro: Client Onboarding Process — Oct 20, 2026
Participants: Jade, Marcus.

Went well: The new onboarding checklist cut kickoff prep time roughly in half for the Willow & Pine account.
Didn''t go well: The checklist doesn''t yet cover credit union clients; Marcus to adapt it before Northbridge Financial onboarding begins Nov 3.', '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, now(), now(), (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'))
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "retrospective_retrospectivetask" WHERE "id" = 'c79902e0-deca-599c-bfa6-282f6326e1a3' AND "campaign_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "decision" = 'Retro: Client Onboarding Process' AND "created_by_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in retrospective_retrospectivetask is occupied by unrelated data (expected %)', 'c79902e0-deca-599c-bfa6-282f6326e1a3', 'RetrospectiveTask:retro-006';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1247652756, 'weekly-standup-oct-7-2026', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Weekly Standup — Oct 7, 2026', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1247652756 AND "slug" = 'weekly-standup-oct-7-2026' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Weekly Standup — Oct 7, 2026' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1247652756, 'Meeting:meeting-013';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1276250419, -1247652756, 'Weekly Standup — Oct 7, 2026
Attendees: Priya, Marcus, Dev.
Priya: Finalizing Nov main-push social creative.
Marcus: Confirmed the main-push paid social budget allocation of $32,000 out of the $85,000 total campaign budget.
Dev: Monitoring checkout flow post-fix verification.
No blockers reported for the sprint otherwise.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1276250419 AND "meeting_id" = -1247652756) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1276250419, 'MeetingDocument:meeting-013';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1225674207, 'main-push-launch-readiness-review', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Main Push Launch Readiness Review', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1225674207 AND "slug" = 'main-push-launch-readiness-review' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Main Push Launch Readiness Review' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1225674207, 'Meeting:meeting-014';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1888429969, -1225674207, 'Main Push Launch Readiness Review — Nov 1, 2026
Attendees: Priya, Marcus, Jade, Dev.
Jade confirmed the main push phase officially launched Nov 1 across email, paid social, and the landing page.
Jade confirmed the gift-card sales tracking dashboard is live.
Interim tally as of Nov 1: 1,200 gift cards sold toward the 4,000 goal.
No blockers reported.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1888429969 AND "meeting_id" = -1225674207) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1888429969, 'MeetingDocument:meeting-014';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1341046350, 'client-check-in-aurora-retail-nov', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Client Check-in — Aurora Retail Nov', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1341046350 AND "slug" = 'client-check-in-aurora-retail-nov' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Client Check-in — Aurora Retail Nov' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1341046350, 'Meeting:meeting-015';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1378338118, -1341046350, 'Client Check-in — Aurora Retail Nov — Nov 10, 2026
Attendees: Jade, Lena Ortiz (Aurora Retail).
Lena requested an extra Instagram carousel ad promoting the last-chance phase.
Jade confirmed the agency will add it for the Dec 15-24 window.
No other topics discussed.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1378338118 AND "meeting_id" = -1341046350) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1378338118, 'MeetingDocument:meeting-015';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1729277584, 'new-copywriter-hiring-debrief', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'New Copywriter — Hiring Debrief', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1729277584 AND "slug" = 'new-copywriter-hiring-debrief' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'New Copywriter — Hiring Debrief' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1729277584, 'Meeting:meeting-016';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1950047419, -1729277584, 'New Copywriter — Hiring Debrief — Oct 12, 2026
Attendees: Priya, Corinne.
Priya reported a shortlist of 4 candidates ready, meeting the Oct 10 deadline set at the Q3 quarterly business review.
The team recommend Nadia Reyes for the Q4 copywriter role.
Start date will match the plan set at the quarterly business review.
Corinne approved the recommendation.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1950047419 AND "meeting_id" = -1729277584) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1950047419, 'MeetingDocument:meeting-016';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1793261339, 'willow-pine-kickoff', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Willow & Pine Kickoff', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1793261339 AND "slug" = 'willow-pine-kickoff' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Willow & Pine Kickoff' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1793261339, 'Meeting:meeting-017';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1963840386, -1793261339, 'Willow & Pine Kickoff — Oct 20, 2026
Attendees: Jade, Marcus, Willow & Pine stakeholder Ben Alden (Marketing Director).
Budget confirmed at $52,000 for the Q4 onboarding phase and Q1 campaign prep.
Primary goal: rebuild the email list through re-engagement, targeting 2,500 reactivated subscribers by end of Q1.
Jade will own the onboarding checklist; Marcus will lead campaign strategy.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1963840386 AND "meeting_id" = -1793261339) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1963840386, 'MeetingDocument:meeting-017';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1529437712, 'northbridge-financial-kickoff', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Northbridge Financial Kickoff', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1529437712 AND "slug" = 'northbridge-financial-kickoff' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Northbridge Financial Kickoff' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1529437712, 'Meeting:meeting-018';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1519255899, -1529437712, 'Northbridge Financial Kickoff — Nov 3, 2026
Attendees: Jade, Marcus, Corinne, Northbridge stakeholder Grace Whitfield (VP Member Experience).
Compliance requirement flagged: all member communications must be reviewed by Northbridge''s compliance team with a 5-business-day turnaround before send.
Marcus confirms updated onboarding checklist now covers a credit-union compliance review step.
Onboarding begins immediately.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1519255899 AND "meeting_id" = -1529437712) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1519255899, 'MeetingDocument:meeting-018';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1890127441, 'rag-tool-beta-kickoff-aurora-retail-pilot', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'RAG Tool Beta Kickoff — Aurora Retail Pilot', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1890127441 AND "slug" = 'rag-tool-beta-kickoff-aurora-retail-pilot' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'RAG Tool Beta Kickoff — Aurora Retail Pilot' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1890127441, 'Meeting:meeting-019';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1858859081, -1890127441, 'RAG Tool Beta Kickoff — Aurora Retail Pilot — Nov 5, 2026
Attendees: Dev, Priya, Marcus, Jade.
Dev presented the beta RAG-assisted knowledge tool, per the FY26 tooling commitment from the all-hands.
The beta covers meeting notes, Notion drafts, and retrospectives.
The pilot will run through end of Q4 with the Aurora Retail team as the first user group.
Dev will collect feedback weekly.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1858859081 AND "meeting_id" = -1890127441) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1858859081, 'MeetingDocument:meeting-019';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1452756575, 'weekly-standup-nov-18-2026', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Weekly Standup — Nov 18, 2026', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1452756575 AND "slug" = 'weekly-standup-nov-18-2026' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Weekly Standup — Nov 18, 2026' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1452756575, 'Meeting:meeting-020';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1980413218, -1452756575, 'Weekly Standup — Nov 18, 2026
Attendees: Priya, Marcus, Dev.
Priya: Last-chance phase creative for the Dec 15-24 reminders is in progress.
Marcus: Gift-card sales tally at 3,100 as of Nov 18.
Dev: No new checkout flow incidents since the Oct 24 spike; monitoring continues.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1980413218 AND "meeting_id" = -1452756575) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1980413218, 'MeetingDocument:meeting-020';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1773043510, 'post-launch-debrief-main-push-phase', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Post-Launch Debrief — Main Push Phase', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1773043510 AND "slug" = 'post-launch-debrief-main-push-phase' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Post-Launch Debrief — Main Push Phase' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1773043510, 'Meeting:meeting-021';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1906468209, -1773043510, 'Post-Launch Debrief — Main Push Phase — Dec 2, 2026
Attendees: Jade, Priya, Marcus, Dev, Corinne.

Sales Results
Marcus reported 2,900 gift cards sold during main push alone. Final cumulative results against the original goal will be confirmed in the upcoming quarterly wrap-up retro.

Budget
Main push paid social spend came in at $34,500 against the $32,000 allocation, a $2,500 overage attributed to a mid-month bid increase to counter rising CPMs. Corinne asked Marcus to document the CPM-driven overage in the next retro for FY26 budgeting reference.

Last-Chance Phase Framing
Jade flagged the last-chance phase (Dec 15-24) will now function as stretch-goal messaging rather than goal-critical, since the 4,000 target was already met.

Checkout Flow
Dev reported the checkout flow held up under Black Friday traffic (Nov 27) with no repeat incidents, confirming Aurora Retail''s engineering team''s fix resolved the issue.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1906468209 AND "meeting_id" = -1773043510) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1906468209, 'MeetingDocument:meeting-021';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1904195200, 'client-check-in-willow-pine', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Client Check-in — Willow & Pine', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1904195200 AND "slug" = 'client-check-in-willow-pine' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Client Check-in — Willow & Pine' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1904195200, 'Meeting:meeting-022';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1376615080, -1904195200, 'Client Check-in — Willow & Pine — Nov 15, 2026
Attendees: Jade, Ben Alden (Willow & Pine).
Ben approved the re-engagement email subject-line test plan.
First send scheduled for Nov 22.
No other topics discussed.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1376615080 AND "meeting_id" = -1904195200) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1376615080, 'MeetingDocument:meeting-022';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1495859823, 'compliance-review-sync-northbridge-financial', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'Compliance Review Sync — Northbridge Financial', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1495859823 AND "slug" = 'compliance-review-sync-northbridge-financial' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'Compliance Review Sync — Northbridge Financial' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1495859823, 'Meeting:meeting-023';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1575759749, -1495859823, 'Compliance Review Sync — Northbridge Financial — Nov 20, 2026
Attendees: Marcus, Jade, Grace Whitfield (Northbridge Financial).
Grace''s compliance team returned first-round feedback on the welcome email series within the agreed 5-business-day window.
Two required disclosure lines must be added per NCUA guidance.
Marcus confirms revised copy will be resubmitted by Nov 24.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1575759749 AND "meeting_id" = -1495859823) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1575759749, 'MeetingDocument:meeting-023';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1137640613, '11-jade-manager-nov', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), '1:1 — Jade & Manager (Nov)', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1137640613 AND "slug" = '11-jade-manager-nov' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = '1:1 — Jade & Manager (Nov)' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1137640613, 'Meeting:meeting-024';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1580389405, -1137640613, '1:1 — Jade & Manager (Nov) — Nov 21, 2026
Attendees: Jade, Corinne.
Jade reported the second account coordinator, added Oct 1, has cut her average weekly overtime from 6 hours to under 1 hour.
Corinne confirmed no further staffing changes needed for Q4.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1580389405 AND "meeting_id" = -1137640613) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1580389405, 'MeetingDocument:meeting-024';
  END IF;
END $$;

INSERT INTO "meetings_meeting" ("id", "slug", "created_at", "updated_at", "project_id", "title", "type_definition_id", "objective", "summary", "scheduled_date", "scheduled_time", "external_reference", "layout_config", "status", "is_archived", "is_deleted", "minutes_published") VALUES (-1524042661, 'fy26-progress-check-in', now(), now(), (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'FY26 Progress Check-in', -1102370946, '', '', NULL, NULL, NULL, '{}', 'draft', false, false, false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meeting" WHERE "id" = -1524042661 AND "slug" = 'fy26-progress-check-in' AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "title" = 'FY26 Progress Check-in' AND "type_definition_id" = -1102370946) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meeting is occupied by unrelated data (expected %)', -1524042661, 'Meeting:meeting-025';
  END IF;
END $$;

INSERT INTO "meetings_meetingdocument" ("id", "meeting_id", "content", "yjs_state", "last_edited_by_id", "created_at", "updated_at") VALUES (-1972238571, -1524042661, 'FY26 Progress Check-in — Dec 5, 2026
Attendees: Full agency team, led by Corinne Blake.
Corinne reviewed FY26 goals progress: revenue growth tracking at 11% year-to-date against the 18% target.
The RAG tool beta is in pilot with the Aurora Retail account team.
Average campaign turnaround time has improved from 21 days to 17 days, short of the 14-day target.
Corinne says a formal FY26 progress report with finalized numbers will go to leadership in January; today''s figures are preliminary.', '', NULL, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "meetings_meetingdocument" WHERE "id" = -1972238571 AND "meeting_id" = -1524042661) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in meetings_meetingdocument is occupied by unrelated data (expected %)', -1972238571, 'MeetingDocument:meeting-025';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1360046904, 'main-push-email-copy-aurora-retail', 'Main Push Email Copy — Aurora Retail', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1360046904 AND "slug" = 'main-push-email-copy-aurora-retail' AND "title" = 'Main Push Email Copy — Aurora Retail' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1360046904, 'Draft:notion-009';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1541321434, -1360046904, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1541321434 AND "draft_id" = -1360046904 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1541321434, 'DraftProjectLink:notion-009';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1330667186, -1360046904, 'text', '{"text": "Main Push Email Copy \u2014 Aurora Retail\nStatus: Approved\nSubject line: Your gift card is calling \ud83c\udf81\n\nBody: The Aurora Retail holiday gift-card sale is live through November. Join 1,200+ shoppers who''ve already grabbed their gift cards.\n\nCTA button: \"Get My Gift Card\""}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1330667186 AND "draft_id" = -1360046904) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1330667186, 'ContentBlock:notion-009';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1202945772, 'willow-pine-re-engagement-email-draft', 'Willow & Pine Re-engagement Email Draft', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1202945772 AND "slug" = 'willow-pine-re-engagement-email-draft' AND "title" = 'Willow & Pine Re-engagement Email Draft' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1202945772, 'Draft:notion-010';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1671208224, -1202945772, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1671208224 AND "draft_id" = -1202945772 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1671208224, 'DraftProjectLink:notion-010';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1560978040, -1202945772, 'text', '{"text": "Willow & Pine Re-engagement Email Draft\nStatus: Client review\nSubject line: We miss you at Willow & Pine\n\nBody: It''s been a while! Come back and enjoy 20% off your next order to lapsed subscribers, only through this email.\n\nGoal note: This email is part of the re-engagement push targeting 2,500 reactivated subscribers by end of Q1."}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1560978040 AND "draft_id" = -1202945772) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1560978040, 'ContentBlock:notion-010';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1844743518, 'northbridge-financial-welcome-series-v1', 'Northbridge Financial Welcome Series — v1', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1844743518 AND "slug" = 'northbridge-financial-welcome-series-v1' AND "title" = 'Northbridge Financial Welcome Series — v1' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1844743518, 'Draft:notion-011';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1242598510, -1844743518, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1242598510 AND "draft_id" = -1844743518 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1242598510, 'DraftProjectLink:notion-011';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1245653774, -1844743518, 'text', '{"text": "Northbridge Financial Welcome Series \u2014 v1\nStatus: Draft \u2014 needs compliance revision\nSubject line: Welcome to Northbridge Financial\n\nBody: Thanks for joining Northbridge Financial Credit Union. Explore your new member benefits today.\n\nNote from Marcus: Pending compliance team''s two required disclosure additions before resend. Do not send this version \u2014 see v2."}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1245653774 AND "draft_id" = -1844743518) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1245653774, 'ContentBlock:notion-011';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1219942096, 'northbridge-financial-welcome-series-v2-compliance-approved', 'Northbridge Financial Welcome Series — v2 (Compliance-Approved)', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1219942096 AND "slug" = 'northbridge-financial-welcome-series-v2-compliance-approved' AND "title" = 'Northbridge Financial Welcome Series — v2 (Compliance-Approved)' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1219942096, 'Draft:notion-012';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1954563491, -1219942096, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1954563491 AND "draft_id" = -1219942096 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1954563491, 'DraftProjectLink:notion-012';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1462423502, -1219942096, 'text', '{"text": "Northbridge Financial Welcome Series \u2014 v2 (Compliance-Approved)\nStatus: Approved for send\nSubject line: Welcome to Northbridge Financial\n\nBody: Thanks for joining Northbridge Financial Credit Union. Explore your new member benefits today. Northbridge Financial Credit Union is federally insured by NCUA.\n\nNote from Marcus: Resubmitted Nov 24 and cleared compliance the same day. This is the version to send."}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1462423502 AND "draft_id" = -1219942096) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1462423502, 'ContentBlock:notion-012';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1712928628, 'last-chance-reminder-email-series-aurora-retail', 'Last-Chance Reminder Email Series — Aurora Retail', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1712928628 AND "slug" = 'last-chance-reminder-email-series-aurora-retail' AND "title" = 'Last-Chance Reminder Email Series — Aurora Retail' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1712928628, 'Draft:notion-013';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1966581937, -1712928628, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1966581937 AND "draft_id" = -1712928628 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1966581937, 'DraftProjectLink:notion-013';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1724880775, -1712928628, 'text', '{"text": "Last-Chance Reminder Email Series \u2014 Aurora Retail\nStatus: Approved\n\nThree-email sequence for the Dec 15-24 window, branded as stretch-goal messaging rather than goal-critical, since the 4,000 gift-card goal was already reached Nov 28.\n\nEmail 1 (Dec 15): \"Last chance for gift cards\"\nEmail 2 (Dec 20): \"5 days left\"\nEmail 3 (Dec 24): \"Final hours\""}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1724880775 AND "draft_id" = -1712928628) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1724880775, 'ContentBlock:notion-013';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1370321670, 'social-caption-bank-november', 'Social Caption Bank — November', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1370321670 AND "slug" = 'social-caption-bank-november' AND "title" = 'Social Caption Bank — November' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1370321670, 'Draft:notion-014';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1434336390, -1370321670, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1434336390 AND "draft_id" = -1370321670 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1434336390, 'DraftProjectLink:notion-014';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1478324742, -1370321670, 'text', '{"text": "Social Caption Bank \u2014 November\nStatus: Approved\n\n1. \"Main push is on. Grab your gift card today.\" \u2014 Instagram, Nov 1\n2. \"Halfway to the season''s biggest savings.\" \u2014 Instagram, Nov 15\n3. \"Goal? Crushed. 4,000 gift cards and counting. \ud83c\udf89\" \u2014 Instagram, Nov 29"}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1478324742 AND "draft_id" = -1370321670) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1478324742, 'ContentBlock:notion-014';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1527038552, 'case-study-draft-aurora-retail-full-year-results', 'Case Study Draft: Aurora Retail Full-Year Results', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1527038552 AND "slug" = 'case-study-draft-aurora-retail-full-year-results' AND "title" = 'Case Study Draft: Aurora Retail Full-Year Results' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1527038552, 'Draft:notion-015';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1357130493, -1527038552, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1357130493 AND "draft_id" = -1527038552 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1357130493, 'DraftProjectLink:notion-015';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1850716176, -1527038552, 'text', '{"text": "Case Study Draft: Aurora Retail Full-Year Results\nStatus: In progress\nAuthor: Marcus\n\nOverview: This case study covers the full Q3-Q4 Aurora Retail engagement, from the loyalty-program launch through the Q4 gift-card campaign.\n\nQ3 Results: The loyalty-program launch generated 5,600 sign-ups against a goal of 4,000, a 40% overperformance, with a 6.2% email click-through rate.\n\nQ4 Results: The Q4 gift-card campaign reached its 4,000-card goal on Nov 28, finishing the main push phase with 4,100 total gift cards sold.\n\nClient quote (pending approval): \"This has been our strongest year of holiday marketing yet, across every channel.\" \u2014 Lena Ortiz, VP of Marketing, Aurora Retail."}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1850716176 AND "draft_id" = -1527038552) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1850716176, 'ContentBlock:notion-015';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1607082095, 'rag-tool-beta-internal-faq-draft', 'RAG Tool Beta — Internal FAQ Draft', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1607082095 AND "slug" = 'rag-tool-beta-internal-faq-draft' AND "title" = 'RAG Tool Beta — Internal FAQ Draft' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1607082095, 'Draft:notion-016';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1385870410, -1607082095, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1385870410 AND "draft_id" = -1607082095 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1385870410, 'DraftProjectLink:notion-016';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1212034605, -1607082095, 'text', '{"text": "RAG Tool Beta \u2014 Internal FAQ Draft\nStatus: Draft\nAuthor: Dev\n\nQ: What is the RAG tool beta?\nA: An internal tool that lets account teams ask natural-language questions across meeting notes, Notion drafts, and retrospectives.\n\nQ: What happens after the pilot?\nA: Beta feedback will shape the full FY26 rollout decision."}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1212034605 AND "draft_id" = -1607082095) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1212034605, 'ContentBlock:notion-016';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1969822314, 'ad-copy-variations-willow-pine-meta-feed', 'Ad Copy Variations — Willow & Pine Meta Feed', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1969822314 AND "slug" = 'ad-copy-variations-willow-pine-meta-feed' AND "title" = 'Ad Copy Variations — Willow & Pine Meta Feed' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1969822314, 'Draft:notion-017';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1709694189, -1969822314, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1709694189 AND "draft_id" = -1969822314 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1709694189, 'DraftProjectLink:notion-017';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1404024461, -1969822314, 'text', '{"text": "Ad Copy Variations \u2014 Willow & Pine Meta Feed\nStatus: Approved\n\nVariant A: \"We miss you. Here''s 20% off to come back.\"\nVariant B: \"Your home, refreshed. Welcome back to Willow & Pine.\""}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1404024461 AND "draft_id" = -1969822314) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1404024461, 'ContentBlock:notion-017';
  END IF;
END $$;

INSERT INTO "notion_editor_draft" ("id", "slug", "title", "user_id", "status", "content_blocks", "created_at", "updated_at", "is_deleted") VALUES (-1943594784, 'blog-draft-choosing-a-credit-union-that-gets-you', 'Blog Draft: Choosing a Credit Union That Gets You', (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'), 'draft', '[]', now(), now(), false)
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draft" WHERE "id" = -1943594784 AND "slug" = 'blog-draft-choosing-a-credit-union-that-gets-you' AND "title" = 'Blog Draft: Choosing a Credit Union That Gets You' AND "user_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draft is occupied by unrelated data (expected %)', -1943594784, 'Draft:notion-018';
  END IF;
END $$;

INSERT INTO "notion_editor_draftprojectlink" ("id", "draft_id", "project_id", "created_at", "updated_at") VALUES (-1959736670, -1943594784, (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_draftprojectlink" WHERE "id" = -1959736670 AND "draft_id" = -1943594784 AND "project_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_draftprojectlink is occupied by unrelated data (expected %)', -1959736670, 'DraftProjectLink:notion-018';
  END IF;
END $$;

INSERT INTO "notion_editor_contentblock" ("id", "draft_id", "block_type", "content", "order", "created_at", "updated_at") VALUES (-1642053790, -1943594784, 'text', '{"text": "Blog Draft: Choosing a Credit Union That Gets You\nStatus: In review\nAuthor: Nadia Reyes\n\nFirst deliverable from new copywriter Nadia Reyes, written for the Northbridge Financial account.\n\nIntro: Choosing a financial institution is a personal decision. Here''s what makes a member-owned credit union different from a traditional bank, and what to look for."}', 0, now(), now())
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "notion_editor_contentblock" WHERE "id" = -1642053790 AND "draft_id" = -1943594784) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in notion_editor_contentblock is occupied by unrelated data (expected %)', -1642053790, 'ContentBlock:notion-018';
  END IF;
END $$;

INSERT INTO "retrospective_retrospectivetask" ("id", "campaign_id", "status", "scheduled_at", "decision", "confidence_level", "primary_assumption", "key_risk_ignore", "outcome_compared_to_expectation", "biggest_wrong_assumption", "would_make_same_decision_again", "started_at", "completed_at", "report_url", "report_generated_at", "reviewed_by_id", "reviewed_at", "created_at", "updated_at", "created_by_id") VALUES ('8826dcf7-2f99-5a43-afdb-ec4b06baa0bd', (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'scheduled', now(), 'Sprint 19 Retrospective — Main Push Kickoff', 3, 'Sprint 19 Retrospective — Main Push Kickoff — Nov 3, 2026
Participants: Marcus, Priya, Dev, Jade.

What went well: Main push launched on time Nov 1 across all channels.

What didn''t go well: The paid social bid strategy needed a mid-month adjustment because CPMs rose faster than forecast.

Action items: Marcus to build a CPM-contingency buffer into future paid social budgets.', '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, now(), now(), (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'))
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "retrospective_retrospectivetask" WHERE "id" = '8826dcf7-2f99-5a43-afdb-ec4b06baa0bd' AND "campaign_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "decision" = 'Sprint 19 Retrospective — Main Push Kickoff' AND "created_by_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in retrospective_retrospectivetask is occupied by unrelated data (expected %)', '8826dcf7-2f99-5a43-afdb-ec4b06baa0bd', 'RetrospectiveTask:retro-007';
  END IF;
END $$;

INSERT INTO "retrospective_retrospectivetask" ("id", "campaign_id", "status", "scheduled_at", "decision", "confidence_level", "primary_assumption", "key_risk_ignore", "outcome_compared_to_expectation", "biggest_wrong_assumption", "would_make_same_decision_again", "started_at", "completed_at", "report_url", "report_generated_at", "reviewed_by_id", "reviewed_at", "created_at", "updated_at", "created_by_id") VALUES ('a7c8c5ba-608d-5b66-9008-528f1c50301d', (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'scheduled', now(), 'Sprint 20 Retrospective — Checkout Flow Fix Deployed', 3, 'Sprint 20 Retrospective — Checkout Flow Fix Deployed — Nov 21, 2026
Participants: Dev, Jade.

What went well: Aurora Retail''s engineering team deployed the session-timeout fix on Nov 20, six weeks after it was first escalated in Sprint 18. Dev confirmed no incidents during the following week, including a stress test.

What didn''t go well: None flagged.

Action items: Close out the checkout-flow risk item that''s been tracked since Sprint 15.', '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, now(), now(), (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'))
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "retrospective_retrospectivetask" WHERE "id" = 'a7c8c5ba-608d-5b66-9008-528f1c50301d' AND "campaign_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "decision" = 'Sprint 20 Retrospective — Checkout Flow Fix Deployed' AND "created_by_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in retrospective_retrospectivetask is occupied by unrelated data (expected %)', 'a7c8c5ba-608d-5b66-9008-528f1c50301d', 'RetrospectiveTask:retro-008';
  END IF;
END $$;

INSERT INTO "retrospective_retrospectivetask" ("id", "campaign_id", "status", "scheduled_at", "decision", "confidence_level", "primary_assumption", "key_risk_ignore", "outcome_compared_to_expectation", "biggest_wrong_assumption", "would_make_same_decision_again", "started_at", "completed_at", "report_url", "report_generated_at", "reviewed_by_id", "reviewed_at", "created_at", "updated_at", "created_by_id") VALUES ('08333ef7-7485-586d-8f5a-b84756db44f1', (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'scheduled', now(), 'Retro: Willow & Pine Onboarding — First Month', 3, 'Retro: Willow & Pine Onboarding — First Month — Nov 18, 2026
Participants: Jade, Marcus.

What went well: The onboarding checklist worked smoothly for a second consumer-brand client.

What didn''t go well: Initial budget scope discussions took longer than expected because Willow & Pine''s Q1 campaign needs weren''t fully defined at kickoff.

Action items: Marcus to draft a Q1 scoping template before the next consumer-brand onboarding.', '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, now(), now(), (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'))
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "retrospective_retrospectivetask" WHERE "id" = '08333ef7-7485-586d-8f5a-b84756db44f1' AND "campaign_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "decision" = 'Retro: Willow & Pine Onboarding — First Month' AND "created_by_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in retrospective_retrospectivetask is occupied by unrelated data (expected %)', '08333ef7-7485-586d-8f5a-b84756db44f1', 'RetrospectiveTask:retro-009';
  END IF;
END $$;

INSERT INTO "retrospective_retrospectivetask" ("id", "campaign_id", "status", "scheduled_at", "decision", "confidence_level", "primary_assumption", "key_risk_ignore", "outcome_compared_to_expectation", "biggest_wrong_assumption", "would_make_same_decision_again", "started_at", "completed_at", "report_url", "report_generated_at", "reviewed_by_id", "reviewed_at", "created_at", "updated_at", "created_by_id") VALUES ('478a12f8-381d-508c-89e4-d7fae9348989', (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'scheduled', now(), 'Retro: Northbridge Financial Compliance Process', 3, 'Retro: Northbridge Financial Compliance Process — Nov 25, 2026
Participants: Marcus, Jade.

What went well: This round of review caught the missing NCUA disclosures before external send, avoiding a compliance issue.

What didn''t go well: The extra 5-business-day compliance turnaround wasn''t factored into the original welcome-series timeline, causing a one-week slip.

Action items: Jade to build compliance-review lead time into future credit-union client timelines by default.', '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, now(), now(), (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'))
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "retrospective_retrospectivetask" WHERE "id" = '478a12f8-381d-508c-89e4-d7fae9348989' AND "campaign_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "decision" = 'Retro: Northbridge Financial Compliance Process' AND "created_by_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in retrospective_retrospectivetask is occupied by unrelated data (expected %)', '478a12f8-381d-508c-89e4-d7fae9348989', 'RetrospectiveTask:retro-010';
  END IF;
END $$;

INSERT INTO "retrospective_retrospectivetask" ("id", "campaign_id", "status", "scheduled_at", "decision", "confidence_level", "primary_assumption", "key_risk_ignore", "outcome_compared_to_expectation", "biggest_wrong_assumption", "would_make_same_decision_again", "started_at", "completed_at", "report_url", "report_generated_at", "reviewed_by_id", "reviewed_at", "created_at", "updated_at", "created_by_id") VALUES ('03df79f6-1802-5fb0-9a29-c90507282f17', (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'scheduled', now(), 'Sprint 21 Retrospective — Main Push Goal Reached', 3, 'Sprint 21 Retrospective — Main Push Goal Reached — Nov 30, 2026
Participants: Marcus, Priya, Dev, Jade.

What went well: Cumulative gift-card sales crossed the 4,000 goal on Nov 28, two days before main push officially ended.

What didn''t go well: The paid social overage wasn''t caught until the Dec 2 debrief; the team agreed budget-tracking check-ins should happen weekly during active pushes, not just at phase-end.

Action items: Marcus to set up weekly budget check-ins for all active campaign pushes.', '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, now(), now(), (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'))
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "retrospective_retrospectivetask" WHERE "id" = '03df79f6-1802-5fb0-9a29-c90507282f17' AND "campaign_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "decision" = 'Sprint 21 Retrospective — Main Push Goal Reached' AND "created_by_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in retrospective_retrospectivetask is occupied by unrelated data (expected %)', '03df79f6-1802-5fb0-9a29-c90507282f17', 'RetrospectiveTask:retro-011';
  END IF;
END $$;

INSERT INTO "retrospective_retrospectivetask" ("id", "campaign_id", "status", "scheduled_at", "decision", "confidence_level", "primary_assumption", "key_risk_ignore", "outcome_compared_to_expectation", "biggest_wrong_assumption", "would_make_same_decision_again", "started_at", "completed_at", "report_url", "report_generated_at", "reviewed_by_id", "reviewed_at", "created_at", "updated_at", "created_by_id") VALUES ('69c378c5-940f-5318-b798-7fdd65a60a2c', (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'scheduled', now(), 'Quarterly Retrospective — Q4 Wrap-up', 3, 'Quarterly Retrospective — Q4 Wrap-up — Dec 3, 2026
Participants: Full account team plus Corinne Blake.

What went well
Aurora Retail''s full Q4 campaign exceeded its gift-card goal (4,100 vs 4,000, reached Nov 28), and the loyalty launch overperformance from Q3 carried strong momentum into Q4. Two new client relationships, Willow & Pine and Northbridge Financial, were onboarded successfully in the quarter.

What didn''t go well
The recurring checkout-flow issue wasn''t fully resolved until Nov 20, nearly 10 weeks after first identified in Sprint 15 -- the team flags escalating directly to client engineering leadership sooner next time a fix stalls this long. The Northbridge Financial welcome series also slipped a week due to an unbudgeted compliance-review turnaround.

Recurring themes
This is the third consecutive quarter where a client-side technical or compliance dependency caused a schedule slip; the team recommends building standard buffer time for external dependencies into every client timeline going forward, not just creative-approval buffers.

Action items
1. Add external-dependency buffer (client eng fixes, compliance reviews) to the standard timeline template, owner Jade.
2. Track paid social budget weekly during active campaign pushes, owner Marcus.
3. Document the RAG tool beta pilot feedback from the Aurora Retail team ahead of the FY26 rollout decision, owner Dev.', '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, now(), now(), (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'))
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "retrospective_retrospectivetask" WHERE "id" = '69c378c5-940f-5318-b798-7fdd65a60a2c' AND "campaign_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "decision" = 'Quarterly Retrospective — Q4 Wrap-up' AND "created_by_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in retrospective_retrospectivetask is occupied by unrelated data (expected %)', '69c378c5-940f-5318-b798-7fdd65a60a2c', 'RetrospectiveTask:retro-012';
  END IF;
END $$;

INSERT INTO "retrospective_retrospectivetask" ("id", "campaign_id", "status", "scheduled_at", "decision", "confidence_level", "primary_assumption", "key_risk_ignore", "outcome_compared_to_expectation", "biggest_wrong_assumption", "would_make_same_decision_again", "started_at", "completed_at", "report_url", "report_generated_at", "reviewed_by_id", "reviewed_at", "created_at", "updated_at", "created_by_id") VALUES ('c00e9c49-03b3-58a3-a492-4c281b38e134', (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'scheduled', now(), 'Retro: New Copywriter Onboarding', 3, 'Retro: New Copywriter Onboarding — Nov 9, 2026
Participants: Priya, Nadia Reyes.

Went well: Nadia Reyes''s first week ramped quickly using the brand-guideline changelog created after Sprint 14.
Didn''t go well: None flagged.', '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, now(), now(), (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'))
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "retrospective_retrospectivetask" WHERE "id" = 'c00e9c49-03b3-58a3-a492-4c281b38e134' AND "campaign_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "decision" = 'Retro: New Copywriter Onboarding' AND "created_by_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in retrospective_retrospectivetask is occupied by unrelated data (expected %)', 'c00e9c49-03b3-58a3-a492-4c281b38e134', 'RetrospectiveTask:retro-013';
  END IF;
END $$;

INSERT INTO "retrospective_retrospectivetask" ("id", "campaign_id", "status", "scheduled_at", "decision", "confidence_level", "primary_assumption", "key_risk_ignore", "outcome_compared_to_expectation", "biggest_wrong_assumption", "would_make_same_decision_again", "started_at", "completed_at", "report_url", "report_generated_at", "reviewed_by_id", "reviewed_at", "created_at", "updated_at", "created_by_id") VALUES ('535e3eef-3e60-58e3-a937-68546effeaf6', (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'scheduled', now(), 'Sprint 22 Retrospective — RAG Tool Pilot Feedback', 3, 'Sprint 22 Retrospective — RAG Tool Pilot Feedback — Dec 8, 2026
Participants: Dev, Priya, Jade.

What went well: Early feedback from the Aurora Retail account team on the RAG tool beta was positive, particularly for surfacing retrospective action items quickly.

What didn''t go well: Dev notes that answers got noticeably shakier whenever a question needed to pull facts from several different meetings and documents at once, rather than just one; this is being tracked as a beta-phase limitation, not yet resolved as of this retro.

Action items: Dev to compile a formal pilot report summarizing beta usage patterns before the next sprint planning cycle.', '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, now(), now(), (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'))
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "retrospective_retrospectivetask" WHERE "id" = '535e3eef-3e60-58e3-a937-68546effeaf6' AND "campaign_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "decision" = 'Sprint 22 Retrospective — RAG Tool Pilot Feedback' AND "created_by_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in retrospective_retrospectivetask is occupied by unrelated data (expected %)', '535e3eef-3e60-58e3-a937-68546effeaf6', 'RetrospectiveTask:retro-014';
  END IF;
END $$;

INSERT INTO "retrospective_retrospectivetask" ("id", "campaign_id", "status", "scheduled_at", "decision", "confidence_level", "primary_assumption", "key_risk_ignore", "outcome_compared_to_expectation", "biggest_wrong_assumption", "would_make_same_decision_again", "started_at", "completed_at", "report_url", "report_generated_at", "reviewed_by_id", "reviewed_at", "created_at", "updated_at", "created_by_id") VALUES ('990b7a8a-efaf-5192-9959-1d9f5f4f9e41', (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture'), 'scheduled', now(), 'Retro: FY26 Turnaround Time Progress', 3, 'Retro: FY26 Turnaround Time Progress — Dec 6, 2026
Participants: Corinne, Jade, Marcus.

What went well: The campaign-turnaround-time metric Corinne reviewed at her December check-in continued to trend in the right direction this year.

What didn''t go well: The metric remains behind its FY26 target; the team attributes the gap partly to the external-dependency delays flagged in the Q4 wrap-up.

Action items: Revisit target feasibility once external-dependency buffers are in place for a full quarter.', '', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, now(), now(), (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal'))
  ON CONFLICT (id) DO NOTHING;
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM "retrospective_retrospectivetask" WHERE "id" = '990b7a8a-efaf-5192-9959-1d9f5f4f9e41' AND "campaign_id" = (SELECT id FROM core_project WHERE name = 'MED-264 RAG Eval Fixture') AND "decision" = 'Retro: FY26 Turnaround Time Progress' AND "created_by_id" = (SELECT id FROM core_customuser WHERE email = 'rag-eval-harness-bot@rag-eval-harness.internal')) THEN
    RAISE EXCEPTION 'MED-264 seed collision: id % in retrospective_retrospectivetask is occupied by unrelated data (expected %)', '990b7a8a-efaf-5192-9959-1d9f5f4f9e41', 'RetrospectiveTask:retro-015';
  END IF;
END $$;

COMMIT;
