const ORIGIN = 'http://studio.test';

type RequestOptions = {
  token?: string;
  authorization?: string;
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
};

export function studioRequest(path: string, options: RequestOptions = {}): Request {
  const headers: Record<string, string> = { ...(options.headers ?? {}) };
  if (options.authorization !== undefined) {
    headers.Authorization = options.authorization;
  } else if (options.token) {
    headers.Authorization = `Bearer ${options.token}`;
  }

  const method = options.method ?? (options.body === undefined ? 'GET' : 'POST');
  if (options.body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }

  return new Request(`${ORIGIN}${path}`, {
    method,
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
}

export async function readJson(response: Response): Promise<Record<string, unknown>> {
  return (await response.json()) as Record<string, unknown>;
}
