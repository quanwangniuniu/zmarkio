'use client';

import { useEffect, useRef, useState } from 'react';
import { Download, File, FileImage, FileText, FileVideo, Loader2, Play, Plus, Trash2, X, ZoomIn } from 'lucide-react';
import * as XLSX from 'xlsx';
import { TaskAPI } from '@/lib/api/taskApi';
import { getValidAccessToken } from '@/lib/api';
import type { TaskAttachment } from '@/types/task';
import AddAttachmentDialog from './AddAttachmentDialog';
import ConfirmDialog from './ConfirmDialog';
import toast from 'react-hot-toast';
import { Skeleton } from '@/components/ui/skeleton';

const EXCEL_TYPES = new Set([
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', // .xlsx
  'application/vnd.ms-excel',                                           // .xls
  'text/csv',
  'application/csv',
]);

const TEXT_MAX_BYTES = 1 * 1024 * 1024; // 1 MB cap for text preview

function isTextPreviewable(ct: string): boolean {
  if (EXCEL_TYPES.has(ct)) return false; // CSV handled by Excel preview
  if (ct === 'application/json') return true;
  if (ct.startsWith('text/')) return true;
  return false;
}

const SCAN_TOKEN: Record<TaskAttachment['scan_status'], { label: string; cls: string }> = {
  pending: { label: 'Scanning…', cls: 'bg-amber-50 text-amber-700' },
  scanning: { label: 'Scanning…', cls: 'bg-amber-50 text-amber-700' },
  clean: { label: 'Clean', cls: 'bg-emerald-50 text-emerald-700' },
  infected: { label: 'Infected', cls: 'bg-rose-50 text-rose-700' },
  error_scanning: { label: 'Scan error', cls: 'bg-gray-100 text-gray-500' },
};

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function FileTypeIcon({ contentType }: { contentType: string | null | undefined }) {
  const ct = contentType ?? '';
  if (ct.startsWith('image/')) return <FileImage className="h-4 w-4 text-blue-500" />;
  if (ct.startsWith('video/')) return <FileVideo className="h-4 w-4 text-purple-500" />;
  if (ct === 'application/pdf' || ct.startsWith('text/')) return <FileText className="h-4 w-4 text-rose-500" />;
  return <File className="h-4 w-4 text-slate-500" />;
}

export default function TaskAttachmentsBlock({
  taskId,
  readOnly,
  loading = false,
  onPreviewChange,
  onMutated,
}: {
  taskId: number;
  readOnly: boolean;
  loading?: boolean;
  onPreviewChange?: (open: boolean) => void;
  onMutated?: () => void;
}) {
  const [items, setItems] = useState<TaskAttachment[] | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [localKey, setLocalKey] = useState(0);
  const [preview, setPreview] = useState<{ url: string; type: 'image' | 'video' | 'pdf' } | null>(null);
  const [pdfLoading, setPdfLoading] = useState(false);
  const [excelData, setExcelData] = useState<{ sheets: { name: string; rows: string[][] }[]; activeIndex: number } | null>(null);
  const [excelLoading, setExcelLoading] = useState(false);
  const [visibleRows, setVisibleRows] = useState(100);
  const [textData, setTextData] = useState<{ filename: string; content: string; truncated: boolean } | null>(null);
  const [textLoading, setTextLoading] = useState(false);

  const onPreviewChangeRef = useRef(onPreviewChange);
  onPreviewChangeRef.current = onPreviewChange;
  useEffect(() => {
    onPreviewChangeRef.current?.(preview !== null || excelData !== null || textData !== null);
  }, [preview, excelData]);

  const closePreview = () => {
    if (preview?.type === 'pdf') URL.revokeObjectURL(preview.url);
    setPreview(null);
  };

  const openPdfPreview = async (fileUrl: string) => {
    setPdfLoading(true);
    try {
      const token = await getValidAccessToken();
      const res = await fetch(fileUrl, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      if (!res.ok) throw new Error('Failed to fetch PDF');
      const blob = await res.blob();
      const blobUrl = URL.createObjectURL(blob);
      setPreview({ url: blobUrl, type: 'pdf' });
    } catch {
      toast.error('Failed to load PDF preview');
    } finally {
      setPdfLoading(false);
    }
  };

  const openExcelPreview = async (fileUrl: string) => {
    setExcelLoading(true);
    try {
      const token = await getValidAccessToken();
      const res = await fetch(fileUrl, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      if (!res.ok) throw new Error('Failed to fetch file');
      const arrayBuffer = await res.arrayBuffer();
      const workbook = XLSX.read(arrayBuffer, { type: 'array' });
      const sheets = workbook.SheetNames.map((name) => ({
        name,
        rows: XLSX.utils.sheet_to_json<string[]>(workbook.Sheets[name], { header: 1, defval: '' }) as string[][],
      }));
      setExcelData({ sheets, activeIndex: 0 });
    } catch {
      toast.error('Failed to load spreadsheet preview');
    } finally {
      setExcelLoading(false);
    }
  };

  const openTextPreview = async (fileUrl: string, filename: string) => {
    setTextLoading(true);
    try {
      const token = await getValidAccessToken();
      const res = await fetch(fileUrl, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      if (!res.ok) throw new Error('Failed to fetch file');
      const contentLength = Number(res.headers.get('content-length') ?? 0);
      if (contentLength > TEXT_MAX_BYTES) {
        toast.error('File too large to preview (max 1 MB)');
        return;
      }
      const text = await res.text();
      const truncated = text.length >= TEXT_MAX_BYTES;
      setTextData({ filename, content: truncated ? text.slice(0, TEXT_MAX_BYTES) : text, truncated });
    } catch {
      toast.error('Failed to load text preview');
    } finally {
      setTextLoading(false);
    }
  };

  const TERMINAL = new Set(['clean', 'infected', 'error_scanning']);

  useEffect(() => {
    let cancelled = false;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;

    if (loading) return;

    const fetch = () => {
      TaskAPI.getAttachments(taskId)
        .then((rows) => {
          if (cancelled) return;
          setItems(rows);
          const stillScanning = rows.some((r) => !TERMINAL.has(r.scan_status));
          if (stillScanning) {
            pollTimer = setTimeout(fetch, 3_000);
          }
        })
        .catch(() => {
          if (!cancelled) setItems([]);
        });
    };

    fetch();

    return () => {
      cancelled = true;
      if (pollTimer) clearTimeout(pollTimer);
    };
  }, [loading, taskId, localKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const doDownload = async (att: TaskAttachment) => {
    try {
      const data: any = await TaskAPI.downloadAttachment(taskId, att.id);
      const url = data?.download_url || data?.url || att.file;
      if (url) window.open(url, '_blank');
      else toast.error('No download URL returned');
    } catch (e) {
      toast.error((e as any)?.response?.data?.detail || 'Download failed');
    }
  };

  const [confirmAtt, setConfirmAtt] = useState<TaskAttachment | null>(null);
  const [deleting, setDeleting] = useState(false);

  const doDelete = async () => {
    if (!confirmAtt) return;
    setDeleting(true);
    try {
      await TaskAPI.deleteAttachment(taskId, confirmAtt.id);
      setConfirmAtt(null);
      setLocalKey((k) => k + 1);
      onMutated?.();
    } catch (e) {
      toast.error((e as any)?.response?.data?.detail || 'Delete failed');
    } finally {
      setDeleting(false);
    }
  };

  return (
    <section className="min-w-0 rounded-xl bg-white p-4 shadow-sm ring-1 ring-gray-100 sm:p-5">
      <div className="mb-3 flex min-w-0 items-center justify-between gap-2">
        <h2 className="text-[13px] font-semibold uppercase tracking-wide text-gray-900">
          Attachments
          {items && items.length > 0 && (
            <span className="ml-2 text-[11px] font-medium normal-case text-gray-400">
              {items.length}
            </span>
          )}
        </h2>
        {!readOnly && (
          <button
            type="button"
            onClick={() => setModalOpen(true)}
            className="inline-flex shrink-0 items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-gray-600 hover:bg-gray-50 hover:text-gray-900"
          >
            <Plus className="h-3.5 w-3.5" />
            Upload
          </button>
        )}
      </div>

      {(loading || items === null) ? (
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, index) => (
            <div key={`task-attachments-skeleton-${index}`} className="flex items-center gap-3 py-2">
              <div className="min-w-0 flex-1 space-y-2">
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-3 w-28" />
              </div>
              <Skeleton className="h-5 w-16 rounded-full" />
              <Skeleton className="h-7 w-7 rounded-md" />
            </div>
          ))}
        </div>
      ) : null}
      {items && items.length === 0 && (
        <p className="text-xs text-gray-400">No attachments yet.</p>
      )}
      {items && items.length > 0 && (
        <ul className="divide-y divide-gray-100">
          {items.map((a) => {
            const scan = SCAN_TOKEN[a.scan_status];
            const ct = a.content_type ?? '';
            const isImage = ct.startsWith('image/') && a.scan_status === 'clean';
            const isVideo = ct.startsWith('video/') && a.scan_status === 'clean';
            const isPdf = ct === 'application/pdf' && a.scan_status === 'clean';
            const isExcel = EXCEL_TYPES.has(ct) && a.scan_status === 'clean';
            const isText = isTextPreviewable(ct) && a.scan_status === 'clean';
            return (
              <li key={a.id} className="flex min-w-0 flex-wrap items-center gap-2 py-2 sm:flex-nowrap sm:gap-3">
                {isImage ? (
                  <button
                    type="button"
                    onClick={() => setPreview({ url: a.file, type: 'image' })}
                    className="group relative h-10 w-12 flex-shrink-0 overflow-hidden rounded border border-gray-100 bg-gray-50"
                    title="Preview image"
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={a.file} alt={a.original_filename} className="h-full w-full object-cover" />
                    <span className="absolute inset-0 flex items-center justify-center bg-black/0 transition-colors group-hover:bg-black/20">
                      <ZoomIn className="h-3.5 w-3.5 text-white opacity-0 transition-opacity group-hover:opacity-100" />
                    </span>
                  </button>
                ) : isVideo ? (
                  <button
                    type="button"
                    onClick={() => setPreview({ url: a.file, type: 'video' })}
                    className="group relative h-10 w-12 flex-shrink-0 overflow-hidden rounded border border-gray-200 bg-gray-50"
                    title="Preview video"
                  >
                    <span className="flex h-full w-full items-center justify-center">
                      <FileVideo className="h-4 w-4 text-purple-500 transition-opacity group-hover:opacity-0" />
                    </span>
                    <span className="absolute inset-0 flex items-center justify-center opacity-0 transition-opacity group-hover:opacity-100">
                      <Play className="h-4 w-4 text-purple-600" />
                    </span>
                  </button>
                ) : isPdf ? (
                  <button
                    type="button"
                    onClick={() => openPdfPreview(a.file)}
                    disabled={pdfLoading}
                    className="group relative h-10 w-12 flex-shrink-0 overflow-hidden rounded border border-gray-200 bg-gray-50 disabled:cursor-wait"
                    title="Preview PDF"
                  >
                    {pdfLoading ? (
                      <span className="flex h-full w-full items-center justify-center">
                        <Loader2 className="h-4 w-4 animate-spin text-rose-400" />
                      </span>
                    ) : (
                      <>
                        <span className="flex h-full w-full items-center justify-center">
                          <FileText className="h-4 w-4 text-rose-500 transition-opacity group-hover:opacity-0" />
                        </span>
                        <span className="absolute inset-0 flex items-center justify-center opacity-0 transition-opacity group-hover:opacity-100">
                          <ZoomIn className="h-4 w-4 text-rose-600" />
                        </span>
                      </>
                    )}
                  </button>
                ) : isExcel ? (
                  <button
                    type="button"
                    onClick={() => openExcelPreview(a.file)}
                    disabled={excelLoading}
                    className="group relative h-10 w-12 flex-shrink-0 overflow-hidden rounded border border-gray-200 bg-gray-50 disabled:cursor-wait"
                    title="Preview spreadsheet"
                  >
                    {excelLoading ? (
                      <span className="flex h-full w-full items-center justify-center">
                        <Loader2 className="h-4 w-4 animate-spin text-green-500" />
                      </span>
                    ) : (
                      <>
                        <span className="flex h-full w-full items-center justify-center">
                          <File className="h-4 w-4 text-green-600 transition-opacity group-hover:opacity-0" />
                        </span>
                        <span className="absolute inset-0 flex items-center justify-center opacity-0 transition-opacity group-hover:opacity-100">
                          <ZoomIn className="h-4 w-4 text-green-700" />
                        </span>
                      </>
                    )}
                  </button>
                ) : isText ? (
                  <button
                    type="button"
                    onClick={() => openTextPreview(a.file, a.original_filename)}
                    disabled={textLoading}
                    className="group relative h-10 w-12 flex-shrink-0 overflow-hidden rounded border border-gray-200 bg-gray-50 disabled:cursor-wait"
                    title="Preview text file"
                  >
                    {textLoading ? (
                      <span className="flex h-full w-full items-center justify-center">
                        <Loader2 className="h-4 w-4 animate-spin text-slate-400" />
                      </span>
                    ) : (
                      <>
                        <span className="flex h-full w-full items-center justify-center">
                          <FileText className="h-4 w-4 text-slate-500 transition-opacity group-hover:opacity-0" />
                        </span>
                        <span className="absolute inset-0 flex items-center justify-center opacity-0 transition-opacity group-hover:opacity-100">
                          <ZoomIn className="h-4 w-4 text-slate-600" />
                        </span>
                      </>
                    )}
                  </button>
                ) : (
                  <span className="flex h-10 w-12 flex-shrink-0 items-center justify-center rounded border border-gray-200 bg-gray-50">
                    <FileTypeIcon contentType={a.content_type} />
                  </span>
                )}
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm text-gray-900">{a.original_filename}</p>
                  <p className="mt-0.5 text-[11px] text-gray-500">
                    {formatSize(a.file_size)} · {a.uploaded_by?.username || 'unknown'}
                  </p>
                </div>
                <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${scan.cls}`}>
                  {scan.label}
                </span>
                <button
                  type="button"
                  onClick={() => doDownload(a)}
                  title="Download"
                  className="shrink-0 rounded p-1.5 text-gray-400 hover:bg-gray-50 hover:text-gray-900"
                  disabled={a.scan_status === 'infected'}
                >
                  <Download className="h-3.5 w-3.5" />
                </button>
                {!readOnly && (
                  <button
                    type="button"
                    onClick={() => setConfirmAtt(a)}
                    title="Delete"
                    className="shrink-0 rounded p-1.5 text-gray-400 hover:bg-rose-50 hover:text-rose-600"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {!loading && <AddAttachmentDialog
        open={modalOpen}
        onOpenChange={setModalOpen}
        taskId={taskId}
        onAdded={() => { setLocalKey((k) => k + 1); onMutated?.(); }}
      />}

      {!loading && <ConfirmDialog
        open={confirmAtt !== null}
        onOpenChange={(o) => !o && setConfirmAtt(null)}
        title="Delete attachment"
        description={confirmAtt ? `"${confirmAtt.original_filename}" will be permanently deleted.` : ''}
        confirmLabel="Delete"
        destructive
        busy={deleting}
        onConfirm={doDelete}
      />}

      {preview && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"
          onClick={closePreview}
        >
          <button
            type="button"
            onClick={closePreview}
            className="absolute right-4 top-4 rounded-full bg-white/10 p-2 text-white hover:bg-white/20"
          >
            <X className="h-5 w-5" />
          </button>
          {preview.type === 'video' ? (
            // eslint-disable-next-line jsx-a11y/media-has-caption
            <video
              src={preview.url}
              controls
              autoPlay
              className="max-h-full max-w-full rounded-lg shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            />
          ) : preview.type === 'pdf' ? (
            <iframe
              src={preview.url}
              title="PDF preview"
              className="h-[85vh] w-[min(90vw,900px)] rounded-lg shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            />
          ) : (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={preview.url}
              alt="Preview"
              className="max-h-full max-w-full rounded-lg object-contain shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            />
          )}
        </div>
      )}

      {excelData && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"
          onClick={() => setExcelData(null)}
        >
          <div
            className="flex h-[85vh] w-[min(95vw,1100px)] flex-col overflow-hidden rounded-xl bg-white shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
              <span className="text-sm font-medium text-gray-700">
                {excelData.sheets[excelData.activeIndex].name}
              </span>
              <button
                type="button"
                onClick={() => setExcelData(null)}
                className="rounded-full p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Sheet tabs */}
            {excelData.sheets.length > 1 && (
              <div className="flex gap-1 overflow-x-auto border-b border-gray-100 bg-gray-50 px-3 py-1.5">
                {excelData.sheets.map((s, i) => (
                  <button
                    key={s.name}
                    type="button"
                    onClick={() => {
                      setExcelData((prev) => prev ? { ...prev, activeIndex: i } : null);
                      setVisibleRows(100);
                    }}
                    className={`flex-shrink-0 rounded px-3 py-1 text-xs font-medium transition ${
                      i === excelData.activeIndex
                        ? 'bg-white text-gray-900 shadow-sm ring-1 ring-gray-200'
                        : 'text-gray-500 hover:bg-white/60 hover:text-gray-700'
                    }`}
                  >
                    {s.name}
                  </button>
                ))}
              </div>
            )}

            {/* Table */}
            {(() => {
              const allRows = excelData.sheets[excelData.activeIndex].rows;
              const shown = allRows.slice(0, visibleRows);
              const remaining = allRows.length - shown.length;
              return (
                <>
                  <div className="overflow-auto flex-1">
                    <table className="min-w-full border-collapse text-xs">
                      <tbody>
                        {shown.map((row, ri) => (
                          <tr key={ri} className={ri === 0 ? 'bg-gray-50' : 'hover:bg-gray-50/60'}>
                            {row.map((cell, ci) => {
                              const Tag = ri === 0 ? 'th' : 'td';
                              return (
                                <Tag
                                  key={ci}
                                  className="whitespace-nowrap border border-gray-100 px-3 py-1.5 text-left font-normal text-gray-800"
                                >
                                  {String(cell)}
                                </Tag>
                              );
                            })}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  {remaining > 0 && (
                    <div className="flex items-center gap-3 border-t border-gray-100 px-4 py-2.5 text-xs text-gray-500">
                      <span>{allRows.length} rows total · showing {shown.length}</span>
                      <button
                        type="button"
                        onClick={() => setVisibleRows((v) => v + 100)}
                        className="rounded-md px-2.5 py-1 font-medium text-[#2fb8c0] ring-1 ring-[#2fb8c0]/30 hover:bg-[#3CCED7]/10"
                      >
                        Load 100 more
                      </button>
                      <button
                        type="button"
                        onClick={() => setVisibleRows(allRows.length)}
                        className="rounded-md px-2.5 py-1 font-medium text-gray-600 ring-1 ring-gray-200 hover:bg-gray-50"
                      >
                        Load all
                      </button>
                    </div>
                  )}
                </>
              );
            })()}
          </div>
        </div>
      )}
      {textData && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-sm"
          onClick={() => setTextData(null)}
        >
          <div
            className="flex h-[85vh] w-[min(95vw,900px)] flex-col overflow-hidden rounded-xl bg-white shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
              <span className="truncate text-sm font-medium text-gray-700">{textData.filename}</span>
              <button
                type="button"
                onClick={() => setTextData(null)}
                className="ml-3 shrink-0 rounded-full p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="flex-1 overflow-auto bg-gray-50 p-4">
              <pre className="whitespace-pre-wrap break-words font-mono text-xs text-gray-800">{textData.content}</pre>
            </div>
            {textData.truncated && (
              <div className="border-t border-gray-100 px-4 py-2 text-xs text-amber-600">
                Preview truncated at 1 MB — download the file to view the full content.
              </div>
            )}
          </div>
        </div>
      )}

    </section>
  );
}
