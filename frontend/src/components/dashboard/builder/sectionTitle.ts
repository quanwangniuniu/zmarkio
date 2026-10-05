export const SECTION_TITLE_ID = 'section-title';

export function isSectionTitle(id: string): boolean {
  return id.startsWith(`${SECTION_TITLE_ID}-`);
}
