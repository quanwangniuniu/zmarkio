export const SECTION_TITLE_ID = 'section-title';
/** Keep in sync with MAX_SECTION_TITLES in backend/dashboard/layout.py. */
export const MAX_SECTION_TITLES = 10;

export function isSectionTitle(id: string): boolean {
  return id.startsWith(`${SECTION_TITLE_ID}-`);
}

export function hasMaxSectionTitles(widgets: { id: string }[]): boolean {
  return widgets.filter((widget) => isSectionTitle(widget.id)).length >= MAX_SECTION_TITLES;
}
