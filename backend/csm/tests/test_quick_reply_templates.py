"""Quick reply template library (MED-213) — targeted API tests.

Covers the acceptance criteria most at risk plus the cross-tenant create check:
- AC1: creating requires at least one tag
- AC4: every edit is recorded in history with the editor
- AC5: a team-scoped template is hidden from non-members of that team
- Security: a user cannot create a template in an organisation they don't belong to
- Slug: templates use slug-only URL lookups (SMP-539)
- TemplateTag: admin-managed tag CRUD (agents denied write/delete)
"""
import pytest
from core.models import Team
from csm.models import CustomerUser, QuickReplyTemplate, QuickReplyTemplateHistory, TemplateTag

pytestmark = pytest.mark.django_db

URL = '/api/csm/templates/'
TAG_URL = '/api/csm/template-tags/'


def _rows(response):
    """Normalise list responses whether or not pagination is enabled."""
    data = response.data
    if isinstance(data, dict) and 'results' in data:
        return data['results']
    return data


def _member(user, org, *, team=None, user_type='agent'):
    return CustomerUser.objects.create(
        user=user, organisation=org, team=team,
        user_type=user_type, is_active=True,
    )


def _seed_tags(org, *names):
    """Populate the admin-managed tag vocabulary (names stored lowercased)."""
    return [TemplateTag.objects.create(organisation=org, name=n) for n in names]


# --- Security: cross-tenant create -----------------------------------------

def test_create_rejected_for_non_member_org(api_client, user2, customer_organisation):
    _seed_tags(customer_organisation, 'greeting')  # vocabulary exists; failure must be the org check
    api_client.force_authenticate(user2)  # user2 has no CustomerUser in the org
    resp = api_client.post(URL, {
        'organisation': customer_organisation.id,
        'title': 'X', 'content': 'hi', 'tags': ['greeting'],
    }, format='json')
    assert resp.status_code == 400
    assert 'organisation' in resp.data


def test_create_allowed_for_member(api_client, user, customer_organisation):
    _member(user, customer_organisation)
    _seed_tags(customer_organisation, 'greeting')  # tag must be in the managed vocabulary
    api_client.force_authenticate(user)
    resp = api_client.post(URL, {
        'organisation': customer_organisation.id,
        'title': 'Welcome', 'content': 'hi there', 'tags': ['greeting'],
    }, format='json')
    assert resp.status_code == 201, resp.data
    assert resp.data['created_by'] == user.id
    assert resp.data['slug']  # slug must be populated


# --- AC1: at least one tag --------------------------------------------------

def test_create_requires_at_least_one_tag(api_client, user, customer_organisation):
    _member(user, customer_organisation)
    api_client.force_authenticate(user)
    resp = api_client.post(URL, {
        'organisation': customer_organisation.id,
        'title': 'No tags', 'content': 'body', 'tags': [],
    }, format='json')
    assert resp.status_code == 400
    assert 'tags' in resp.data


# --- Tags are an admin-managed allowlist ------------------------------------

def test_create_rejects_tag_not_in_vocabulary(api_client, user, customer_organisation):
    _member(user, customer_organisation)
    _seed_tags(customer_organisation, 'billing')  # only 'billing' is allowed
    api_client.force_authenticate(user)
    resp = api_client.post(URL, {
        'organisation': customer_organisation.id,
        'title': 'X', 'content': 'hi', 'tags': ['billing', 'random'],
    }, format='json')
    assert resp.status_code == 400
    assert 'tags' in resp.data


def test_update_rejects_tag_not_in_vocabulary(api_client, user, customer_organisation):
    _member(user, customer_organisation)
    _seed_tags(customer_organisation, 'greeting')
    tmpl = QuickReplyTemplate.objects.create(
        organisation=customer_organisation, title='T', content='x', tags=['greeting'],
    )
    api_client.force_authenticate(user)
    resp = api_client.patch(f'{URL}{tmpl.slug}/', {'tags': ['greeting', 'unknown']}, format='json')
    assert resp.status_code == 400
    assert 'tags' in resp.data


# --- AC5: team-scoped visibility -------------------------------------------

def test_team_scoped_template_hidden_from_non_team_members(
    api_client, user, user2, organization, customer_organisation,
):
    team = Team.objects.create(organization=organization, name='Frontline')
    _member(user, customer_organisation, team=team)       # in the team
    _member(user2, customer_organisation, team=None)      # member of org, not team

    workspace = QuickReplyTemplate.objects.create(
        organisation=customer_organisation, title='WS', content='x', tags=['a'],
    )
    team_only = QuickReplyTemplate.objects.create(
        organisation=customer_organisation, title='TeamOnly', content='y',
        tags=['b'], team=team,
    )

    api_client.force_authenticate(user)
    ids_member = {t['id'] for t in _rows(api_client.get(URL, {'organisation': customer_organisation.id}))}
    assert {workspace.id, team_only.id} <= ids_member

    api_client.force_authenticate(user2)
    ids_outsider = {t['id'] for t in _rows(api_client.get(URL, {'organisation': customer_organisation.id}))}
    assert workspace.id in ids_outsider
    assert team_only.id not in ids_outsider


# --- AC4: edit history (slug-based) -----------------------------------------

def test_edit_records_history_with_editor(api_client, user, customer_organisation):
    _member(user, customer_organisation, user_type='admin')
    tmpl = QuickReplyTemplate.objects.create(
        organisation=customer_organisation, title='Orig', content='old',
        tags=['a'], created_by=user,
    )
    api_client.force_authenticate(user)
    # Use slug-based URL for PATCH
    resp = api_client.patch(f'{URL}{tmpl.slug}/', {'title': 'New', 'content': 'new'}, format='json')
    assert resp.status_code == 200, resp.data

    history = QuickReplyTemplateHistory.objects.filter(template=tmpl)
    assert history.count() == 1
    snapshot = history.first()
    assert snapshot.title == 'Orig'        # snapshot of the pre-edit state
    assert snapshot.content == 'old'
    assert snapshot.edited_by_id == user.id

    # History endpoint also uses slug
    rows = _rows(api_client.get(f'{URL}{tmpl.slug}/history/'))
    assert len(rows) == 1
    assert rows[0]['edited_by_name']


# --- Slug lookup: numeric IDs must 404 -------------------------------------

def test_numeric_id_returns_404(api_client, user, customer_organisation):
    _member(user, customer_organisation)
    tmpl = QuickReplyTemplate.objects.create(
        organisation=customer_organisation, title='Test', content='x', tags=['a'],
    )
    api_client.force_authenticate(user)
    # Slug lookup should work
    assert api_client.get(f'{URL}{tmpl.slug}/').status_code == 200
    # Numeric ID must 404 (slug-only convention)
    assert api_client.get(f'{URL}{tmpl.id}/').status_code == 404


# --- TemplateTag CRUD (admin-managed) ---------------------------------------

def test_create_and_list_template_tags(api_client, user, customer_organisation):
    _member(user, customer_organisation, user_type='admin')
    api_client.force_authenticate(user)

    resp = api_client.post(TAG_URL, {
        'organisation': customer_organisation.id,
        'name': 'Billing',
    }, format='json')
    assert resp.status_code == 201, resp.data
    assert resp.data['name'] == 'billing'  # normalised to lowercase

    rows = _rows(api_client.get(TAG_URL, {'organisation': customer_organisation.id}))
    assert any(t['name'] == 'billing' for t in rows)


def test_duplicate_tag_rejected(api_client, user, customer_organisation):
    _member(user, customer_organisation, user_type='admin')
    api_client.force_authenticate(user)
    TemplateTag.objects.create(organisation=customer_organisation, name='billing')

    resp = api_client.post(TAG_URL, {
        'organisation': customer_organisation.id,
        'name': 'Billing',  # same name, different case
    }, format='json')
    assert resp.status_code == 400


def test_delete_template_tag(api_client, user, customer_organisation):
    _member(user, customer_organisation, user_type='admin')
    tag = TemplateTag.objects.create(organisation=customer_organisation, name='old-tag')
    api_client.force_authenticate(user)

    resp = api_client.delete(f'{TAG_URL}{tag.id}/')
    assert resp.status_code == 204
    assert not TemplateTag.objects.filter(id=tag.id).exists()


def test_agent_cannot_manage_tags(api_client, user, customer_organisation):
    """Agents may read the tag vocabulary but not create or delete tags."""
    _member(user, customer_organisation)  # default user_type='agent'
    tag = TemplateTag.objects.create(organisation=customer_organisation, name='read-only')
    api_client.force_authenticate(user)

    # Read is allowed
    assert api_client.get(TAG_URL, {'organisation': customer_organisation.id}).status_code == 200

    # Create is denied
    create_resp = api_client.post(TAG_URL, {
        'organisation': customer_organisation.id, 'name': 'sneaky',
    }, format='json')
    assert create_resp.status_code == 403

    # Delete is denied
    assert api_client.delete(f'{TAG_URL}{tag.id}/').status_code == 403
    assert TemplateTag.objects.filter(id=tag.id).exists()


# --- Sandbox "view as team" preview (CSM-S03-05) -----------------------------

@pytest.fixture
def team_templates(organization, customer_organisation):
    frontline = Team.objects.create(organization=organization, name='Frontline')
    billing = Team.objects.create(organization=organization, name='Billing')
    workspace = QuickReplyTemplate.objects.create(
        organisation=customer_organisation, title='WS', content='x', tags=['a'],
    )
    frontline_only = QuickReplyTemplate.objects.create(
        organisation=customer_organisation, title='Frontline', content='y', tags=['a'], team=frontline,
    )
    billing_only = QuickReplyTemplate.objects.create(
        organisation=customer_organisation, title='Billing', content='z', tags=['a'], team=billing,
    )
    return {
        'frontline': frontline, 'billing': billing, 'workspace': workspace,
        'frontline_only': frontline_only, 'billing_only': billing_only,
    }


def _ids(api_client, org, **params):
    resp = api_client.get(URL, {'organisation': org.id, **params})
    assert resp.status_code == 200, resp.data
    return {t['id'] for t in _rows(resp)}


def test_view_as_team_shows_that_teams_view(api_client, user, customer_organisation, team_templates):
    # The admin is in Billing, but previews Frontline.
    _member(user, customer_organisation, team=team_templates['billing'], user_type='admin')
    api_client.force_authenticate(user)
    ids = _ids(api_client, customer_organisation, view_as_team=team_templates['frontline'].id)
    assert ids == {team_templates['workspace'].id, team_templates['frontline_only'].id}


def test_view_as_no_team_shows_workspace_templates_only(
    api_client, user, customer_organisation, team_templates,
):
    _member(user, customer_organisation, team=team_templates['billing'], user_type='admin')
    api_client.force_authenticate(user)
    assert _ids(api_client, customer_organisation, view_as_team='none') == {team_templates['workspace'].id}


def test_default_scope_unchanged_without_view_as_team(
    api_client, user, customer_organisation, team_templates,
):
    _member(user, customer_organisation, team=team_templates['billing'], user_type='admin')
    api_client.force_authenticate(user)
    assert _ids(api_client, customer_organisation) == {
        team_templates['workspace'].id, team_templates['billing_only'].id,
    }


def test_view_as_team_forbidden_for_agents(api_client, user, customer_organisation, team_templates):
    _member(user, customer_organisation, user_type='agent')
    api_client.force_authenticate(user)
    resp = api_client.get(URL, {
        'organisation': customer_organisation.id, 'view_as_team': team_templates['frontline'].id,
    })
    assert resp.status_code == 403


def test_view_as_team_forbidden_for_admin_of_other_org(
    api_client, user, organization, customer_organisation, team_templates,
):
    from customer.models import CustomerOrganisation
    other = CustomerOrganisation.objects.create(name='Other', organization=organization)
    _member(user, other, user_type='admin')
    api_client.force_authenticate(user)
    resp = api_client.get(URL, {
        'organisation': customer_organisation.id, 'view_as_team': 'none',
    })
    assert resp.status_code == 403


def test_view_as_team_requires_organisation(api_client, user, customer_organisation):
    _member(user, customer_organisation, user_type='admin')
    api_client.force_authenticate(user)
    resp = api_client.get(URL, {'view_as_team': 'none'})
    assert resp.status_code == 400


def test_view_as_team_rejects_foreign_team(api_client, user, customer_organisation):
    from core.models import Organization
    foreign = Team.objects.create(organization=Organization.objects.create(name='Elsewhere'), name='X')
    _member(user, customer_organisation, user_type='admin')
    api_client.force_authenticate(user)
    resp = api_client.get(URL, {'organisation': customer_organisation.id, 'view_as_team': foreign.id})
    assert resp.status_code == 400
    assert 'view_as_team' in resp.data


def test_preview_teams_lists_agent_and_template_teams(
    api_client, user, user2, organization, customer_organisation, team_templates,
):
    agents_only = Team.objects.create(organization=organization, name='Agents only')
    Team.objects.create(organization=organization, name='Unused')
    _member(user, customer_organisation, user_type='admin')
    _member(user2, customer_organisation, team=agents_only)
    api_client.force_authenticate(user)
    resp = api_client.get(f'{URL}preview-teams/', {'organisation': customer_organisation.id})
    assert resp.status_code == 200
    assert [t['name'] for t in resp.data] == ['Agents only', 'Billing', 'Frontline']


def test_preview_teams_forbidden_for_agents(api_client, user, customer_organisation):
    _member(user, customer_organisation, user_type='agent')
    api_client.force_authenticate(user)
    resp = api_client.get(f'{URL}preview-teams/', {'organisation': customer_organisation.id})
    assert resp.status_code == 403


def test_view_as_team_rejects_workspace_team_unused_by_this_organisation(
    api_client, user, organization, customer_organisation, team_templates,
):
    # Same workspace, but no agent of this organisation is in it and no template targets it.
    unused = Team.objects.create(organization=organization, name='Unused')
    _member(user, customer_organisation, user_type='admin')
    api_client.force_authenticate(user)
    resp = api_client.get(URL, {'organisation': customer_organisation.id, 'view_as_team': unused.id})
    assert resp.status_code == 400
    assert 'view_as_team' in resp.data


def test_view_as_team_accepts_every_team_the_picker_offers(
    api_client, user, user2, organization, customer_organisation, team_templates,
):
    agents_only = Team.objects.create(organization=organization, name='Agents only')
    _member(user, customer_organisation, user_type='admin')
    _member(user2, customer_organisation, team=agents_only)
    api_client.force_authenticate(user)
    offered = api_client.get(f'{URL}preview-teams/', {'organisation': customer_organisation.id}).data
    assert offered
    for team in offered:
        resp = api_client.get(URL, {'organisation': customer_organisation.id, 'view_as_team': team['id']})
        assert resp.status_code == 200, (team, resp.data)


def test_preview_teams_forbidden_for_admin_of_other_org(
    api_client, user, organization, customer_organisation, team_templates,
):
    from customer.models import CustomerOrganisation
    other = CustomerOrganisation.objects.create(name='Other', organization=organization)
    _member(user, other, user_type='admin')
    api_client.force_authenticate(user)
    resp = api_client.get(f'{URL}preview-teams/', {'organisation': customer_organisation.id})
    assert resp.status_code == 403


# --- Security: another organisation's templates ------------------------------

@pytest.fixture
def foreign_template(organization):
    """A template in an organisation the test user is not a member of."""
    from customer.models import CustomerOrganisation
    other = CustomerOrganisation.objects.create(name='Other customer', organization=organization)
    return QuickReplyTemplate.objects.create(
        organisation=other, title='Secret', content='not yours', tags=['a'],
    )


@pytest.fixture
def agent_client(api_client, user, customer_organisation):
    _member(user, customer_organisation, user_type='agent')
    api_client.force_authenticate(user)
    return api_client


def test_list_of_another_organisation_is_forbidden(agent_client, foreign_template):
    resp = agent_client.get(URL, {'organisation': foreign_template.organisation_id})
    assert resp.status_code == 403


@pytest.mark.parametrize('with_param', [True, False])
def test_another_organisations_template_cannot_be_read_or_changed(
    agent_client, foreign_template, with_param,
):
    params = f'?organisation={foreign_template.organisation_id}' if with_param else ''
    detail = f'{URL}{foreign_template.slug}/'
    expected = 403 if with_param else 404  # without the param it is simply not in scope

    assert agent_client.get(detail + params).status_code == expected
    assert agent_client.get(f'{detail}history/{params}').status_code == expected
    assert agent_client.patch(detail + params, {'title': 'Hacked'}, format='json').status_code == expected
    assert agent_client.delete(detail + params).status_code == expected

    foreign_template.refresh_from_db()
    assert foreign_template.title == 'Secret'
    assert foreign_template.is_active is True


@pytest.mark.parametrize('value', ['abc', '\u00b2', '-1'])
def test_non_numeric_organisation_is_a_400(agent_client, value):
    resp = agent_client.get(URL, {'organisation': value})
    assert resp.status_code == 400
    assert 'organisation' in resp.data


def test_own_organisation_still_listed(agent_client, customer_organisation, foreign_template):
    own = QuickReplyTemplate.objects.create(
        organisation=customer_organisation, title='Mine', content='x', tags=['a'],
    )
    assert _ids(agent_client, customer_organisation) == {own.id}


@pytest.fixture
def foreign_queue(project, foreign_template):
    from csm.models import Queue
    return Queue.objects.create(
        project=project, organisation=foreign_template.organisation, name='Q', tier='T1',
    )


def test_queue_agent_can_read_but_not_change_that_organisations_templates(
    api_client, user, foreign_template, foreign_queue,
):
    # Assigned straight to one of the organisation's queues, as the agents page allows:
    # they can work its conversations, so the composer must still list its templates,
    # but editing and deleting need membership (as creating does).
    from csm.models import QueueAgent
    QueueAgent.objects.create(queue=foreign_queue, user=user)
    api_client.force_authenticate(user)
    org = foreign_template.organisation
    assert _ids(api_client, org) == {foreign_template.id}
    detail = f'{URL}{foreign_template.slug}/'
    assert api_client.get(detail).status_code == 200
    assert api_client.patch(detail, {'title': 'Hacked'}, format='json').status_code == 404
    assert api_client.delete(detail).status_code == 404
    assert api_client.patch(f'{detail}?organisation={org.id}', {'title': 'Hacked'}, format='json').status_code == 403
    foreign_template.refresh_from_db()
    assert (foreign_template.title, foreign_template.is_active) == ('Secret', True)


def test_member_row_with_only_a_queue_can_read_that_organisations_templates(
    api_client, user, foreign_template, foreign_queue,
):
    CustomerUser.objects.create(user=user, queue=foreign_queue, user_type='agent', is_active=True)
    api_client.force_authenticate(user)
    assert _ids(api_client, foreign_template.organisation) == {foreign_template.id}


def test_staff_may_name_any_organisation_but_plain_list_stays_scoped(
    api_client, user, foreign_template,
):
    user.is_staff = True
    user.save()
    api_client.force_authenticate(user)
    assert _ids(api_client, foreign_template.organisation) == {foreign_template.id}
    resp = api_client.get(URL)
    assert resp.status_code == 200
    assert foreign_template.id not in {t['id'] for t in _rows(resp)}

    # Reading is not changing: staff can't edit or delete a non-member organisation's template.
    detail = f'{URL}{foreign_template.slug}/?organisation={foreign_template.organisation_id}'
    assert api_client.patch(detail, {'title': 'Hacked'}, format='json').status_code == 403
    assert api_client.delete(detail).status_code == 403
    foreign_template.refresh_from_db()
    assert (foreign_template.title, foreign_template.is_active) == ('Secret', True)


def test_assignment_to_an_inactive_queue_gives_no_access(api_client, user, foreign_template, foreign_queue):
    from csm.models import QueueAgent
    foreign_queue.is_active = False
    foreign_queue.save()
    QueueAgent.objects.create(queue=foreign_queue, user=user)
    api_client.force_authenticate(user)
    resp = api_client.get(URL, {'organisation': foreign_template.organisation_id})
    assert resp.status_code == 403
