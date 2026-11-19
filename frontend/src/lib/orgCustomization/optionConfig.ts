import type { OptionConfig } from '@/types/orgCustomization';

export interface OptionTarget {
  labels: Record<string, string>;
  defaults: Record<string, string>;
  values: string[];
  colors: Record<string, string>;
}

export const createOptionTarget = (labels: Record<string, string>): OptionTarget => ({
  labels,
  defaults: { ...labels },
  values: Object.keys(labels),
  colors: {},
});

export const applyOptionConfig = (configs: OptionConfig[], targets: Record<string, OptionTarget>): void => {
  Object.entries(targets).forEach(([field, target]) => {
    const rows = Object.keys(target.defaults)
      .map((value, index) => configs.find((row) => row.field === field && row.value === value)
        ?? { value, label: null, color: null, enabled: true, order: index })
      .sort((a, b) => a.order - b.order);
    rows.forEach((row) => {
      target.labels[row.value] = row.label || target.defaults[row.value];
      if (row.color) target.colors[row.value] = row.color;
      else delete target.colors[row.value];
    });
    target.values.splice(0, target.values.length, ...rows.filter((row) => row.enabled).map((row) => row.value));
  });
};

export const visibleOptions = (target: OptionTarget, current?: string | string[] | null): string[] => [
  ...target.values,
  ...[current ?? []].flat().filter((value) => value && !target.values.includes(value)),
];

export const optionColorStyle = (color?: string) =>
  color ? { backgroundColor: `${color}1A`, color } : undefined;

export const optionChoices = <T extends string = string>(target: OptionTarget, current?: string | string[] | null) =>
  visibleOptions(target, current).map((value) => ({ value: value as T, label: target.labels[value] }));

export const preferredOption = (target: OptionTarget, preferred: string): string =>
  target.values.includes(preferred) ? preferred : target.values[0] ?? preferred;

export const optionLabel = (target: OptionTarget, value?: string | null): string =>
  value ? target.labels[value] ?? value : '';
