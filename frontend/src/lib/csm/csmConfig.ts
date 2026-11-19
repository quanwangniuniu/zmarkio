import { fetchOptionConfig } from '@/lib/api/orgCustomizationApi';
import { createConfigLoader } from '@/lib/orgCustomization/configLoader';
import { applyOptionConfig, createOptionTarget } from '@/lib/orgCustomization/optionConfig';
import { PRIORITY_LABELS } from '@/types/csm';

export const CSM_PRIORITY = createOptionTarget(PRIORITY_LABELS);

const csmConfigLoader = createConfigLoader(
  (projectId) => fetchOptionConfig('csm', projectId),
  (config) => applyOptionConfig(config.options, { ticket_priority: CSM_PRIORITY }),
);

export const loadCsmConfig = csmConfigLoader.load;
export const storeCsmConfig = csmConfigLoader.store;
