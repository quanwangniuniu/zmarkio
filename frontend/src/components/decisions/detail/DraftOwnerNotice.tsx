import { resolveUserName } from '@/components/decisions/detail/DecisionPropertiesAside';
import type { ProjectMember } from '@/components/decisions/detail/hooks/useProjectRole';

interface Props {
  ownerId: number | null | undefined;
  members: ProjectMember[];
}

export default function DraftOwnerNotice({ ownerId, members }: Props) {
  return (
    <p className="rounded-md bg-gray-50 px-3 py-2 text-xs text-gray-600 ring-1 ring-gray-100">
      Owned by {resolveUserName(members, ownerId)}. Options, reasoning and signals are read-only.
    </p>
  );
}
