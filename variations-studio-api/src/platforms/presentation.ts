import type { PlatformPresentation, PlatformSpec } from './types';

/** Serializable UI input from the same rules, without internal prompt text. */
export function toPlatformPresentation(spec: PlatformSpec): PlatformPresentation {
  const { id, displayName, fields, cta, aspectRatios, language } = spec;
  // Return a detached data object so form consumers cannot mutate server rules.
  return JSON.parse(JSON.stringify({ id, displayName, fields, cta, aspectRatios, language }));
}
