import { Conversation, ConversationDetail } from '@/types/csmConversation';

export type QualityRating = 'good' | 'needs_improvement' | 'poor';

export const QUALITY_RATINGS: QualityRating[] = ['good', 'needs_improvement', 'poor'];

export const QUALITY_RATING_LABELS: Record<QualityRating, string> = {
  good: 'Good',
  needs_improvement: 'Needs Improvement',
  poor: 'Poor',
};

export interface ConversationQualityReview {
  id: number;
  conversation: number;
  rating: QualityRating;
  rating_display: string;
  comment: string;
  reviewer: number | null;
  reviewer_name: string;
  reviewed_at: string;
  agent_user: number | null;
  agent_name: string;
  queue: number | null;
  organisation: number | null;
  /** Present only on the annotate response: true when the review was created. */
  created?: boolean;
}

export interface QualityConversationRow extends Conversation {
  assigned_to_user_id: number | null;
  message_count: number;
  review_count: number;
  latest_rating: QualityRating | null;
  my_review: ConversationQualityReview | null;
}

export interface QualityConversationDetail extends ConversationDetail {
  reviews: ConversationQualityReview[];
  my_review: ConversationQualityReview | null;
}

/** The date range applies either to when a conversation started or when it was reviewed. */
export type QualityDateBasis = 'review' | 'conversation';

export type QualityBucket = 'day' | 'week' | 'month';

export interface QualityFilters {
  date_from?: string;
  date_to?: string;
  /** Auth user ids, plus the sentinel 'unassigned'. */
  agent: (number | 'unassigned')[];
  queue: number[];
  channel: string[];
  customer: number[];
  customer_search?: string;
  tag: string[];
  status: string[];
  date_basis?: QualityDateBasis;
  bucket?: QualityBucket;
}

export const EMPTY_QUALITY_FILTERS: QualityFilters = {
  agent: [],
  queue: [],
  channel: [],
  customer: [],
  tag: [],
  status: [],
};

/** Every option carries how many conversations in scope it accounts for. */
export interface QualityFilterOptions {
  organisations: { id: number; name: string }[];
  queues: {
    id: number;
    name: string;
    organisation: number | null;
    is_active: boolean;
    conversation_count: number;
  }[];
  agents: { user_id: number; name: string; email: string; conversation_count: number }[];
  /** Conversations with no assigned agent, for the Unassigned option. */
  unassigned_count: number;
  channels: { value: string; label: string; conversation_count: number }[];
  statuses: { value: string; label: string; conversation_count: number }[];
  tags: { value: string; conversation_count: number }[];
  customers: { id: number; name: string; email: string; conversation_count: number }[];
}

export interface QualityReportRatingRow {
  rating: QualityRating;
  rating_display: string;
  count: number;
  pct: number;
}

export interface QualityReportAgentRow {
  agent_user_id: number | null;
  agent_name: string;
  total: number;
  good: number;
  needs_improvement: number;
  poor: number;
}

export interface QualityReportDateRow {
  bucket: string | null;
  total: number;
  good: number;
  needs_improvement: number;
  poor: number;
}

export interface QualityReport {
  filters_echo: Record<string, unknown> & { bucket: QualityBucket; date_basis: QualityDateBasis };
  totals: {
    reviews: number;
    conversations_reviewed: number;
    conversations_in_scope: number;
    coverage_pct: number;
  };
  by_rating: QualityReportRatingRow[];
  by_agent: QualityReportAgentRow[];
  by_date: QualityReportDateRow[];
  generated_at: string;
}

export interface QualityConversationPage {
  results: QualityConversationRow[];
  count: number;
}
