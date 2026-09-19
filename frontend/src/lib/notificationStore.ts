import { create } from "zustand";

export type NotificationConnectionStatus =
  | "disconnected"
  | "connecting"
  | "connected"
  | "reconnecting";

export type ToastTag = "success" | "error" | "loading" | "info";

export interface DedupeToastQueueItem {
  dedupeKey: string;
  /** Normalized message used for rendering + dedupe stability */
  message: string;
  type: ToastTag;
  /**
   * Optional caller-provided operation scope (e.g. "klaviyo.create").
   * Same message + different operation must not merge.
   */
  operation?: string;
  count: number;
}

function normalizeWhitespace(input: string): string {
  // Collapses consecutive whitespace so "Network   error" === "Network error"
  return input.trim().replace(/\s+/g, " ");
}

export function normalizeToastMessage(message: string): string {
  return normalizeWhitespace(message);
}

function fnv1a32(str: string): number {
  // FNV-1a 32-bit hash (stable across runtimes; avoids node:crypto in tests/bundles)
  let hash = 0x811c9dc5;
  for (let i = 0; i < str.length; i += 1) {
    hash ^= str.charCodeAt(i);
    // eslint-disable-next-line no-bitwise
    hash = (hash * 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

/**
 * Dedupe contract:
 * - Same type + same normalized message + same operation → same key (merge / count++)
 * - Different type, message, or operation → different key (do not merge)
 * - `operation` is optional; omit only when message alone is enough to identify the action.
 */
export function computeToastDedupeKey(
  message: string,
  type: ToastTag,
  operation?: string,
): string {
  const normalized = normalizeToastMessage(message);
  const hash = fnv1a32(normalized);
  const op = (operation ?? "").trim();
  return op ? `${type}:${hash.toString(16)}:${op}` : `${type}:${hash.toString(16)}`;
}

export interface NotificationStore { 
  /** Global unread count shown in Header bell badge */
  unreadCount: number;
  /** Unread count for chat-activity notifications only (shown on Activity Bell in Messages) */
  chatActivityCount: number;
  /** Timestamp of last refresh - used to trigger re-fetches */
  lastRefresh: number;
  /** Health of the global notification SSE connection */
  connectionStatus: NotificationConnectionStatus;
  /** Set the unread count */
  setUnreadCount: (count: number) => void;
  /** Set the chat-activity unread count */
  setChatActivityCount: (count: number) => void;
  /** Trigger a refresh across all notification consumers */
  triggerRefresh: () => void;
  /** Update the global notification SSE connection health */
  setConnectionStatus: (status: NotificationConnectionStatus) => void;

  // Toast dedupe state
  toastQueue: Record<string, DedupeToastQueueItem>;
  /**
   * Increment count for the dedupeKey derived from message+type(+optional operation).
   * Returns the effective dedupeKey and updated count.
   */
  incrementToast: (params: {
    message: string;
    type: ToastTag;
    operation?: string;
  }) => { dedupeKey: string; count: number };
  /**
   * Drop one dedupe entry after its toast is dismissed or times out,
   * so the next identical error starts again at count 1.
   * Called by ToastDedupeCleaner; react-hot-toast has no onClose option.
   */
  clearToast: (dedupeKey: string) => void;
  /** Reset toast queue state (intended for unit tests). */
  resetToastQueue: () => void;
}

type UseNotificationStore = {
  (): NotificationStore;
  <T>(selector: (state: NotificationStore) => T): T;
  getState: () => NotificationStore;
  setState: (
    partial:
      | NotificationStore
      | Partial<NotificationStore>
      | ((state: NotificationStore) => NotificationStore | Partial<NotificationStore>),
    replace?: boolean
  ) => void;
  subscribe: (
    listener: (state: NotificationStore, prevState: NotificationStore) => void
  ) => () => void;
  getInitialState: () => NotificationStore;
};

export const useNotificationStore = create<NotificationStore>((set) => ({
  unreadCount: 0,
  chatActivityCount: 0,
  lastRefresh: Date.now(),
  connectionStatus: "disconnected",
  setUnreadCount: (count) => set({ unreadCount: count }),
  setChatActivityCount: (count) => set({ chatActivityCount: count }),
  triggerRefresh: () => {
    set({ lastRefresh: Date.now() });
  },
  setConnectionStatus: (connectionStatus) => set({ connectionStatus }),

  toastQueue: {},
  incrementToast: ({ message, type, operation }) => {
    const op = (operation ?? "").trim() || undefined;
    const dedupeKey = computeToastDedupeKey(message, type, op);
    const normalized = normalizeToastMessage(message);
    let nextCount = 1;

    set((state) => {
      const existing = state.toastQueue[dedupeKey];
      nextCount = (existing?.count ?? 0) + 1;
      return {
        toastQueue: {
          ...state.toastQueue,
          [dedupeKey]: {
            dedupeKey,
            message: normalized,
            type,
            operation: op,
            count: nextCount,
          },
        },
      };
    });

    return { dedupeKey, count: nextCount };
  },
  clearToast: (dedupeKey) =>
    set((state) => {
      if (!state.toastQueue[dedupeKey]) return state;
      const { [dedupeKey]: _removed, ...toastQueue } = state.toastQueue;
      return { toastQueue };
    }),
  resetToastQueue: () =>
    set({
      toastQueue: {},
    }),
})) as unknown as UseNotificationStore; 
