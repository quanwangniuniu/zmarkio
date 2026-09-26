import { downloadBlob, filenameFromContentDisposition } from '@/lib/downloadBlob';

describe('filenameFromContentDisposition', () => {
  it('extracts a quoted filename', () => {
    expect(
      filenameFromContentDisposition('attachment; filename="report-2026.csv"', 'fallback.csv'),
    ).toBe('report-2026.csv');
  });

  it('falls back when the header is absent or unparseable', () => {
    expect(filenameFromContentDisposition(undefined, 'fallback.csv')).toBe('fallback.csv');
    expect(filenameFromContentDisposition('attachment', 'fallback.csv')).toBe('fallback.csv');
  });
});

describe('downloadBlob', () => {
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;

  beforeEach(() => {
    jest.useFakeTimers();
    URL.createObjectURL = jest.fn(() => 'blob:mock');
    URL.revokeObjectURL = jest.fn();
  });

  afterEach(() => {
    jest.useRealTimers();
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  });

  it('clicks a detached anchor and revokes the object URL', () => {
    const anchor = document.createElement('a');
    const click = jest.spyOn(anchor, 'click').mockImplementation(() => {});
    const createElement = jest
      .spyOn(document, 'createElement')
      .mockReturnValue(anchor as HTMLAnchorElement);
    const append = jest.spyOn(document.body, 'appendChild');
    const remove = jest.spyOn(document.body, 'removeChild');

    downloadBlob(new Blob(['a']), 'quality.csv');

    expect(anchor.href).toContain('blob:mock');
    expect(anchor.download).toBe('quality.csv');
    expect(click).toHaveBeenCalled();
    expect(append).toHaveBeenCalledWith(anchor);
    expect(remove).toHaveBeenCalledWith(anchor);
    expect(document.body.contains(anchor)).toBe(false);

    jest.runAllTimers();
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:mock');

    createElement.mockRestore();
    append.mockRestore();
    remove.mockRestore();
  });
});
