"""Streaming responses that keep the request's tenant schema.

``TenantSchemaMiddleware`` resets ``search_path`` to public in a ``finally``
block that runs as soon as the view returns. The body of a
``StreamingHttpResponse`` is iterated after that, so any query the stream runs
lazily would read the empty public tables instead of the tenant's.

``TenantAwareStreamingResponse`` records the schema while the view is still
running and re-selects it for the duration of the iteration, restoring the
previous schema afterwards (also when the stream raises or the client goes
away), so the pooled connection is handed back the way the middleware left it.
"""

from django.http import StreamingHttpResponse

from core.tenant_context import current_tenant_schema, tenant_schema_context


def _in_tenant_schema(content, schema_name):
    with tenant_schema_context(schema_name):
        yield from content


class TenantAwareStreamingResponse(StreamingHttpResponse):
    """A ``StreamingHttpResponse`` whose content is iterated in the tenant
    schema that was active when the response was created."""

    def __init__(self, streaming_content=(), *args, **kwargs):
        schema_name = current_tenant_schema()
        super().__init__(_in_tenant_schema(streaming_content, schema_name), *args, **kwargs)
