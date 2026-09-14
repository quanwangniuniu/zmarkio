export type StoredProject = { id: number; slug: string };

/** Read one complete project, never combining fields from different versions. */
export function readStoredProject(rawValues: (string | null)[]): StoredProject | null {
  for (const raw of rawValues) {
    if (!raw) continue;
    try {
      const project = JSON.parse(raw)?.state?.activeProject;
      if (
        Number.isSafeInteger(project?.id) && project.id > 0 &&
        typeof project?.slug === 'string' && project.slug.trim().length > 0
      ) {
        return { id: project.id, slug: project.slug };
      }
    } catch {
      // A malformed new value must not prevent reading valid legacy data.
    }
  }
  return null;
}
