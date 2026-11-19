import { EventEmitter } from 'node:events';
import * as http from 'node:http';
import * as https from 'node:https';
import { lookup as dnsLookup } from 'node:dns/promises';
import { fetchUrlText } from '@/lib/urlFetch';

jest.mock('node:dns/promises', () => ({
  lookup: jest.fn(),
}));

jest.mock('node:http', () => ({
  request: jest.fn(),
}));

jest.mock('node:https', () => ({
  request: jest.fn(),
}));

const dnsMock = dnsLookup as jest.Mock;
const httpMock = http.request as unknown as jest.Mock;
const httpsMock = https.request as unknown as jest.Mock;

type MockResponse = {
  statusCode: number;
  headers: Record<string, string>;
  body?: string;
};

function mockResponses(...responses: MockResponse[]) {
  const queue = [...responses];

  const implementation = (
    _url: URL,
    options: http.RequestOptions,
    onResponse: (response: http.IncomingMessage) => void
  ) => {
    const request = new EventEmitter() as EventEmitter & {
      end: () => void;
    };

    request.end = () => {
      const responseData = queue.shift();

      if (!responseData) {
        throw new Error('No mocked HTTP response available');
      }

      const lookup = options.lookup as (
        hostname: string,
        options: { all?: boolean },
        callback: (...args: any[]) => void
      ) => void;

      lookup('example.com', { all: true }, (error: Error | null) => {
        if (error) {
          request.emit('error', error);
          return;
        }
      });

      const response = new EventEmitter() as EventEmitter & {
        statusCode: number;
        headers: http.IncomingHttpHeaders;
      };

      response.statusCode = responseData.statusCode;
      response.headers = responseData.headers;

      onResponse(response as http.IncomingMessage);

      process.nextTick(() => {
        if (responseData.body) {
          response.emit('data', Buffer.from(responseData.body));
        }

        response.emit('end');
      });
    };

    return request;
  };

  httpMock.mockImplementation(implementation);
  httpsMock.mockImplementation(implementation);
}

beforeEach(() => {
  delete process.env.BROWSERLESS_API_KEY;
  dnsMock.mockReset();
  httpMock.mockReset();
  httpsMock.mockReset();
});

describe('fetchUrlText direct-fetch SSRF protection', () => {
  it.each([
    'http://127.0.0.1',
    'http://10.0.0.1',
    'http://172.16.0.1',
    'http://192.168.1.1',
    'http://169.254.169.254',
    'http://100.64.0.1',
    'http://224.0.0.1',
    'http://240.0.0.1',
    'http://[::]',
    'http://[::1]',
    'http://[fc00::1]',
    'http://[fe80::1]',
    'http://[ff02::1]',
  ])('rejects private or reserved IP literal: %s', async (url) => {
    await expect(fetchUrlText(url)).rejects.toThrow();
    expect(dnsMock).not.toHaveBeenCalled();
    expect(httpMock).not.toHaveBeenCalled();
    expect(httpsMock).not.toHaveBeenCalled();
  });

  it('rejects a public-looking hostname resolving to a private IPv4 address', async () => {
    dnsMock.mockResolvedValue([
      { address: '10.0.0.8', family: 4 },
    ]);

    await expect(
      fetchUrlText('https://example.com/page')
    ).rejects.toThrow('URL host resolves to a non-public address');

    expect(httpMock).not.toHaveBeenCalled();
    expect(httpsMock).not.toHaveBeenCalled();
  });

  it('rejects a hostname when any DNS result is private', async () => {
    dnsMock.mockResolvedValue([
      { address: '93.184.216.34', family: 4 },
      { address: '192.168.1.10', family: 4 },
    ]);

    await expect(
      fetchUrlText('https://example.com/page')
    ).rejects.toThrow('URL host resolves to a non-public address');

    expect(httpsMock).not.toHaveBeenCalled();
  });

  it('uses the validated DNS address for the actual HTTPS connection', async () => {
    dnsMock.mockResolvedValue([
      { address: '93.184.216.34', family: 4 },
    ]);

    let pinnedAddresses: unknown;

    httpsMock.mockImplementation(
      (_url: URL, options: http.RequestOptions, onResponse: Function) => {
        const lookup = options.lookup as Function;

        lookup(
          'example.com',
          { all: true },
          (error: Error | null, addresses: unknown) => {
            if (error) throw error;
            pinnedAddresses = addresses;
          }
        );

        const request = new EventEmitter() as EventEmitter & {
          end: () => void;
        };

        request.end = () => {
          const response = new EventEmitter() as EventEmitter & {
            statusCode: number;
            headers: http.IncomingHttpHeaders;
          };

          response.statusCode = 200;
          response.headers = { 'content-type': 'text/html' };

          onResponse(response);

          process.nextTick(() => {
            response.emit('data', Buffer.from('<html><body>Public page</body></html>'));
            response.emit('end');
          });
        };

        return request;
      }
    );

    await expect(
      fetchUrlText('https://example.com/page')
    ).resolves.toContain('Public page');

    expect(pinnedAddresses).toEqual([
      { address: '93.184.216.34', family: 4 },
    ]);
  });

  it('rejects a redirect from a public page to localhost before making another request', async () => {
    dnsMock.mockResolvedValue([
      { address: '93.184.216.34', family: 4 },
    ]);

    mockResponses({
      statusCode: 302,
      headers: {
        location: 'http://127.0.0.1/admin',
      },
    });

    await expect(
      fetchUrlText('https://example.com/redirect')
    ).rejects.toThrow('URL host is not allowed');

    expect(httpsMock).toHaveBeenCalledTimes(1);
    expect(httpMock).not.toHaveBeenCalled();
    expect(dnsMock).toHaveBeenCalledTimes(1);
  });

  it('allows a successful public HTML response', async () => {
    dnsMock.mockResolvedValue([
      { address: '93.184.216.34', family: 4 },
    ]);

    mockResponses({
      statusCode: 200,
      headers: {
        'content-type': 'text/html; charset=utf-8',
      },
      body: '<html><body>Product page content</body></html>',
    });

    await expect(
      fetchUrlText('https://example.com/product')
    ).resolves.toContain('Product page content');

    expect(dnsMock).toHaveBeenCalledTimes(1);
    expect(httpsMock).toHaveBeenCalledTimes(1);
  });
});

describe('additional hostname and redirect protection', () => {
  it.each([
    'http://metadata.google.internal',
    'http://service.internal',
    'http://printer.lan',
    'http://server.localdomain',
  ])('rejects internal hostname: %s', async (url) => {
    await expect(fetchUrlText(url)).rejects.toThrow();
    expect(dnsMock).not.toHaveBeenCalled();
    expect(httpMock).not.toHaveBeenCalled();
    expect(httpsMock).not.toHaveBeenCalled();
  });

  it.each([
    'fc00::1',
    'fe80::1',
    '::1',
    'ff02::1',
  ])('rejects DNS resolving to private IPv6 address: %s', async (address) => {
    dnsMock.mockResolvedValue([
      { address, family: 6 },
    ]);

    await expect(
      fetchUrlText('https://example.com/page')
    ).rejects.toThrow('URL host resolves to a non-public address');

    expect(httpsMock).not.toHaveBeenCalled();
  });

  it('re-resolves and rejects a redirect hostname resolving to a private address', async () => {
    dnsMock
      .mockResolvedValueOnce([
        { address: '93.184.216.34', family: 4 },
      ])
      .mockResolvedValueOnce([
        { address: '192.168.1.20', family: 4 },
      ]);

    mockResponses({
      statusCode: 302,
      headers: {
        location: 'https://redirected-example.com/page',
      },
    });

    await expect(
      fetchUrlText('https://example.com/redirect')
    ).rejects.toThrow('URL host resolves to a non-public address');

    expect(dnsMock).toHaveBeenCalledTimes(2);
    expect(httpsMock).toHaveBeenCalledTimes(1);
  });

  it('allows a redirect to another public hostname after validating its DNS', async () => {
    dnsMock
      .mockResolvedValueOnce([
        { address: '93.184.216.34', family: 4 },
      ])
      .mockResolvedValueOnce([
        { address: '1.1.1.1', family: 4 },
      ]);

    mockResponses(
      {
        statusCode: 302,
        headers: {
          location: 'https://another-example.com/final',
        },
      },
      {
        statusCode: 200,
        headers: {
          'content-type': 'text/html',
        },
        body: '<html><body>Redirected public page</body></html>',
      }
    );

    await expect(
      fetchUrlText('https://example.com/redirect')
    ).resolves.toContain('Redirected public page');

    expect(dnsMock).toHaveBeenCalledTimes(2);
    expect(httpsMock).toHaveBeenCalledTimes(2);
  });
});
