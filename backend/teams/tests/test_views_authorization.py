"""
Tenant-schema authorization tests for the real Teams endpoints.

Policy under test:

* GET  /api/teams/<team_id>/                       any member of that team
* GET  /api/teams/<team_id>/members/               any member of that team
* POST /api/teams/<team_id>/members/               Team Leader of that team
* PATCH  /api/teams/<team_id>/members/<user_id>/   Team Leader of that team
* DELETE /api/teams/<team_id>/members/<user_id>/   Team Leader of that team
* GET  /api/teams/?search=                          org admin only (403 otherwise)
* POST /api/teams/                                  org admin only; 409 on duplicate name
* DELETE /api/teams/<team_id>/                      org admin only; 409 if it has members or child teams, else soft delete
* Superusers and Level-2 org admins of the CURRENT organization (a valid,
  non-deleted level-2 role of that organization that is not scoped to a
  Team) pass every Teams check without being a member. A generic
  OrganizationMembership "admin" label, a Team-scoped, expired or other
  organization's level-2 role does not.
* POST members/ requires the target user to belong to the Team's organization.

Team / TeamMember are tenant-scoped tables. Requests are authenticated with a
real JWT plus the organization slug header (same approach as
authentication/tests/test_me.py), so TenantSchemaMiddleware selects the
organization schema exactly as it does in production, and
team_permission_required must read membership from that schema.
"""
import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory, TestCase
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from access_control.middleware.authorization import AuthorizationMiddleware
from access_control.models import UserRole
from access_control.services import user_is_current_org_admin
from core.models import Organization, OrganizationMembership, Role, Team, TeamMember, TeamRole
from core.services.tenant import slug_to_schema_name

User = get_user_model()


def _set_schema(org):
    with connection.cursor() as cursor:
        cursor.execute(f'SET search_path TO {slug_to_schema_name(org.slug)}, public')


def _reset_schema():
    with connection.cursor() as cursor:
        cursor.execute('SET search_path TO public')


def _current_search_path():
    with connection.cursor() as cursor:
        cursor.execute('SHOW search_path')
        return cursor.fetchone()[0]


class TeamsAuthorizationBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="TeamsAuthzOrg")
        cls.other_org = Organization.objects.create(name="TeamsAuthzOtherOrg")

        def mk(username, **extra):
            return User.objects.create_user(
                username=username,
                email=f"{username}@teamsauthz.test",
                password="pw",
                is_verified=True,
                is_active=True,
                organization=cls.org,
                **extra,
            )

        cls.leader = mk("tleader")
        cls.member = mk("tmember")
        cls.outsider = mk("toutsider")
        cls.other_team_leader = mk("totherleader")
        cls.superuser = mk("tsuper", is_superuser=True)
        cls.target = mk("ttarget")        # user that leaders add/modify/remove
        cls.new_user = mk("tnewuser")     # user that is not on any team yet

        _set_schema(cls.org)
        cls.team = Team.objects.create(organization=cls.org, name="Alpha")
        cls.other_team = Team.objects.create(organization=cls.org, name="Beta")
        TeamMember.objects.create(user=cls.leader, team=cls.team, role_id=TeamRole.LEADER)
        TeamMember.objects.create(user=cls.member, team=cls.team, role_id=TeamRole.MEMBER)
        TeamMember.objects.create(user=cls.target, team=cls.team, role_id=TeamRole.MEMBER)
        TeamMember.objects.create(user=cls.other_team_leader, team=cls.other_team, role_id=TeamRole.LEADER)
        _reset_schema()

    def setUp(self):
        _set_schema(self.org)

    def tearDown(self):
        super().tearDown()
        _reset_schema()

    # -- helpers ---------------------------------------------------------
    def _headers(self, user, org=None):
        if user is None:
            return {}
        token = str(RefreshToken.for_user(user).access_token)
        return {
            'HTTP_AUTHORIZATION': f'Bearer {token}',
            'HTTP_X_ORGANIZATION_SLUG': (org or self.org).slug,
        }

    def _call(self, method, path, user=None, body=None, org=None):
        kwargs = self._headers(user, org)
        if body is not None:
            kwargs['data'] = json.dumps(body)
            kwargs['content_type'] = 'application/json'
        elif method.lower() in ('post', 'patch', 'delete'):
            kwargs['content_type'] = 'application/json'
        return getattr(self.client, method.lower())(path, **kwargs)

    def _detail(self):
        return f'/api/teams/{self.team.id}/'

    def _members(self):
        return f'/api/teams/{self.team.id}/members/'

    def _member_detail(self, user):
        return f'/api/teams/{self.team.id}/members/{user.id}/'

    def _role_of(self, user, team=None):
        _set_schema(self.org)  # the request middleware resets search_path to public
        return TeamMember.objects.filter(user=user, team=team or self.team).values_list(
            'role_id', flat=True
        ).first()

    def _all_endpoints(self):
        """(method, path, body) for every protected Teams handler."""
        return [
            ('get', self._detail(), None),
            ('get', self._members(), None),
            ('post', self._members(), {'user_id': self.new_user.id, 'role_id': TeamRole.MEMBER}),
            ('patch', self._member_detail(self.target), {'role_id': TeamRole.LEADER}),
            ('delete', self._member_detail(self.target), None),
        ]

    def _assert_team_state_unchanged(self):
        self.assertIsNone(self._role_of(self.new_user))
        self.assertEqual(self._role_of(self.target), TeamRole.MEMBER)


class TeamPermissionDecoratorTenantSchemaTest(TeamsAuthorizationBase):
    """Unit-level proof that the decorator reads the CURRENT tenant schema."""

    def _view(self, required_role="LEADER"):
        from django.http import HttpResponse
        decorator = AuthorizationMiddleware.team_permission_required(required_role=required_role)
        return decorator(lambda request, team_id=None: HttpResponse(b"TEAM OK"))

    def _req(self, user):
        req = RequestFactory().post(f'/api/teams/{self.team.id}/members/')
        req.user = user
        return req

    def test_leader_in_tenant_schema_is_allowed(self):
        resp = self._view()(self._req(self.leader), team_id=self.team.id)
        self.assertEqual(resp.status_code, 200)

    def test_member_in_tenant_schema_is_denied_for_leader_role(self):
        resp = self._view()(self._req(self.member), team_id=self.team.id)
        self.assertEqual(resp.status_code, 403)
        self.assertIn(b'must be team leader', resp.content)

    def test_member_in_tenant_schema_is_allowed_for_member_role(self):
        resp = self._view("MEMBER")(self._req(self.member), team_id=self.team.id)
        self.assertEqual(resp.status_code, 200)

    def test_non_member_in_tenant_schema_is_denied(self):
        resp = self._view("MEMBER")(self._req(self.outsider), team_id=self.team.id)
        self.assertEqual(resp.status_code, 403)
        self.assertIn(b'not a team member', resp.content)

    def test_search_path_is_preserved(self):
        before = _current_search_path()
        self._view()(self._req(self.leader), team_id=self.team.id)
        self.assertEqual(_current_search_path(), before)
        self.assertIn(slug_to_schema_name(self.org.slug), before)

    def test_search_path_is_preserved_on_denial(self):
        before = _current_search_path()
        self._view()(self._req(self.outsider), team_id=self.team.id)
        self.assertEqual(_current_search_path(), before)

    def test_membership_in_another_organization_schema_is_not_considered(self):
        # A leader row that exists only in the other organization's schema
        # must not grant access when the request is served from this org's
        # schema (no cross-organization search).
        _set_schema(self.other_org)
        foreign_team = Team.objects.create(organization=self.other_org, name="Foreign")
        TeamMember.objects.create(user=self.outsider, team=foreign_team, role_id=TeamRole.LEADER)
        _set_schema(self.org)

        resp = self._view()(self._req(self.outsider), team_id=foreign_team.id)
        self.assertEqual(resp.status_code, 403)
        self.assertIn(b'not a team member', resp.content)


class TeamsEndpointAuthorizationTest(TeamsAuthorizationBase):
    """End-to-end through middleware + URLconf + Teams views."""

    def test_anonymous_gets_401_on_every_endpoint(self):
        for method, path, body in self._all_endpoints():
            with self.subTest(method=method, path=path):
                resp = self._call(method, path, user=None, body=body)
                self.assertEqual(resp.status_code, 401)
        self._assert_team_state_unchanged()

    def test_non_member_gets_403_on_every_endpoint(self):
        for method, path, body in self._all_endpoints():
            with self.subTest(method=method, path=path):
                resp = self._call(method, path, user=self.outsider, body=body)
                self.assertEqual(resp.status_code, 403)
                self.assertIn('not a team member', resp.json()['error'])
        self._assert_team_state_unchanged()

    def test_member_can_read_team_and_members(self):
        detail = self._call('get', self._detail(), user=self.member)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()['team']['id'], self.team.id)
        members = self._call('get', self._members(), user=self.member)
        self.assertEqual(members.status_code, 200)
        self.assertEqual(members.json()['member_count'], 3)

    def test_member_cannot_add_change_or_remove_members(self):
        for method, path, body in self._all_endpoints()[2:]:
            with self.subTest(method=method, path=path):
                resp = self._call(method, path, user=self.member, body=body)
                self.assertEqual(resp.status_code, 403)
                self.assertIn('must be team leader', resp.json()['error'])
        self._assert_team_state_unchanged()

    def test_leader_can_read_own_team(self):
        detail = self._call('get', self._detail(), user=self.leader)
        self.assertEqual(detail.status_code, 200)
        members = self._call('get', self._members(), user=self.leader)
        self.assertEqual(members.status_code, 200)

    def test_leader_can_add_member(self):
        resp = self._call('post', self._members(), user=self.leader,
                          body={'user_id': self.new_user.id, 'role_id': TeamRole.MEMBER})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(self._role_of(self.new_user), TeamRole.MEMBER)

    def test_leader_can_change_member_role(self):
        resp = self._call('patch', self._member_detail(self.target), user=self.leader,
                          body={'role_id': TeamRole.LEADER})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._role_of(self.target), TeamRole.LEADER)

    def test_leader_can_remove_member(self):
        resp = self._call('delete', self._member_detail(self.target), user=self.leader)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(self._role_of(self.target))

    def test_leader_invalid_role_still_reaches_view_validation(self):
        resp = self._call('post', self._members(), user=self.leader,
                          body={'user_id': self.new_user.id, 'role_id': 99})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['code'], 'INVALID_ROLE')

    def test_superuser_passes_on_every_endpoint(self):
        expected = [200, 200, 201, 200, 200]
        for (method, path, body), status in zip(self._all_endpoints(), expected):
            with self.subTest(method=method, path=path):
                resp = self._call(method, path, user=self.superuser, body=body)
                self.assertEqual(resp.status_code, status)

    def test_superuser_gets_404_for_missing_team(self):
        resp = self._call('get', '/api/teams/999999/', user=self.superuser)
        self.assertEqual(resp.status_code, 404)

    def test_leader_of_a_different_team_is_denied_everywhere(self):
        for method, path, body in self._all_endpoints():
            with self.subTest(method=method, path=path):
                resp = self._call(method, path, user=self.other_team_leader, body=body)
                self.assertEqual(resp.status_code, 403)
                self.assertIn('not a team member', resp.json()['error'])
        self._assert_team_state_unchanged()

    def test_other_team_leader_still_manages_own_team(self):
        resp = self._call('get', f'/api/teams/{self.other_team.id}/members/', user=self.other_team_leader)
        self.assertEqual(resp.status_code, 200)

    def test_non_member_of_missing_team_gets_403_not_404(self):
        # The decorator runs before the view's get_object_or_404, so a
        # non-superuser cannot probe which team ids exist.
        resp = self._call('get', '/api/teams/999999/', user=self.outsider)
        self.assertEqual(resp.status_code, 403)


class OrgAdminTeamsPolicyTest(TeamsAuthorizationBase):
    """Level-2 org admins manage every Team of their own
    organization (and nothing outside it)."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        def mk(username, org):
            return User.objects.create_user(
                username=username, email=f"{username}@teamsauthz.test", password="pw",
                is_verified=True, is_active=True, organization=org,
            )

        cls.org_admin = mk("torgadmin", cls.org)
        cls.team_scoped_admin = mk("tscopedadmin", cls.org)
        cls.expired_admin = mk("texpiredadmin", cls.org)
        cls.deleted_role_admin = mk("tdeletedadmin", cls.org)
        cls.wrong_org_role_user = mk("twrongorgrole", cls.org)
        cls.level3_user = mk("tlevel3", cls.org)
        cls.membership_only_admin = mk("tmemberadmin", cls.org)
        cls.other_org_admin = mk("totherorgadmin", cls.other_org)
        cls.foreign_user = mk("tforeign", cls.other_org)
        cls.dual_membership_user = mk("tdual", cls.other_org)
        cls.superuser_no_org = User.objects.create_user(
            username="tsupernoorg", email="tsupernoorg@teamsauthz.test", password="pw",
            is_verified=True, is_active=True, is_superuser=True,
        )

        OrganizationMembership.objects.create(
            user=cls.membership_only_admin, organization=cls.org, role='admin', is_active=True)
        OrganizationMembership.objects.create(
            user=cls.dual_membership_user, organization=cls.org, role='member', is_active=True)

        _set_schema(cls.org)
        admin_role = Role.objects.create(organization=cls.org, name="Organization Admin", level=2)
        staff_role = Role.objects.create(organization=cls.org, name="Staff", level=3)
        foreign_role = Role.objects.create(organization=cls.other_org, name="Organization Admin", level=2)
        UserRole.objects.create(user=cls.org_admin, role=admin_role)
        UserRole.objects.create(user=cls.team_scoped_admin, role=admin_role, team=cls.team)
        UserRole.objects.create(user=cls.expired_admin, role=admin_role,
                                valid_to=timezone.now() - timedelta(days=1))
        UserRole.objects.create(user=cls.deleted_role_admin, role=admin_role, is_deleted=True)
        UserRole.objects.create(user=cls.wrong_org_role_user, role=foreign_role)
        UserRole.objects.create(user=cls.level3_user, role=staff_role)

        _set_schema(cls.other_org)
        other_role = Role.objects.create(organization=cls.other_org, name="Organization Admin", level=2)
        UserRole.objects.create(user=cls.other_org_admin, role=other_role)
        Team.objects.create(organization=cls.other_org, name="Foreign Team")
        _reset_schema()

    # -- helpers ---------------------------------------------------------
    def _names(self, resp):
        return sorted(t['name'] for t in resp.json()['teams'])

    def _db(self):
        """The request middleware resets search_path to public; point ORM
        assertions back at this organization's schema."""
        _set_schema(self.org)

    def _not_admins(self):
        return [self.member, self.leader, self.outsider, self.team_scoped_admin,
                self.expired_admin, self.deleted_role_admin, self.wrong_org_role_user,
                self.level3_user, self.membership_only_admin]

    # -- who counts as an org admin -------------------------------------
    def test_org_admin_predicate(self):
        self.assertTrue(user_is_current_org_admin(self.org_admin))
        for user in self._not_admins() + [self.other_org_admin]:
            with self.subTest(user=user.username):
                self.assertFalse(user_is_current_org_admin(user))

    # -- list / search ---------------------------------------------------
    def test_org_admin_lists_every_team_of_own_organization(self):
        resp = self._call('get', '/api/teams/', user=self.org_admin)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._names(resp), ['Alpha', 'Beta'])
        counts = {t['name']: t['member_count'] for t in resp.json()['teams']}
        self.assertEqual(counts, {'Alpha': 3, 'Beta': 1})

    def test_org_admin_can_search_teams_case_insensitively(self):
        resp = self._call('get', '/api/teams/?search=ALP', user=self.org_admin)
        self.assertEqual(self._names(resp), ['Alpha'])
        resp = self._call('get', '/api/teams/?search=zzz', user=self.org_admin)
        self.assertEqual(resp.json()['teams'], [])

    def test_superuser_can_list_teams(self):
        resp = self._call('get', '/api/teams/', user=self.superuser)
        self.assertEqual(self._names(resp), ['Alpha', 'Beta'])

    def test_anonymous_gets_401_on_org_wide_endpoints(self):
        for method, path, body in [('get', '/api/teams/', None),
                                   ('post', '/api/teams/', {'name': 'X'}),
                                   ('delete', self._detail(), None)]:
            with self.subTest(method=method):
                self.assertEqual(self._call(method, path, body=body).status_code, 401)

    def test_non_admins_get_403_on_org_wide_endpoints(self):
        for user in self._not_admins():
            for method, path, body in [('get', '/api/teams/', None),
                                       ('post', '/api/teams/', {'name': 'Nope'}),
                                       ('delete', self._detail(), None)]:
                with self.subTest(user=user.username, method=method):
                    self.assertEqual(self._call(method, path, user=user, body=body).status_code, 403)
        self._db()
        self.assertFalse(Team.objects.filter(name='Nope').exists())
        self.assertTrue(Team.objects.filter(id=self.team.id, is_deleted=False).exists())

    # -- create ----------------------------------------------------------
    def test_org_admin_creates_team_in_own_organization(self):
        resp = self._call('post', '/api/teams/', user=self.org_admin,
                          body={'name': '  Gamma  ', 'desc': 'd', 'organization_id': self.other_org.id})
        self.assertEqual(resp.status_code, 201)
        self._db()
        team = Team.objects.get(id=resp.json()['team']['id'])
        self.assertEqual((team.name, team.desc, team.organization_id), ('Gamma', 'd', self.org.id))

    def test_create_validation(self):
        for body in ({}, {'name': '   '}, {'name': 5}, {'name': 'x' * 201}, {'name': 'ok', 'desc': 3}):
            with self.subTest(body=body):
                resp = self._call('post', '/api/teams/', user=self.org_admin, body=body)
                self.assertEqual(resp.status_code, 400)

    def test_duplicate_name_returns_409_including_soft_deleted_teams(self):
        resp = self._call('post', '/api/teams/', user=self.org_admin, body={'name': 'Alpha'})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()['code'], 'TEAM_NAME_TAKEN')
        self._db()
        empty = Team.objects.create(organization=self.org, name="Temp")
        self.assertEqual(self._call('delete', f'/api/teams/{empty.id}/', user=self.org_admin).status_code, 204)
        resp = self._call('post', '/api/teams/', user=self.org_admin, body={'name': 'Temp'})
        self.assertEqual(resp.status_code, 409)

    # -- delete ----------------------------------------------------------
    def test_org_admin_cannot_delete_team_with_members(self):
        resp = self._call('delete', self._detail(), user=self.org_admin)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()['code'], 'TEAM_NOT_EMPTY')
        self._db()
        self.team.refresh_from_db()
        self.assertFalse(self.team.is_deleted)
        self.assertEqual(TeamMember.objects.filter(team=self.team).count(), 3)

    def test_org_admin_cannot_delete_team_with_child_teams(self):
        parent = Team.objects.create(organization=self.org, name="Parent")
        child = Team.objects.create(organization=self.org, name="Child", parent=parent)
        resp = self._call('delete', f'/api/teams/{parent.id}/', user=self.org_admin)
        self.assertEqual(resp.status_code, 409)
        self._db()
        parent.refresh_from_db()
        child.refresh_from_db()
        self.assertFalse(parent.is_deleted)
        self.assertEqual(child.parent_id, parent.id)

    def test_org_admin_soft_deletes_empty_team(self):
        empty = Team.objects.create(organization=self.org, name="Empty")
        resp = self._call('delete', f'/api/teams/{empty.id}/', user=self.org_admin)
        self.assertEqual(resp.status_code, 204)
        self._db()
        empty.refresh_from_db()
        self.assertTrue(empty.is_deleted)

    def test_deleted_team_is_hidden_everywhere(self):
        from teams.services import soft_delete_team
        self._db()
        soft_delete_team(self.team)
        resp = self._call('get', '/api/teams/', user=self.org_admin)
        self.assertEqual(self._names(resp), ['Beta'])
        self.assertEqual(self._call('get', self._detail(), user=self.org_admin).status_code, 404)
        self.assertEqual(self._call('get', self._members(), user=self.org_admin).status_code, 404)
        resp = self._call('get', '/auth/me/teams/', user=self.member)
        self.assertEqual(resp.json()['team_ids'], [])

    def test_org_admin_gets_404_for_missing_team(self):
        for method in ('get', 'delete'):
            with self.subTest(method=method):
                self.assertEqual(self._call(method, '/api/teams/999999/', user=self.org_admin).status_code, 404)

    # -- member management without membership --------------------------
    def test_org_admin_passes_on_every_member_endpoint_without_membership(self):
        self.assertFalse(TeamMember.objects.filter(user=self.org_admin).exists())
        expected = [200, 200, 201, 200, 200]
        for (method, path, body), status in zip(self._all_endpoints(), expected):
            with self.subTest(method=method, path=path):
                resp = self._call(method, path, user=self.org_admin, body=body)
                self.assertEqual(resp.status_code, status)

    def test_team_scoped_expired_and_membership_only_admins_stay_denied(self):
        for user in (self.team_scoped_admin, self.expired_admin, self.membership_only_admin):
            for method, path, body in self._all_endpoints():
                with self.subTest(user=user.username, method=method, path=path):
                    resp = self._call(method, path, user=user, body=body)
                    self.assertEqual(resp.status_code, 403)
        self._assert_team_state_unchanged()

    def test_org_admin_edit_dialog_saves_team_role_changes_without_membership(self):
        """Mirrors the Edit dialog Save: one PATCH per selected member."""
        self.assertFalse(TeamMember.objects.filter(user=self.org_admin).exists())
        for user, role in ((self.member, TeamRole.LEADER), (self.target, TeamRole.LEADER)):
            resp = self._call('patch', self._member_detail(user), user=self.org_admin,
                              body={'role_id': role})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()['membership']['role_id'], role)
        self.assertEqual(self._role_of(self.member), TeamRole.LEADER)
        self.assertEqual(self._role_of(self.target), TeamRole.LEADER)
        resp = self._call('patch', self._member_detail(self.member), user=self.org_admin,
                          body={'role_id': TeamRole.MEMBER})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._role_of(self.member), TeamRole.MEMBER)
        # the dialog reloads the team afterwards
        self.assertEqual(self._call('get', self._detail(), user=self.org_admin).status_code, 200)

    # -- frontend signal (/auth/me/ can_manage_all_teams) ----------------
    def _can_manage_flag(self, user, org=None):
        resp = self._call('get', '/auth/me/', user=user, org=org)
        self.assertEqual(resp.status_code, 200)
        return resp.json()['can_manage_all_teams']

    def test_me_flag_matches_backend_org_wide_team_access(self):
        """The UI flag must be true exactly where the org-wide endpoints
        accept the caller (strict org admin or superuser with an org)."""
        self.assertTrue(self._can_manage_flag(self.org_admin))
        self.assertTrue(self._can_manage_flag(self.superuser))
        self.assertTrue(self._can_manage_flag(self.other_org_admin, org=self.other_org))
        for user in self._not_admins() + [self.superuser_no_org]:
            with self.subTest(user=user.username):
                self.assertFalse(self._can_manage_flag(user))

    def test_me_flag_agrees_with_endpoint_status(self):
        for user in [self.org_admin, self.superuser, self.membership_only_admin,
                     self.team_scoped_admin, self.expired_admin, self.member]:
            with self.subTest(user=user.username):
                flag = self._can_manage_flag(user)
                status = self._call('get', '/api/teams/', user=user).status_code
                self.assertEqual(status == 200, flag)

    def test_superuser_without_organization_cannot_use_org_wide_list(self):
        resp = self._call('get', '/api/teams/', user=self.superuser_no_org)
        self.assertNotEqual(resp.status_code, 200)

    # -- tenant isolation -----------------------------------------------
    def test_other_organization_admin_cannot_reach_this_organizations_teams(self):
        # Served from the other organization's schema: ids are per schema, so
        # only assert that nothing of this organization is exposed or changed.
        resp = self._call('get', '/api/teams/', user=self.other_org_admin, org=self.other_org)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self._names(resp), ['Foreign Team'])
        for method, path, body in [('get', self._detail(), None),
                                   ('get', self._members(), None),
                                   ('post', self._members(), {'user_id': self.new_user.id}),
                                   ('delete', self._detail(), None)]:
            with self.subTest(method=method, path=path):
                resp = self._call(method, path, user=self.other_org_admin, body=body, org=self.other_org)
                self.assertNotIn('Alpha', resp.content.decode())
        self._db()
        self.assertTrue(Team.objects.filter(id=self.team.id, is_deleted=False).exists())
        self._assert_team_state_unchanged()

    def test_org_admin_list_excludes_other_organizations_teams(self):
        resp = self._call('get', '/api/teams/', user=self.org_admin)
        self.assertNotIn('Foreign Team', self._names(resp))

    # -- same-organization validation when adding members ---------------
    def test_add_member_rejects_user_from_another_organization(self):
        for actor in (self.leader, self.org_admin):
            with self.subTest(actor=actor.username):
                resp = self._call('post', self._members(), user=actor,
                                  body={'user_id': self.foreign_user.id, 'role_id': TeamRole.MEMBER})
                self.assertEqual(resp.status_code, 400)
                self.assertEqual(resp.json()['code'], 'USER_NOT_IN_ORGANIZATION')
        self.assertIsNone(self._role_of(self.foreign_user))

    def test_add_member_accepts_user_with_active_membership_in_this_organization(self):
        resp = self._call('post', self._members(), user=self.org_admin,
                          body={'user_id': self.dual_membership_user.id, 'role_id': TeamRole.MEMBER})
        self.assertEqual(resp.status_code, 201)
