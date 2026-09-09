import { useNotificationStore, computeToastDedupeKey, normalizeToastMessage, type ToastTag } from "@/lib/notificationStore";

describe("notificationStore — toast dedupe semantics", () => {
  beforeEach(() => {
    useNotificationStore.getState().resetToastQueue();
  });

  it("normalizes whitespace for dedupe stability", () => {
    expect(normalizeToastMessage("  Network   error ")).toBe("Network error");
  });

  it("computeToastDedupeKey matches for same message+type with whitespace differences", () => {
    const type: ToastTag = "error";
    const key1 = computeToastDedupeKey("Network error", type);
    const key2 = computeToastDedupeKey("  Network   error  ", type);
    expect(key1).toBe(key2);
  });

  it("incrementToast accumulates count for the same dedupeKey", () => {
    const type: ToastTag = "error";
    const message = "Network error";
    const { dedupeKey } = useNotificationStore.getState().incrementToast({ message, type });

    useNotificationStore.getState().incrementToast({ message, type });
    useNotificationStore.getState().incrementToast({ message, type });

    const item = useNotificationStore.getState().toastQueue[dedupeKey];
    expect(item).toBeTruthy();
    expect(item.count).toBe(3);
    expect(item.type).toBe("error");
    expect(item.message).toBe("Network error");
  });

  it("different messages do not merge", () => {
    const type: ToastTag = "error";

    const keyA = computeToastDedupeKey("Network error", type);
    const keyB = computeToastDedupeKey("Timeout error", type);

    useNotificationStore.getState().incrementToast({ message: "Network error", type });
    useNotificationStore.getState().incrementToast({ message: "Timeout error", type });

    expect(useNotificationStore.getState().toastQueue[keyA].count).toBe(1);
    expect(useNotificationStore.getState().toastQueue[keyB].count).toBe(1);
  });

  it("same message but different type does not merge", () => {
    const message = "Network error";
    const keyError = computeToastDedupeKey(message, "error");
    const keySuccess = computeToastDedupeKey(message, "success");

    useNotificationStore.getState().incrementToast({ message, type: "error" });
    useNotificationStore.getState().incrementToast({ message, type: "success" });

    expect(useNotificationStore.getState().toastQueue[keyError].count).toBe(1);
    expect(useNotificationStore.getState().toastQueue[keySuccess].count).toBe(1);
  });
});

