'use client';

import type { ReactNode } from 'react';
import { tasksTabLabel, visibleTasksTabs } from '@/lib/tasks/tasksModuleConfig';

export type TasksTab = 'summary' | 'tasks' | 'board' | 'gantt' | 'insights' | 'my-actions' | 'planning' | 'status-reports';

interface TabNavProps {
  active: TasksTab;
  onChange: (tab: TasksTab) => void;
  trailing?: ReactNode;
}

export default function TabNav({ active, onChange, trailing }: TabNavProps) {
  return (
    <div className="mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 pb-3">
      <nav className="flex min-w-0 flex-wrap items-center gap-1">
        {visibleTasksTabs().map((tab) => {
          const isActive = tab === active;
          return (
            <button
              key={tab}
              type="button"
              data-testid={`tab-${tab}`}
              onClick={() => onChange(tab)}
              className={`inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-[13px] font-medium transition ${
                isActive
                  ? 'bg-gray-100 text-gray-900'
                  : 'text-gray-500 hover:bg-gray-50 hover:text-gray-700'
              }`}
            >
              <span
                className={`inline-block h-1.5 w-1.5 rounded-full transition ${
                  isActive ? 'bg-[#3CCED7]' : 'bg-transparent'
                }`}
                aria-hidden
              />
              {tasksTabLabel(tab)}
            </button>
          );
        })}
      </nav>
      {trailing ? (
        <div className="flex w-full shrink-0 flex-wrap items-center justify-end gap-2 sm:w-auto sm:justify-end">
          {trailing}
        </div>
      ) : null}
    </div>
  );
}
