declare module 'zustand' {
    type SetState<T> = (
      partial: T | Partial<T> | ((state: T) => T | Partial<T>),
      replace?: boolean,
    ) => void;
  
    type StoreHook<T> = {
      (): T;
      <U>(selector: (state: T) => U): U;
      getState: () => T;
      setState: SetState<T>;
      subscribe: (listener: (state: T, prevState: T) => void) => () => void;
      getInitialState: () => T;
    };
  
    export function create<T>(
      initializer: (set: SetState<T>, get: () => T) => T,
    ): StoreHook<T>;
  
    export function create<T>(): (
      initializer: (set: SetState<T>, get: () => T) => T,
    ) => StoreHook<T>;
}

declare module 'zustand/middleware' {
    export function persist<T>(
        initializer: (
        set: (
            partial: T | Partial<T> | ((state: T) => T | Partial<T>),
            replace?: boolean,
        ) => void,
        get: () => T,
        ) => T,
        options: {
        name: string;
        storage?: unknown;
        partialize?: (state: T) => unknown;
        onRehydrateStorage?: (
            state: T,
        ) => ((state?: T, error?: unknown) => void) | void;
        },
    ): (
        set: (
        partial: T | Partial<T> | ((state: T) => T | Partial<T>),
        replace?: boolean,
        ) => void,
        get: () => T,
    ) => T;

    export function createJSONStorage(
        getStorage: () => {
        getItem: (name: string) => string | null;
        setItem: (name: string, value: string) => void;
        removeItem: (name: string) => void;
        },
    ): unknown;
}