import { create } from 'zustand';

export type SheetWsState = 'connecting' | 'connected' | 'reconnecting' | 'closed' | 'idle';

export type SheetPresenceCursor = {
  row: number | null;
  col: number | null;
  startRow: number | null;
  endRow: number | null;
  startCol: number | null;
  endCol: number | null;
  isActive: boolean;
};

export type SheetPresenceUser = {
  userId: number;
  username: string;
  clientId: string;
  color: string;
  cursor: SheetPresenceCursor | null;
};

function presenceKey(userId: number, clientId?: string | null): string {
  return `${userId}:${clientId?.trim() || 'default'}`;
}

function hashColorForUser(userId: number, key: string): string {
  let hash = userId >>> 0;
  for (let i = 0; i < key.length; i += 1) {
    hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  }
  const hue = hash % 360;
  const sat = 48 + (hash % 14);
  const light = 44 + (hash % 10);
  const h = hue / 360;
  const s = sat / 100;
  const l = light / 100;
  const hue2rgb = (p: number, q: number, t: number) => {
    let tt = t;
    if (tt < 0) tt += 1;
    if (tt > 1) tt -= 1;
    if (tt < 1 / 6) return p + (q - p) * 6 * tt;
    if (tt < 1 / 2) return q;
    if (tt < 2 / 3) return p + (q - p) * (2 / 3 - tt) * 6;
    return p;
  };
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const r = Math.round(hue2rgb(p, q, h + 1 / 3) * 255);
  const g = Math.round(hue2rgb(p, q, h) * 255);
  const b = Math.round(hue2rgb(p, q, h - 1 / 3) * 255);
  const to = (n: number) => n.toString(16).padStart(2, '0');
  return `#${to(r)}${to(g)}${to(b)}`;
}

type SheetSocketStore = {
  sheetId: number | null;
  wsState: SheetWsState;
  closeCode: number | null;
  usersByKey: Record<string, SheetPresenceUser>;
  setSheetId: (sheetId: number | null) => void;
  setWsState: (state: SheetWsState) => void;
  setCloseCode: (code: number | null) => void;
  applySnapshot: (
    users: Array<{ user_id: number; username?: string; client_id?: string | null }>
  ) => void;
  applyJoin: (user: { user_id: number; username?: string; client_id?: string | null }) => void;
  applyLeave: (user: { user_id: number; client_id?: string | null }) => void;
  applyCursor: (event: {
    user_id: number;
    username?: string;
    client_id?: string | null;
    row?: number | null;
    col?: number | null;
    start_row?: number | null;
    end_row?: number | null;
    start_col?: number | null;
    end_col?: number | null;
    is_active?: boolean;
  }) => void;
  reset: () => void;
};

export const useSheetSocketStore = create<SheetSocketStore>((set) => ({
  sheetId: null,
  wsState: 'idle',
  closeCode: null,
  usersByKey: {},

  setSheetId: (sheetId) => set({ sheetId }),
  setWsState: (wsState) => set({ wsState }),
  setCloseCode: (closeCode) => set({ closeCode }),

  applySnapshot: (users) =>
    set((state) => {
      const next: Record<string, SheetPresenceUser> = {};
      for (const u of users) {
        const key = presenceKey(u.user_id, u.client_id);
        const existing = state.usersByKey[key];
        next[key] = {
          userId: u.user_id,
          username: u.username?.trim() || `User ${u.user_id}`,
          clientId: u.client_id?.trim() || 'default',
          color: existing?.color || hashColorForUser(u.user_id, key),
          // Mid-session authoritative snapshots are also emitted after stale
          // channel pruning. Preserve live cursor state for identities that
          // remain present while removing identities absent from the snapshot.
          cursor: existing?.cursor ?? null,
        };
      }
      return { usersByKey: next };
    }),

  applyJoin: (user) =>
    set((state) => {
      const key = presenceKey(user.user_id, user.client_id);
      if (state.usersByKey[key]) return state;
      return {
        usersByKey: {
          ...state.usersByKey,
          [key]: {
            userId: user.user_id,
            username: user.username?.trim() || `User ${user.user_id}`,
            clientId: user.client_id?.trim() || 'default',
            color: hashColorForUser(user.user_id, key),
            cursor: null,
          },
        },
      };
    }),

  applyLeave: (user) =>
    set((state) => {
      const key = presenceKey(user.user_id, user.client_id);
      if (!state.usersByKey[key]) {
        // Leave without client_id: drop all entries for that user
        if (!user.client_id) {
          const next = { ...state.usersByKey };
          Object.keys(next).forEach((k) => {
            if (next[k].userId === user.user_id) delete next[k];
          });
          return { usersByKey: next };
        }
        return state;
      }
      const next = { ...state.usersByKey };
      delete next[key];
      return { usersByKey: next };
    }),

  applyCursor: (event) =>
    set((state) => {
      const key = presenceKey(event.user_id, event.client_id);
      const existing = state.usersByKey[key];
      const cursor: SheetPresenceCursor | null =
        event.is_active === false
          ? null
          : {
              row: event.row ?? null,
              col: event.col ?? null,
              startRow: event.start_row ?? null,
              endRow: event.end_row ?? null,
              startCol: event.start_col ?? null,
              endCol: event.end_col ?? null,
              isActive: true,
            };
      return {
        usersByKey: {
          ...state.usersByKey,
          [key]: {
            userId: event.user_id,
            username: event.username?.trim() || existing?.username || `User ${event.user_id}`,
            clientId: event.client_id?.trim() || existing?.clientId || 'default',
            color: existing?.color || hashColorForUser(event.user_id, key),
            cursor,
          },
        },
      };
    }),

  reset: () =>
    set({
      sheetId: null,
      wsState: 'idle',
      closeCode: null,
      usersByKey: {},
    }),
}));

export function selectRemotePresenceUsers(
  usersByKey: Record<string, SheetPresenceUser>,
  localUserId: number | null | undefined,
  localClientId: string
): SheetPresenceUser[] {
  return Object.values(usersByKey).filter((u) => {
    if (localUserId == null) return true;
    if (u.userId !== localUserId) return true;
    // Same user can have multiple tabs; hide only this tab's client.
    return u.clientId !== localClientId;
  });
}
