import api from '../api';
import {
  DraftDetail,
  DraftPayload,
  DraftProjectLink,
  DraftSummary,
  NotionContentBlockRecord,
} from '@/types/notion';

const BASE_PATH = '/api/notion/api';

const toQuery = (path: string) => `${BASE_PATH}${path}`;

export const NotionDraftAPI = {
  listDrafts: async (): Promise<DraftSummary[]> => {
    const response = await api.get<any>(toQuery('/drafts/'));
    // Handle paginated response (Django REST Framework default)
    if (response.data && Array.isArray(response.data.results)) {
      return response.data.results;
    }
    // Handle direct array response
    if (Array.isArray(response.data)) {
      return response.data;
    }
    // Fallback to empty array
    return [];
  },

  getDraft: async (draftId: number | string): Promise<DraftDetail> => {
    const response = await api.get<DraftDetail>(toQuery(`/drafts/${draftId}/`));
    return response.data;
  },

  createDraft: async (payload: DraftPayload): Promise<DraftDetail> => {
    const response = await api.post<DraftDetail>(toQuery('/drafts/'), payload);
    return response.data;
  },

  updateDraft: async (draftId: number | string, payload: DraftPayload): Promise<DraftDetail> => {
    const response = await api.put<DraftDetail>(toQuery(`/drafts/${draftId}/`), payload);
    return response.data;
  },

  deleteDraft: async (draftId: number | string): Promise<void> => {
    await api.delete(toQuery(`/drafts/${draftId}/`));
  },

  linkProject: async (draftId: number | string, projectId: number): Promise<DraftProjectLink> => {
    const response = await api.post<DraftProjectLink>(toQuery(`/drafts/${draftId}/project/`), {
      project_id: projectId,
    });
    return response.data;
  },

  unlinkProject: async (draftId: number | string): Promise<void> => {
    await api.delete(toQuery(`/drafts/${draftId}/project/`));
  },

  addBlock: async (draftId: number | string, block: NotionContentBlockRecord) => {
    const response = await api.post(toQuery(`/drafts/${draftId}/add_block/`), block);
    return response.data;
  },

  updateBlock: async (
    draftId: number | string,
    blockId: string,
    block: NotionContentBlockRecord
  ) => {
    const response = await api.put(toQuery(`/drafts/${draftId}/update_block/`), {
      block_id: blockId,
      block_data: block,
    });
    return response.data;
  },

  deleteBlock: async (draftId: number | string, blockId: string) => {
    const response = await api.delete(toQuery(`/drafts/${draftId}/delete_block/`), {
      data: { block_id: blockId },
    });
    return response.data;
  },

  duplicateDraft: async (draftId: number | string, title?: string): Promise<DraftDetail> => {
    const response = await api.post<DraftDetail>(
      toQuery(`/drafts/${draftId}/duplicate/`),
      title ? { title } : undefined
    );
    return response.data;
  },

  exportDraft: async (draftId: number | string) => {
    const response = await api.get(
      toQuery(`/drafts/${draftId}/export/`),
      { responseType: 'blob' }
    );
    return response.data;
  },

  listRevisions: async (draftId: number | string) => {
    const response = await api.get(toQuery(`/drafts/${draftId}/revisions/`));
    return response.data;
  },

  restoreRevision: async (revisionId: number | string) => {
    const response = await api.post(toQuery(`/revisions/${revisionId}/restore/`));
    return response.data;
  },

  // Media upload API
  uploadMedia: async (file: File, mediaType?: string, draftId?: number | string) => {
    const formData = new FormData();
    formData.append('file', file);
    if (mediaType) {
      formData.append('media_type', mediaType);
    }
    if (draftId) {
      formData.append('draft_id', draftId.toString());
    }
    const response = await api.post(toQuery('/media/upload/'), formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
    return response.data;
  },

  // Web bookmark API
  createWebBookmark: async (url: string, title?: string, description?: string, favicon?: string, draftId?: number | string) => {
    const response = await api.post(toQuery('/web-bookmark/'), {
      url,
      title,
      description,
      favicon,
      draft_id: draftId,
    });
    return response.data;
  },
};

