import api, { SYNC_TIMEOUT_MS } from "../api";

export interface GoogleCalendarStatus {
  connected: boolean;
  google_email?: string | null;
  needs_reconnect?: boolean;
  last_import_at?: string | null;
  last_export_at?: string | null;
  last_error_message?: string | null;
}

export const googleCalendarApi = {
  getStatus: async (): Promise<GoogleCalendarStatus> => {
    const response = await api.get("/api/google-calendar/status/");
    return response.data;
  },

  connect: async (): Promise<{ auth_url: string; state: string }> => {
    const response = await api.get("/api/google-calendar/connect/");
    return response.data;
  },

  disconnect: async (): Promise<{ success: boolean }> => {
    const response = await api.post("/api/google-calendar/disconnect/");
    return response.data;
  },

  syncNow: async (): Promise<{
    success: boolean;
    last_import_at?: string | null;
    last_export_at?: string | null;
  }> => {
    const response = await api.post("/api/google-calendar/sync/", null, {
      timeout: SYNC_TIMEOUT_MS,
    });
    return response.data;
  },
};
