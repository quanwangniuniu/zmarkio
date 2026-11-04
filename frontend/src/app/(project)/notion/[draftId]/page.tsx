'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import {
  Copy,
  Download,
  Eye,
  FileUp,
  FileDown,
  Clock,
  ChevronDown,
  MoreHorizontal,
  Trash2,
  Sparkles,
  X,
} from 'lucide-react';
import { toast } from 'react-hot-toast';
import DashboardLayout from '@/components/dashboard/DashboardLayout';
import { ProtectedRoute } from '@/components/auth/ProtectedRoute';
import NotionEditor, { createEmptyBlock } from '@/components/notion/NotionEditor';
import NotionStatusPill from '@/components/notion/NotionStatusPill';
import VersionHistoryPanel from '@/components/notion/VersionHistoryPanel';
import BrandDialog from '@/components/tasks/detail/BrandDialog';
import ConfirmDialog from '@/components/tasks/detail/ConfirmDialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { NotionDraftAPI } from '@/lib/api/notionDraftApi';
import { ProjectAPI, ProjectData } from '@/lib/api/projectApi';
import { useBuildUrl } from '@/lib/buildUrl';
import { GoogleDocListItem, googleDocsApi } from '@/lib/api/googleDocsApi';
import { notionIntegrationApi } from '@/lib/api/notionIntegrationApi';
import { stageDraftContext } from '@/lib/agentLaunchContext';
import { openAgentSidePanel } from '@/lib/agentSidePanelStore';
import type {
  DraftStatus,
  EditorBlock,
  NotionContentBlockRecord,
} from '@/types/notion';

const TODO_STATE_REGEX = /data-todo-state="(checked|unchecked)"/i;
const GOOGLE_DOC_URL_ID_REGEX = /\/document\/(?:u\/\d+\/)?d\/([a-zA-Z0-9_-]+)/i;

const getApiErrorMessage = (error: any, fallback: string) =>
  error?.response?.data?.error ||
  error?.response?.data?.detail ||
  error?.response?.data?.message ||
  fallback;

const extractGoogleDocId = (value: string): string => {
  const trimmed = value.trim();
  if (!trimmed) return '';
  const match = trimmed.match(GOOGLE_DOC_URL_ID_REGEX);
  return match?.[1] || trimmed;
};

const addTodoMarkerIfMissing = (html: string) => {
  if (TODO_STATE_REGEX.test(html)) return html;
  const safeHtml = html && html.trim() ? html : '<br>';
  return `<span data-todo-state="unchecked"></span>${safeHtml}`;
};

const blockFromRecord = (
  record: NotionContentBlockRecord,
  fallbackId: string
): EditorBlock => {
  const content = record?.content ?? {};
  const fromHtml = typeof content.html === 'string' ? content.html : undefined;
  const fromText = typeof content.text === 'string' ? content.text : undefined;

  if (
    record.type === 'image' ||
    record.type === 'video' ||
    record.type === 'audio' ||
    record.type === 'file'
  ) {
    const fileUrl = content.file_url || content.url;
    const filename = content.filename || '';
    if (fileUrl) {
      return createEmptyBlock(record.type, {
        file_url: fileUrl,
        filename,
        file_size: content.file_size,
        content_type: content.content_type,
      });
    }
  }

  if (record.type === 'web_bookmark') {
    const url = content.url;
    if (url) {
      return createEmptyBlock(record.type, {
        url,
        title: content.title || '',
        description: content.description || '',
        favicon: content.favicon || '',
      });
    }
  }

  let fromRichText: string | undefined;
  if (!fromHtml && !fromText) {
    const richText = Array.isArray(content.content)
      ? content.content
      : Array.isArray(content.rich_text)
      ? content.rich_text
      : null;
    if (richText) {
      fromRichText = richText
        .map((item: any) => {
          if (!item) return '';
          if (typeof item === 'string') return item;
          if (item?.text?.content) return item.text.content;
          if (item?.plain_text) return item.plain_text;
          return '';
        })
        .join('');
    }
  }

  const resolvedHtml = fromHtml ?? fromText ?? fromRichText ?? '';
  let normalizedHtml = resolvedHtml;
  if (record.type === 'todo_list') normalizedHtml = addTodoMarkerIfMissing(resolvedHtml);
  if (record.type === 'divider') normalizedHtml = normalizedHtml || '<hr />';

  const result: EditorBlock = {
    id: typeof record.id === 'string' ? record.id : fallbackId,
    type: record.type || 'rich_text',
    html: normalizedHtml,
  };
  if (record.type === 'code' && content.language) {
    result.language = content.language;
  }
  return result;
};

const convertContentBlocksFromApi = (
  records: NotionContentBlockRecord[] | undefined
): EditorBlock[] => {
  if (!records || records.length === 0) return [createEmptyBlock()];
  return records.map((record, index) =>
    blockFromRecord(record, `block_${index}_${Date.now()}`)
  );
};

const convertBlocksToPayload = (
  blocks: EditorBlock[]
): NotionContentBlockRecord[] =>
  blocks.map((block, order) => {
    if (
      block.type === 'image' ||
      block.type === 'video' ||
      block.type === 'audio' ||
      block.type === 'file'
    ) {
      const tempDiv = document.createElement('div');
      tempDiv.innerHTML = block.html;
      const img = tempDiv.querySelector('img');
      const video = tempDiv.querySelector('video');
      const audio = tempDiv.querySelector('audio');
      const link = tempDiv.querySelector('a');
      const fileUrl =
        img?.getAttribute('src') ||
        video?.getAttribute('src') ||
        audio?.getAttribute('src') ||
        link?.getAttribute('href') ||
        '';
      const filename =
        img?.getAttribute('alt') || link?.textContent?.trim() || '';
      return {
        id: block.id,
        type: block.type,
        order,
        content: { file_url: fileUrl, filename },
      };
    }
    if (block.type === 'web_bookmark') {
      const tempDiv = document.createElement('div');
      tempDiv.innerHTML = block.html;
      const link = tempDiv.querySelector('a');
      const url = link?.getAttribute('href') || '';
      const titleEl = tempDiv.querySelector('.font-medium');
      const descEl = tempDiv.querySelector('.text-sm');
      const faviconEl = tempDiv.querySelector('img');
      return {
        id: block.id,
        type: block.type,
        order,
        content: {
          url,
          title: titleEl?.textContent?.trim() || '',
          description: descEl?.textContent?.trim() || '',
          favicon: faviconEl?.getAttribute('src') || '',
        },
      };
    }
    if (block.type === 'code') {
      return {
        id: block.id,
        type: block.type,
        order,
        content: {
          html: block.html,
          language: block.language || 'plain',
        },
      };
    }
    return {
      id: block.id,
      type: block.type || 'rich_text',
      order,
      content: {
        html: block.type === 'divider' ? block.html || '<hr />' : block.html,
      },
    };
  });

const buildSnapshot = (title: string, status: string, blocks: EditorBlock[]) =>
  JSON.stringify({
    title,
    status,
    blocks: blocks.map((block) => ({
      id: block.id,
      type: block.type,
      html: block.html,
      ...(block.language !== undefined && { language: block.language }),
    })),
  });

function NotionV2DetailContent() {
  const params = useParams<{ draftId: string }>();
  const router = useRouter();
  const buildUrl = useBuildUrl();
  const draftIdParam = params?.draftId;
  // Resource lookups are slug-only; keep the route value as an opaque string.
  const draftId = useMemo(() => {
    if (!draftIdParam || !draftIdParam.trim()) return null;
    return draftIdParam;
  }, [draftIdParam]);

  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [title, setTitle] = useState('Untitled');
  const [status, setStatus] = useState<DraftStatus>('draft');
  const [blocks, setBlocks] = useState<EditorBlock[]>([createEmptyBlock()]);
  const [isSaving, setIsSaving] = useState(false);
  const [projects, setProjects] = useState<ProjectData[]>([]);
  const [linkedProjectId, setLinkedProjectId] = useState('');
  const [linkingProject, setLinkingProject] = useState(false);
  const [hasChanges, setHasChanges] = useState(false);
  const [lastEditedAt, setLastEditedAt] = useState<Date | null>(null);

  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  const [versionPanelOpen, setVersionPanelOpen] = useState(false);
  const [pendingDelete, setPendingDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const [isImportModalOpen, setIsImportModalOpen] = useState(false);
  const [importDocumentId, setImportDocumentId] = useState('');
  const [googleDocsListLoading, setGoogleDocsListLoading] = useState(false);
  const [googleDocsDocuments, setGoogleDocsDocuments] = useState<GoogleDocListItem[]>([]);
  const [googleDocsListError, setGoogleDocsListError] = useState<string | null>(null);
  const [googleDocsImportBusy, setGoogleDocsImportBusy] = useState(false);
  const [googleDocsExportBusy, setGoogleDocsExportBusy] = useState(false);
  const [isNotionImportModalOpen, setIsNotionImportModalOpen] = useState(false);
  const [isNotionExportModalOpen, setIsNotionExportModalOpen] = useState(false);
  const [notionImportPageRef, setNotionImportPageRef] = useState('');
  const [notionExportParentPageRef, setNotionExportParentPageRef] = useState('');
  const [notionImportBusy, setNotionImportBusy] = useState(false);
  const [notionExportBusy, setNotionExportBusy] = useState(false);

  const snapshotRef = useRef<string>(buildSnapshot(title, status, blocks));

  const syncSnapshot = useCallback(
    (nextTitle: string, nextStatus: string, nextBlocks: EditorBlock[]) => {
      snapshotRef.current = buildSnapshot(nextTitle, nextStatus, nextBlocks);
      setHasChanges(false);
    },
    []
  );

  const loadDraft = useCallback(async () => {
    if (!draftId) return;
    setIsLoading(true);
    setLoadError(null);
    try {
      const draft = await NotionDraftAPI.getDraft(draftId);
      const nextBlocks = convertContentBlocksFromApi(draft.content_blocks || []);
      const nextTitle = draft.title || 'Untitled';
      const nextStatus = (draft.status as DraftStatus) || 'draft';
      setBlocks(nextBlocks);
      setTitle(nextTitle);
      setStatus(nextStatus);
      setLinkedProjectId(draft.project_id == null ? '' : String(draft.project_id));
      setLastEditedAt(null);
      setHasChanges(false);
      syncSnapshot(nextTitle, nextStatus, nextBlocks);
    } catch (error: any) {
      console.error('Failed to load draft', error);
      const msg =
        error?.response?.data?.detail ||
        error?.response?.data?.message ||
        error?.message ||
        'Failed to load draft';
      setLoadError(msg);
      toast.error(msg);
    } finally {
      setIsLoading(false);
    }
  }, [draftId, syncSnapshot]);

  useEffect(() => {
    if (draftId) {
      loadDraft();
    } else {
      setIsLoading(false);
    }
  }, [draftId, loadDraft]);

  useEffect(() => {
    let cancelled = false;
    ProjectAPI.getProjects()
      .then((list) => {
        if (!cancelled) setProjects(Array.isArray(list) ? list : []);
      })
      .catch((error) => {
        console.error('Failed to load projects', error);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleProjectChange = useCallback(async (nextValue: string) => {
    if (!draftId || linkingProject) return;
    const previous = linkedProjectId;
    setLinkedProjectId(nextValue);
    setLinkingProject(true);
    try {
      if (!nextValue) {
        await NotionDraftAPI.unlinkProject(draftId);
        return;
      }
      const projectId = Number(nextValue);
      if (!Number.isInteger(projectId) || projectId < 1) {
        throw new Error('Project id is missing');
      }
      await NotionDraftAPI.linkProject(draftId, projectId);
    } catch (error) {
      console.error('Failed to update draft project', error);
      setLinkedProjectId(previous);
      toast.error('Could not update the project for this draft.');
    } finally {
      setLinkingProject(false);
    }
  }, [draftId, linkedProjectId, linkingProject]);

  useEffect(() => {
    const snapshot = buildSnapshot(title, status, blocks);
    const changed = snapshot !== snapshotRef.current;
    setHasChanges(changed);
    if (changed) setLastEditedAt(new Date());
  }, [title, status, blocks]);

  const lastEditedLabel = useMemo(() => {
    if (!lastEditedAt) return null;
    const delta = Date.now() - lastEditedAt.getTime();
    const seconds = Math.floor(delta / 1000);
    const minutes = Math.floor(seconds / 60);
    const hours = Math.floor(minutes / 60);
    const days = Math.floor(hours / 24);
    if (seconds < 60) return 'Edited just now';
    if (minutes < 60) return `Edited ${minutes}m ago`;
    if (hours < 24) return `Edited ${hours}h ago`;
    return `Edited ${days}d ago`;
  }, [lastEditedAt]);

  const handleSave = useCallback(async () => {
    if (!draftId) return;
    setIsSaving(true);
    try {
      const updated = await NotionDraftAPI.updateDraft(draftId, {
        title: title.trim() || 'Untitled',
        status,
        content_blocks: convertBlocksToPayload(blocks),
      });
      const nextTitle = updated.title || 'Untitled';
      const nextStatus = (updated.status as DraftStatus) || 'draft';
      const nextBlocks = convertContentBlocksFromApi(updated.content_blocks);
      setTitle(nextTitle);
      setStatus(nextStatus);
      setBlocks(nextBlocks);
      syncSnapshot(nextTitle, nextStatus, nextBlocks);
      setLastEditedAt(null);
      toast.success('Draft saved');
    } catch (error: any) {
      console.error('Failed to save draft', error);
      toast.error(error?.response?.data?.detail || 'Failed to save draft');
    } finally {
      setIsSaving(false);
    }
  }, [blocks, draftId, status, syncSnapshot, title]);

  const handleDuplicate = useCallback(async () => {
    if (!draftId) return;
    try {
      const created = await NotionDraftAPI.duplicateDraft(draftId);
      if (!created?.id) throw new Error('Duplicated draft id missing');
      toast.success('Draft duplicated');
      router.push(buildUrl(`/notion/${created.slug}`));
    } catch (error: any) {
      console.error('Failed to duplicate draft', error);
      toast.error(error?.response?.data?.detail || 'Failed to duplicate draft');
    }
  }, [draftId, router]);

  const handleExportJson = useCallback(async () => {
    if (!draftId) return;
    try {
      const blob = await NotionDraftAPI.exportDraft(draftId);
      const safeName =
        (title || 'draft').replace(/[^\w\s-]/g, '').trim().replace(/\s+/g, '_') || 'draft';
      const url = window.URL.createObjectURL(new Blob([blob], { type: 'application/json' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = `${safeName}_export.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(url);
      toast.success('Draft exported');
    } catch (error: any) {
      console.error('Failed to export draft', error);
      toast.error(error?.response?.data?.detail || 'Failed to export draft');
    }
  }, [draftId, title]);

  const handleConfirmDelete = useCallback(async () => {
    if (!draftId) return;
    setDeleting(true);
    try {
      await NotionDraftAPI.deleteDraft(draftId);
      toast.success('Draft deleted');
      router.push(buildUrl('/notion'));
    } catch (error: any) {
      console.error('Failed to delete draft', error);
      toast.error(error?.response?.data?.detail || 'Failed to delete draft');
    } finally {
      setDeleting(false);
      setPendingDelete(false);
    }
  }, [draftId, router]);

  const handleVersionRestored = useCallback(() => {
    loadDraft();
    toast.success('Version restored');
  }, [loadDraft]);

  const htmlToPlainText = useCallback((html: string) => {
    if (typeof window === 'undefined') return html;
    const temp = document.createElement('div');
    temp.innerHTML = html;
    return (temp.textContent || temp.innerText || '').trim();
  }, []);

  const plainTextToHtml = useCallback((text: string) => {
    const escaped = text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    return escaped.replace(/\n/g, '<br>');
  }, []);

  const handleOpenImportGoogleDoc = useCallback(() => {
    setImportDocumentId('');
    setGoogleDocsDocuments([]);
    setGoogleDocsListError(null);
    setIsImportModalOpen(true);
  }, []);

  const handleOpenImportNotion = useCallback(() => {
    setNotionImportPageRef('');
    setIsNotionImportModalOpen(true);
  }, []);

  const handleOpenExportNotion = useCallback(() => {
    setNotionExportParentPageRef('');
    setIsNotionExportModalOpen(true);
  }, []);

  useEffect(() => {
    if (!isImportModalOpen) return;
    let cancelled = false;
    const loadDocuments = async () => {
      setGoogleDocsListLoading(true);
      try {
        const docs = await googleDocsApi.listDocuments(30);
        if (!cancelled) {
          setGoogleDocsDocuments(docs);
          setGoogleDocsListError(null);
        }
      } catch (error: any) {
        if (!cancelled) {
          setGoogleDocsDocuments([]);
          setGoogleDocsListError(
            error?.response?.data?.error ||
              'Failed to load Google Docs files. You can paste a Doc URL/ID.'
          );
        }
      } finally {
        if (!cancelled) setGoogleDocsListLoading(false);
      }
    };
    loadDocuments();
    return () => {
      cancelled = true;
    };
  }, [isImportModalOpen]);

  const handleConfirmImportGoogleDoc = useCallback(async () => {
    const normalizedId = extractGoogleDocId(importDocumentId);
    if (!normalizedId) {
      toast.error('Select a document or paste a Google Doc URL/ID.');
      return;
    }
    setGoogleDocsImportBusy(true);
    try {
      const payload = await googleDocsApi.importDocument(normalizedId);
      const importedTitle = payload?.title || title;
      const importedContent = payload?.content || '';
      const importedHtml = payload?.content_html || plainTextToHtml(importedContent);
      const block = createEmptyBlock('rich_text');
      block.html = importedHtml;
      setTitle(importedTitle);
      setBlocks([block]);
      setLastEditedAt(new Date());
      setHasChanges(true);
      setIsImportModalOpen(false);
      toast.success('Imported. Click Save to persist.');
    } catch (error: any) {
      toast.error(error?.response?.data?.error || 'Failed to import Google Doc.');
    } finally {
      setGoogleDocsImportBusy(false);
    }
  }, [importDocumentId, plainTextToHtml, title]);

  const handleExportGoogleDoc = useCallback(async () => {
    if (!draftId) {
      toast.error('Save the draft first.');
      return;
    }
    setGoogleDocsExportBusy(true);
    try {
      const content = blocks
        .map((block) => {
          if (block.type === 'divider') return '----------------';
          return htmlToPlainText(block.html || '');
        })
        .filter((line) => line.length > 0)
        .join('\n\n');
      const payload = await googleDocsApi.exportRawContent(title || 'Untitled', content);
      if (payload?.url) window.open(payload.url, '_blank');
      toast.success('Exported to Google Docs.');
    } catch (error: any) {
      toast.error(error?.response?.data?.error || 'Failed to export Google Doc.');
    } finally {
      setGoogleDocsExportBusy(false);
    }
  }, [blocks, draftId, htmlToPlainText, title]);

  const handleConfirmImportNotion = useCallback(async () => {
    const page = notionImportPageRef.trim();
    if (!draftId) {
      toast.error('Open a saved draft before importing.');
      return;
    }
    if (!page) {
      toast.error('Paste a Notion page URL or page ID.');
      return;
    }
    if (hasChanges) {
      toast.error('Save your current changes before importing from Notion.');
      return;
    }

    setNotionImportBusy(true);
    try {
      const result = await notionIntegrationApi.importPage({
        page,
        draft_id: draftId,
      });
      setIsNotionImportModalOpen(false);
      await loadDraft();
      toast.success(`Imported from Notion: ${result.draft.title || 'Untitled'}`);
    } catch (error: any) {
      toast.error(getApiErrorMessage(error, 'Failed to import Notion page.'));
    } finally {
      setNotionImportBusy(false);
    }
  }, [draftId, hasChanges, loadDraft, notionImportPageRef]);

  const handleConfirmExportNotion = useCallback(async () => {
    const parentPageId = notionExportParentPageRef.trim();
    if (!draftId) {
      toast.error('Open a saved draft before exporting.');
      return;
    }
    if (!parentPageId) {
      toast.error('Paste the Notion parent page URL or page ID.');
      return;
    }
    if (hasChanges) {
      toast.error('Save your current changes before exporting to Notion.');
      return;
    }

    setNotionExportBusy(true);
    try {
      const result = await notionIntegrationApi.exportDraft({
        draft_id: draftId,
        parent_page_id: parentPageId,
        title: title || 'Untitled',
      });
      setIsNotionExportModalOpen(false);
      if (result.url) window.open(result.url, '_blank');
      toast.success('Exported to Notion.');
    } catch (error: any) {
      toast.error(getApiErrorMessage(error, 'Failed to export to Notion.'));
    } finally {
      setNotionExportBusy(false);
    }
  }, [draftId, hasChanges, notionExportParentPageRef, title]);

  const integrationActionBusy =
    googleDocsImportBusy || googleDocsExportBusy || notionImportBusy || notionExportBusy;

  return (
    <DashboardLayout>
      <div className="-m-5 h-[calc(100vh-3rem)] flex flex-col bg-white overflow-hidden">
        <div className="sticky top-0 z-20 border-b border-gray-100 bg-white/90 backdrop-blur-sm px-6 py-3 flex flex-col gap-2">
          {/* Title on its own row so the action cluster cannot shrink it to 0 width. */}
          <div className="flex min-w-0 items-center gap-3">
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Untitled"
              disabled={isLoading}
              aria-label="Draft title"
              className="min-w-0 flex-1 border-0 focus:outline-none focus:ring-0 text-2xl font-semibold text-gray-900 placeholder:text-gray-300 bg-transparent"
            />
            <NotionStatusPill status={status} />
            {lastEditedLabel && (
              <span
                aria-live="polite"
                className="text-xs text-gray-500 whitespace-nowrap hidden sm:inline"
              >
                {lastEditedLabel}
              </span>
            )}
          </div>
          <div className="flex flex-wrap items-center justify-end gap-1.5">
            <button
              type="button"
              onClick={() => setIsPreviewOpen(true)}
              disabled={isLoading}
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-md transition disabled:opacity-50"
            >
              <Eye className="w-4 h-4" /> Preview
            </button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  disabled={isLoading || integrationActionBusy}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1.5 text-sm font-medium border border-[#3CCED7]/30 text-[#3CCED7] hover:bg-[#3CCED7]/8 rounded-md transition disabled:opacity-50"
                >
                  {integrationActionBusy ? 'Working…' : 'Import / Export'}
                  <ChevronDown className="w-4 h-4" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuItem onSelect={handleOpenImportGoogleDoc}>
                  <FileUp className="w-4 h-4 mr-2" /> Import from Google Docs
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={handleExportGoogleDoc}>
                  <FileDown className="w-4 h-4 mr-2" /> Export to Google Docs
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={handleOpenImportNotion}>
                  <FileUp className="w-4 h-4 mr-2" /> Import from Notion
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={handleOpenExportNotion}>
                  <FileDown className="w-4 h-4 mr-2" /> Export to Notion
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  type="button"
                  disabled={isLoading}
                  className="h-8 w-8 inline-flex items-center justify-center rounded-md text-gray-600 hover:bg-gray-100 transition disabled:opacity-50"
                  aria-label="More actions"
                >
                  <MoreHorizontal className="w-4 h-4" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-48">
                <DropdownMenuItem onSelect={() => setVersionPanelOpen(true)}>
                  <Clock className="w-4 h-4 mr-2" /> Version history
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={handleDuplicate}>
                  <Copy className="w-4 h-4 mr-2" /> Duplicate
                </DropdownMenuItem>
                <DropdownMenuItem onSelect={handleExportJson}>
                  <Download className="w-4 h-4 mr-2" /> Download JSON
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onSelect={() => setPendingDelete(true)}
                  className="text-red-600 focus:text-red-700 focus:bg-red-50"
                >
                  <Trash2 className="w-4 h-4 mr-2" /> Delete
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <button
              type="button"
              onClick={() => {
                if (!draftId) return;
                // "Open in Agent": attach the draft as context but don't ask
                // anything yet — the user types their own first question.
                stageDraftContext({ draftId, title, autoSend: false });
                openAgentSidePanel();
              }}
              disabled={!draftId}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md border border-violet-200 bg-violet-50 text-violet-800 hover:bg-violet-100 transition disabled:opacity-50 disabled:cursor-not-allowed"
              title="Open this draft in the Agent"
            >
              <Sparkles className="w-4 h-4" />
              Open in Agent
            </button>
            <label className="inline-flex items-center gap-2 text-sm text-gray-600">
              <span className="sr-only">Project</span>
              <select
                aria-label="Project"
                value={linkedProjectId}
                disabled={isLoading || linkingProject || !draftId}
                onChange={(event) => {
                  void handleProjectChange(event.target.value);
                }}
                className="h-8 max-w-[10rem] rounded-md border border-gray-200 bg-white px-2 text-sm text-gray-800 disabled:opacity-50"
              >
                <option value="">No project</option>
                {projects.map((project) => (
                  <option key={String(project.id)} value={String(project.id)}>
                    {project.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="button"
              onClick={handleSave}
              disabled={isLoading || isSaving || !hasChanges}
              className="inline-flex items-center px-4 py-1.5 text-sm font-medium text-white rounded-md bg-gradient-to-r from-[#3CCED7] to-[#A6E661] hover:opacity-95 transition shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isSaving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>

        <div className="flex-1 min-h-0 relative overflow-hidden">
          {isLoading ? (
            <div className="h-full flex items-center justify-center text-gray-400">
              Loading draft…
            </div>
          ) : (!draftId || loadError) ? (
            <div className="h-full flex flex-col items-center justify-center text-center text-gray-400 space-y-3 px-16">
              <h2 className="text-3xl font-semibold text-gray-900">Draft not found</h2>
              <p className="max-w-md text-sm text-gray-600">
                This draft may have been deleted or the URL is invalid.
              </p>
              <button
                type="button"
                onClick={() => router.push(buildUrl('/notion'))}
                className="px-4 py-2 text-sm font-medium text-white rounded-md bg-gradient-to-r from-[#3CCED7] to-[#A6E661] hover:opacity-95"
              >
                Back to list
              </button>
            </div>
          ) : (
            <NotionEditor blocks={blocks} setBlocks={setBlocks} draftId={draftId} />
          )}
        </div>
      </div>

      <VersionHistoryPanel
        isOpen={versionPanelOpen}
        onClose={() => setVersionPanelOpen(false)}
        draftId={draftId}
        onRestored={handleVersionRestored}
      />

      <BrandDialog
        open={isPreviewOpen}
        onOpenChange={setIsPreviewOpen}
        title={title || 'Untitled'}
        subtitle="Preview mode"
      >
        <div className="max-h-[70vh] overflow-y-auto space-y-5 pr-1">
          {blocks.length === 0 ? (
            <div className="text-sm text-gray-400 italic">No content yet.</div>
          ) : (
            blocks.map((block) => {
              if (block.type === 'divider') {
                return (
                  <div key={block.id} className="w-full border-t border-gray-300 my-4" />
                );
              }
              if (!block.html) {
                return (
                  <div key={block.id} className="text-gray-300 italic text-sm">
                    Empty block
                  </div>
                );
              }
              return (
                <div
                  key={block.id}
                  className="prose prose-gray max-w-none"
                  dangerouslySetInnerHTML={{ __html: block.html }}
                />
              );
            })
          )}
        </div>
      </BrandDialog>

      <ConfirmDialog
        open={pendingDelete}
        onOpenChange={setPendingDelete}
        title="Delete this draft?"
        description={`"${title || 'Untitled'}" will be moved to trash.`}
        confirmLabel={deleting ? 'Deleting…' : 'Delete'}
        destructive
        busy={deleting}
        onConfirm={handleConfirmDelete}
      />

      <Dialog open={isImportModalOpen} onOpenChange={setIsImportModalOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Import Google Doc</DialogTitle>
            <DialogDescription>
              Select a Google Doc from the list. If listing is unavailable, paste a Doc URL or ID.
            </DialogDescription>
          </DialogHeader>
          {googleDocsListError ? (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              {googleDocsListError}
            </div>
          ) : null}
          <div className="max-h-[320px] overflow-y-auto rounded-md border border-gray-200">
            {googleDocsListLoading ? (
              <div className="px-4 py-8 text-center text-sm text-gray-500">
                Loading Google Docs…
              </div>
            ) : googleDocsDocuments.length === 0 ? (
              <div className="px-4 py-8 text-center text-sm text-gray-500">
                No Google Docs files found for this connected account.
              </div>
            ) : (
              <ul className="divide-y divide-gray-100">
                {googleDocsDocuments.map((doc) => {
                  const selected = importDocumentId === doc.id;
                  return (
                    <li key={doc.id}>
                      <button
                        type="button"
                        onClick={() => setImportDocumentId(doc.id)}
                        className={`w-full px-4 py-3 text-left transition-colors ${
                          selected ? 'bg-[#3CCED7]/8' : 'hover:bg-gray-50'
                        }`}
                      >
                        <div className="text-sm font-medium text-gray-900">{doc.name}</div>
                        <div className="mt-1 text-xs text-gray-500">
                          {doc.modified_time
                            ? `Updated ${new Date(doc.modified_time).toLocaleString()}`
                            : 'No modified time'}
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
          <div className="space-y-2">
            <label htmlFor="google-doc-url-or-id" className="text-sm font-medium text-gray-700">
              Google Doc URL or ID
            </label>
            <input
              id="google-doc-url-or-id"
              value={importDocumentId}
              onChange={(event) => setImportDocumentId(event.target.value)}
              placeholder="https://docs.google.com/document/d/... or document ID"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/20"
            />
          </div>
          <DialogFooter>
            <button
              type="button"
              onClick={() => setIsImportModalOpen(false)}
              className="rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
              disabled={googleDocsImportBusy}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirmImportGoogleDoc}
              className="rounded-md bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-3 py-2 text-sm font-medium text-white hover:opacity-95 disabled:opacity-60"
              disabled={googleDocsImportBusy || !importDocumentId.trim()}
            >
              {googleDocsImportBusy ? 'Importing…' : 'Import'}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isNotionImportModalOpen} onOpenChange={setIsNotionImportModalOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Import Notion Page</DialogTitle>
            <DialogDescription>
              Paste a Notion page URL or ID. The imported content will replace this saved draft.
            </DialogDescription>
          </DialogHeader>
          {hasChanges ? (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              Save your current changes before importing from Notion.
            </div>
          ) : null}
          <div className="space-y-2">
            <label htmlFor="notion-page-url-or-id" className="text-sm font-medium text-gray-700">
              Notion page URL or ID
            </label>
            <input
              id="notion-page-url-or-id"
              value={notionImportPageRef}
              onChange={(event) => setNotionImportPageRef(event.target.value)}
              placeholder="https://www.notion.so/... or page ID"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/20"
            />
            <p className="text-xs text-gray-500">
              The page must be shared with the connected MediaJira integration in Notion.
            </p>
          </div>
          <DialogFooter>
            <button
              type="button"
              onClick={() => setIsNotionImportModalOpen(false)}
              className="rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
              disabled={notionImportBusy}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirmImportNotion}
              className="rounded-md bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-3 py-2 text-sm font-medium text-white hover:opacity-95 disabled:opacity-60"
              disabled={notionImportBusy || hasChanges || !notionImportPageRef.trim()}
            >
              {notionImportBusy ? 'Importing…' : 'Import'}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={isNotionExportModalOpen} onOpenChange={setIsNotionExportModalOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Export to Notion</DialogTitle>
            <DialogDescription>
              Paste the Notion parent page where MediaJira should create a new page.
            </DialogDescription>
          </DialogHeader>
          {hasChanges ? (
            <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
              Save your current changes before exporting to Notion.
            </div>
          ) : null}
          <div className="space-y-2">
            <label htmlFor="notion-parent-page-url-or-id" className="text-sm font-medium text-gray-700">
              Parent page URL or ID
            </label>
            <input
              id="notion-parent-page-url-or-id"
              value={notionExportParentPageRef}
              onChange={(event) => setNotionExportParentPageRef(event.target.value)}
              placeholder="https://www.notion.so/... or parent page ID"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm focus:border-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/20"
            />
            <p className="text-xs text-gray-500">
              The connected integration needs access to this parent page in Notion.
            </p>
          </div>
          <DialogFooter>
            <button
              type="button"
              onClick={() => setIsNotionExportModalOpen(false)}
              className="rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
              disabled={notionExportBusy}
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleConfirmExportNotion}
              className="rounded-md bg-gradient-to-r from-[#3CCED7] to-[#A6E661] px-3 py-2 text-sm font-medium text-white hover:opacity-95 disabled:opacity-60"
              disabled={notionExportBusy || hasChanges || !notionExportParentPageRef.trim()}
            >
              {notionExportBusy ? 'Exporting…' : 'Export'}
            </button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </DashboardLayout>
  );
}

export default function NotionV2DetailPage() {
  return (
    <ProtectedRoute>
      <NotionV2DetailContent />
    </ProtectedRoute>
  );
}
