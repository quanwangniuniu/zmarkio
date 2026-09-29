import type { Metadata } from 'next';
import PublicKPIShareView from '@/components/dashboard/kpi/PublicKPIShare';

/**
 * Public Custom KPI share page.
 *
 * Lives in the (public) route group, so it has no sidebar and no login wall.
 * The token in the path is the only credential. The view calls the anonymous
 * share client, which does not attach a JWT.
 */

interface PublicKPISharePageProps {
  params: { token: string };
}

export const metadata: Metadata = {
  title: 'Shared Custom KPIs',
  robots: { index: false, follow: false },
};

export default function PublicKPISharePage({ params }: PublicKPISharePageProps) {
  return (
    <main className="min-h-screen bg-[#f3f6f6]">
      <PublicKPIShareView token={params.token} />
    </main>
  );
}
