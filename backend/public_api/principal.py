"""The identity a public API request runs as."""

from public_api.scopes import grants


class ApiPrincipal:
    """
    `request.user` for a request authenticated by an API key or OAuth client.

    It is deliberately not a user: no human's role, membership or staff flag
    applies, so internal permission checks that look at a user cannot grant it
    anything. Everything it may see is derived from (organization_id, project_id).
    """

    is_authenticated = True
    is_anonymous = False
    is_active = True
    is_staff = False
    is_superuser = False
    pk = None
    id = None

    def __init__(self, *, kind, credential_id, name, organization_id, organization_slug, project_id, scopes):
        self.kind = kind
        self.credential_id = credential_id
        self.name = name
        self.organization_id = organization_id
        self.organization_slug = organization_slug
        self.project_id = project_id
        self.scopes = list(scopes)

    @property
    def throttle_key(self):
        return f'{self.kind}:{self.credential_id}'

    def can(self, resource, access):
        return grants(self.scopes, resource, access)

    def __str__(self):
        return f'{self.kind} {self.name}'
