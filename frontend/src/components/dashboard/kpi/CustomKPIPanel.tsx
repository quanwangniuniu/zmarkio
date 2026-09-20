'use client';

import { AlertCircle, Pencil, Plus, Trash2 } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import ReportAPI from '@/lib/api/reportApi';
import type { CustomKPI, KPIMetric } from '@/types/report';
import KPIBuilderDialog from './KPIBuilderDialog';
import { formatKPIValue } from './formatKPIValue';

interface CustomKPIPanelProps {
  projectSlug: string | null | undefined;
}

function KPITile({
  kpi,
  onEdit,
  onDelete,
}: {
  kpi: CustomKPI;
  onEdit: (kpi: CustomKPI) => void;
  onDelete: (kpi: CustomKPI) => void;
}) {
  return (
    <div
      className="group relative rounded-lg border-[0.5px] border-gray-200 bg-white p-3.5"
      data-testid="custom-kpi-tile"
      data-kpi-name={kpi.name}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-xs font-medium uppercase tracking-wide text-gray-400">
            {kpi.name}
          </div>
          {kpi.error ? (
            <div
              className="mt-1 flex items-start gap-1.5 text-xs text-red-600"
              data-testid="custom-kpi-tile-error"
            >
              <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
              <span>{kpi.error.message}</span>
            </div>
          ) : (
            <div
              className="mt-0.5 text-2xl font-semibold tracking-tight text-gray-900"
              data-testid="custom-kpi-tile-value"
            >
              {formatKPIValue(kpi.value, kpi.display_format)}
            </div>
          )}
          <div className="mt-1 truncate font-mono text-[11px] text-gray-400">
            {kpi.formula}
          </div>
        </div>

        <div className="flex shrink-0 gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
          <button
            type="button"
            aria-label={`Edit ${kpi.name}`}
            onClick={() => onEdit(kpi)}
            className="rounded p-1 text-gray-400 hover:bg-gray-50 hover:text-gray-600"
          >
            <Pencil className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            aria-label={`Delete ${kpi.name}`}
            onClick={() => onDelete(kpi)}
            className="rounded p-1 text-gray-400 hover:bg-gray-50 hover:text-red-600"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}

export default function CustomKPIPanel({ projectSlug }: CustomKPIPanelProps) {
  const [kpis, setKpis] = useState<CustomKPI[]>([]);
  const [metrics, setMetrics] = useState<KPIMetric[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<CustomKPI | null>(null);

  const loadKPIs = useCallback(async () => {
    if (!projectSlug) return;
    setLoading(true);
    setLoadError(null);
    try {
      const response = await ReportAPI.listKPIs({ project: projectSlug });
      setKpis(response.data);
    } catch {
      setLoadError('Custom KPIs could not be loaded.');
    } finally {
      setLoading(false);
    }
  }, [projectSlug]);

  useEffect(() => {
    void loadKPIs();
  }, [loadKPIs]);

  // The metric catalog is static, so it is fetched once rather than per open.
  useEffect(() => {
    let cancelled = false;
    ReportAPI.listKPIMetrics()
      .then((response) => {
        if (!cancelled) setMetrics(response.data.metrics);
      })
      .catch(() => {
        // Autocomplete degrades to nothing; the editor still works.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSaved = useCallback(() => {
    // Refetch rather than splicing the response in: a saved KPI's value is
    // computed over the whole window, which only the list endpoint returns.
    void loadKPIs();
  }, [loadKPIs]);

  const handleDelete = useCallback(async (kpi: CustomKPI) => {
    setKpis((current) => current.filter((item) => item.id !== kpi.id));
    try {
      await ReportAPI.deleteKPI(kpi.id);
    } catch {
      void loadKPIs(); // Put it back if the delete did not stick.
    }
  }, [loadKPIs]);

  const openCreate = useCallback(() => {
    setEditing(null);
    setDialogOpen(true);
  }, []);

  const openEdit = useCallback((kpi: CustomKPI) => {
    setEditing(kpi);
    setDialogOpen(true);
  }, []);

  if (!projectSlug) return null;

  return (
    <section
      className="rounded-xl border-[0.5px] border-gray-200 bg-white p-4"
      data-testid="custom-kpi-panel"
    >
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-gray-900">Custom KPIs</h2>
          <p className="text-[11px] text-gray-400">
            Metrics you define with a formula, over the last 30 days.
          </p>
        </div>
        <Button size="sm" onClick={openCreate} data-testid="new-kpi-button">
          <Plus className="mr-1 h-3.5 w-3.5" />
          New KPI
        </Button>
      </div>

      {loadError && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {loadError}
        </div>
      )}

      {!loadError && loading && kpis.length === 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-24 animate-pulse rounded-lg bg-gray-50" />
          ))}
        </div>
      )}

      {!loadError && !loading && kpis.length === 0 && (
        <div
          className="rounded-lg border border-dashed border-gray-200 px-4 py-6 text-center text-xs text-gray-400"
          data-testid="custom-kpi-empty"
        >
          No custom KPIs yet. Create one to track a metric your team cares about.
        </div>
      )}

      {kpis.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {kpis.map((kpi) => (
            <KPITile
              key={kpi.id}
              kpi={kpi}
              onEdit={openEdit}
              onDelete={handleDelete}
            />
          ))}
        </div>
      )}

      <KPIBuilderDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        projectSlug={projectSlug}
        metrics={metrics}
        editing={editing}
        onSaved={handleSaved}
      />
    </section>
  );
}
