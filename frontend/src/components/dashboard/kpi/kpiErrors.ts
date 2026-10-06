import type { KPIFormulaError } from '@/types/report';

/** Matches `report.kpi_registry.NO_DATA_CODE`.
 *
 * It arrives on the same `error` channel as a formula error, but it is not one:
 * the formula is valid and there is simply nothing in the window to evaluate
 * it against. Presented as information so an unsynced project does not look
 * like the author made a mistake.
 */
export const KPI_NO_DATA_CODE = '#NODATA';

export function isNoData(error: KPIFormulaError | null | undefined): boolean {
  return error?.code === KPI_NO_DATA_CODE;
}

/** A genuine formula problem the author has to fix. */
export function isFormulaFault(error: KPIFormulaError | null | undefined): boolean {
  return Boolean(error) && !isNoData(error);
}
