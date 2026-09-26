'use client';

import React, { useState, useCallback, useEffect } from 'react';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import Placeholder from '@tiptap/extension-placeholder';
import CsmConversationAPI, { QuickReplyTemplateAPI } from '@/lib/api/csmConversationApi';
import { useCsmConversationStore } from '@/lib/csmConversationStore';
import type { QuickReplyTemplate } from '@/types/csmConversation';
import { plainTextToParagraphs } from './plainTextToParagraphs';
import { AlertCircle, FileImage, Tag, Search, X, Bold, Italic, List, ListOrdered, LayoutTemplate, ImagePlus } from 'lucide-react';

type ComposerFormat = 'bold' | 'italic' | 'bulletList' | 'orderedList';

const EMPTY_ACTIVE_FORMATS: Record<ComposerFormat, boolean> = {
  bold: false,
  italic: false,
  bulletList: false,
  orderedList: false,
};

interface ConversationComposerProps {
  conversationId: number;
  organisationId?: number | null;
  onTyping?: (isTyping: boolean) => void;
  // Fired after a message sends, so the ticket list can refresh its SLA
  // (an agent's first reply stops the first-response countdown).
  onSent?: () => void;
}

const MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024;
const SUPPORTED_IMAGE_TYPES = [
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/webp',
  'image/heic',
  'image/heif',
  'image/heic-sequence',
  'image/heif-sequence',
];
const SUPPORTED_IMAGE_EXTENSIONS = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'heic', 'heif'];
const SUPPORTED_IMAGE_ACCEPT = [
  ...SUPPORTED_IMAGE_TYPES,
  ...SUPPORTED_IMAGE_EXTENSIONS.map((ext) => `.${ext}`),
].join(',');
const IMAGE_REQUIREMENTS_LABEL = 'PNG, JPG, GIF, WebP, or HEIC up to 10 MB';

function getFileExtension(fileName: string): string {
  return fileName.split('.').pop()?.toLowerCase() ?? '';
}

function formatFileSize(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

// ---------------------------------------------------------------------------
// TemplatePicker
// ---------------------------------------------------------------------------
export function TemplatePicker({
  organisationId,
  onSelect,
  onClose,
}: {
  organisationId: number;
  onSelect: (template: QuickReplyTemplate) => void;
  onClose: () => void;
}) {
  const [templates, setTemplates] = useState<QuickReplyTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [filterTag, setFilterTag] = useState('');
  const [previewId, setPreviewId] = useState<number | null>(null);

  useEffect(() => {
    QuickReplyTemplateAPI.list({ organisation: organisationId })
      .then(setTemplates)
      .catch(() => setTemplates([]))
      .finally(() => setLoading(false));
  }, [organisationId]);

  const allTags = Array.from(new Set(templates.flatMap((t) => t.tags))).sort();

  const filtered = templates.filter((t) => {
    const matchesSearch =
      !search ||
      t.title.toLowerCase().includes(search.toLowerCase()) ||
      t.content.toLowerCase().includes(search.toLowerCase());
    const matchesTag = !filterTag || t.tags.includes(filterTag);
    return matchesSearch && matchesTag;
  });

  const previewTemplate = previewId !== null ? templates.find((t) => t.id === previewId) : null;

  return (
    <div className="border border-gray-200 rounded-xl bg-white shadow-md mb-2 overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-100 bg-gray-50">
        <span className="text-xs font-medium text-gray-600">
          {previewTemplate ? `Preview: ${previewTemplate.title}` : 'Quick Reply Templates'}
        </span>
        <div className="flex items-center gap-1">
          {previewTemplate && (
            <button
              onClick={() => setPreviewId(null)}
              className="text-[10px] px-2 py-0.5 rounded text-gray-500 hover:text-gray-700 hover:bg-gray-100"
            >
              ← Back
            </button>
          )}
          <button onClick={onClose} className="p-0.5 text-gray-400 hover:text-gray-600">
            <X size={14} />
          </button>
        </div>
      </div>

      {previewTemplate ? (
        /* Full preview */
        <div className="p-3 max-h-64 overflow-y-auto flex flex-col gap-3">
          {previewTemplate.tags.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {previewTemplate.tags.map((tag) => (
                <span key={tag} className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 bg-blue-50 text-blue-500 rounded-full">
                  <Tag size={8} />
                  {tag}
                </span>
              ))}
            </div>
          )}
          <p className="text-xs text-gray-700 leading-relaxed whitespace-pre-wrap">{previewTemplate.content}</p>
          <button
            onClick={() => { onSelect(previewTemplate); setPreviewId(null); }}
            className="self-end text-xs px-3 py-1.5 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-medium transition-colors"
          >
            Insert
          </button>
        </div>
      ) : (
        <>
          {/* Filters */}
          <div className="flex gap-2 px-3 py-2 border-b border-gray-100">
            <div className="relative flex-1">
              <Search size={12} className="absolute left-2 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search…"
                className="w-full pl-6 pr-2 py-1 text-xs rounded border border-gray-200 outline-none focus:border-blue-400"
              />
            </div>
            {allTags.length > 0 && (
              <select
                value={filterTag}
                onChange={(e) => setFilterTag(e.target.value)}
                className="text-xs rounded border border-gray-200 px-2 py-1 outline-none focus:border-blue-400 bg-white"
              >
                <option value="">All tags</option>
                {allTags.map((tag) => (
                  <option key={tag} value={tag}>{tag}</option>
                ))}
              </select>
            )}
          </div>

          {/* Template list */}
          <div className="max-h-52 overflow-y-auto">
            {loading ? (
              <div className="px-3 py-4 text-xs text-gray-400 text-center">Loading…</div>
            ) : filtered.length === 0 ? (
              <div className="px-3 py-4 text-center">
                <p className="text-xs font-medium text-gray-500">
                  {search || filterTag ? 'No templates found' : 'No quick reply templates yet'}
                </p>
                <p className="mt-1 text-[11px] leading-relaxed text-gray-400">
                  {search || filterTag
                    ? 'Try another search term or clear the tag filter.'
                    : 'Admins can manage templates in CSM > Templates.'}
                </p>
              </div>
            ) : (
              filtered.map((t) => (
                <div
                  key={t.id}
                  className="flex items-start gap-2 px-3 py-2.5 hover:bg-blue-50 border-b border-gray-50 last:border-0 transition-colors group"
                >
                  <button onClick={() => onSelect(t)} className="flex-1 text-left min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="text-xs font-medium text-gray-800">{t.title}</span>
                      {t.tags.slice(0, 2).map((tag) => (
                        <span key={tag} className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 bg-blue-50 text-blue-500 rounded-full">
                          <Tag size={8} />{tag}
                        </span>
                      ))}
                    </div>
                    <p className="text-xs text-gray-400 line-clamp-2 leading-relaxed">{t.content}</p>
                  </button>
                  <button
                    onClick={() => setPreviewId(t.id)}
                    className="shrink-0 text-[10px] px-1.5 py-0.5 rounded text-gray-400 hover:text-blue-500 hover:bg-blue-50 opacity-0 group-hover:opacity-100 transition-opacity mt-0.5"
                  >
                    Preview
                  </button>
                </div>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ConversationComposer
// ---------------------------------------------------------------------------
export function ConversationComposer({
  conversationId,
  organisationId,
  onTyping,
  onSent,
}: ConversationComposerProps) {
  const [sending, setSending] = useState(false);
  const [showTemplates, setShowTemplates] = useState(false);
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [imagePreview, setImagePreview] = useState<string | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  const [sendError, setSendError] = useState<string | null>(null);
  const [isDraggingImage, setIsDraggingImage] = useState(false);
  const [hasReplyContent, setHasReplyContent] = useState(false);
  const imageInputRef = React.useRef<HTMLInputElement>(null);
  const handleSendRef = React.useRef<() => void>(() => {});
  const addMessage = useCsmConversationStore((s) => s.addMessage);
  const setDraft = useCsmConversationStore((s) => s.setDraft);
  const clearDraft = useCsmConversationStore((s) => s.clearDraft);
  const pendingInsert = useCsmConversationStore((s) => s.pendingComposerInsert);
  const consumeComposerInsert = useCsmConversationStore((s) => s.consumeComposerInsert);
  const draftForConversation = useCsmConversationStore(
    (s) => s.draftsByConversation[conversationId]
  );

  // Shared validation used by both the file picker and drag-and-drop. Mirrors
  // the backend limits so the user gets immediate feedback.
  const acceptImageFile = useCallback((file: File | null | undefined) => {
    if (!file) return;
    const extension = getFileExtension(file.name);
    const isSupportedType = SUPPORTED_IMAGE_TYPES.includes(file.type);
    const isSupportedExtension = SUPPORTED_IMAGE_EXTENSIONS.includes(extension);
    if (!isSupportedType && !isSupportedExtension) {
      setImageError(`Unsupported image format. Upload ${IMAGE_REQUIREMENTS_LABEL}.`);
      if (imageInputRef.current) imageInputRef.current.value = '';
      return;
    }
    if (file.size > MAX_IMAGE_SIZE_BYTES) {
      setImageError(`Image is too large. Upload ${IMAGE_REQUIREMENTS_LABEL}.`);
      if (imageInputRef.current) imageInputRef.current.value = '';
      return;
    }
    setImageError(null);
    setSendError(null);
    setImageFile(file);
    setImagePreview(URL.createObjectURL(file));
  }, []);

  const handleImageSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    acceptImageFile(e.target.files?.[0] ?? null);
  };

  const clearImage = () => {
    setImageFile(null);
    setImagePreview(null);
    setImageError(null);
    setSendError(null);
    if (imageInputRef.current) imageInputRef.current.value = '';
  };

  useEffect(() => {
    return () => {
      if (imagePreview) URL.revokeObjectURL(imagePreview);
    };
  }, [imagePreview]);

  const editor = useEditor({
    extensions: [
      StarterKit,
      Placeholder.configure({ placeholder: 'Type a reply… (Enter to send, Shift+Enter for new line)' }),
    ],
    immediatelyRender: false,
    editorProps: {
      // Enter sends; Shift+Enter inserts a newline. Handling it here (and
      // returning true) stops ProseMirror from also inserting a paragraph
      // break, which previously got sent as a trailing blank line. Enter is
      // ignored while an IME composition is active (e.g. confirming a CJK
      // candidate) so it doesn't send mid-typing.
      handleKeyDown(_view, event) {
        if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
          event.preventDefault();
          handleSendRef.current();
          return true;
        }
        return false;
      },
      attributes: {
        class: 'outline-none text-sm text-gray-900 min-h-[24px] max-h-36 overflow-y-auto prose prose-sm max-w-none [&_p]:my-0 [&_ul]:my-1 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-1 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-0',
      },
    },
    onUpdate({ editor }) {
      const hasText = editor.getText().trim().length > 0;
      setHasReplyContent(hasText);
      onTyping?.(hasText);
    },
  });

  // Restore an in-memory draft (text + image) when this conversation becomes
  // active. Drafts live in the non-persisted store, so this only restores
  // within the current page session — a reload clears everything.
  useEffect(() => {
    if (!editor || !draftForConversation) return;
    const { richBody, imageFile: draftImage, imagePreviewUrl } = draftForConversation;
    if (richBody && !editor.isEmpty) {
      // Only restore text if the editor is still empty (don't clobber a fresh
      // edit started before the restore effect runs).
      return;
    }
    if (richBody) {
      editor.commands.setContent(richBody as Parameters<typeof editor.commands.setContent>[0]);
      setHasReplyContent(editor.getText().trim().length > 0);
    }
    if (draftImage) {
      setImageFile(draftImage);
      if (imagePreviewUrl) setImagePreview(imagePreviewUrl);
    }
    // Run once per (editor, conversationId) mount. draftForConversation is
    // captured at mount; subsequent store edits are ignored to avoid loops.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor, conversationId]);

  // Snapshot the current draft (text + image) into the store on unmount or
  // before the composer is remounted for another conversation. Because the
  // parent keys the composer by activeId, switching conversations always
  // triggers this cleanup — which is how the draft survives the switch.
  useEffect(() => {
    return () => {
      if (!editor) return;
      const richBody = editor.getJSON();
      const hasText = editor.getText().trim().length > 0;
      // Drop the draft entirely once there is nothing to restore; this keeps
      // the store small and means a cleared composer stays cleared on return.
      if (!hasText && !imageFile) {
        clearDraft(conversationId);
        return;
      }
      setDraft(conversationId, {
        richBody: hasText ? richBody : null,
        imageFile,
        imagePreviewUrl: imagePreview,
      });
    };
    // Intentionally snapshot imageFile/imagePreview at cleanup time; re-running
    // on every keystroke would write the store on each character.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor, conversationId]);

  const [activeFormats, setActiveFormats] = useState<Record<ComposerFormat, boolean>>(EMPTY_ACTIVE_FORMATS);

  useEffect(() => {
    if (!editor) {
      setActiveFormats(EMPTY_ACTIVE_FORMATS);
      return;
    }

    const updateActiveFormats = () => {
      setActiveFormats({
        bold: editor.isActive('bold'),
        italic: editor.isActive('italic'),
        bulletList: editor.isActive('bulletList'),
        orderedList: editor.isActive('orderedList'),
      });
    };

    updateActiveFormats();
    editor.on('selectionUpdate', updateActiveFormats);
    editor.on('transaction', updateActiveFormats);

    return () => {
      editor.off('selectionUpdate', updateActiveFormats);
      editor.off('transaction', updateActiveFormats);
    };
  }, [editor]);

  const canSend = hasReplyContent || !!imageFile;

  const handleSend = useCallback(async () => {
    if (!editor) return;
    const plainText = editor.getText().trim();
    if (!plainText && !imageFile) return;
    if (sending) return;

    const richBody = editor.getJSON();
    setSending(true);
    setSendError(null);
    try {
      const msg = await CsmConversationAPI.sendMessage(conversationId, {
        content: plainText,
        rich_body: imageFile ? null : richBody,
        image: imageFile,
      });
      addMessage(conversationId, msg);
      editor.commands.clearContent(true);
      setHasReplyContent(false);
      clearImage();
      clearDraft(conversationId);
      onTyping?.(false);
      onSent?.();
    } catch (err) {
      console.error('Failed to send message', err);
      const detail = (err as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      setSendError(detail ?? 'Failed to send message.');
    } finally {
      setSending(false);
    }
  }, [editor, sending, conversationId, addMessage, onTyping, onSent, imageFile, clearDraft]);

  // Keep the editor's keydown handler pointing at the latest handleSend so the
  // Enter-to-send shortcut never calls a stale closure (e.g. wrong conversation).
  useEffect(() => {
    handleSendRef.current = handleSend;
  }, [handleSend]);

  const handleInsertTemplate = useCallback((template: QuickReplyTemplate) => {
    if (!editor) return;
    if (template.rich_body) {
      // Replace entire editor content with the template's rich body
      editor.commands.setContent(template.rich_body as Parameters<typeof editor.commands.setContent>[0]);
    } else {
      editor.commands.insertContent(template.content);
    }
    setHasReplyContent(editor.getText().trim().length > 0);
    setShowTemplates(false);
    editor.commands.focus();
  }, [editor]);

  // Text pushed from a sibling panel (e.g. a guidance entry's "Insert into
  // reply"). Appended at the end so an in-progress reply is never overwritten.
  // The request is consumed only after a successful insert; while the editor
  // is being torn down it stays pending for the next mounted composer.
  useEffect(() => {
    if (!editor || editor.isDestroyed) return;
    if (!pendingInsert || pendingInsert.conversationId !== conversationId) return;
    editor.commands.focus('end');
    const inserted = editor.commands.insertContent(plainTextToParagraphs(pendingInsert.text));
    if (!inserted) return;
    consumeComposerInsert(pendingInsert.nonce);
    setHasReplyContent(editor.getText().trim().length > 0);
  }, [editor, pendingInsert, conversationId, consumeComposerInsert]);

  return (
    <div
      className="relative border-t border-gray-200 bg-white px-4 py-3"
      onDragOver={(e) => { e.preventDefault(); if (!isDraggingImage) setIsDraggingImage(true); }}
      onDragLeave={(e) => {
        // Ignore dragleave that fires when crossing into a child element.
        if (e.currentTarget.contains(e.relatedTarget as Node)) return;
        setIsDraggingImage(false);
      }}
      onDrop={(e) => {
        e.preventDefault();
        setIsDraggingImage(false);
        acceptImageFile(e.dataTransfer.files?.[0]);
      }}
    >
      {/* Drag-and-drop overlay */}
      {isDraggingImage && (
        <div className="absolute inset-0 z-20 m-2 flex items-center justify-center rounded-xl border-2 border-dashed border-blue-300 bg-blue-50/90 pointer-events-none">
          <span className="flex items-center gap-2 text-sm font-medium text-blue-600">
            <ImagePlus size={16} />
            Drop image to attach
          </span>
        </div>
      )}

      {/* Template picker panel */}
      {showTemplates && organisationId && (
        <TemplatePicker
          organisationId={organisationId}
          onSelect={handleInsertTemplate}
          onClose={() => setShowTemplates(false)}
        />
      )}

      <div className="rounded-xl border border-gray-200 overflow-hidden bg-white shadow-sm">
        {/* Formatting toolbar */}
        <div className="flex items-center gap-0.5 px-2 py-1.5 border-b border-gray-100 bg-gray-50">
          {(
            [
              { cmd: 'toggleBold', active: 'bold', icon: <Bold size={14} />, title: 'Bold' },
              { cmd: 'toggleItalic', active: 'italic', icon: <Italic size={14} />, title: 'Italic' },
            ] as const
          ).map(({ cmd, active, icon, title }) => (
            <button
              key={cmd}
              type="button"
              title={title}
              aria-label={title}
              aria-pressed={activeFormats[active]}
              onMouseDown={(e) => { e.preventDefault(); editor?.chain().focus()[cmd]().run(); }}
              className={`w-7 h-7 flex items-center justify-center rounded transition-colors ${
                activeFormats[active]
                  ? 'bg-blue-100 text-blue-700'
                  : 'text-gray-400 hover:text-gray-700 hover:bg-gray-200'
              }`}
            >
              {icon}
            </button>
          ))}
          <div className="w-px h-4 bg-gray-200 mx-1" />
          {(
            [
              { cmd: 'toggleBulletList', active: 'bulletList', icon: <List size={14} />, title: 'Bullet list' },
              { cmd: 'toggleOrderedList', active: 'orderedList', icon: <ListOrdered size={14} />, title: 'Ordered list' },
            ] as const
          ).map(({ cmd, active, icon, title }) => (
            <button
              key={cmd}
              type="button"
              title={title}
              aria-label={title}
              aria-pressed={activeFormats[active]}
              onMouseDown={(e) => { e.preventDefault(); editor?.chain().focus()[cmd]().run(); }}
              className={`w-7 h-7 flex items-center justify-center rounded transition-colors ${
                activeFormats[active]
                  ? 'bg-blue-100 text-blue-700'
                  : 'text-gray-400 hover:text-gray-700 hover:bg-gray-200'
              }`}
            >
              {icon}
            </button>
          ))}
          <div className="flex-1" />
          {/* Image upload button */}
          <button
            type="button"
            onClick={() => imageInputRef.current?.click()}
            title="Attach image"
            className={`w-7 h-7 flex items-center justify-center rounded transition-colors ${
              imageFile ? 'bg-green-100 text-green-700' : 'text-gray-400 hover:text-gray-700 hover:bg-gray-200'
            }`}
          >
            <ImagePlus size={14} />
          </button>
          <input
            ref={imageInputRef}
            type="file"
            accept={SUPPORTED_IMAGE_ACCEPT}
            className="hidden"
            onChange={handleImageSelect}
          />
          {/* Template button */}
          <button
            type="button"
            onClick={() => setShowTemplates((v) => !v)}
            title="Insert quick reply template"
            className={`w-7 h-7 flex items-center justify-center rounded transition-colors ${
              showTemplates ? 'bg-blue-100 text-blue-700' : 'text-gray-400 hover:text-gray-700 hover:bg-gray-200'
            }`}
          >
            <LayoutTemplate size={14} />
          </button>
        </div>

        {/* Image preview / attachment card */}
        {imageFile && (
          <div className="px-3 pb-2 flex items-start gap-2">
            <div className="relative inline-block">
              {imagePreview ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={imagePreview}
                  alt="preview"
                  className="max-h-32 max-w-[200px] rounded-lg object-cover border border-gray-200"
                  onError={() => setImagePreview(null)}
                />
              ) : (
                <div className="flex max-w-[260px] items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-600">
                  <FileImage className="h-4 w-4 shrink-0 text-gray-400" />
                  <div className="min-w-0">
                    <p className="truncate font-medium text-gray-700">{imageFile.name}</p>
                    <p className="text-gray-400">{formatFileSize(imageFile.size)} - Preview not available</p>
                  </div>
                </div>
              )}
              <button
                type="button"
                onClick={clearImage}
                className="absolute -top-1.5 -right-1.5 w-5 h-5 bg-gray-700 text-white rounded-full flex items-center justify-center hover:bg-red-500 transition-colors"
              >
                <X size={10} />
              </button>
            </div>
          </div>
        )}

        {/* Image attach error */}
        {imageError && (
          <div className="px-3 pb-2">
            <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <div>
                <p className="font-medium">Image not attached</p>
                <p className="mt-0.5 text-red-600">{imageError}</p>
              </div>
              <button
                type="button"
                onClick={() => setImageError(null)}
                className="ml-auto rounded p-0.5 text-red-400 hover:bg-red-100 hover:text-red-700"
                aria-label="Dismiss image error"
              >
                <X size={12} />
              </button>
            </div>
          </div>
        )}

        {/* Message send error */}
        {sendError && (
          <div className="px-3 pb-2">
            <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <div>
                <p className="font-medium">Message not sent</p>
                <p className="mt-0.5 text-red-600">{sendError}</p>
              </div>
              <button
                type="button"
                onClick={() => setSendError(null)}
                className="ml-auto rounded p-0.5 text-red-400 hover:bg-red-100 hover:text-red-700"
                aria-label="Dismiss send error"
              >
                <X size={12} />
              </button>
            </div>
          </div>
        )}

        {/* Editor + Send */}
        <div className="flex items-end gap-2 px-3 py-2.5">
          <div className="flex-1 min-w-0">
            <EditorContent editor={editor} />
          </div>
          <button
            type="button"
            onClick={handleSend}
            disabled={sending || !canSend}
            className="shrink-0 rounded-lg bg-brand-agent px-4 py-1.5 text-xs font-medium text-white hover:bg-brand-agent-dark disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {sending ? 'Sending…' : 'Send'}
          </button>
        </div>
      </div>
    </div>
  );
}
