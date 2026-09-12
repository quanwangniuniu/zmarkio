import logger from '@/lib/logger';

// Ensure this route uses Node.js runtime (not edge runtime)
export const runtime = 'nodejs';

/** Base origin for Django (no trailing slash, no /api suffix — we add /api/ when proxying). */
function resolveBackendOrigin() {
  let base = (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000').trim();
  base = base.replace(/\/$/, '');
  // Misconfig: NEXT_PUBLIC_API_URL=http://host:8000/api → would produce /api/api/... and 404 on Django
  if (base.endsWith('/api')) {
    base = base.slice(0, -4);
  }
  return base;
}

function resolveDecisionServiceOrigin() {
  return (process.env.DECISION_SERVICE_URL || 'http://decision-service:8080').trim().replace(/\/$/, '');
}

/**
 * Segment after /api/ as requested by the client, preserving a trailing slash.
 * Next [...path] drops the final empty segment when the URL ends with /, which breaks
 * Django APPEND_SLASH routes like POST /api/v1/linear/push-task/ → 404 without this.
 */
function apiPathFromRequest(request, params) {
  const pathname = new URL(request.url).pathname;
  const prefix = '/api/';
  if (pathname.startsWith(prefix)) {
    const rest = pathname.slice(prefix.length);
    if (rest) return rest;
  }
  return Array.isArray(params?.path) ? params.path.join('/') : '';
}

async function proxyRequest(request, params, method) {
  const path = apiPathFromRequest(request, params);
  const url = new URL(request.url);
  const searchParams = url.searchParams.toString();
  const contentType = request.headers.get('content-type') || '';
  const backendUrl = resolveBackendOrigin();
  const targetOrigin = path === 'decisions' || path.startsWith('decisions/')
    ? resolveDecisionServiceOrigin()
    : backendUrl;
  const targetUrl = `${targetOrigin}/api/${path}${searchParams ? `?${searchParams}` : ''}`;

  try {
    const hasBody = !['GET', 'HEAD'].includes(method);
    const body = hasBody ? await request.arrayBuffer() : undefined;
    const outgoingHeaders = {
      ...(request.headers.get('authorization') && {
        'Authorization': request.headers.get('authorization')
      }),
      ...(request.headers.get('cookie') && {
        'Cookie': request.headers.get('cookie')
      }),
      ...(request.headers.get('x-project-id') && {
        'x-project-id': request.headers.get('x-project-id')
      }),
      ...(request.headers.get('x-organization-token') && {
        'X-Organization-Token': request.headers.get('x-organization-token')
      }),
      ...(contentType && { 'Content-Type': contentType }),
    };

    const response = await fetch(targetUrl, {
      method,
      headers: outgoingHeaders,
      body,
    });

    const text = await response.text();
    const contentResponseType = response.headers.get('content-type') || '';
    const data = text && contentResponseType.includes('application/json')
      ? JSON.parse(text)
      : text;

    // Print sucess log
    logger.info({ route: path, method, status: response.status }, `Proxy ${method} request`);

    if (typeof data === 'string') {
      return new Response(data, {
        status: response.status,
        headers: {
          'Content-Type': contentResponseType || 'text/plain',
        },
      });
    }

    return Response.json(data ?? {}, {
      status: response.status,
      headers: {
        'Content-Type': 'application/json',
      },
    });
  } catch (error) {
    // Print error log
    logger.error({ route: path, method, error: String(error) }, 'API proxy error');
    return Response.json(
      { error: 'Failed to fetch data from backend' },
      { status: 500 }
    );
  }
}

export async function GET(request, { params }) {
  return proxyRequest(request, params, 'GET');
}

export async function POST(request, { params }) {
  return proxyRequest(request, params, 'POST');
}

export async function PUT(request, { params }) {
  return proxyRequest(request, params, 'PUT');
}

export async function PATCH(request, { params }) {
  return proxyRequest(request, params, 'PATCH');
}

export async function DELETE(request, { params }) {
  return proxyRequest(request, params, 'DELETE');
}
