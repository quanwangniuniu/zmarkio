'use client';

import React, { useState } from 'react';
import toast from 'react-hot-toast';
import { Download } from 'lucide-react';

import CsmQualityAPI from '@/lib/api/csmQualityApi';
import { downloadBlob } from '@/lib/downloadBlob';
import { QualityFilters } from '@/types/csmQuality';

interface QualityExportButtonProps {
  filters: Partial<QualityFilters>;
  disabled?: boolean;
}

export function QualityExportButton({ filters, disabled }: QualityExportButtonProps) {
  const [exporting, setExporting] = useState(false);

  const handleExport = async () => {
    setExporting(true);
    const toastId = toast.loading('Preparing export…');
    try {
      const { blob, filename } = await CsmQualityAPI.exportCsv(filters);
      downloadBlob(blob, filename);
      toast.success('Export ready.', { id: toastId });
    } catch (error) {
      const detail =
        (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? 'Could not export the report.', { id: toastId });
    } finally {
      setExporting(false);
    }
  };

  return (
    <button
      type="button"
      onClick={handleExport}
      disabled={disabled || exporting}
      className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
    >
      <Download className="h-4 w-4" />
      {exporting ? 'Exporting…' : 'Export CSV'}
    </button>
  );
}

export default QualityExportButton;
