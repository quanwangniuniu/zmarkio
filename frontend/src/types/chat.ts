// Chat feature TypeScript types
// Based on OpenAPI spec: /openapi/openapi_spec/chat.yaml

import type { DragEvent, MouseEvent, ReactNode } from 'react';
import type { TiptapJSONContent } from '@/types/comment';

// ==================== User Types ====================

export interface User {
  id: number;
  email: string;
  username: string;
  avatar?: string | null;
  is_online?: boolean;
}

export interface UserWithName extends User {
  first_name?: string;
  last_name?: string;
}

// ==================== Chat Types ====================

export type ChatType = 'private' | 'group';
export type ChannelVisibility = 'public' | 'member_invite' | 'manager_invite';

export interface ChatParticipant {
  id: number;
  user: User;
  chat_id: number;
  joined_at: string;
  last_read_at?: string | null;
  is_active?: boolean;
  is_manager?: boolean;
  is_muted?: boolean;
  muted_until?: string | null;
  notification_level?: 'all' | 'mentions' | 'none';
}

export interface Chat {
  id: number;
  slug: string;
  project_id: number | string;
  project?: number | string; // Backend may send this instead of project_id
  type: ChatType;
  name?: string | null;
  topic?: string | null;
  description?: string | null;
  visibility?: ChannelVisibility;
  created_by_id?: number | null;
  created_by?: User | null;
  participants: ChatParticipant[];
  created_at: string;
  updated_at: string;
  last_message?: Message | null;
  unread_count?: number;
  mention_unread_count?: number;
}

// ==================== Message Types ====================

export type MessageStatusType = 'sent' | 'delivered' | 'read';

export interface MessageStatus {
  id: number;
  message_id: number;
  user_id: number;
  status: MessageStatusType;
  delivered_at?: string | null;
  read_at?: string | null;
}

export interface MessageAttachment {
  id: number;
  message: number | null;
  file_type: 'image' | 'video' | 'document';
  file_url: string;
  thumbnail_url: string | null;
  file_size: number;
  file_size_display: string;
  original_filename: string;
  mime_type: string;
  created_at: string;
}

export interface PendingAttachment {
  /** Temporary client-side id. */
  id: string;
  file: File;
  preview?: string;
  progress: number;
  uploading: boolean;
  uploaded?: MessageAttachment;
  error?: string;
}

export interface MissingForwardedAttachment {
  id: number;
  kind: 'audio' | 'video' | 'image' | 'document' | 'unknown';
  original_filename: string;
  file_size_display?: string;
  reason: 'original_deleted';
}

export interface ChatContext {
  id: number;
  slug: string;
  type: ChatType;
  name?: string | null;
}

export interface ChatFileListItem extends MessageAttachment {
  uploader: UserWithName;
  chat: ChatContext | null;
  message_id: number | null;
  /** Root message id when the attachment belongs to a thread reply. */
  thread_root_message_id?: number | null;
  /** True when this attachment was forwarded and its original source message has been deleted. */
  is_orphaned_forward?: boolean;
}

export interface ReactionUser {
  id: number;
  username: string;
}

export interface Reaction {
  emoji: string;
  count: number;
  users: ReactionUser[];
  reacted_by_me: boolean;
}

export interface Message {
  id: number;
  /** Server-assigned monotonic ordering key within this chat. */
  seq?: number;
  chat_id: number;
  chat?: number;  // Backend may send this instead of chat_id
  sender: User;
  content: string;
  is_forwarded?: boolean;
  forwarded_from?: {
    message_id: number | null;
    sender_display: string;
    created_at: string | null;
  } | null;
  reply_to?: {
    id: number;
    sender: User;
    content: string;
    created_at: string | null;
    attachments?: Array<{
      id: number;
      file_type: 'image' | 'video' | 'document';
      original_filename: string | null;
      file_url: string | null;
      mime_type: string | null;
    }>;
  } | null;
  reactions?: Reaction[];
  created_at: string;
  updated_at: string;
  statuses?: MessageStatus[];
  is_read?: boolean;
  is_edited?: boolean;
  is_deleted?: boolean;
  deleted_at?: string | null;
  is_revoked?: boolean;
  revoked_at?: string | null;
  can_revoke?: boolean;
  has_attachments?: boolean;
  attachment_count?: number;
  attachments?: MessageAttachment[];
  missing_forwarded_attachments?: MissingForwardedAttachment[];
  is_hidden_by_me?: boolean;
  /** Tiptap JSON document, present when the message was composed with the rich editor. */
  rich_body?: TiptapJSONContent | null;
  /** IDs of users @-mentioned in the message. */
  mentioned_user_ids?: number[];
  /** ID of the root message when this is a thread reply. Null for root/timeline messages. */
  parent_message_id?: number | null;
  /** Number of thread replies on this root message. */
  thread_reply_count?: number | null;
  /** ISO timestamp of the most recent thread reply. */
  thread_last_reply_at?: string | null;
  /** Up to 4 participant stubs for the thread avatar stack. */
  thread_participants?: Array<{ id: number; username: string; email: string; avatar?: string | null }>;
  /** True when there are thread replies the current user has not seen. */
  has_unread_thread_replies?: boolean;
  /** Server-resolved OpenGraph card for the message's first URL (MED-279). */
  link_preview?: MessageLinkPreview | null;
  /** Client idempotency key while a send is still in the outbox. */
  client_message_id?: string;
  /** Local delivery state for optimistic / retried sends. */
  send_status?: OutboxSendStatus;
}

// ==================== Outbox Types ====================

export type OutboxSendStatus = 'pending' | 'sending' | 'failed';

export interface OutboxEntry {
  clientMessageId: string;
  chatId: number;
  content: string;
  richBody?: TiptapJSONContent | null;
  attachmentIds: number[];
  mentionIds?: number[];
  replyToId?: number | null;
  parentMessageId?: number | null;
  status: OutboxSendStatus;
  enqueuedAt: string;
}

export interface OutboxAckCommit {
  client_message_id: string;
  message_id: number;
  // Embedded by the server so reconnect reconciliation needs no per-id REST
  // fetch. Optional so an older/absent payload safely falls back to getMessage.
  message?: Message | null;
}

// ==================== API Request/Response Types ====================

export interface CreateChatRequest {
  type: ChatType;
  project_id: number | string;
  participant_ids: number[];
  name?: string;
}

export interface CreateChatResponse extends Chat {}

export interface SendMessageRequest {
  chat_id: number;
  content: string;
  attachment_ids?: number[];
  reply_to_id?: number | null;
  /** ID of the root message when posting a thread reply. */
  parent_message_id?: number | null;
  /** Tiptap JSON for rich messages. */
  rich_body?: TiptapJSONContent | null;
  /** IDs of @-mentioned users. */
  mention_ids?: number[];
  /** Idempotency key for retried sends after reconnect. */
  client_message_id?: string;
}

export interface SendMessageResponse extends Message {}

export interface ForwardBatchRequest {
  source_chat_id: number;
  source_message_ids: number[];
  target_chat_ids?: number[];
  target_user_ids?: number[];
}

export interface ForwardFailureItem {
  target_chat_id: number | null;
  target_user_id?: number | null;
  source_message_id: number | null;
  reason: string;
}

export interface ForwardBatchResponse {
  status: 'success' | 'partial_success' | 'failed';
  summary: {
    requested_messages: number;
    forwardable_messages: number;
    target_chats: number;
    attempted_sends: number;
    succeeded_sends: number;
    failed_sends: number;
  };
  resolved: {
    target_chat_ids: number[];
    created_private_chat_ids: number[];
    skipped_message_ids: number[];
  };
  failures: ForwardFailureItem[];
  created_messages?: Message[];
}

export interface GetChatsParams {
  project_id?: number | string;
  type?: ChatType;
  limit?: number;
  offset?: number;
}

export interface GetMessagesParams {
  chat_id: number;
  before?: string; // ISO timestamp for cursor-based pagination
  after?: string;  // ISO timestamp for cursor-based pagination
  limit?: number;
}

// Response type for messages with cursor pagination
export interface MessagesPaginatedResponse {
  results: Message[];
  next_cursor: string | null;
  prev_cursor: string | null;
  page_size: number;
}

export interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface MarkAsReadRequest {
  message_id: number;
}

// ==================== WebSocket Types ====================

export type WebSocketMessageType =
  | 'new_message'
  | 'chat_message'
  | 'message_status_update'
  | 'send_message'
  | 'typing_start'
  | 'typing_stop'
  | 'typing_indicator'
  | 'chat_created'
  | 'in_app_notification'
  | 'reaction_update'
  | 'presence_update'
  | 'presence_snapshot'
  | 'user_session_revoked'
  | 'pong'
  | 'error';

/** In-app notification payload mirrors API NotificationSerializer. */
export interface WebSocketInAppNotificationPayload {
  id: string;
  category: string;
  event_type: string;
  related_object_type: string;
  related_object_id: string;
  title: string;
  body: string;
  is_read: boolean;
  action_url: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface WebSocketReactionPayload {
  message_id: number;
  chat_id: number;
  user: ReactionUser;
  emoji: string;
  action: 'added' | 'removed';
}

export interface WebSocketMessage {
  type: WebSocketMessageType;
  message?: Message;
  chat?: Chat;
  chat_id?: number;
  content?: string;
  status?: MessageStatusType;
  message_id?: number;
  user_id?: number;
  is_online?: boolean;
  version?: number | null;
  users?: Array<{ user_id: number; is_online: boolean; version?: number | null }>;
  is_typing?: boolean;
  error?: string;
  notification?: WebSocketInAppNotificationPayload;
  reaction?: WebSocketReactionPayload;
}

// ==================== Store Types ====================

export interface ChatState {
  // Data
  chatsByProject: Record<number | string, Chat[]>; // Keyed by project_id
  currentChatId: number | null;  // For Messages page
  widgetChatId: number | null;   // For Chat Widget (independent)
  messages: Record<number, Message[]>; // Keyed by chat_id
  outbox: OutboxEntry[];
  pendingAttachmentsByChat: Record<number, PendingAttachment[]>; // Ephemeral upload outbox keyed by chat_id
  unreadCounts: Record<number, number>; // Keyed by chat_id
  capturedUnreadCounts: Record<number, number>; // Snapshot taken at the moment a chat is opened — used for the "New messages" divider
  globalUnreadCount: number; // Total unread across ALL projects
  typingUsersByChat: Record<number, number[]>; // chatId -> userIds currently typing
  presenceByUserId: Record<number, boolean>; // Current online/offline state keyed by user id
  presenceVersionByUserId: Record<number, number>; // Last applied presence event version keyed by user id
  unseenPinChatIds: Record<number, true>; // Chat IDs with a pin update not yet acknowledged by this user
  
  // UI State
  isWidgetOpen: boolean;
  isMessagePageOpen: boolean;
  selectedProjectId: number | string | null;
  widgetProjectId: number | string | null;  // Widget's own project selection
  currentView: 'list' | 'chat';
  widgetView: 'list' | 'chat';     // Widget's own view state
  isLoading: boolean;
  
  // Actions
  setChatsForProject: (projectId: number | string, chats: Chat[]) => void;
  getChatsForProject: (projectId: number | string | null) => Chat[];
  addChat: (chat: Chat) => void;
  removeChat: (chatId: number) => void;
  updateChat: (chatId: number, updates: Partial<Chat>) => void;
  setCurrentChat: (chatId: number | null) => void;
  setWidgetChat: (chatId: number | null) => void;
  setWidgetProjectId: (projectId: number | string | null) => void;
  setWidgetView: (view: 'list' | 'chat') => void;
  
  setMessages: (chatId: number, messages: Message[]) => void;
  addMessage: (chatId: number, message: Message, currentUserId?: number, replaceClientMessageId?: string) => void;
  enqueueOutbox: (entry: OutboxEntry) => void;
  markOutboxSending: (clientMessageId: string) => void;
  markOutboxSent: (clientMessageId: string, message: Message) => void;
  markOutboxFailed: (clientMessageId: string) => void;
  getOutboxDigest: () => string[];
  flushOutbox: () => Promise<void>;
  retryOutboxEntry: (clientMessageId: string) => Promise<void>;
  reconcileOutboxAck: (committed: OutboxAckCommit[]) => Promise<void>;

    prependMessages: (chatId: number, messages: Message[]) => void;
  updateMessage: (messageId: number, updates: Partial<Message>) => void;
  removeMessage: (messageId: number) => void;
  addPendingAttachments: (chatId: number, attachments: PendingAttachment[]) => void;
  updatePendingAttachment: (chatId: number, attachmentId: string, updates: Partial<PendingAttachment>) => void;
  removePendingAttachment: (chatId: number, attachmentId: string) => void;
  clearPendingAttachments: (chatId: number) => void;
  applyReactionUpdate: (messageId: number, emoji: string, action: 'added' | 'removed', user: ReactionUser, currentUserId: number | null) => void;
  applyLinkPreview: (messageId: number, preview: MessageLinkPreview) => void;
  clearLinkPreview: (messageId: number) => void;
  updateUserPresence: (userId: number, isOnline: boolean, version?: number | null) => void;
  setPresenceSnapshot: (users: Array<{ user_id: number; is_online: boolean; version?: number | null }>) => void;
  
  updateUnreadCount: (chatId: number, count: number) => void;
  decrementUnreadCount: (chatId: number) => void;

  // Shared pin alerts
  markChatPinUnseen: (chatId: number) => void;
  clearChatPinUnseen: (chatId: number) => void;

  // Typing indicator actions
  setTypingUser: (chatId: number, userId: number) => void;
  clearTypingUser: (chatId: number, userId: number) => void;
  getTypingUsers: (chatId: number) => number[];
  
  // Global unread count actions
  fetchGlobalUnreadCount: () => Promise<number>;
  setGlobalUnreadCount: (count: number) => void;
  incrementGlobalUnreadCount: () => void;
  decrementGlobalUnreadCount: (amount?: number) => void;
  
  openWidget: () => void;
  closeWidget: () => void;
  setMessagePageOpen: (isOpen: boolean) => void;
  setSelectedProjectId: (projectId: number | string | null) => void;
  setView: (view: 'list' | 'chat') => void;
  
  setLoading: (loading: boolean) => void;
  
  // SSE-driven chat activity signal
  /** Epoch ms bumped whenever an SSE notification for a chat event is received. */
  lastChatActivity: number;
  /** Called by useNotificationSSE when a chat-related event arrives. */
  triggerChatActivity: () => void;

  /** Timestamp bumped when a message is deleted/revoked — signals FilesSidebarView to refetch. */
  filesRefreshAt: number;
  triggerFilesRefresh: () => void;

  // Mention badges: chat IDs where the current user has an unread @-mention
  mentionedChatIds: Record<number, true>;
  addMentionedChat: (chatId: number) => void;
  clearMentionedChat: (chatId: number) => void;

  // Thread panel
  /** ID of the root message whose thread panel is currently open, or null. */
  activeThreadMessageId: number | null;
  setActiveThreadMessageId: (id: number | null) => void;
  /** Thread replies keyed by root message ID. */
  threadReplies: Record<number, Message[]>;
  setThreadReplies: (rootId: number, replies: Message[]) => void;
  addThreadReply: (rootId: number, reply: Message) => void;
  updateThreadReply: (replyId: number, updates: Partial<Message>) => void;

  // Helpers
  getCurrentChat: () => Chat | undefined;
  getCurrentMessages: () => Message[];
  getTotalUnreadCount: () => number;

  // Clears all per-user in-memory state on logout so the next login starts
  // with a clean slate and always picks up fresh counts from the backend.
  clearUserState: () => void;
}

// ==================== Component Props Types ====================

export interface ChatWidgetProps {
  projectId: string;
}

export interface ChatListProps {
  chats: Chat[];
  currentChatId: number | null;
  onSelectChat: (chatId: number) => void;
  onCreateChat: () => void;
  roleByUserId?: Record<number, string>;
}

export interface ChatListItemProps {
  chat: Chat;
  isActive: boolean;
  onClick: () => void;
  roleByUserId?: Record<number, string>;
  /** Show star / unstar control (Messages sidebar). */
  showStarToggle?: boolean;
  isStarred?: boolean;
  onStarToggle?: (e: MouseEvent<HTMLElement>) => void;
  /** HTML5 drag — starred section reordering. */
  draggable?: boolean;
  onDragStart?: (e: DragEvent<HTMLElement>) => void;
  onDragOver?: (e: DragEvent<HTMLElement>) => void;
  onDrop?: (e: DragEvent<HTMLElement>) => void;
}

export interface ChatWindowProps {
  chat: Chat;
  messages: Message[];
  onBack: () => void;
  onSendMessage: (content: string) => void;
  onLoadMore: () => void;
  hasMore: boolean;
  isLoading: boolean;
}

export interface MessageItemProps {
  message: Message;
  isOwnMessage: boolean;
  showSender?: boolean;
  isCompact?: boolean;
  senderRole?: string;
  isSelectMode?: boolean;
  isSelected?: boolean;
  onToggleSelect?: (messageId: number) => void;
  /** When true, visually emphasize this message (e.g. jump target). */
  isHighlighted?: boolean;
  onEdit?: (messageId: number, newContent: string) => void;
  onDelete?: (messageId: number) => void;
  /** When true, show hover actions (e.g. more button). */
  isHovered?: boolean;
  /** Render prop for actions to show in the message action area. */
  renderActions?: () => ReactNode;
  /** Callback when a reaction is clicked (toggle reaction). */
  onReactionClick?: (emoji: string, isReactedByMe: boolean) => void;
  onReactionAdd?: (emoji: string) => void;
  onReactionRemove?: (emoji: string) => void;
  onQuoteReply?: () => void;
  onForwardSingle?: () => void;
  onEnterSelectMode?: () => void;
  onOpenThread?: () => void;
  /** True when this message's thread panel is open. */
  isThreadActive?: boolean;
  onPin?: (messageId: number) => void;
  onSave?: (messageId: number) => void;
  onRemind?: (messageId: number) => void;
  isPinned?: boolean;
  isSaved?: boolean;
  /** Canonical chat slug for copy-link URLs. */
  chatSlug?: string;
}

export interface MessageListProps {
  messages: Message[];
  chatSlug?: string;
  currentUserId: number;
  onLoadMore: () => void;
  hasMore: boolean;
  isLoading: boolean;
  isLoadingMoreMessages?: boolean;
  showSwitchLoadingSkeleton?: boolean;
  roleByUserId?: Record<number, string>;
  isGroupChat?: boolean;
  isSelectMode?: boolean;
  selectedMessageIds?: number[];
  onToggleSelectMessage?: (messageId: number) => void;
  firstUnreadMessageId?: number | null;
  onEditMessage?: (messageId: number, newContent: string) => void;
  onDeleteMessage?: (messageId: number) => void;
  onReactionAdd?: (messageId: number, emoji: string) => void;
  onReactionRemove?: (messageId: number, emoji: string) => void;
  onQuoteReply?: (message: Message) => void;
  onForwardSingle?: (messageId: number) => void;
  onEnterSelectMode?: () => void;
  onOpenThread?: (message: Message) => void;
  /** ID of the message whose thread panel is currently open (highlights the row). */
  activeThreadMessageId?: number | null;
  onPinMessage?: (messageId: number) => void;
  onSaveMessage?: (messageId: number) => void;
  onRemindMessage?: (messageId: number) => void;
  pinnedMessageIds?: Set<number>;
  savedMessageIds?: Set<number>;
  /** Route-driven jump target from Files/search deep links. */
  jumpTarget?: {
    messageId: number;
    attachmentId?: number;
    requestId: string;
  } | null;
}

export interface MessageInputProps {
  onSend: (content: string) => void;
  disabled?: boolean;
  /** Drawer-style input: brand top border handled by parent; gradient send button. */
  variant?: 'default' | 'drawer';
  replyingTo?: Message | null;
  onClearReply?: () => void;
}

export interface CreateChatDialogProps {
  isOpen: boolean;
  onClose: () => void;
  projectId: string;
  onChatCreated: (chatId: number, chatSlug?: string) => void;
  /** When set, only group (channel) creation is shown — for Slack-style “Add channel”. */
  variant?: 'default' | 'channel';
}

/** Row from GET /api/chat/starred/?project_id= */
export interface ChatStarRow {
  id: number;
  position: number;
  chat: Chat;
  created_at: string;
  updated_at: string;
}

export interface ParticipantSelectorProps {
  projectId: number | string;
  selectedIds: number[];
  onSelect: (ids: number[]) => void;
  maxSelection?: number;
  currentUserId: number;
  allowSolo?: boolean;
}

// ==================== Project Member Types ====================

export interface ProjectMember {
  id: number;
  user: User;
  project: {
    id: number;
    name: string;
  };
  role: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

// ==================== Link Preview Types ====================

/**
 * Preview served on the message payload — resolved server-side, cached per URL.
 * Distinct from `LinkPreview` below, which is the shape of the on-demand
 * /api/chat/link-preview/ endpoint still used by comments.
 */
export interface MessageLinkPreview {
  url: string;
  title: string | null;
  description: string | null;
  image_url: string | null;
}

export interface LinkPreview {
  url: string;
  title: string | null;
  description: string | null;
  image: string | null;
  site_name: string | null;
  type: string;
}

// ==================== Search Types ====================

export interface MessageSearchSender {
  id: number;
  username: string;
  email: string;
  avatar?: string | null;
}

export interface MessageSearchResult {
  id: number;
  chat_id: number;
  chat_name: string;
  chat_type: 'private' | 'group';
  project_id: number;
  content: string;
  /** HTML snippet with <mark> tags around matched terms */
  highlight: string;
  created_at: string;
  sender: MessageSearchSender;
  has_attachments: boolean;
  attachment_count: number;
}

export interface SearchMessagesParams {
  q: string;
  from_user?: string;
  in_chat?: number;
  has?: 'file' | 'link';
  date_after?: string;
  date_before?: string;
  threads_only?: boolean;
  mentions_me?: string;
  limit?: number;
  offset?: number;
  cursor?: string;
}

export interface SearchMessagesResponse {
  results: MessageSearchResult[];
  total: number;
  q: string;
  next_cursor?: string | null;
}
