// Chat API client
import api, { UPLOAD_TIMEOUT_MS } from '../api';
import {
  formatFileSize,
  getFileTypeFromMime,
} from './attachmentApi';
export { formatFileSize, getFileTypeFromMime };
import type {
  Chat,
  ChatParticipant,
  ChannelVisibility,
  ChatStarRow,
  Message,
  MessageAttachment,
  CreateChatRequest,
  CreateChatResponse,
  SendMessageRequest,
  SendMessageResponse,
  ForwardBatchRequest,
  ForwardBatchResponse,
  GetChatsParams,
  GetMessagesParams,
  PaginatedResponse,
  SearchMessagesParams,
  SearchMessagesResponse,
} from '@/types/chat';
import type { TiptapJSONContent } from '@/types/comment';

const ATTACHMENT_FILE_SIZE_LIMITS = {
  image: 10 * 1024 * 1024,
  video: 25 * 1024 * 1024,
  document: 20 * 1024 * 1024,
} as const;

const ALLOWED_ATTACHMENT_IMAGE_MIME_PREFIX = 'image/';
const ALLOWED_ATTACHMENT_MIME_TYPES = new Set([
  'application/pdf',
  'application/msword',
  'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'application/vnd.ms-excel',
  'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'application/vnd.ms-powerpoint',
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
]);

const SUPPORTED_ATTACHMENT_DOCUMENT_LABEL =
  'images, PDF, Word, Excel, and PowerPoint files';

export function isAllowedAttachmentMimeType(mimeType: string): boolean {
  const normalizedMimeType = mimeType.trim().toLowerCase();
  return (
    normalizedMimeType.startsWith(ALLOWED_ATTACHMENT_IMAGE_MIME_PREFIX) ||
    ALLOWED_ATTACHMENT_MIME_TYPES.has(normalizedMimeType)
  );
}

export function buildUnsupportedAttachmentMessage(mimeType: string): string {
  const normalizedMimeType = mimeType.trim() || 'unknown';
  return `Unsupported file type "${normalizedMimeType}". Accepted formats: ${SUPPORTED_ATTACHMENT_DOCUMENT_LABEL}.`;
}

export function validateFile(file: File): { isValid: boolean; error?: string } {
  const mimeType = file.type.trim().toLowerCase();
  if (!isAllowedAttachmentMimeType(mimeType)) {
    return {
      isValid: false,
      error: buildUnsupportedAttachmentMessage(mimeType),
    };
  }

  const fileType = getFileTypeFromMime(mimeType);
  const maxSize = ATTACHMENT_FILE_SIZE_LIMITS[fileType];
  if (file.size > maxSize) {
    const maxMB = maxSize / (1024 * 1024);
    return {
      isValid: false,
      error: `File too large. Maximum size for ${fileType} is ${maxMB} MB`,
    };
  }

  return { isValid: true };
}

export function getAttachmentUploadErrorMessage(error: unknown, fallbackMimeType?: string): string {
  const responseData = (error as { response?: { data?: Record<string, unknown> } })?.response?.data;
  const mimeType = typeof responseData?.mime_type === 'string'
    ? responseData.mime_type
    : fallbackMimeType;

  if (responseData?.code === 'unsupported_mime_type' && mimeType) {
    return buildUnsupportedAttachmentMessage(mimeType);
  }

  if (typeof responseData?.error === 'string' && responseData.error.trim()) {
    return responseData.error;
  }

  if ((error as Error)?.message) {
    return (error as Error).message;
  }

  if (mimeType) {
    return buildUnsupportedAttachmentMessage(mimeType);
  }

  return 'Failed to upload attachment';
}

export async function uploadAttachment(
  file: File,
  onProgress?: (progress: number) => void,
): Promise<MessageAttachment> {
  const validation = validateFile(file);
  if (!validation.isValid) {
    throw new Error(validation.error || 'Unsupported file type');
  }

  const formData = new FormData();
  formData.append('file', file);

  const response = await api.post('/api/chat/attachments/', formData, {
    // Attachments can include large files (video); the shared client's 10s
    // default is too short for an upload over a slow connection.
    timeout: UPLOAD_TIMEOUT_MS,
    headers: {
      'Content-Type': 'multipart/form-data',
    },
    onUploadProgress: (progressEvent) => {
      if (progressEvent.total && onProgress) {
        const progress = Math.round((progressEvent.loaded * 100) / progressEvent.total);
        onProgress(progress);
      }
    },
  });

  return response.data;
}

// ==================== Chat Endpoints ====================

const PROJECT_DRAFT_KEY_PREFIX = (projectId: number | string) => `chat_draft_v2_${projectId}_`;

function pruneProjectChatDrafts(projectId: number | string, chats: Chat[]) {
  if (typeof window === 'undefined') return;

  const prefix = PROJECT_DRAFT_KEY_PREFIX(projectId);
  const activeChatIds = new Set(chats.map((chat) => chat.id));

  try {
    for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
      const key = window.localStorage.key(index);
      if (!key?.startsWith(prefix)) continue;

      const chatId = Number(key.slice(prefix.length));
      if (!activeChatIds.has(chatId)) {
        window.localStorage.removeItem(key);
      }
    }
  } catch {
    // localStorage can be unavailable in private browsing or restricted contexts.
  }
}

/**
 * Get all chats for the current user, optionally filtered by project
 */
export const getChats = async (params?: GetChatsParams): Promise<PaginatedResponse<Chat>> => {
  const response = await api.get('/api/chat/chats/', { params });
  const data = response.data as PaginatedResponse<Chat>;
  if (params?.project_id && !params.type && !params.offset && data.next === null) {
    pruneProjectChatDrafts(params.project_id, data.results);
  }
  return data;
};

/**
 * Get a specific chat by slug (canonical) or legacy numeric id via resolve endpoint.
 */
export const getChat = async (chatKey: number | string): Promise<Chat> => {
  const response = await api.get(`/api/chat/chats/${chatKey}/`);
  return response.data;
};

/** Resolve legacy numeric chat pk to slug (bookmarks / old notifications). */
export const resolveLegacyChatSlug = async (chatId: number): Promise<string> => {
  const response = await api.get<{ slug: string }>('/api/chat/chats/legacy-id-slug/', {
    params: { id: chatId },
  });
  return response.data.slug;
};

/**
 * Create a new chat (private or group)
 */
export const createChat = async (data: CreateChatRequest): Promise<CreateChatResponse> => {
  // Transform project_id to project (backend expects 'project' field)
  const payload = {
    type: data.type,
    project: data.project_id, // Backend expects 'project' not 'project_id'
    participant_ids: data.participant_ids,
    ...(data.name && { name: data.name }), // Only include name if provided
  };
  
  const response = await api.post('/api/chat/chats/', payload);
  return response.data;
};

/**
 * Delete a chat
 */
export const deleteChat = async (chatId: number): Promise<void> => {
  await api.delete(`/api/chat/chats/${chatId}/`);
};

/**
 * Leave a chat (soft-removes the current user from participants).
 * Backend DELETE on the chat maps to ChatService.leave_chat for the requester.
 */
export const leaveChat = async (chatId: number): Promise<void> => {
  await api.delete(`/api/chat/chats/${chatId}/`);
};

// ==================== Pinned Messages ====================

export interface PinnedMessageRow {
  id: number;
  chat: number;
  pinned_by: { id: number; username: string; email: string } | null;
  created_at: string;
  message: Pick<Message, 'id' | 'sender' | 'content' | 'created_at' | 'parent_message_id'> & {
    chat: number;
  };
}

export const listPins = async (chatSlug: string): Promise<PinnedMessageRow[]> => {
  const response = await api.get(`/api/chat/chats/${chatSlug}/pins/`);
  return response.data;
};

export interface ChatFileRow {
  id: number;
  original_filename: string;
  file_type: string; // 'image' | 'video' | 'document' | 'audio'
  file_size: number;
  file_url: string;
  created_at: string;
  message_id: number;
  uploader: { id: number; username: string; email: string } | null;
}

export const listChatFiles = async (chatId: number, page = 1): Promise<{ results: ChatFileRow[]; total: number }> => {
  const response = await api.get(`/api/chat/chats/${chatId}/files/`, { params: { page, page_size: 25 } });
  return response.data;
};

export const pinMessage = async (chatSlug: string, messageId: number): Promise<PinnedMessageRow> => {
  const response = await api.post(`/api/chat/chats/${chatSlug}/pin/`, { message_id: messageId });
  return response.data;
};

export const unpinMessage = async (chatSlug: string, messageId: number): Promise<void> => {
  await api.delete(`/api/chat/chats/${chatSlug}/pin/${messageId}/`);
};

// ==================== Browse channels ====================

export interface BrowseChannelRow {
  id: number;
  slug: string;
  name: string;
  topic: string;
  description: string;
  visibility: ChannelVisibility;
  participant_count: number;
  is_member: boolean;
}

export const browseChannels = async (projectId: number | string): Promise<BrowseChannelRow[]> => {
  const response = await api.get('/api/chat/chats/browse/', { params: { project_id: projectId } });
  return response.data;
};

// ==================== Saved messages ====================

export interface SavedMessageRow {
  id: number;
  message: Message;
  chat_id?: number | null;
  project_id?: number | null;
  chat_name?: string | null;
  chat_type?: string | null;
  created_at: string;
}

export const listSavedMessages = async (): Promise<SavedMessageRow[]> => {
  const response = await api.get('/api/chat/saved/');
  // Backend returns paginated response { count, next, results }
  return response.data.results ?? response.data;
};

export const saveMessage = async (messageId: number): Promise<SavedMessageRow> => {
  const response = await api.post('/api/chat/saved/', { message_id: messageId });
  return response.data;
};

export const unsaveMessage = async (savedId: number): Promise<void> => {
  await api.delete(`/api/chat/saved/${savedId}/`);
};

// ==================== Notification settings ====================

export const updateNotificationSettings = async (
  chatId: number,
  data: { is_muted?: boolean; muted_until?: string | null; notification_level?: 'all' | 'mentions' | 'none' }
): Promise<{ is_muted: boolean; muted_until: string | null; notification_level: string }> => {
  const response = await api.patch(`/api/chat/chats/${chatId}/notification_settings/`, data);
  return response.data;
};

// ==================== Channel Details ====================

export const updateChatDetails = async (
  chatId: number,
  data: { name?: string; topic?: string; description?: string; visibility?: ChannelVisibility }
): Promise<Chat> => {
  const response = await api.patch(`/api/chat/chats/${chatId}/update_details/`, data);
  return response.data;
};

// ==================== Starred chats ====================

export const listStarredChats = async (projectId: number | string): Promise<ChatStarRow[]> => {
  const response = await api.get('/api/chat/starred/', { params: { project_id: projectId } });
  return response.data;
};

export const starChat = async (chatId: number): Promise<ChatStarRow> => {
  const response = await api.post('/api/chat/starred/', { chat_id: chatId });
  return response.data;
};

export const unstarChat = async (chatId: number): Promise<void> => {
  await api.delete(`/api/chat/starred/${chatId}/`);
};

export const reorderStarredChats = async (
  projectId: number | string,
  chatIds: number[]
): Promise<void> => {
  await api.post('/api/chat/starred/reorder/', {
    project_id: projectId,
    chat_ids: chatIds,
  });
};

/**
 * Add a participant to a group chat
 */
export const addParticipant = async (chatSlug: string, userId: number): Promise<ChatParticipant> => {
  const response = await api.post(`/api/chat/chats/${chatSlug}/add_participant/`, {
    user_id: userId,
  });
  return response.data;
};

/**
 * Remove a participant from a group chat
 */
export const removeParticipant = async (chatRef: number | string, userId: number): Promise<void> => {
  await api.post(`/api/chat/chats/${chatRef}/remove_participant/`, {
    user_id: userId,
  });
};

export const updateParticipantManager = async (
  chatId: number,
  userId: number,
  isManager: boolean
): Promise<ChatParticipant> => {
  const response = await api.patch(`/api/chat/chats/${chatId}/manager/`, {
    user_id: userId,
    is_manager: isManager,
  });
  return response.data;
};

// ==================== Message Endpoints ====================

/**
 * Get messages for a specific chat with cursor-based pagination
 */
export const getMessages = async (params: GetMessagesParams): Promise<{
  results: Message[];
  next_cursor: string | null;
  prev_cursor: string | null;
  page_size: number;
}> => {
  // Transform params to match backend API (page_size instead of limit)
  const queryParams: Record<string, any> = {
    chat_id: params.chat_id,
  };
  
  if (params.limit) {
    queryParams.page_size = params.limit;
  }
  if (params.before) {
    queryParams.before = params.before;
  }
  if (params.after) {
    queryParams.after = params.after;
  }
  
  const response = await api.get('/api/chat/messages/', { params: queryParams });
  return response.data;
};

/**
 * Get a specific message by ID
 */
export const getMessage = async (messageId: number): Promise<Message> => {
  const response = await api.get(`/api/chat/messages/${messageId}/`);
  return response.data;
};

/**
 * Send a new message
 */
export const sendMessage = async (data: SendMessageRequest): Promise<SendMessageResponse> => {
  // Transform chat_id to chat (backend expects 'chat' field)
  const payload: Record<string, any> = {
    chat: data.chat_id, // Backend expects 'chat' not 'chat_id'
    content: data.content,
  };

  // Include attachment_ids if present
  if (data.attachment_ids && data.attachment_ids.length > 0) {
    payload.attachment_ids = data.attachment_ids;
  }

  // Include reply_to_id if present (quote reply)
  if (data.reply_to_id) {
    payload.reply_to_id = data.reply_to_id;
  }

  // Include rich_body if present
  if (data.rich_body != null) {
    payload.rich_body = data.rich_body;
  }

  // Include mention_ids if present
  if (data.mention_ids && data.mention_ids.length > 0) {
    payload.mention_ids = data.mention_ids;
  }

  // Include parent_message_id if this is a thread reply
  if (data.parent_message_id) {
    payload.parent_message_id = data.parent_message_id;
  }

  if (data.client_message_id) {
    payload.client_message_id = data.client_message_id;
  }

  const response = await api.post('/api/chat/messages/', payload);
  return response.data;
};

// ── Thread API ────────────────────────────────────────────────────────────────

export const getThreadReplies = async (rootMessageId: number): Promise<{ results: Message[] }> => {
  const response = await api.get(`/api/chat/messages/${rootMessageId}/thread_replies/`);
  return response.data;
};

export const markThreadAsRead = async (rootMessageId: number): Promise<void> => {
  await api.post(`/api/chat/messages/${rootMessageId}/mark_thread_as_read/`);
};

export const editMessage = async (
  messageId: number,
  content: string,
  richBody?: TiptapJSONContent | null,
  mentionIds?: number[]
): Promise<Message> => {
  const payload: Record<string, any> = { content };
  if (richBody !== undefined) {
    payload.rich_body = richBody;
  }
  if (mentionIds !== undefined) {
    payload.mention_ids = mentionIds;
  }
  const response = await api.patch(`/api/chat/messages/${messageId}/`, payload);
  return response.data;
};

/**
 * Mark a message as read
 */
export const markMessageAsRead = async (messageId: number): Promise<Message> => {
  const response = await api.post(`/api/chat/messages/${messageId}/mark_as_read/`);
  return response.data;
};

/**
 * Mark all messages in a chat as read (via backend endpoint)
 */
export const markChatAsRead = async (chatSlug: string): Promise<void> => {
  await api.post(`/api/chat/chats/${chatSlug}/mark_as_read/`);
};

/**
 * Forward multiple messages to multiple chats/users
 */
export const forwardMessagesBatch = async (
  data: ForwardBatchRequest
): Promise<ForwardBatchResponse> => {
  const payload = {
    source_chat_id: data.source_chat_id,
    source_message_ids: data.source_message_ids,
    target_chat_ids: data.target_chat_ids || [],
    target_user_ids: data.target_user_ids || [],
  };

  const response = await api.post('/api/chat/messages/forward_batch/', payload);
  return response.data;
};

// ==================== Helper Functions ====================

/**
 * Check if a private chat already exists between two users
 */
export const findPrivateChat = async (
  projectId: number | string,
  otherUserId: number
): Promise<Chat | null> => {
  try {
    const response = await getChats({
      project_id: projectId,
      type: 'private',
      limit: 100,
    });
    
    // Find chat with the specific user
    const existingChat = response.results.find(chat => {
      return chat.participants.some(p => p.user.id === otherUserId);
    });
    
    return existingChat || null;
  } catch (error) {
    console.error('Error finding private chat:', error);
    return null;
  }
};

/**
 * Get unread message count
 * @param chatId - Optional. If provided, returns unread count for specific chat.
 *                 If not provided, returns total unread count across ALL chats/projects.
 */
export const getUnreadCount = async (chatId?: number): Promise<number> => {
  try {
    const params = chatId ? { chat_id: chatId } : {};
    const response = await api.get('/api/chat/messages/unread_count/', { params });
    return response.data.unread_count || 0;
  } catch (error) {
    console.error('Error getting unread count:', error);
    return 0;
  }
};

// ==================== Reaction Endpoints ====================

/**
 * Add or toggle a reaction on a message
 * If the user already has this reaction, it will be removed (toggle behavior)
 */
export const addReaction = async (
  messageId: number,
  emoji: string
): Promise<{ status: 'added' | 'removed'; message: Message }> => {
  const response = await api.post(`/api/chat/messages/${messageId}/react/`, {
    emoji,
  });
  return response.data;
};

/**
 * Remove a specific reaction from a message
 */
export const removeReaction = async (
  messageId: number,
  emoji: string
): Promise<{ status: 'removed'; message: Message }> => {
  const response = await api.delete(
    `/api/chat/messages/${messageId}/react/${encodeURIComponent(emoji)}/`
  );
  return response.data;
};

// ==================== Reminder Endpoints ====================

/**
 * Set or update a reminder for a message
 */
export const setMessageReminder = async (
  messageId: number,
  remindAt: Date,
  note?: string
): Promise<{ status: 'created' | 'updated'; reminder: { id: number; remind_at: string; note: string } }> => {
  const response = await api.post(`/api/chat/messages/${messageId}/remind/`, {
    remind_at: remindAt.toISOString(),
    note: note || '',
  });
  return response.data;
};

/**
 * Cancel a reminder for a message
 */
export const cancelMessageReminder = async (
  messageId: number
): Promise<{ status: 'cancelled' }> => {
  const response = await api.delete(`/api/chat/messages/${messageId}/cancel_remind/`);
  return response.data;
};

// ==================== Revoke/Delete Endpoints ====================

/**
 * Revoke a message (within 2 minutes of sending)
 * The message will be marked as revoked and show a notice to all users
 */
export const revokeMessage = async (
  messageId: number
): Promise<{ status: 'revoked'; message: Message }> => {
  const response = await api.post(`/api/chat/messages/${messageId}/revoke/`);
  return response.data;
};

/**
 * Delete a message for everyone.
 * The backend keeps a tombstone so the timeline does not collapse.
 */
export const deleteMessage = async (
  messageId: number
): Promise<{ status: 'deleted'; message: Message }> => {
  const response = await api.delete(`/api/chat/messages/${messageId}/`);
  return response.data;
};

/**
 * Hide a message for the current user only (personal hide, does not affect others).
 * POST /api/chat/messages/{messageId}/hide/
 */
export const hideMessage = async (
  messageId: number
): Promise<{ status: 'hidden'; message: Message }> => {
  const response = await api.post(`/api/chat/messages/${messageId}/hide/`);
  return response.data;
};

/**
 * Route a preview thumbnail through our own backend (MED-279).
 *
 * Loading og:image directly would hand every viewer's IP to the third-party host.
 * The backend only proxies images a guarded fetch already recorded, and decides the
 * content type by sniffing the bytes rather than trusting the remote host's claim.
 */
export const linkPreviewImageUrl = (imageUrl: string): string =>
  `/api/chat/link-preview-image/?url=${encodeURIComponent(imageUrl)}`;

/**
 * Dismiss a message's link preview card for the current user only (MED-279).
 * POST /api/chat/messages/{messageId}/hide_link_preview/
 */
export const hideMessageLinkPreview = async (
  messageId: number
): Promise<{ status: 'link_preview_hidden'; message: Message }> => {
  const response = await api.post(`/api/chat/messages/${messageId}/hide_link_preview/`);
  return response.data;
};

/**
 * Forward multiple messages to multiple chats/users in batch.
 * POST /api/chat/messages/forward_batch/
 */
export const forwardBatch = async (
  data: ForwardBatchRequest
): Promise<ForwardBatchResponse> => {
  const response = await api.post('/api/chat/messages/forward_batch/', data);
  return response.data;
};

// ==================== Scheduled Messages ====================

export interface ScheduledMessageRow {
  id: number;
  chat: number;
  content: string;
  rich_body: object | null;
  attachment_ids: number[];
  mention_ids: number[];
  reply_to: number | null;
  scheduled_at: string;
  status: 'pending' | 'sending' | 'sent' | 'cancelled' | 'failed';
  task_id: string;
  created_at: string;
}

export interface CreateScheduledMessageRequest {
  chat_id: number;
  content?: string;
  rich_body?: object | null;
  attachment_ids?: number[];
  mention_ids?: number[];
  reply_to_id?: number | null;
  scheduled_at: string; // ISO 8601
}

export const createScheduledMessage = async (
  data: CreateScheduledMessageRequest
): Promise<ScheduledMessageRow> => {
  const response = await api.post('/api/chat/scheduled/', data);
  return response.data;
};

export const listScheduledMessages = async (chatId: number): Promise<ScheduledMessageRow[]> => {
  const response = await api.get('/api/chat/scheduled/', { params: { chat_id: chatId } });
  return response.data.results ?? response.data;
};

export const cancelScheduledMessage = async (id: number): Promise<void> => {
  await api.delete(`/api/chat/scheduled/${id}/`);
};

// Export all functions as a single API object (optional alternative style)
const chatApi = {
  getChats,
  getChat,
  createChat,
  deleteChat,
  leaveChat,
  listStarredChats,
  starChat,
  unstarChat,
  reorderStarredChats,
  addParticipant,
  removeParticipant,
  getMessages,
  getMessage,
  sendMessage,
  forwardMessagesBatch,
  markMessageAsRead,
  markChatAsRead,
  findPrivateChat,
  getUnreadCount,
  addReaction,
  removeReaction,
  setMessageReminder,
  cancelMessageReminder,
  revokeMessage,
  deleteMessage,
};

export default chatApi;

// ==================== Search ====================

export const searchMessages = async (params: SearchMessagesParams): Promise<SearchMessagesResponse> => {
  const response = await api.get('/api/chat/search/', { params });
  return response.data;
};
