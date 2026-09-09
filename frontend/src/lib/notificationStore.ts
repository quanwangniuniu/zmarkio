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

export function computeToastDedupeKey(message: string, type: ToastTag): string {
  const normalized = normalizeToastMessage(message);
  const hash = fnv1a32(normalized);
  return `${type}:${hash.toString(16)}`;
}

interface NotificationStore {
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
   * Increment count for the dedupeKey derived from message+type.
   * Returns the effective dedupeKey and updated count.
   */
  incrementToast: (params: { message: string; type: ToastTag }) => { dedupeKey: string; count: number };
  /** Reset toast queue state (intended for unit tests). */
  resetToastQueue: () => void;
}

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
  incrementToast: ({ message, type }) => {
    const dedupeKey = computeToastDedupeKey(message, type);
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
            count: nextCount,
          },
        },
      };
    });

    return { dedupeKey, count: nextCount };
  },
  resetToastQueue: () =>
    set({
      toastQueue: {},
    }),
}));
