import type { SourceModeHandler } from '../types';
import { existingMode } from './existing';
import { customMode } from './custom';
import { externalUrlMode } from './externalUrl';

const handlers: SourceModeHandler[] = [existingMode, customMode, externalUrlMode];

export const sourceModeRegistry = new Map<string, SourceModeHandler>(
  handlers.map((h) => [h.mode, h])
);
