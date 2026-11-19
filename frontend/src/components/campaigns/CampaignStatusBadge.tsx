import { Badge } from "@/components/ui/badge";
import { CampaignStatus } from "@/types/campaign";
import { CAMPAIGN_STATUS } from "@/lib/campaigns/campaignConfig";
import { useOptionConfigVersion } from "@/lib/orgCustomization/configLoader";
import { optionColorStyle } from "@/lib/orgCustomization/optionConfig";

interface CampaignStatusBadgeProps {
  status: CampaignStatus;
  className?: string;
}

const statusVariant: Record<CampaignStatus, "default" | "secondary" | "outline" | "destructive"> = {
  PLANNING: "secondary",
  TESTING: "default",
  SCALING: "default",
  OPTIMIZING: "default",
  PAUSED: "outline",
  COMPLETED: "secondary",
  ARCHIVED: "outline",
};

export default function CampaignStatusBadge({ status, className }: CampaignStatusBadgeProps) {
  useOptionConfigVersion();
  const key = statusVariant[status] ? status : 'PLANNING';
  return (
    <Badge variant={statusVariant[key]} className={className} style={optionColorStyle(CAMPAIGN_STATUS.colors[key])}>
      {CAMPAIGN_STATUS.labels[key]}
    </Badge>
  );
}

