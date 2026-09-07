import api from '../api';
import { AiConsentRequiredError, aiConsentSpreadsheetId, isAiConsentRequired } from './aiConsentApi';

export class PivotAgentError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'PivotAgentError';
  }
}

export interface GeneratedPivotConfig {
  rows_config: string[];
  columns_config: Array<string | { field: string; sort?: 'asc' | 'desc' }>;
  values_config: Array<{ field: string; aggregation: string; display: string }>;
  filters_config?: Record<string, unknown>;
  show_grand_total_row: boolean;
}

export const PivotAPI = {
  /**
   * Send a natural-language pivot table description to Gemini and get back a
   * validated PivotConfig. Throws PivotAgentError when the instruction cannot
   * be fulfilled (unknown column, ambiguous request, etc).
   */
  generatePivotConfig: async (sheetId: number, instruction: string): Promise<GeneratedPivotConfig> => {
    let response;
    try {
      response = await api.post<{ config?: GeneratedPivotConfig; error?: string }>(
        `/api/spreadsheet/sheets/${sheetId}/generate-pivot-config/`,
        { instruction },
        { timeout: 60000 } // Gemini can take 15-30s; override the global 10s default
      );
    } catch (err) {
      if (isAiConsentRequired(err)) throw new AiConsentRequiredError(aiConsentSpreadsheetId(err));
      throw err;
    }
    const { config, error } = response.data;
    if (error) throw new PivotAgentError(error);
    if (!config) throw new Error('No pivot config returned.');
    return config;
  },
};
