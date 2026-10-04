"""
csm rows that point at tenant-scoped core.Team / core.Project.

csm tables live in public, while core.Team and core.Project live in each org's
schema. A database FK from public would check the (empty) public copy, so these
fields keep the Django FK but drop the DB constraint (as core/0009 and
audit/0003 do). These tests run inside a real tenant schema with FK checks
forced to run immediately; Django creates them deferred, and tests never commit.
"""
from django.db import connection

from core.models import Project, Team
from core.test_utils import TenantTestCase
from csm.models import (
    CustomerUser, Queue, QueueTeam, QuickReplyTemplate, RoutingRule,
)
from customer.models import CustomerOrganisation
from experience_group.models import ExperienceGroup

UNCONSTRAINED = [
    ('csm_customeruser', 'team_id'),
    ('csm_quickreplytemplate', 'team_id'),
    ('csm_queueteam', 'team_id'),
    ('csm_csminvitation', 'team_id'),
    ('csm_routingrule', 'project_id'),
]


class TenantTeamAndProjectReferencesTest(TenantTestCase):
    def setUp(self):
        super().setUp()
        with connection.cursor() as cursor:
            cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
        self.user = self.create_user(email='agent@example.com', username='agent')
        self.team = Team.objects.create(organization=self.test_org, name='Billing')
        self.project = Project.objects.create(
            name='Support', organization=self.test_org, owner=self.user,
            objectives=['awareness'], kpis={},
        )
        self.organisation = CustomerOrganisation.objects.create(name='Acme', organization=self.test_org)
        self.queue = Queue.objects.create(organisation=self.organisation, name='Billing', tier='T1')

    def _exists_in_public(self, table, pk):
        with connection.cursor() as cursor:
            cursor.execute(f'SELECT 1 FROM public.{table} WHERE id = %s', [pk])
            return cursor.fetchone() is not None

    def test_the_tenant_rows_are_not_in_public(self):
        # Guards the premise: with a public copy, the tests below would pass anyway.
        assert not self._exists_in_public('core_team', self.team.pk)
        assert not self._exists_in_public('core_project', self.project.pk)

    def test_team_scoped_rows_save_and_load_the_tenant_team(self):
        member = CustomerUser.objects.create(
            user=self.user, organisation=self.organisation, team=self.team, is_active=True,
        )
        template = QuickReplyTemplate.objects.create(
            organisation=self.organisation, title='Refund', content='x', tags=['a'], team=self.team,
        )
        QueueTeam.objects.create(queue=self.queue, team=self.team)

        assert CustomerUser.objects.select_related('team').get(pk=member.pk).team.name == 'Billing'
        assert QuickReplyTemplate.objects.select_related('team').get(pk=template.pk).team == self.team
        assert QueueTeam.objects.filter(team__name='Billing').count() == 1

    def test_routing_rule_saves_with_a_tenant_project(self):
        group = ExperienceGroup.objects.create(name='VIP')
        rule = RoutingRule.objects.create(
            organization=self.test_org, project=self.project, experience_group=group,
            name='Refunds', target_queue=self.queue,
        )
        assert RoutingRule.objects.select_related('project').get(pk=rule.pk).project.name == 'Support'

    def test_deleting_a_team_still_nulls_or_removes_its_references(self):
        member = CustomerUser.objects.create(
            user=self.user, organisation=self.organisation, team=self.team, is_active=True,
        )
        template = QuickReplyTemplate.objects.create(
            organisation=self.organisation, title='Refund', content='x', tags=['a'], team=self.team,
        )
        QueueTeam.objects.create(queue=self.queue, team=self.team)

        self.team.delete()  # Django applies SET_NULL / CASCADE itself

        member.refresh_from_db()
        template.refresh_from_db()
        assert member.team_id is None
        assert template.team_id is None
        assert not QueueTeam.objects.filter(queue=self.queue).exists()

    def test_no_database_fk_on_these_columns(self):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT rel.relname, att.attname
                FROM pg_constraint con
                JOIN pg_class rel ON rel.oid = con.conrelid
                JOIN pg_namespace ns ON ns.oid = rel.relnamespace
                JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
                WHERE con.contype = 'f' AND ns.nspname = 'public'
                """,
            )
            constrained = set(cursor.fetchall())
        assert constrained.isdisjoint(UNCONSTRAINED), constrained & set(UNCONSTRAINED)
