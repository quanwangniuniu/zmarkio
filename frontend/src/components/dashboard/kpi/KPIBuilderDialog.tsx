'use client';

import { AlertCircle, Loader2 } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import ReportAPI from '@/lib/api/reportApi';
import type {
  CustomKPI,
  KPIDisplayFormat,
  KPIMetric,
  KPIPreviewResponse,
} from '@/types/report';
import FormulaEditor from './FormulaEditor';
import { formatKPIValue } from './formatKPIValue';
import { isFormulaFault, isNoData } from './kpiErrors';

/** How long the formula must sit still before we ask the server to evaluate it. */
const PREVIEW_DEBOUNCE_MS = 400;

interface KPIBuilderDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectSlug: string;
  metrics: KPIMetric[];
  /** The KPI being edited, or null to create a new one. */
  editing: CustomKPI | null;
  onSaved: (kpi: CustomKPI) => void;
}

/** Pull a field error out of a DRF 400 body without assuming its shape. */
function readFieldError(error: unknown, field: string): string | null {
  const data = (error as { response?: { data?: Record<string, unknown> } })
    ?.response?.data;
  if (!data || typeof data !== 'object') return null;
  const value = data[field];
  if (Array.isArray(value)) return String(value[0]);
  if (typeof value === 'string') return value;
  return null;
}

export default function KPIBuilderDialog({
  open,
  onOpenChange,
  projectSlug,
  metrics,
  editing,
  onSaved,
}: KPIBuilderDialogProps) {
  const [name, setName] = useState('');
  const [formula, setFormula] = useState('');
  const [displayFormat, setDisplayFormat] = useState<KPIDisplayFormat>('number');

  const [preview, setPreview] = useState<KPIPreviewResponse | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [formulaError, setFormulaError] = useState<string | null>(null);
  const [nameError, setNameError] = useState<string | null>(null);

  // Reset the form whenever the dialog opens, so a cancelled edit never leaks
  // into the next one.
  useEffect(() => {
    if (!open) return;
    setName(editing?.name ?? '');
    setFormula(editing?.formula ?? '');
    setDisplayFormat(editing?.display_format ?? 'number');
    setPreview(null);
    setPreviewing(false);
    setFormulaError(null);
    setNameError(null);
  }, [open, editing]);

  // Debounced preview. `requestId` discards responses that arrive out of order
  // after fast typing.
  const requestIdRef = useRef(0);
  useEffect(() => {
    if (!open) return;

    const trimmed = formula.trim();
    if (!trimmed) {
      setPreview(null);
      setPreviewing(false);
      return;
    }

    setPreviewing(true);
    const requestId = ++requestIdRef.current;
    const timer = setTimeout(async () => {
      try {
        const response = await ReportAPI.previewKPI({
          project: projectSlug,
          formula: trimmed,
        });
        if (requestId !== requestIdRef.current) return;
        setPreview(response.data);
        // "No data" is shown in the preview box, not as a formula fault: the
        // formula is saveable and there is nothing for the author to fix.
        const error = response.data.error;
        setFormulaError(isFormulaFault(error) ? error!.message : null);
      } catch {
        if (requestId !== requestIdRef.current) return;
        setPreview(null);
        setFormulaError('Could not reach the server to check this formula.');
      } finally {
        if (requestId === requestIdRef.current) setPreviewing(false);
      }
    }, PREVIEW_DEBOUNCE_MS);

    return () => clearTimeout(timer);
  }, [formula, open, projectSlug]);

  const canSave =
    name.trim().length > 0 && formula.trim().length > 0 && !saving;

  const handleSave = useCallback(async () => {
    if (!canSave) return;
    setSaving(true);
    setFormulaError(null);
    setNameError(null);
    try {
      const payload = {
        name: name.trim(),
        formula: formula.trim(),
        display_format: displayFormat,
      };
      const response = editing
        ? await ReportAPI.updateKPI(editing.id, payload)
        : await ReportAPI.createKPI({ ...payload, project: projectSlug });
      onSaved(response.data);
      onOpenChange(false);
    } catch (error) {
      setFormulaError(readFieldError(error, 'formula'));
      setNameError(readFieldError(error, 'name'));
    } finally {
      setSaving(false);
    }
  }, [
    canSave,
    displayFormat,
    editing,
    formula,
    name,
    onOpenChange,
    onSaved,
    projectSlug,
  ]);

  const hasValue = preview?.value != null && !preview.error;
  const previewHasNoData = isNoData(preview?.error);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg" data-testid="kpi-builder-dialog">
        <DialogHeader>
          <DialogTitle>{editing ? 'Edit KPI' : 'New KPI'}</DialogTitle>
          <DialogDescription>
            Build a metric from your warehouse data, for example{' '}
            <code className="rounded bg-gray-100 px-1 py-0.5 text-[11px]">
              revenue / spend
            </code>
            .
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="kpi-name">Name</Label>
            <Input
              id="kpi-name"
              data-testid="kpi-name-input"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Blended ROAS"
            />
            {nameError && (
              <p className="text-xs text-red-600" data-testid="kpi-name-error">
                {nameError}
              </p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label>Formula</Label>
            <FormulaEditor
              value={formula}
              onChange={setFormula}
              metrics={metrics}
              invalid={Boolean(formulaError)}
              onSubmit={handleSave}
            />
            {formulaError ? (
              <p
                className="flex items-start gap-1.5 text-xs text-red-600"
                data-testid="kpi-formula-error"
              >
                <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
                <span>{formulaError}</span>
              </p>
            ) : (
              <p className="text-[11px] text-gray-400">
                Type a metric name for suggestions. Press Enter to save.
              </p>
            )}
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="kpi-format">Format</Label>
            <Select
              value={displayFormat}
              onValueChange={(next) => setDisplayFormat(next as KPIDisplayFormat)}
            >
              <SelectTrigger id="kpi-format" data-testid="kpi-format-select">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="number">Number</SelectItem>
                <SelectItem value="currency">Currency</SelectItem>
                <SelectItem value="percent">Percent</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="rounded-lg bg-gray-50 px-3 py-2.5">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">
              Preview
            </div>
            <div
              className="mt-0.5 flex items-center gap-2 text-lg font-semibold text-gray-900"
              data-testid="kpi-preview-value"
            >
              {previewing ? (
                <Loader2 className="h-4 w-4 animate-spin text-gray-400" />
              ) : hasValue ? (
                formatKPIValue(preview!.value, displayFormat)
              ) : previewHasNoData ? (
                <span
                  className="text-sm font-normal text-gray-500"
                  data-testid="kpi-preview-no-data"
                >
                  {preview!.error!.message} You can still save this KPI.
                </span>
              ) : (
                <span className="text-sm font-normal text-gray-400">
                  {formula.trim() ? 'No value' : 'Enter a formula'}
                </span>
              )}
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleSave}
            disabled={!canSave}
            data-testid="kpi-save-button"
          >
            {saving ? 'Saving…' : editing ? 'Save changes' : 'Create KPI'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
