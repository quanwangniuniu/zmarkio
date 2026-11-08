'use client';

import { Plus, Search } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

interface TeamAdminToolbarProps {
  search: string;
  onSearchChange: (value: string) => void;
  onCreateClick: () => void;
}

/** Search box and "Create Team" action shown to organization admins. */
export default function TeamAdminToolbar({ search, onSearchChange, onCreateClick }: TeamAdminToolbarProps) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="relative w-full sm:max-w-sm">
        <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" aria-hidden="true" />
        <Input
          type="search"
          value={search}
          onChange={(e) => onSearchChange(e.target.value)}
          placeholder="Search teams"
          aria-label="Search teams"
          className="bg-white pl-9"
        />
      </div>
      <Button className="bg-indigo-600 text-white hover:bg-indigo-700" onClick={onCreateClick}>
        <Plus aria-hidden="true" />
        Create Team
      </Button>
    </div>
  );
}
