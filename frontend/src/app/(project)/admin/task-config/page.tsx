'use client';

import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import Layout from '@/components/layout/Layout';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { getApiErrorDetail } from '@/lib/api/errorMessage';
import { fetchTaskConfig, saveTaskConfig } from '@/lib/api/orgCustomizationApi';
import type { TaskConfig, TaskStatusConfig, TaskTypeConfig } from '@/types/orgCustomization';

const headerCell = 'px-4 py-2 text-left text-xs font-medium uppercase text-gray-500 dark:text-gray-400';
const bodyCell = 'px-4 py-2 text-sm text-gray-900 dark:text-gray-100';

export default function TaskConfigPage() {
  const [config, setConfig] = useState<TaskConfig | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchTaskConfig()
      .then(setConfig)
      .catch((error) => toast.error(getApiErrorDetail(error, 'Failed to load task config')));
  }, []);

  const updateType = (key: string, patch: Partial<TaskTypeConfig>) =>
    setConfig((prev) => prev && {
      ...prev,
      task_types: prev.task_types.map((item) => (item.key === key ? { ...item, ...patch } : item)),
    });

  const updateStatus = (key: string, patch: Partial<TaskStatusConfig>) =>
    setConfig((prev) => prev && {
      ...prev,
      task_statuses: prev.task_statuses.map((item) => (item.key === key ? { ...item, ...patch } : item)),
    });

  const handleSave = async () => {
    if (!config) return;
    setSaving(true);
    try {
      setConfig(await saveTaskConfig(config));
      toast.success('Task config saved');
    } catch (error) {
      toast.error(getApiErrorDetail(error, 'Failed to save task config'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <ProtectedRoute requireAdmin fallback="/unauthorized">
      <Layout showPermissionRole={true}>
        <div className="p-6 space-y-6">
          <div className="flex items-start justify-between">
            <div>
              <h1 className="text-3xl font-bold text-gray-900 dark:text-gray-100">Task Config</h1>
              <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                Choose which task types your organization uses, and rename or reorder types and statuses.
              </p>
            </div>
            <Button onClick={handleSave} disabled={!config || saving}>
              {saving ? 'Saving...' : 'Save'}
            </Button>
          </div>

          {!config ? (
            <p className="text-sm text-gray-500 dark:text-gray-400">Loading...</p>
          ) : (
            <>
              <section className="space-y-2">
                <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Task Types</h2>
                <table className="min-w-full divide-y divide-gray-200 rounded-lg border border-gray-200 dark:divide-gray-700 dark:border-gray-700">
                  <thead>
                    <tr>
                      <th className={headerCell}>Enabled</th>
                      <th className={headerCell}>Type</th>
                      <th className={headerCell}>Label</th>
                      <th className={headerCell}>Order</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                    {config.task_types.map((item) => (
                      <tr key={item.key}>
                        <td className={bodyCell}>
                          <Checkbox
                            checked={item.enabled}
                            onCheckedChange={(checked) => updateType(item.key, { enabled: checked === true })}
                            aria-label={`Enable ${item.default_label}`}
                          />
                        </td>
                        <td className={bodyCell}>{item.default_label}</td>
                        <td className={bodyCell}>
                          <Input
                            value={item.label ?? ''}
                            placeholder={item.default_label}
                            onChange={(e) => updateType(item.key, { label: e.target.value })}
                          />
                        </td>
                        <td className={bodyCell}>
                          <Input
                            type="number"
                            min={0}
                            className="w-24"
                            value={item.order}
                            onChange={(e) => updateType(item.key, { order: Number(e.target.value) || 0 })}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>

              <section className="space-y-2">
                <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Task Statuses</h2>
                <table className="min-w-full divide-y divide-gray-200 rounded-lg border border-gray-200 dark:divide-gray-700 dark:border-gray-700">
                  <thead>
                    <tr>
                      <th className={headerCell}>Status</th>
                      <th className={headerCell}>Label</th>
                      <th className={headerCell}>Color</th>
                      <th className={headerCell}>Order</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200 dark:divide-gray-700">
                    {config.task_statuses.map((item) => (
                      <tr key={item.key}>
                        <td className={bodyCell}>{item.default_label}</td>
                        <td className={bodyCell}>
                          <Input
                            value={item.label ?? ''}
                            placeholder={item.default_label}
                            onChange={(e) => updateStatus(item.key, { label: e.target.value })}
                          />
                        </td>
                        <td className={bodyCell}>
                          <input
                            type="color"
                            className="h-9 w-14 cursor-pointer rounded border border-gray-200 dark:border-gray-700"
                            value={item.color ?? '#9CA3AF'}
                            onChange={(e) => updateStatus(item.key, { color: e.target.value })}
                            aria-label={`${item.default_label} color`}
                          />
                        </td>
                        <td className={bodyCell}>
                          <Input
                            type="number"
                            min={0}
                            className="w-24"
                            value={item.order}
                            onChange={(e) => updateStatus(item.key, { order: Number(e.target.value) || 0 })}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            </>
          )}
        </div>
      </Layout>
    </ProtectedRoute>
  );
}
