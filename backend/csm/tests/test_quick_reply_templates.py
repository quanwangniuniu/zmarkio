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
