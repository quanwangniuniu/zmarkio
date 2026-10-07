from rest_framework.throttling import SimpleRateThrottle

from public_api.principal import ApiPrincipal


class ApiCredentialRateThrottle(SimpleRateThrottle):
    """Rate-limits per credential, so integrations sharing an IP don't share a budget."""

    scope = 'public_api'

    def get_cache_key(self, request, view):
        principal = request.user
        if not isinstance(principal, ApiPrincipal):
            return None
        return self.cache_format % {'scope': self.scope, 'ident': principal.throttle_key}
