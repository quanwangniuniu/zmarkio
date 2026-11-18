import { useSyncExternalStore } from 'react';

const listeners = new Set<() => void>();
let configVersion = 0;

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
};

export const useOptionConfigVersion = () =>
  useSyncExternalStore(subscribe, () => configVersion, () => 0);

export function createConfigLoader<T>(
  fetchConfig: (projectId: number | string) => Promise<T>,
  applyConfig: (config: T) => void,
) {
  const requests = new Map<string, Promise<T | null>>();
  let currentProjectKey: string | null = null;

  const apply = (config: T) => {
    applyConfig(config);
    configVersion += 1;
    listeners.forEach((listener) => listener());
  };

  const load = (projectId: number | string): Promise<T | null> => {
    const key = String(projectId);
    currentProjectKey = key;
    let request = requests.get(key);
    if (!request) {
      request = fetchConfig(projectId).catch(() => {
        requests.delete(key);
        return null;
      });
      requests.set(key, request);
    }
    return request.then((config) => {
      if (config && currentProjectKey === key) apply(config);
      return config;
    });
  };

  const store = (projectId: number | string, config: T): void => {
    requests.set(String(projectId), Promise.resolve(config));
    if (currentProjectKey === String(projectId)) apply(config);
  };

  return { load, store };
}
