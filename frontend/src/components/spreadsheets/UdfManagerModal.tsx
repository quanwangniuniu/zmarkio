'use client';

import { useState, useEffect, useCallback } from 'react';
import { X, Plus, Pencil, Trash2, Check, AlertCircle } from 'lucide-react';
import Modal from '@/components/ui/Modal';
import { SpreadsheetAPI, UdfData, UdfPayload } from '@/lib/api/spreadsheetApi';

interface Props {
  isOpen: boolean;
  onClose: () => void;
  projectSlug: string;
}

interface FormState {
  name: string;
  params: string;
  expression: string;
}

const EMPTY_FORM: FormState = { name: '', params: '', expression: '' };

export default function UdfManagerModal({ isOpen, onClose, projectSlug }: Props) {
  const [udfs, setUdfs] = useState<UdfData[]>([]);
  const [loadingList, setLoadingList] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [errors, setErrors] = useState<Partial<FormState & { general: string }>>({});

  const fetchUdfs = useCallback(async () => {
    if (!projectSlug) return;
    setLoadingList(true);
    try {
      const data = await SpreadsheetAPI.listUdfs(projectSlug);
      setUdfs(data);
    } catch {
      // silently fail — list just stays empty
    } finally {
      setLoadingList(false);
    }
  }, [projectSlug]);

  useEffect(() => {
    if (isOpen) void fetchUdfs();
    else {
      setShowForm(false);
      setEditingId(null);
      setForm(EMPTY_FORM);
      setErrors({});
    }
  }, [isOpen, fetchUdfs]);

  function openCreate() {
    setEditingId(null);
    setForm(EMPTY_FORM);
    setErrors({});
    setShowForm(true);
  }

  function openEdit(udf: UdfData) {
    setEditingId(udf.id);
    setForm({
      name: udf.name,
      params: udf.params.join(', '),
      expression: udf.expression,
    });
    setErrors({});
    setShowForm(true);
  }

  function cancelForm() {
    setShowForm(false);
    setEditingId(null);
    setForm(EMPTY_FORM);
    setErrors({});
  }

  function validate(): boolean {
    const next: typeof errors = {};
    if (!form.name.trim()) next.name = 'Name is required';
    else if (!/^[A-Za-z]+$/.test(form.name.trim()))
      next.name = 'Name must contain letters only (no digits or underscores)';
    if (!form.params.trim()) next.params = 'At least one parameter is required';
    if (!form.expression.trim()) next.expression = 'Expression is required';
    setErrors(next);
    return Object.keys(next).length === 0;
  }

  async function handleSave() {
    if (!validate()) return;
    setSaving(true);
    setErrors({});
    const payload: UdfPayload = {
      name: form.name.trim().toUpperCase(),
      params: form.params.split(',').map((p) => p.trim()).filter(Boolean),
      expression: form.expression.trim(),
    };
    try {
      if (editingId != null) {
        const updated = await SpreadsheetAPI.updateUdf(projectSlug, editingId, payload);
        setUdfs((prev) => prev.map((u) => (u.id === editingId ? updated : u)));
      } else {
        const created = await SpreadsheetAPI.createUdf(projectSlug, payload);
        setUdfs((prev) => [...prev, created]);
      }
      cancelForm();
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { name?: string[]; expression?: string[]; detail?: string } } })
          ?.response?.data?.name?.[0] ||
        (err as { response?: { data?: { expression?: string[] } } })?.response?.data?.expression?.[0] ||
        (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
        'Failed to save function';
      setErrors({ general: msg });
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(id: number) {
    setDeleting(id);
    try {
      await SpreadsheetAPI.deleteUdf(projectSlug, id);
      setUdfs((prev) => prev.filter((u) => u.id !== id));
      if (editingId === id) cancelForm();
    } catch {
      // ignore
    } finally {
      setDeleting(null);
    }
  }

  return (
    <Modal isOpen={isOpen} onClose={onClose}>
      <div className="w-[min(560px,calc(100vw-2rem))]">
        <div className="relative overflow-hidden rounded-2xl bg-white shadow-2xl ring-1 ring-gray-100">
          {/* Header */}
          <div className="flex items-start justify-between px-6 pt-6 pb-4 border-b border-gray-100">
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Custom Functions</h2>
              <p className="mt-0.5 text-sm text-gray-500">
                Define reusable formula functions for this project.
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="ml-4 inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gray-100 text-gray-600 hover:bg-gray-200 transition"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="p-6 space-y-4 max-h-[70vh] overflow-y-auto">
            {/* UDF list */}
            {loadingList ? (
              <p className="text-sm text-gray-400 text-center py-6">Loading…</p>
            ) : udfs.length === 0 && !showForm ? (
              <div className="flex flex-col items-center justify-center py-10 text-center">
                <p className="text-sm text-gray-500">No custom functions yet.</p>
                <p className="text-xs text-gray-400 mt-1">
                  Create one to use it like <code className="bg-gray-100 px-1 rounded">=MYROAS(A1, B1)</code>
                </p>
              </div>
            ) : (
              <ul className="space-y-2">
                {udfs.map((udf) =>
                  editingId === udf.id ? (
                    // Inline edit form replaces the row being edited
                    <li key={udf.id}>
                      <InlineForm
                        form={form}
                        errors={errors}
                        saving={saving}
                        isEdit
                        onChange={(patch) => setForm((p) => ({ ...p, ...patch }))}
                        onSave={handleSave}
                        onCancel={cancelForm}
                      />
                    </li>
                  ) : (
                    <li
                      key={udf.id}
                      className="flex items-center justify-between rounded-lg border border-gray-100 bg-gray-50 px-4 py-3 gap-3"
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-gray-900 font-mono">
                          {udf.name}({udf.params.join(', ')})
                        </p>
                        <p className="text-xs text-gray-500 truncate mt-0.5 font-mono">
                          {udf.expression}
                        </p>
                      </div>
                      <div className="flex shrink-0 gap-1">
                        <button
                          type="button"
                          onClick={() => openEdit(udf)}
                          className="inline-flex h-7 w-7 items-center justify-center rounded-md text-gray-500 hover:bg-gray-200 transition"
                          title="Edit"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDelete(udf.id)}
                          disabled={deleting === udf.id}
                          className="inline-flex h-7 w-7 items-center justify-center rounded-md text-gray-500 hover:bg-red-100 hover:text-red-600 transition disabled:opacity-40"
                          title="Delete"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </li>
                  )
                )}

                {/* New function form appended at the bottom of the list */}
                {showForm && editingId === null && (
                  <li>
                    <InlineForm
                      form={form}
                      errors={errors}
                      saving={saving}
                      isEdit={false}
                      onChange={(patch) => setForm((p) => ({ ...p, ...patch }))}
                      onSave={handleSave}
                      onCancel={cancelForm}
                    />
                  </li>
                )}
              </ul>
            )}
          </div>

          {/* Footer */}
          <div className="flex justify-between items-center px-6 py-4 border-t border-gray-100">
            <button
              type="button"
              onClick={openCreate}
              disabled={showForm}
              className="inline-flex items-center gap-1.5 text-sm font-medium text-[#3CCED7] hover:text-[#2AB5BD] transition disabled:opacity-40"
            >
              <Plus className="h-4 w-4" />
              New function
            </button>
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm font-medium text-gray-700 rounded-lg border border-gray-200 hover:bg-gray-50 transition"
            >
              Done
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}

interface InlineFormProps {
  form: FormState;
  errors: Partial<FormState & { general: string }>;
  saving: boolean;
  isEdit: boolean;
  onChange: (patch: Partial<FormState>) => void;
  onSave: () => void;
  onCancel: () => void;
}

function InlineForm({ form, errors, saving, isEdit, onChange, onSave, onCancel }: InlineFormProps) {
  return (
    <div className="rounded-lg border border-[#3CCED7]/40 bg-[#f0fdfe] p-4 space-y-3">
      <p className="text-sm font-medium text-gray-800">
        {isEdit ? 'Edit function' : 'New function'}
      </p>

      {errors.general && (
        <div className="flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          {errors.general}
        </div>
      )}

      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1">
          Function name <span className="text-red-500">*</span>
        </label>
        <input
          type="text"
          value={form.name}
          onChange={(e) => onChange({ name: e.target.value.toUpperCase() })}
          placeholder="e.g. MYROAS"
          className={`w-full rounded-md border px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-[#3CCED7] ${
            errors.name ? 'border-red-300 bg-red-50' : 'border-gray-300 bg-white'
          }`}
        />
        {errors.name && <p className="mt-1 text-xs text-red-600">{errors.name}</p>}
      </div>

      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1">
          Parameters <span className="text-red-500">*</span>
          <span className="font-normal text-gray-500 ml-1">
            (comma-separated — each param accepts a scalar value or a cell range like A1:A10)
          </span>
        </label>
        <input
          type="text"
          value={form.params}
          onChange={(e) => onChange({ params: e.target.value })}
          placeholder="e.g. revenue, cost  or  data  (pass A1:A10 as a range)"
          className={`w-full rounded-md border px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-[#3CCED7] ${
            errors.params ? 'border-red-300 bg-red-50' : 'border-gray-300 bg-white'
          }`}
        />
        {errors.params && <p className="mt-1 text-xs text-red-600">{errors.params}</p>}
      </div>

      <div>
        <label className="block text-xs font-medium text-gray-700 mb-1">
          Expression <span className="text-red-500">*</span>
        </label>
        <input
          type="text"
          value={form.expression}
          onChange={(e) => onChange({ expression: e.target.value })}
          placeholder="e.g. revenue / cost"
          className={`w-full rounded-md border px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-[#3CCED7] ${
            errors.expression ? 'border-red-300 bg-red-50' : 'border-gray-300 bg-white'
          }`}
        />
        {errors.expression && (
          <p className="mt-1 text-xs text-red-600">{errors.expression}</p>
        )}
      </div>

      <div className="flex items-center justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={onCancel}
          className="px-3 py-1.5 text-xs font-medium text-gray-700 rounded-md border border-gray-200 hover:bg-gray-50 transition"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onSave}
          disabled={saving}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-white bg-[#3CCED7] rounded-md hover:bg-[#2AB5BD] transition disabled:opacity-70"
        >
          {saving ? (
            'Saving…'
          ) : (
            <>
              <Check className="h-3.5 w-3.5" />
              {isEdit ? 'Update' : 'Create'}
            </>
          )}
        </button>
      </div>
    </div>
  );
}
