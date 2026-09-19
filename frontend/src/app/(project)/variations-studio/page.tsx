"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Id } from "@/types/common";
import { useSearchParams } from "next/navigation";
import toast from "react-hot-toast";
import { AlertTriangle, ArrowLeft, Loader2, Pencil, } from "lucide-react";

import DashboardLayout from "@/components/dashboard/DashboardLayout";
import { ProtectedRoute } from "@/components/auth/ProtectedRoute";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  bulkDeleteVariations,
  bulkReviewVariations,
  generateVariation,
  getLatestVariationBatch,
  listAiVariations,
  reviewVariationBatch,
  updateVariation,
} from "@/lib/api/adCopyVariationApi";
import { facebookApi, type MetaCreativeDetail } from "@/lib/api/facebookApi";
import { useProjectStore } from "@/lib/projectStore";
import { useBuildUrl } from "@/lib/buildUrl";
import type {
  AdCopyVariation,
  AdCopyVariationCopy,
  AdCopyVariationSourceMode,
  AdCopyVariationStatus,
  CopyViolation,
} from "@/types/adCopyVariation";

const SECTION_LABEL =
  "text-[11px] font-semibold uppercase tracking-wider text-gray-400 mb-2";

const TEXTAREA_BASE =
  "mt-2 w-full resize-none bg-transparent text-[14px] text-gray-700 placeholder:text-gray-400 outline-none border-0 leading-5 py-1 focus:ring-0";

const TAB_TRIGGER_BASE =
  "px-1 pb-2 rounded-none bg-transparent text-[14px] font-medium text-gray-700 border-b-2 border-transparent shadow-none transition hover:text-gray-900 focus-visible:outline-none data-[state=active]:bg-transparent data-[state=active]:text-gray-900 data-[state=active]:border-[#3CCED7] data-[state=active]:shadow-none disabled:opacity-100";

const STUDIO_TAB_BUTTON_BASE =
  "inline-flex h-9 min-w-[116px] items-center justify-center rounded-lg px-4 text-[13px] font-semibold ring-1 transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#3CCED7]/40";

const STUDIO_TAB_ACTIVE =
  "bg-gradient-to-br from-[#3CCED7] to-[#A6E661] text-white shadow-sm ring-transparent";

const STUDIO_TAB_INACTIVE =
  "bg-white text-gray-600 ring-gray-200 hover:bg-[#3CCED7]/10 hover:text-[#1a9ba3] hover:ring-[#3CCED7]/40";

const COUNT_PILL_PRESETS = [5, 10, 20] as const;
const MAX_COUNT = 50;

const EMPTY_COPY: AdCopyVariationCopy = {
  hook: "",
  headline: "",
  description: "",
  cta: "",
};

interface CardState {
  key: string;
  id: number;
  slug?: string;
  batch_id: string;
  source_mode: AdCopyVariationSourceMode;
  source_ref: string;
  creative_id: number | null;
  copy: AdCopyVariationCopy;
  draft: AdCopyVariationCopy;
  status: AdCopyVariationStatus;
  selected: boolean;
  editing: boolean;
  saving: boolean;
}

function extractErrorMessage(err: unknown, fallback: string): string {
  if (typeof err === "object" && err !== null) {
    const maybeAxios = err as {
      response?: { data?: { detail?: unknown; error?: unknown } };
      message?: unknown;
    };
    const detail = maybeAxios.response?.data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
    const apiError = maybeAxios.response?.data?.error;
    if (typeof apiError === "string" && apiError.trim()) return apiError;
    if (typeof maybeAxios.message === "string" && maybeAxios.message.trim()) {
      return maybeAxios.message;
    }
  }
  return fallback;
}

function copyFromVariation(row: AdCopyVariation): AdCopyVariationCopy {
  return {
    hook: row.hook,
    headline: row.headline,
    description: row.description,
    cta: row.cta,
  };
}

function cardFromVariation(row: AdCopyVariation): CardState {
  const copy = copyFromVariation(row);
  return {
    key: `draft-${row.id}`,
    id: row.id,
    slug: row.slug,
    batch_id: row.batch_id || "",
    source_mode: row.source_mode,
    source_ref: row.source_ref || "",
    creative_id: row.creative,
    copy,
    draft: { ...copy },
    validationWarnings: row.validation_warnings ?? [],
    status: row.status,
    selected: false,
    editing: false,
    saving: false,
  };
}

export default function VariationsStudioPage() {
  return (
    <ProtectedRoute>
      <DashboardLayout>
        <VariationsStudioContent />
      </DashboardLayout>
    </ProtectedRoute>
  );
}

function VariationsStudioContent() {
  const searchParams = useSearchParams();
  const buildUrl = useBuildUrl();
  const activeProject = useProjectStore((state) => state.activeProject);
  const hasProjectStoreHydrated = useProjectStore((state) => state.hasHydrated);
  const initialCreativeIdParam = searchParams.get("creative");
  const initialCreativeId = initialCreativeIdParam || null;
  const initialMode: AdCopyVariationSourceMode =
    initialCreativeId ? "existing" : "custom";

  const [studioTab, setStudioTab] = useState<"generate" | "drafts">("generate");
  const [mode, setMode] = useState<AdCopyVariationSourceMode>(initialMode);
  const [creativeMeta, setCreativeMeta] = useState<MetaCreativeDetail | null>(null);
  const [creativeMetaLoading, setCreativeMetaLoading] = useState<boolean>(false);
  const [customBase, setCustomBase] = useState<AdCopyVariationCopy>(EMPTY_COPY);
  const [externalUrl, setExternalUrl] = useState<string>("");
  const [instruction, setInstruction] = useState<string>("");
  const [count, setCount] = useState<number>(10);
  const [isGenerating, setIsGenerating] = useState<boolean>(false);
  const [lastBatchSummary, setLastBatchSummary] = useState<{
    requested: number;
    succeeded: number;
    failed: number;
    error?: string;
  } | null>(null);
  const [cards, setCards] = useState<CardState[]>([]);
  const [activeBatchId, setActiveBatchId] = useState<string>("");
  const [bulkReviewing, setBulkReviewing] = useState<boolean>(false);

  useEffect(() => {
    if (!initialCreativeId) return;
    let active = true;
    setCreativeMetaLoading(true);
    facebookApi
      .getMetaCreativeDetail(initialCreativeId, 28)
      .then((detail) => {
        if (active) setCreativeMeta(detail);
      })
      .catch((err) => {
        if (!active) return;
        toast.error(extractErrorMessage(err, "Failed to load creative."));
      })
      .finally(() => {
        if (active) setCreativeMetaLoading(false);
      });
    return () => {
      active = false;
    };
  }, [initialCreativeId]);

  const sourceHook = creativeMeta
    ? (creativeMeta.body || "").split("\n", 1)[0]
    : "";

  const externalUrlValid = /^https?:\/\//.test(externalUrl.trim());

  const sourceInputValid =
    (mode === "existing" && !!creativeMeta) ||
    mode === "custom" ||
    (mode === "external_url" && externalUrlValid);

  const generateLabel = isGenerating
    ? `Generating ${count} variations…`
    : `Generate ${count} variation${count === 1 ? "" : "s"}`;

  const moreLabel = isGenerating
    ? `Generating ${count} more…`
    : `Generate ${count} more`;

  const currentBatchId = activeBatchId || cards[0]?.batch_id || "";
  const selectedDraftCards = cards.filter(
    (c) => c.selected && c.status === "draft" && c.batch_id === currentBatchId
  );
  const draftCardCount = cards.filter(
    (c) => c.status === "draft" && c.batch_id === currentBatchId
  ).length;
  const projectId = activeProject?.id ?? null;

  const handleCountInput = (value: string) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return;
    const clamped = Math.max(1, Math.min(MAX_COUNT, Math.floor(n)));
    setCount(clamped);
  };

  const buildSourcePayload = () => {
    if (mode === "existing") {
      return {
        creative_id: creativeMeta?.id,
        creative_meta: creativeMeta,
      };
    }
    if (mode === "external_url") {
      return { url: externalUrl.trim() };
    }
    return { base_copy: customBase };
  };

  const runGenerate = async (append: boolean) => {
    if (!projectId) {
      toast.error("Select an active project before generating variations.");
      return;
    }
    if (!sourceInputValid) {
      toast.error("Source input is incomplete.");
      return;
    }
    setIsGenerating(true);
    try {
      const payload = buildSourcePayload();
      const res = await generateVariation({
        project_id: projectId,
        source_mode: mode,
        count,
        instruction,
        creative_id: payload.creative_id,
        base_copy: mode === "custom" ? customBase : undefined,
        url: mode === "external_url" ? externalUrl.trim() : undefined,
      });
      setLastBatchSummary({
        requested: res.count_requested,
        succeeded: res.count_succeeded,
        failed: res.count_failed,
        error: res.error,
      });
      if (res.error) {
        toast.error(res.error);
      }
      setActiveBatchId(res.batch_id);
      const newCards: CardState[] = res.results.map(cardFromVariation);
      setCards((prev) =>
        append ? [...prev.map((c) => ({ ...c, selected: false })), ...newCards] : newCards
      );
    } catch (err) {
      toast.error(extractErrorMessage(err, "Failed to generate variations."));
    } finally {
      setIsGenerating(false);
    }
  };

  const updateCard = (key: string, patch: Partial<CardState>) => {
    setCards((prev) =>
      prev.map((c) => (c.key === key ? { ...c, ...patch } : c))
    );
  };

  const startEdit = (key: string) => {
    setCards((prev) =>
      prev.map((c) =>
        c.key === key ? { ...c, editing: true, draft: c.copy } : c
      )
    );
  };

  const cancelEdit = (key: string) => {
    setCards((prev) =>
      prev.map((c) => (c.key === key ? { ...c, editing: false, draft: c.copy } : c))
    );
  };

  const saveCard = async (key: string) => {
    const card = cards.find((c) => c.key === key);
    if (!card) return;
    updateCard(key, { saving: true });
    try {
      const updated = await updateVariation(card.slug ?? card.id, card.draft);
      const copy = copyFromVariation(updated);
      setCards((prev) =>
        prev.map((c) =>
          c.key === key
            ? {
                ...c,
                copy,
                draft: { ...copy },
                validationWarnings: updated.validation_warnings ?? [],
                status: updated.status,
                editing: false,
                saving: false,
              }
            : c
        )
      );
      toast.success("Draft updated");
    } catch (err) {
      updateCard(key, { saving: false });
      toast.error(extractErrorMessage(err, "Failed to update draft."));
    }
  };

  const toggleSelected = (key: string) => {
    setCards((prev) =>
      prev.map((c) =>
        c.key === key && c.status === "draft" && c.batch_id === currentBatchId
          ? { ...c, selected: !c.selected }
          : c
      )
    );
  };

  const handleBulkReview = async () => {
    if (!projectId || !currentBatchId || selectedDraftCards.length === 0) return;
    setBulkReviewing(true);
    try {
      const result = await reviewVariationBatch({
        project_id: projectId,
        batch_id: currentBatchId,
        selected_ids: selectedDraftCards.map((c) => c.id),
      });
      const survivingCurrentBatch = result.results.map(cardFromVariation);
      setCards((prev) =>
        prev
          .filter((c) => c.batch_id !== currentBatchId)
          .concat(survivingCurrentBatch)
      );
      toast.success(
        `Reviewed ${result.reviewed_count} draft${result.reviewed_count === 1 ? "" : "s"}.`
      );
      setStudioTab("drafts");
    } catch (err) {
      toast.error(extractErrorMessage(err, "Failed to review selected drafts."));
    } finally {
      setBulkReviewing(false);
    }
  };

  const handleClearAll = () => {
    setCards([]);
    setActiveBatchId("");
    setLastBatchSummary(null);
  };

  return (
    <div className="min-h-[calc(100vh-3rem)] bg-gray-50">
      <div className="mx-auto flex min-h-[calc(100vh-3rem)] max-w-[1440px] flex-col px-6 py-4">
        {initialCreativeId !== null && (
          <Link
            href={buildUrl("/meta-ads?tab=creatives")}
            className="mb-4 inline-flex w-fit items-center gap-1 text-[14px] text-gray-500 transition-colors hover:text-gray-900"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Back to creatives
          </Link>
        )}
        <header className="shrink-0">
          <h1 className="text-[24px] font-semibold text-gray-900">
            Variations Studio
          </h1>
          <p className="mt-1 text-[14px] text-gray-500">
            Generate AI ad copy variations in batches of 1 to 50.
          </p>
        </header>

        <div className="mt-5 flex h-11 shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={() => setStudioTab("generate")}
            className={
              studioTab === "generate"
                ? `${STUDIO_TAB_BUTTON_BASE} ${STUDIO_TAB_ACTIVE}`
                : `${STUDIO_TAB_BUTTON_BASE} ${STUDIO_TAB_INACTIVE}`
            }
            aria-current={studioTab === "generate" ? "page" : undefined}
          >
            Generate
          </button>
          <button
            type="button"
            onClick={() => setStudioTab("drafts")}
            className={
              studioTab === "drafts"
                ? `${STUDIO_TAB_BUTTON_BASE} ${STUDIO_TAB_ACTIVE}`
                : `${STUDIO_TAB_BUTTON_BASE} ${STUDIO_TAB_INACTIVE}`
            }
            aria-current={studioTab === "drafts" ? "page" : undefined}
          >
            AI Drafts
          </button>
        </div>

        {studioTab === "generate" && (
          <div className="mt-5 min-h-[720px] space-y-5">

        <section className="rounded-xl border-[0.5px] border-gray-200 bg-white p-5">
          <Tabs
            value={mode}
            onValueChange={(value) => setMode(value as AdCopyVariationSourceMode)}
          >
            <TabsList className="h-auto w-full justify-start gap-4 rounded-none bg-transparent p-0 border-b border-gray-200">
              <TabsTrigger value="existing" className={TAB_TRIGGER_BASE}>
                Existing creative
              </TabsTrigger>
              <TabsTrigger value="custom" className={TAB_TRIGGER_BASE}>
                Custom content
              </TabsTrigger>
              <TabsTrigger value="external_url" className={TAB_TRIGGER_BASE}>
                External URL
              </TabsTrigger>
            </TabsList>

            <TabsContent value="existing" className="pt-4 mt-0">
              {creativeMetaLoading ? (
                <div className="text-[12px] text-gray-500">Loading creative…</div>
              ) : creativeMeta ? (
                <div>
                  <div className={SECTION_LABEL}>Source preview</div>
                  <dl className="space-y-1.5 text-[14px] leading-5">
                    <div className="grid grid-cols-[88px_1fr] gap-2">
                      <dt className="text-gray-500">Hook</dt>
                      <dd className="text-gray-900">{sourceHook || "—"}</dd>
                    </div>
                    <div className="grid grid-cols-[88px_1fr] gap-2">
                      <dt className="text-gray-500">Headline</dt>
                      <dd className="text-gray-900">{creativeMeta.title || "—"}</dd>
                    </div>
                    <div className="grid grid-cols-[88px_1fr] gap-2">
                      <dt className="text-gray-500">Description</dt>
                      <dd className="whitespace-pre-wrap text-gray-900">
                        {creativeMeta.body || "—"}
                      </dd>
                    </div>
                    <div className="grid grid-cols-[88px_1fr] gap-2">
                      <dt className="text-gray-500">CTA</dt>
                      <dd className="text-gray-900">
                        {creativeMeta.call_to_action_type || "—"}
                      </dd>
                    </div>
                  </dl>
                </div>
              ) : (
                <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] text-amber-800">
                  Select a creative on the Meta Ads → Creatives tab and click
                  &ldquo;Generate variations&rdquo; to deep-link here. Or pick
                  Custom content / External URL above.
                </p>
              )}
            </TabsContent>

            <TabsContent value="custom" className="pt-4 mt-0">
              <div className={SECTION_LABEL}>Custom base copy</div>
              <div className="space-y-3">
                <div className="grid grid-cols-[88px_1fr] items-start gap-2">
                  <span className="pt-1 text-[14px] text-gray-500">Hook</span>
                  <textarea
                    value={customBase.hook}
                    onChange={(e) =>
                      setCustomBase({ ...customBase, hook: e.target.value })
                    }
                    placeholder="First line that catches the eye"
                    rows={2}
                    className={TEXTAREA_BASE}
                  />
                </div>
                <div className="grid grid-cols-[88px_1fr] items-start gap-2">
                  <span className="pt-1 text-[14px] text-gray-500">Headline</span>
                  <textarea
                    value={customBase.headline}
                    onChange={(e) =>
                      setCustomBase({ ...customBase, headline: e.target.value })
                    }
                    placeholder="Short hero headline"
                    rows={2}
                    className={TEXTAREA_BASE}
                  />
                </div>
                <div className="grid grid-cols-[88px_1fr] items-start gap-2">
                  <span className="pt-1 text-[14px] text-gray-500">
                    Description
                  </span>
                  <textarea
                    value={customBase.description}
                    onChange={(e) =>
                      setCustomBase({ ...customBase, description: e.target.value })
                    }
                    placeholder="Body copy"
                    rows={4}
                    className={TEXTAREA_BASE}
                  />
                </div>
                <div className="grid grid-cols-[88px_1fr] items-start gap-2">
                  <span className="pt-1 text-[14px] text-gray-500">CTA</span>
                  <textarea
                    value={customBase.cta}
                    onChange={(e) =>
                      setCustomBase({ ...customBase, cta: e.target.value })
                    }
                    placeholder="e.g. SHOP_NOW"
                    rows={1}
                    className={TEXTAREA_BASE}
                  />
                </div>
              </div>
            </TabsContent>

            <TabsContent value="external_url" className="pt-4 mt-0">
              <div className={SECTION_LABEL}>Ad URL</div>
              <input
                type="url"
                value={externalUrl}
                onChange={(e) => setExternalUrl(e.target.value)}
                placeholder="https://www.facebook.com/ads/library/?id=..."
                className="w-full bg-transparent text-[14px] text-gray-700 placeholder:text-gray-400 outline-none border-0 border-b border-gray-200 focus:border-[#3CCED7] transition py-1"
              />
              <p className="mt-1 text-[11px] text-gray-400">
                Paste a public Meta Ad Library URL. The page is fetched via a
                managed headless browser, then ad copy is extracted and rewritten.
              </p>
            </TabsContent>
          </Tabs>
        </section>

        <section className="rounded-xl border-[0.5px] border-gray-200 bg-white p-5">
          <div className={SECTION_LABEL}>
            Instruction
            <span className="ml-1.5 text-gray-300 normal-case tracking-normal font-normal">
              optional
            </span>
          </div>
          <textarea
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder="Focus the rewrite, e.g. 'rewrite only the Hook' or 'make it more urgent'"
            rows={3}
            className={TEXTAREA_BASE}
          />
        </section>

        <section className="rounded-xl border-[0.5px] border-gray-200 bg-white p-5">
          <div className={SECTION_LABEL}>How many variations</div>
          <div className="flex flex-wrap items-center gap-3">
            <div className="flex flex-wrap gap-1.5">
              {COUNT_PILL_PRESETS.map((preset) => {
                const active = count === preset;
                return (
                  <button
                    key={preset}
                    type="button"
                    onClick={() => setCount(preset)}
                    className={
                      active
                        ? "px-3 py-1 rounded-full text-[12px] font-medium border border-transparent shadow-sm bg-gradient-to-br from-[#3CCED7] to-[#A6E661] text-white"
                        : "px-3 py-1 rounded-full text-[12px] font-medium border border-transparent bg-gray-100 text-gray-700 hover:border-[#3CCED7]/40 hover:bg-white transition"
                    }
                  >
                    {preset}
                  </button>
                );
              })}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[12px] text-gray-400">or</span>
              <input
                type="number"
                min={1}
                max={MAX_COUNT}
                step={1}
                value={count}
                onChange={(e) => handleCountInput(e.target.value)}
                aria-label="Custom count"
                className="h-8 w-20 bg-transparent text-[14px] text-gray-900 placeholder:text-gray-300 outline-none border-0 border-b border-gray-200 focus:border-[#3CCED7] transition py-1 text-center"
              />
              <span className="text-[11px] text-gray-400">1-50</span>
            </div>
            <button
              type="button"
              onClick={() => runGenerate(false)}
              disabled={!projectId || !sourceInputValid || isGenerating || !hasProjectStoreHydrated}
              className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-br from-[#3CCED7] to-[#A6E661] px-4 py-1.5 text-[14px] font-semibold text-white shadow-sm transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isGenerating && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {generateLabel}
            </button>
          </div>
        </section>

        {(isGenerating && cards.length === 0) && (
          <section className="rounded-xl border-[0.5px] border-gray-200 bg-white p-8 text-center">
            <Loader2 className="mx-auto h-5 w-5 animate-spin text-[#3CCED7]" />
            <p className="mt-2 text-[14px] text-gray-700">
              Generating {count} variations…
            </p>
            <p className="mt-1 text-[11px] text-gray-400">
              This typically takes 5-15 seconds depending on count.
            </p>
          </section>
        )}

        {lastBatchSummary?.error && (
          <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[13px] text-amber-900">
            {lastBatchSummary.error}
          </div>
        )}

        {lastBatchSummary && lastBatchSummary.failed > 0 && !lastBatchSummary.error && (
          <div className="rounded-md border border-yellow-200 bg-yellow-50 px-3 py-2 text-[13px] text-yellow-800">
            {lastBatchSummary.succeeded} of {lastBatchSummary.requested}
            {" succeeded. "}
            {lastBatchSummary.failed} failed. Click &ldquo;Generate{" "}
            {count} more&rdquo; to retry.
          </div>
        )}

        {cards.length > 0 && (
          <section className="grid grid-cols-1 gap-5 md:grid-cols-2 lg:grid-cols-3">
            {cards.map((card) => (
              <ResultCard
                key={card.key}
                card={card}
                currentBatchId={currentBatchId}
                onToggleSelect={() => toggleSelected(card.key)}
                onStartEdit={() => startEdit(card.key)}
                onCancelEdit={() => cancelEdit(card.key)}
                onChangeDraft={(draft) => updateCard(card.key, { draft })}
                onSave={() => saveCard(card.key)}
              />
            ))}
          </section>
        )}

        {cards.length > 0 && (
          <section className="flex flex-wrap items-center justify-between gap-3 rounded-xl border-[0.5px] border-gray-200 bg-white p-5">
            <div className="text-[12px] text-gray-500">
              {cards.length} card{cards.length === 1 ? "" : "s"} ·{" "}
              {draftCardCount} draft{draftCardCount === 1 ? "" : "s"} in current batch ·{" "}
              {selectedDraftCards.length} selected
              <div className="mt-1 text-[11px] text-gray-400">
                Review selected drafts from this batch.
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={handleClearAll}
                className="rounded-lg px-3 py-1.5 text-[14px] font-medium text-gray-600 transition hover:bg-gray-100 hover:text-gray-900"
              >
                Clear all
              </button>
              <button
              type="button"
              onClick={() => runGenerate(true)}
                disabled={!projectId || !sourceInputValid || isGenerating || !hasProjectStoreHydrated}
                className="inline-flex items-center gap-1.5 rounded-lg bg-white px-3 py-1.5 text-[14px] font-medium text-gray-700 ring-1 ring-gray-200 transition hover:ring-gray-300 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {isGenerating && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {moreLabel}
              </button>
              <button
                type="button"
                onClick={handleBulkReview}
                disabled={selectedDraftCards.length === 0 || bulkReviewing}
                className="inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-br from-[#3CCED7] to-[#A6E661] px-4 py-1.5 text-[14px] font-semibold text-white shadow-sm transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {bulkReviewing && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {bulkReviewing
                  ? "Reviewing…"
                  : `Review selected (${selectedDraftCards.length})`}
              </button>
            </div>
          </section>
        )}
          </div>
        )}

        {studioTab === "drafts" && (
          <div className="mt-5 min-h-[720px]">
            <AiDraftsTab projectId={projectId} initialCreativeId={initialCreativeId} />
          </div>
        )}
      </div>
    </div>
  );
}

interface ResultCardProps {
  card: CardState;
  currentBatchId: string;
  onToggleSelect: () => void;
  onStartEdit: () => void;
  onCancelEdit: () => void;
  onChangeDraft: (draft: AdCopyVariationCopy) => void;
  onSave: () => void;
}

function VariationStatusPill({ status }: { status: AdCopyVariationStatus }) {
  const className =
    status === "reviewed"
      ? "bg-emerald-100 text-emerald-700"
      : "bg-sky-100 text-sky-700";
  const label = status === "reviewed" ? "Reviewed" : "Draft";
  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${className}`}>
      {label}
    </span>
  );
}

function ValidationWarnings({
  warnings,
}: {
  warnings: CopyViolation[];
}) {
  if (warnings.length === 0) return null;

  return (
    <div
      role="status"
      className="mb-3 flex gap-2 rounded-md border border-amber-300 bg-amber-50 p-3"
    >
      <AlertTriangle
        className="mt-0.5 h-4 w-4 shrink-0 text-amber-700"
        aria-hidden="true"
      />

      <div>
        <p className="text-[13px] font-semibold text-amber-900">
          Length review needed
        </p>

        <ul className="mt-1 space-y-1 text-[12px] text-amber-800">
          {warnings.map((warning) => {
            const field =
              warning.field.charAt(0).toUpperCase()
              + warning.field.slice(1);
            const unit =
              warning.rule === "max_words"
                ? "words"
                : "characters";

            return (
              <li key={`${warning.field}-${warning.rule}`}>
                {field}: {warning.actual} {unit}; maximum{" "}
                {warning.limit}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

function ResultCard({
  card,
  currentBatchId,
  onToggleSelect,
  onStartEdit,
  onCancelEdit,
  onChangeDraft,
  onSave,
}: ResultCardProps) {
  const sourceBadgeLabel = useMemo(() => {
    switch (card.source_mode) {
      case "existing":
        return "Existing";
      case "custom":
        return "Custom";
      case "external_url":
        return "External URL";
    }
  }, [card.source_mode]);

  const display = card.copy;
  const draft = card.draft;
  const selectable = card.status === "draft" && card.batch_id === currentBatchId;

  return (
    <article
      className={
        card.status === "reviewed"
          ? "rounded-xl border-[0.5px] border-emerald-200 bg-emerald-50/30 p-5"
          : "rounded-xl border-[0.5px] border-gray-200 bg-white p-5"
      }
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <input
          type="checkbox"
          aria-label="Select draft for review"
          checked={card.selected}
          disabled={!selectable}
          onChange={onToggleSelect}
          className="h-4 w-4 rounded accent-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/30 disabled:opacity-40"
        />
        <div className="flex items-center gap-1">
          <VariationStatusPill status={card.status} />
          {!card.editing && (
            <button
              type="button"
              onClick={onStartEdit}
              aria-label="Edit variation"
              title="Edit"
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-gray-500 transition-colors hover:bg-gray-50 hover:text-gray-900"
            >
              <Pencil className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
      </div>

      <ValidationWarnings
        warnings={card.validationWarnings}
      />

      <div className="space-y-3">
        <CardField
          label="Hook"
          editing={card.editing}
          value={card.editing ? draft.hook : display.hook}
          onChange={(v) => onChangeDraft({ ...draft, hook: v })}
          rows={2}
        />
        <CardField
          label="Headline"
          editing={card.editing}
          value={card.editing ? draft.headline : display.headline}
          onChange={(v) => onChangeDraft({ ...draft, headline: v })}
          rows={2}
        />
        <CardField
          label="Description"
          editing={card.editing}
          value={card.editing ? draft.description : display.description}
          onChange={(v) => onChangeDraft({ ...draft, description: v })}
          rows={3}
        />
        <CtaCardField
          editing={card.editing}
          value={card.editing ? draft.cta : display.cta}
          onChange={(v) => onChangeDraft({ ...draft, cta: v })}
        />
      </div>

      <div className="mt-3 flex items-center justify-between">
        <span className="text-[10px] text-gray-400">{sourceBadgeLabel}</span>
        {card.editing && (
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onCancelEdit}
              disabled={card.saving}
              className="rounded-lg px-3 py-1 text-[13px] font-medium text-gray-600 transition hover:bg-gray-100 hover:text-gray-900 disabled:opacity-60"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onSave}
              disabled={card.saving}
              className="inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-br from-[#3CCED7] to-[#A6E661] px-3 py-1 text-[13px] font-semibold text-white shadow-sm transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {card.saving && <Loader2 className="h-3 w-3 animate-spin" />}
              {card.saving ? "Saving…" : "Update"}
            </button>
          </div>
        )}
      </div>
    </article>
  );
}

interface CardFieldProps {
  label: string;
  editing: boolean;
  value: string;
  onChange: (next: string) => void;
  rows: number;
}

function CardField({ label, editing, value, onChange, rows }: CardFieldProps) {
  return (
    <div>
      <div className={SECTION_LABEL}>{label}</div>
      {editing ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={rows}
          className={TEXTAREA_BASE}
        />
      ) : (
        <div className="whitespace-pre-wrap text-[14px] text-gray-700">
          {value || "—"}
        </div>
      )}
    </div>
  );
}

interface CtaCardFieldProps {
  editing: boolean;
  value: string;
  onChange: (next: string) => void;
}

function CtaCardField({ editing, value, onChange }: CtaCardFieldProps) {
  return (
    <div>
      <div className={SECTION_LABEL}>CTA</div>
      {editing ? (
        <textarea
          value={value}
          onChange={(e) => onChange(e.target.value)}
          rows={1}
          className={TEXTAREA_BASE}
        />
      ) : value ? (
        <span className="inline-flex rounded bg-gray-100 px-2 py-0.5 font-mono text-[12px] text-gray-700">
          {value}
        </span>
      ) : (
        <span className="text-[14px] text-gray-400">—</span>
      )}
    </div>
  );
}

type DraftStatusFilter = "all" | AdCopyVariationStatus;

function statusParamForFilter(filter: DraftStatusFilter): string | undefined {
  if (filter === "all") return undefined;
  return filter;
}

function sourceModeLabel(mode: AdCopyVariationSourceMode): string {
  switch (mode) {
    case "existing":
      return "Existing";
    case "custom":
      return "Custom";
    case "external_url":
      return "External URL";
  }
}

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  if (!Number.isFinite(then)) return "";
  const diff = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (diff < 60) return `${diff}s ago`;
  const m = Math.floor(diff / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function AiDraftsTab({
  projectId,
  initialCreativeId,
}: {
  projectId: Id | null;
  initialCreativeId: string | null;
}) {
  const buildUrl = useBuildUrl();
  const [rows, setRows] = useState<AdCopyVariation[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [statusFilter, setStatusFilter] = useState<DraftStatusFilter>("all");
  const [sourceFilter, setSourceFilter] = useState<AdCopyVariationSourceMode | "all">("all");
  const [creativeFilter, setCreativeFilter] = useState<string>(
    initialCreativeId ? String(initialCreativeId) : ""
  );
  const [batchFilter, setBatchFilter] = useState<string>("");
  const [loadingLatestBatch, setLoadingLatestBatch] = useState<boolean>(false);
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [bulkAction, setBulkAction] = useState<"review" | "delete-draft" | "delete-reviewed" | null>(null);
  const [refreshKey, setRefreshKey] = useState<number>(0);
  const [editingRowId, setEditingRowId] = useState<number | null>(null);
  const [editingDraft, setEditingDraft] = useState<AdCopyVariationCopy>(EMPTY_COPY);
  const [savingEditId, setSavingEditId] = useState<number | null>(null);
  const [page, setPage] = useState<number>(1);
  const [total, setTotal] = useState<number>(0);
  const pageSize = 10;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const creativeIdFilter = creativeFilter.trim() ? Number(creativeFilter.trim()) : null;
  const selectedRows = rows.filter((row) => selectedIds.has(row.id));
  const selectedDraftRows = selectedRows.filter((row) => row.status === "draft");
  const selectedReviewedRows = selectedRows.filter((row) => row.status === "reviewed");
  const selectedCount = selectedRows.length;
  const canReviewSelectedDrafts = selectedCount > 0 && selectedCount === selectedDraftRows.length;
  const canDeleteSelectedDrafts = selectedCount > 0 && selectedCount === selectedDraftRows.length;
  const canDeleteSelectedReviewed = selectedCount > 0 && selectedCount === selectedReviewedRows.length;
  const allVisibleSelected = rows.length > 0 && rows.every((row) => selectedIds.has(row.id));

  const refreshList = useCallback(() => {
    setRefreshKey((prev) => prev + 1);
  }, []);

  useEffect(() => {
    if (!projectId) return;
    let active = true;
    setLoading(true);
    listAiVariations({
      project_id: projectId,
      status: statusParamForFilter(statusFilter),
      source_mode: sourceFilter === "all" ? "" : sourceFilter,
      creative: creativeIdFilter !== null && Number.isFinite(creativeIdFilter) ? creativeIdFilter : undefined,
      batch_id: batchFilter || undefined,
      page,
      page_size: pageSize,
    })
      .then((res) => {
        if (!active) return;
        setRows(res.results);
        setTotal(res.total);
        const visibleIds = new Set(res.results.map((row) => row.id));
        setSelectedIds((prev) => new Set([...prev].filter((id) => visibleIds.has(id))));
      })
      .catch((err) => {
        if (!active) return;
        toast.error(extractErrorMessage(err, "Failed to load AI drafts."));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [projectId, statusFilter, sourceFilter, creativeIdFilter, batchFilter, page, refreshKey]);

  const resetToFirstPage = () => setPage(1);

  const toggleRowSelection = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAllVisible = () => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (allVisibleSelected) {
        rows.forEach((row) => next.delete(row.id));
      } else {
        rows.forEach((row) => next.add(row.id));
      }
      return next;
    });
  };

  const applyCreativeFilter = (creativeId: number) => {
    setSourceFilter("existing");
    setCreativeFilter(String(creativeId));
    setPage(1);
  };

  const startRowEdit = (row: AdCopyVariation) => {
    setEditingRowId(row.id);
    setEditingDraft(copyFromVariation(row));
  };

  const cancelRowEdit = () => {
    setEditingRowId(null);
    setEditingDraft(EMPTY_COPY);
  };

  const saveRowEdit = async () => {
    if (!editingRowId) return;
    setSavingEditId(editingRowId);
    try {
      const editingRow = rows.find((r) => r.id === editingRowId);
      const updated = await updateVariation(editingRow?.slug ?? editingRowId, editingDraft);
      setRows((prev) => prev.map((row) => (row.id === updated.id ? updated : row)));
      setEditingRowId(null);
      setEditingDraft(EMPTY_COPY);
      toast.success("Draft updated");
    } catch (err) {
      toast.error(extractErrorMessage(err, "Failed to update draft."));
    } finally {
      setSavingEditId(null);
    }
  };

  const handleBulkReviewDrafts = async () => {
    if (!projectId || !canReviewSelectedDrafts) return;
    setBulkAction("review");
    try {
      const res = await bulkReviewVariations({
        project_id: projectId,
        selected_ids: selectedDraftRows.map((row) => row.id),
      });
      setSelectedIds(new Set());
      toast.success(`Reviewed ${res.reviewed_count} draft${res.reviewed_count === 1 ? "" : "s"}.`);
      refreshList();
    } catch (err) {
      toast.error(extractErrorMessage(err, "Failed to review selected drafts."));
    } finally {
      setBulkAction(null);
    }
  };

  const handleBulkDelete = async (targetStatus: AdCopyVariationStatus) => {
    const targetRows = targetStatus === "draft" ? selectedDraftRows : selectedReviewedRows;
    const enabled = targetStatus === "draft" ? canDeleteSelectedDrafts : canDeleteSelectedReviewed;
    if (!projectId || !enabled) return;
    setBulkAction(targetStatus === "draft" ? "delete-draft" : "delete-reviewed");
    try {
      const res = await bulkDeleteVariations({
        project_id: projectId,
        selected_ids: targetRows.map((row) => row.id),
        status: targetStatus,
      });
      setSelectedIds(new Set());
      toast.success(`Deleted ${res.deleted_count} ${targetStatus} draft${res.deleted_count === 1 ? "" : "s"}.`);
      refreshList();
    } catch (err) {
      toast.error(extractErrorMessage(err, `Failed to delete selected ${targetStatus} drafts.`));
    } finally {
      setBulkAction(null);
    }
  };

  const handleLatestBatch = async () => {
    if (!projectId) return;
    setLoadingLatestBatch(true);
    try {
      const res = await getLatestVariationBatch(projectId);
      if (!res.batch_id) {
        toast("No generated batches yet.");
        return;
      }
      setBatchFilter(res.batch_id);
      setPage(1);
    } catch (err) {
      toast.error(extractErrorMessage(err, "Failed to load latest batch."));
    } finally {
      setLoadingLatestBatch(false);
    }
  };

  if (!projectId) {
    return (
      <section className="rounded-xl border-[0.5px] border-gray-200 bg-white p-8 text-center">
        <p className="text-[14px] text-gray-700">Select an active project to view AI drafts.</p>
      </section>
    );
  }

  return (
    <section className="rounded-xl border-[0.5px] border-gray-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 p-5">
        <div>
          <h2 className="text-[16px] font-semibold text-gray-900">AI Drafts</h2>
          <p className="mt-1 text-[12px] text-gray-500">
            Persisted AI-generated drafts scoped to the active project.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={handleLatestBatch}
            disabled={loading || loadingLatestBatch}
            className="inline-flex h-8 items-center gap-1.5 rounded-md border border-gray-200 bg-white px-2.5 text-[12px] font-medium text-gray-700 transition hover:border-[#3CCED7] hover:text-[#1a9ba3] disabled:cursor-not-allowed disabled:opacity-60"
          >
            {loadingLatestBatch && <Loader2 className="h-3 w-3 animate-spin" />}
            Latest batch
          </button>
          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value as DraftStatusFilter);
              resetToFirstPage();
            }}
            className="rounded-md border border-gray-200 bg-white px-2 py-1.5 text-[12px] text-gray-700 outline-none focus:ring-2 focus:ring-[#3CCED7]/30"
          >
            <option value="all">All statuses</option>
            <option value="draft">Draft</option>
            <option value="reviewed">Reviewed</option>
          </select>
          <select
            value={sourceFilter}
            onChange={(e) => {
              setSourceFilter(e.target.value as AdCopyVariationSourceMode | "all");
              resetToFirstPage();
            }}
            className="rounded-md border border-gray-200 bg-white px-2 py-1.5 text-[12px] text-gray-700 outline-none focus:ring-2 focus:ring-[#3CCED7]/30"
          >
            <option value="all">All sources</option>
            <option value="existing">Existing creative</option>
            <option value="custom">Custom content</option>
            <option value="external_url">External URL</option>
          </select>
          <input
            value={creativeFilter}
            onChange={(e) => {
              setCreativeFilter(e.target.value);
              resetToFirstPage();
            }}
            inputMode="numeric"
            placeholder="Creative ID"
            className="h-8 w-28 rounded-md border border-gray-200 bg-white px-2 text-[12px] text-gray-700 outline-none focus:ring-2 focus:ring-[#3CCED7]/30"
          />
        </div>
      </div>

      {batchFilter && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 bg-gray-50 px-5 py-2">
          <div className="min-w-0 text-[12px] text-gray-500">
            Batch filter:{" "}
            <span className="font-mono text-gray-700">{batchFilter}</span>
          </div>
          <button
            type="button"
            onClick={() => {
              setBatchFilter("");
              setPage(1);
            }}
            className="rounded-md px-2 py-1 text-[12px] font-medium text-gray-600 transition hover:bg-white hover:text-gray-900"
          >
            Clear batch
          </button>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 bg-white px-5 py-3">
        <div className="flex items-center gap-3">
          <label className="inline-flex items-center gap-2 text-[12px] font-medium text-gray-700">
            <input
              type="checkbox"
              checked={allVisibleSelected}
              onChange={toggleAllVisible}
              disabled={rows.length === 0 || loading}
              className="h-4 w-4 rounded border-gray-300 text-[#1a9ba3] focus:ring-[#3CCED7]"
            />
            Select visible
          </label>
          <span className="text-[12px] text-gray-500">
            {selectedCount} selected
          </span>
        </div>
        {selectedCount > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={handleBulkReviewDrafts}
              disabled={!canReviewSelectedDrafts || bulkAction !== null}
              title={!canReviewSelectedDrafts ? "Only draft rows can be reviewed." : undefined}
              className="inline-flex h-8 items-center gap-1.5 rounded-md bg-[#1a9ba3] px-2.5 text-[12px] font-semibold text-white transition hover:bg-[#168992] disabled:cursor-not-allowed disabled:opacity-50"
            >
              {bulkAction === "review" && <Loader2 className="h-3 w-3 animate-spin" />}
              Review selected drafts
            </button>
            <button
              type="button"
              onClick={() => handleBulkDelete("draft")}
              disabled={!canDeleteSelectedDrafts || bulkAction !== null}
              title={!canDeleteSelectedDrafts ? "Select only draft rows to delete drafts." : undefined}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-gray-200 bg-white px-2.5 text-[12px] font-medium text-gray-700 transition hover:border-red-200 hover:text-red-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {bulkAction === "delete-draft" && <Loader2 className="h-3 w-3 animate-spin" />}
              Delete selected drafts
            </button>
            <button
              type="button"
              onClick={() => handleBulkDelete("reviewed")}
              disabled={!canDeleteSelectedReviewed || bulkAction !== null}
              title={!canDeleteSelectedReviewed ? "Select only reviewed rows to delete reviewed drafts." : undefined}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-gray-200 bg-white px-2.5 text-[12px] font-medium text-gray-700 transition hover:border-red-200 hover:text-red-700 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {bulkAction === "delete-reviewed" && <Loader2 className="h-3 w-3 animate-spin" />}
              Delete selected reviewed
            </button>
          </div>
        )}
      </div>

      <div className="divide-y divide-gray-100">
        {loading ? (
          <div className="p-5 text-[14px] text-gray-500">Loading drafts…</div>
        ) : rows.length === 0 ? (
          <div className="p-8 text-center text-[14px] text-gray-500">
            No AI drafts match these filters.
          </div>
        ) : (
          rows.map((row) => {
            const isEditing = editingRowId === row.id;
            const savingThisRow = savingEditId === row.id;
            const creativeId = row.creative;
            return (
              <article key={row.id} className="p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex min-w-0 flex-1 items-start gap-3">
                    <input
                      type="checkbox"
                      checked={selectedIds.has(row.id)}
                      onChange={() => toggleRowSelection(row.id)}
                      className="mt-1 h-4 w-4 rounded border-gray-300 text-[#1a9ba3] focus:ring-[#3CCED7]"
                      aria-label={`Select draft ${row.id}`}
                    />
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <VariationStatusPill status={row.status} />
                        <span className="text-[12px] text-gray-500">
                          {sourceModeLabel(row.source_mode)}
                        </span>
                        <span className="text-[12px] text-gray-300">Draft #{row.id}</span>
                        {creativeId !== null && (
                          <button
                            type="button"
                            onClick={() => applyCreativeFilter(creativeId)}
                            className="inline-flex items-center rounded bg-gray-100 px-1.5 py-0.5 font-mono text-[11px] text-gray-700 transition hover:bg-[#3CCED7]/10 hover:text-[#1a9ba3]"
                            aria-label={`Filter drafts by creative ${creativeId}`}
                            title="Filter AI drafts by this Creative ID"
                          >
                            Creative ID {creativeId}
                          </button>
                        )}
                        <span className="text-[12px] text-gray-300">
                          {relativeTime(row.created_at)}
                        </span>
                      </div>
                      {!isEditing && (
                        <>
                          <h3 className="mt-2 text-[15px] font-semibold text-gray-900">
                            {row.headline || row.hook || "Untitled variation"}
                          </h3>
                          <p className="mt-1 max-w-3xl whitespace-pre-wrap text-[13px] leading-5 text-gray-600">
                            {row.description || "No description"}
                          </p>
                        </>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 flex-wrap items-center gap-2">
                    {!isEditing ? (
                      <button
                        type="button"
                        onClick={() => startRowEdit(row)}
                        aria-label={`Edit draft ${row.id}`}
                        title="Edit"
                        className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-gray-500 ring-1 ring-gray-200 transition hover:text-gray-900 hover:ring-gray-300"
                      >
                        <Pencil className="h-3.5 w-3.5" />
                      </button>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={cancelRowEdit}
                          disabled={savingThisRow}
                          className="rounded-lg px-3 py-1.5 text-[12px] font-medium text-gray-600 transition hover:bg-gray-100 hover:text-gray-900 disabled:opacity-60"
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          onClick={saveRowEdit}
                          disabled={savingThisRow}
                          className="inline-flex items-center gap-1.5 rounded-lg bg-gradient-to-br from-[#3CCED7] to-[#A6E661] px-3 py-1.5 text-[12px] font-semibold text-white shadow-sm transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {savingThisRow && <Loader2 className="h-3 w-3 animate-spin" />}
                          {savingThisRow ? "Saving…" : "Update"}
                        </button>
                      </>
                    )}
                    {creativeId !== null ? (
                      <Link
                        href={buildUrl(`/meta-ads/creatives/${row.creative_slug ?? creativeId}`)}
                        className="rounded-lg px-3 py-1.5 text-[12px] font-medium text-gray-700 ring-1 ring-gray-200 transition hover:text-[#1a9ba3] hover:ring-[#3CCED7]"
                      >
                        Creative detail
                      </Link>
                    ) : (
                      <span className="rounded-lg bg-gray-100 px-3 py-1.5 text-[12px] text-gray-500">
                        Project-scoped
                      </span>
                    )}
                  </div>
                </div>

                <ValidationWarnings
                  warnings={row.validation_warnings ?? []}
                />

                {isEditing ? (
                  <div className="mt-4 space-y-3 rounded-lg border border-gray-100 bg-gray-50/60 p-4">
                    <CardField
                      label="Hook"
                      editing
                      value={editingDraft.hook}
                      onChange={(v) => setEditingDraft((prev) => ({ ...prev, hook: v }))}
                      rows={2}
                    />
                    <CardField
                      label="Headline"
                      editing
                      value={editingDraft.headline}
                      onChange={(v) => setEditingDraft((prev) => ({ ...prev, headline: v }))}
                      rows={2}
                    />
                    <CardField
                      label="Description"
                      editing
                      value={editingDraft.description}
                      onChange={(v) => setEditingDraft((prev) => ({ ...prev, description: v }))}
                      rows={3}
                    />
                    <CtaCardField
                      editing
                      value={editingDraft.cta}
                      onChange={(v) => setEditingDraft((prev) => ({ ...prev, cta: v }))}
                    />
                  </div>
                ) : (
                  <div className="mt-3 grid gap-3 text-[12px] text-gray-500 md:grid-cols-2">
                    <div>
                      <span className="font-medium text-gray-700">Hook:</span>{" "}
                      {row.hook || "—"}
                    </div>
                    <div>
                      <span className="font-medium text-gray-700">CTA:</span>{" "}
                      {row.cta || "—"}
                    </div>
                    {row.source_ref && (
                      <div className="md:col-span-2">
                        <span className="font-medium text-gray-700">Source:</span>{" "}
                        <span className="break-all">{row.source_ref}</span>
                      </div>
                    )}
                  </div>
                )}
              </article>
            );
          })
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-gray-100 p-5">
        <div className="text-[12px] text-gray-500">
          {total} draft{total === 1 ? "" : "s"} · page {page} of {pageCount}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setPage((prev) => Math.max(1, prev - 1))}
            disabled={page <= 1 || loading}
            className="rounded-lg px-3 py-1.5 text-[12px] font-medium text-gray-700 ring-1 ring-gray-200 transition hover:ring-gray-300 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Previous
          </button>
          <button
            type="button"
            onClick={() => setPage((prev) => Math.min(pageCount, prev + 1))}
            disabled={page >= pageCount || loading}
            className="rounded-lg px-3 py-1.5 text-[12px] font-medium text-gray-700 ring-1 ring-gray-200 transition hover:ring-gray-300 disabled:cursor-not-allowed disabled:opacity-50"
          >
            Next
          </button>
        </div>
      </div>
    </section>
  );
}
