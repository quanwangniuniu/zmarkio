export type DraftStatus = 'draft' | 'published' | 'archived';

export interface NotionBlockAction {
  id: number;
  action_type: string;
  label: string;
  icon: string | null;
  is_enabled: boolean;
  order: number;
  created_at: string;
}

export type NotionBlockType =
  | 'text'
  | 'rich_text'
  | 'heading'
  | 'list'
  | 'numbered_list'
  | 'todo_list'
  | 'table'
  | 'quote'
  | 'code'
  | 'divider'
  | 'image'
  | 'video'
  | 'audio'
  | 'file'
  | 'web_bookmark';

export interface NotionContentBlockRecord {
  id?: string;
  type: NotionBlockType | string;
  content: Record<string, any>;
  order?: number;
  [key: string]: any;
}

export interface NotionContentBlock {
  id: number;
  draft: number;
  block_type: NotionBlockType | string;
  content: Record<string, any>;
  order: number;
  actions: NotionBlockAction[];
  text_content: string;
  created_at: string;
  updated_at: string;
}

export interface DraftSummary {
  id: number;
  slug?: string;
  title: string;
  user_email: string;
  status: DraftStatus | string;
  content_blocks_count: number;
  created_at: string;
  updated_at: string;
}

export interface DraftDetail {
  id: number;
  slug?: string;
  title: string;
  user: number;
  user_email: string;
  status: DraftStatus | string;
  content_blocks: NotionContentBlockRecord[];
  blocks: NotionContentBlock[];
  content_blocks_count: number;
  created_at: string;
  updated_at: string;
  is_deleted: boolean;
  project_id: number | null;
  project_slug: string | null;
}

export interface DraftProjectLink {
  project_id: number;
  project_slug: string;
}

export interface DraftPayload {
  title: string;
  status: DraftStatus | string;
  content_blocks: NotionContentBlockRecord[];
  change_summary?: string;
}

export interface EditorBlock {
  id: string;
  type: NotionBlockType | string;
  html: string;
  language?: string;
}

