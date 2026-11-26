"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { Filter } from "lucide-react";
import type { TaskListFilters } from "@/types/task";
import { Button } from "@/components/ui/button";
import { statusFilterOptions, TASKS_FILTER } from "@/lib/tasks/tasksModuleConfig";

type FilterKey =
  | "assignee"
  | "owner"
  | "type"
  | "status"
  | "priority"
  | "project"
  | "subtasks"
  | "due_date"
  | "created_date"
  | "tags";

export interface SimpleOption {
  value: string | number;
  label: string;
}

export interface TaskFilterPanelProps {
  filters: TaskListFilters;
  onChange: (next: TaskListFilters) => void;
  onClearAll: () => void;
  projectOptions?: { id: number; name: string }[];
  ownerOptions?: { id: number; name: string }[];
  approverOptions?: { id: number; name: string }[];
  typeOptions?: SimpleOption[];
  tagOptions?: { name: string; color: string }[];
}

const PRIORITY_OPTIONS: SimpleOption[] = [
  { value: "HIGHEST", label: "Highest" },
  { value: "HIGH", label: "High" },
  { value: "MEDIUM", label: "Medium" },
  { value: "LOW", label: "Low" },
  { value: "LOWEST", label: "Lowest" },
];

export function TaskFilterPanel({
  filters,
  onChange,
  onClearAll,
  projectOptions,
  ownerOptions,
  approverOptions,
  typeOptions,
  tagOptions = [],
}: TaskFilterPanelProps) {
  const [open, setOpen] = useState(false);
  const [activeKey, setActiveKey] = useState<FilterKey>("assignee");
  const rootRef = useRef<HTMLDivElement>(null);
  const filterList = TASKS_FILTER.values.map((key) => ({ key: key as FilterKey, label: TASKS_FILTER.labels[key] }));
  const shownKey = filterList.some((item) => item.key === activeKey) ? activeKey : filterList[0]?.key;

  const activeCount = useMemo(() => {
    const keys: (keyof TaskListFilters)[] = [
      "project_id",
      "type",
      "status",
      "priority",
      "owner_id",
      "current_approver_id",
      "due_date_after",
      "due_date_before",
      "created_after",
      "created_before",
    ];
    return keys.reduce((acc, key) => {
      const v = filters[key];
      return acc + (v === undefined || v === null || v === "" ? 0 : 1);
    }, 0) +
      (filters.has_subtasks !== undefined ? 1 : 0) +
      (filters.tag_names?.length ? 1 : 0);
  }, [filters]);

  useEffect(() => {
    if (!open) return;
    const handleClickOutside = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  const getFieldCount = (key: FilterKey) => {
    const len = (v: unknown) =>
      Array.isArray(v)
        ? v.length
        : v === undefined || v === null || v === ""
          ? 0
          : 1;

    switch (key) {
      case "assignee":
        return len(filters.current_approver_id);
      case "owner":
        return len(filters.owner_id);
      case "type":
        return len(filters.type);
      case "status":
        return len(filters.status);
      case "priority":
        return len(filters.priority);
      case "project":
        return len(filters.project_id);
      case "tags":
        return filters.tag_names?.length ?? 0;
      case "subtasks":
        return filters.has_subtasks !== undefined ? 1 : 0;
      case "due_date":
        return (filters.due_date_after ? 1 : 0) + (filters.due_date_before ? 1 : 0);
      case "created_date":
        return (filters.created_after ? 1 : 0) + (filters.created_before ? 1 : 0);
      default:
        return 0;
    }
  };

  const toggleMulti = <T,>(value: T, current: T | T[] | undefined) => {
    const arr = Array.isArray(current) ? current.slice() : current ? [current] : [];
    const idx = arr.indexOf(value);
    if (idx >= 0) arr.splice(idx, 1);
    else arr.push(value);
    return arr.length ? arr : undefined;
  };

  const handlePartialChange = (patch: Partial<TaskListFilters>) => {
    onChange({ ...filters, ...patch });
  };

  const renderRightPanel = () => {
    switch (shownKey) {
      case "assignee":
        return (
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground">
              Current approver
            </label>
            <div className="max-h-56 overflow-auto rounded-md border border-input bg-background">
              <button
                type="button"
                className="w-full px-3 py-2 text-left text-sm hover:bg-muted"
                onClick={() => handlePartialChange({ current_approver_id: undefined })}
              >
                Any assignee
              </button>
              {(approverOptions || []).map((o) => {
                const selected = Array.isArray(filters.current_approver_id)
                  ? filters.current_approver_id.includes(o.id)
                  : filters.current_approver_id === o.id;
                return (
                  <label
                    key={o.id}
                    className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm hover:bg-muted"
                  >
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() =>
                        handlePartialChange({
                          current_approver_id: toggleMulti(
                            o.id,
                            filters.current_approver_id as any,
                          ) as any,
                        })
                      }
                    />
                    <span>{o.name}</span>
                  </label>
                );
              })}
            </div>
          </div>
        );
      case "owner":
        return (
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground">
              Owner
            </label>
            <div className="max-h-56 overflow-auto rounded-md border border-input bg-background">
              <button
                type="button"
                className="w-full px-3 py-2 text-left text-sm hover:bg-muted"
                onClick={() => handlePartialChange({ owner_id: undefined })}
              >
                Any owner
              </button>
              {(ownerOptions || []).map((o) => {
                const selected = Array.isArray(filters.owner_id)
                  ? filters.owner_id.includes(o.id)
                  : filters.owner_id === o.id;
                return (
                  <label
                    key={o.id}
                    className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm hover:bg-muted"
                  >
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() =>
                        handlePartialChange({
                          owner_id: toggleMulti(o.id, filters.owner_id as any) as any,
                        })
                      }
                    />
                    <span>{o.name}</span>
                  </label>
                );
              })}
            </div>
          </div>
        );
      case "type":
        return (
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground">
              Work type
            </label>
            <div className="max-h-56 overflow-auto rounded-md border border-input bg-background">
              <button
                type="button"
                className="w-full px-3 py-2 text-left text-sm hover:bg-muted"
                onClick={() => handlePartialChange({ type: undefined })}
              >
                Any type
              </button>
              {(typeOptions || []).map((o) => {
                const value = String(o.value);
                const selected = Array.isArray(filters.type)
                  ? filters.type.includes(value)
                  : filters.type === value;
                return (
                  <label
                    key={value}
                    data-testid={`task-filter-option-type-${value}`}
                    className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm hover:bg-muted"
                  >
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() =>
                        handlePartialChange({
                          type: toggleMulti(value, filters.type as any) as any,
                        })
                      }
                    />
                    <span>{o.label}</span>
                  </label>
                );
              })}
            </div>
          </div>
        );
      case "status":
        return (
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground">
              Status
            </label>
            <div className="max-h-56 overflow-auto rounded-md border border-input bg-background">
              <button
                type="button"
                className="w-full px-3 py-2 text-left text-sm hover:bg-muted"
                onClick={() => handlePartialChange({ status: undefined })}
              >
                Any status
              </button>
              {statusFilterOptions().map((o) => {
                const value = String(o.value);
                const selected = Array.isArray(filters.status)
                  ? filters.status.includes(value)
                  : filters.status === value;
                return (
                  <label
                    key={value}
                    data-testid={`task-filter-option-status-${value}`}
                    className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm hover:bg-muted"
                  >
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() =>
                        handlePartialChange({
                          status: toggleMulti(value, filters.status as any) as any,
                        })
                      }
                    />
                    <span>{o.label}</span>
                  </label>
                );
              })}
            </div>
          </div>
        );
      case "priority":
        return (
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground">
              Priority
            </label>
            <div className="max-h-56 overflow-auto rounded-md border border-input bg-background">
              <button
                type="button"
                className="w-full px-3 py-2 text-left text-sm hover:bg-muted"
                onClick={() => handlePartialChange({ priority: undefined })}
              >
                Any priority
              </button>
              {PRIORITY_OPTIONS.map((o) => {
                const value = String(o.value);
                const selected = Array.isArray(filters.priority)
                  ? filters.priority.includes(value)
                  : filters.priority === value;
                return (
                  <label
                    key={value}
                    className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm hover:bg-muted"
                  >
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() =>
                        handlePartialChange({
                          priority: toggleMulti(value, filters.priority as any) as any,
                        })
                      }
                    />
                    <span>{o.label}</span>
                  </label>
                );
              })}
            </div>
          </div>
        );
      case "tags":
        return (
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground">Tags</label>
            <div className="max-h-56 overflow-auto rounded-md border border-input bg-background">
              <button
                type="button"
                className="w-full px-3 py-2 text-left text-sm hover:bg-muted"
                onClick={() => handlePartialChange({ tag_names: undefined })}
              >
                Any tag
              </button>
              {tagOptions.length === 0 && (
                <p className="px-3 py-2 text-xs text-muted-foreground">No tags in this project yet.</p>
              )}
              {tagOptions.map((opt) => {
                const selected = (filters.tag_names ?? []).includes(opt.name);
                return (
                  <label
                    key={opt.name}
                    data-testid={`task-filter-option-tag-${opt.name}`}
                    className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm hover:bg-muted"
                  >
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() =>
                        handlePartialChange({
                          tag_names: toggleMulti(opt.name, filters.tag_names) as string[] | undefined,
                        })
                      }
                    />
                    <span
                      className="h-2.5 w-2.5 shrink-0 rounded-full ring-1 ring-black/10"
                      style={{ backgroundColor: opt.color }}
                    />
                    <span>{opt.name}</span>
                  </label>
                );
              })}
            </div>
          </div>
        );
      case "project":
        return (
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground">
              Project
            </label>
            <div className="max-h-56 overflow-auto rounded-md border border-input bg-background">
              <button
                type="button"
                className="w-full px-3 py-2 text-left text-sm hover:bg-muted"
                onClick={() => handlePartialChange({ project_id: undefined })}
              >
                Any project
              </button>
              {(projectOptions || []).map((p) => {
                const selected = filters.project_id === p.id;
                return (
                  <label
                    key={p.id}
                    className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm hover:bg-muted"
                  >
                    <input
                      type="radio"
                      name="task-filter-project"
                      checked={selected}
                      onChange={() => handlePartialChange({ project_id: p.id })}
                    />
                    <span>{p.name}</span>
                  </label>
                );
              })}
            </div>
          </div>
        );
      case "subtasks":
        return (
          <div className="space-y-2">
            <label className="text-xs font-medium text-muted-foreground">
              Subtasks
            </label>
            <div className="overflow-hidden rounded-md border border-input bg-background">
              <button
                type="button"
                className="w-full px-3 py-2 text-left text-sm hover:bg-muted"
                onClick={() => handlePartialChange({ has_subtasks: undefined })}
              >
                All tasks
              </button>
              <label className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm hover:bg-muted">
                <input
                  type="radio"
                  name="task-filter-subtasks"
                  checked={filters.has_subtasks === true}
                  onChange={() => handlePartialChange({ has_subtasks: true })}
                />
                <span>Parent tasks only</span>
              </label>
              <label className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm hover:bg-muted">
                <input
                  type="radio"
                  name="task-filter-subtasks"
                  checked={filters.has_subtasks === false}
                  onChange={() => handlePartialChange({ has_subtasks: false })}
                />
                <span>Single tasks only</span>
              </label>
            </div>
          </div>
        );
      case "due_date":
        return (
          <div className="space-y-3">
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">
                Due on or after
              </label>
              <input
                type="date"
                className="w-full rounded-md border border-input bg-background px-2 py-1 text-sm"
                value={filters.due_date_after ?? ""}
                onChange={(e) =>
                  handlePartialChange({
                    due_date_after: e.target.value || undefined,
                  })
                }
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">
                Due on or before
              </label>
              <input
                type="date"
                className="w-full rounded-md border border-input bg-background px-2 py-1 text-sm"
                value={filters.due_date_before ?? ""}
                onChange={(e) =>
                  handlePartialChange({
                    due_date_before: e.target.value || undefined,
                  })
                }
              />
            </div>
          </div>
        );
      case "created_date":
        return (
          <div className="space-y-3">
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">
                Created on or after
              </label>
              <input
                type="date"
                className="w-full rounded-md border border-input bg-background px-2 py-1 text-sm"
                value={filters.created_after ?? ""}
                onChange={(e) =>
                  handlePartialChange({
                    created_after: e.target.value || undefined,
                  })
                }
              />
            </div>
            <div className="space-y-1">
              <label className="text-xs font-medium text-muted-foreground">
                Created on or before
              </label>
              <input
                type="date"
                className="w-full rounded-md border border-input bg-background px-2 py-1 text-sm"
                value={filters.created_before ?? ""}
                onChange={(e) =>
                  handlePartialChange({
                    created_before: e.target.value || undefined,
                  })
                }
              />
            </div>
          </div>
        );
      default:
        return null;
    }
  };

  return (
    <div ref={rootRef} className="relative inline-flex text-left">
      <button
        type="button"
        data-testid="filter-panel-trigger"
        className="inline-flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
        onClick={() => setOpen((prev) => !prev)}
      >
        <Filter className="h-4 w-4 text-slate-500" />
        <span>Filter</span>
        {activeCount > 0 ? (
          <span className="ml-1 rounded-full bg-[#3CCED7] px-2 py-0.5 text-xs font-semibold text-white">
            {activeCount}
          </span>
        ) : null}
      </button>

      {open && (
        <div className="absolute z-50 mt-2 w-[520px] rounded-md border bg-popover shadow-md">
          <div className="flex">
            <div className="w-40 border-r bg-muted/40 py-2">
              {filterList.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  data-testid={`task-filter-tab-${item.key}`}
                  className={`flex w-full items-center justify-between px-3 py-1.5 text-left text-sm ${
                    shownKey === item.key
                      ? "bg-background font-medium text-foreground"
                      : "text-muted-foreground hover:bg-muted"
                  }`}
                  onClick={() => setActiveKey(item.key)}
                >
                  <span>{item.label}</span>
                  {getFieldCount(item.key) > 0 ? (
                    <span className="ml-2 rounded-full bg-slate-200 px-2 py-0.5 text-xs font-semibold text-slate-700">
                      {getFieldCount(item.key)}
                    </span>
                  ) : null}
                </button>
              ))}
            </div>
            <div className="flex-1 p-3">
              <div className="mb-3 flex items-center justify-between">
                <div className="text-sm font-medium text-foreground">
                  {filterList.find((f) => f.key === shownKey)?.label}
                </div>
                <div className="flex gap-2">
                  <Button
                    type="button"
                    variant="ghost"
                    className="text-xs text-muted-foreground"
                    onClick={() => {
                      onClearAll();
                      setOpen(false);
                    }}
                  >
                    Clear all
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    className="text-xs"
                    onClick={() => setOpen(false)}
                  >
                    Close
                  </Button>
                </div>
              </div>
              {renderRightPanel()}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
