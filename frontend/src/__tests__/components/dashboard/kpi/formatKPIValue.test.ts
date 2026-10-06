import { formatKPIValue } from '@/components/dashboard/kpi/formatKPIValue';

describe('formatKPIValue', () => {
  it('scales percent by 100 so a ratio reads as a percentage', () => {
    // The bug this locks down: appending "%" to the raw ratio rendered
    // clicks/impressions = 0.025 as "0.03%" instead of "2.5%".
    expect(formatKPIValue('0.025', 'percent')).toBe('2.5%');
    expect(formatKPIValue('0.5', 'percent')).toBe('50%');
    expect(formatKPIValue('1', 'percent')).toBe('100%');
  });

  it('does not scale number or currency', () => {
    expect(formatKPIValue('0.025', 'number')).toBe('0.03');
    expect(formatKPIValue('3.96', 'currency')).toBe('$3.96');
  });

  it('rounds to at most two decimals', () => {
    expect(formatKPIValue('3.957363050884869111997977849', 'number')).toBe('3.96');
  });

  it('renders a missing value as a dash', () => {
    expect(formatKPIValue(null, 'number')).toBe('—');
    expect(formatKPIValue('', 'percent')).toBe('—');
  });

  it('shows a non-numeric value verbatim rather than NaN', () => {
    expect(formatKPIValue('not-a-number', 'number')).toBe('not-a-number');
  });
});
