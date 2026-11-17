export function createConfigLoader<T>(
  fetchConfig: (projectId: number | string) => Promise<T>,
  applyConfig: (config: T) => void,
) {
  const requests = new Map<string, Promise<T | null>>();
  let currentProjectKey: string | null = null;

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
      if (config && currentProjectKey === key) applyConfig(config);
      return config;
    });
  };

  const store = (projectId: number | string, config: T): void => {
    requests.set(String(projectId), Promise.resolve(config));
    if (currentProjectKey === String(projectId)) applyConfig(config);
  };

  return { load, store };
}
