'use client';

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Bookmark, Hash, MessageSquare, PanelLeftOpen, Search } from 'lucide-react';
import { useRouter, useSearchParams, usePathname } from 'next/navigation';
import { useAuthStore } from '@/lib/authStore';
import { useChatStore } from '@/lib/chatStore';
import { useChatData } from '@/hooks/useChatData';
import { useProjectMemberRoles } from '@/hooks/useProjectMemberRoles';
import { useProjectMembers } from '@/hooks/useProjectMembers';
import { useProjectStore } from '@/lib/projectStore';
import { activateProjectForNavigation } from '@/lib/notificationRoutes';
import { buildUrl } from '@/lib/buildUrl';
import { getChat, resolveLegacyChatSlug } from '@/lib/api/chatApi';
import {
  buildMessagesPath,
  chatsForProjectKey,
  getLegacyChatIdFromQuery,
  normalizeProjectKey,
  parseChatSlugFromPathname,
  preferredProjectKey,
  projectKeysMatch,
} from '@/lib/messages/messagesRoutes';
import ChatWindow from '@/components/chat/ChatWindow';
import CreateChatDialog from '@/components/chat/CreateChatDialog';
import SlackMessagesLayout from '@/components/messages/SlackMessagesLayout';
import SearchPanel from '@/components/chat/search/SearchPanel';
import SavedItemsPanel from '@/components/chat/SavedItemsPanel';
import ChatCommandPalette from '@/components/chat/ChatCommandPalette';
import BrowseChannelsDialog from '@/components/chat/BrowseChannelsDialog';
import type { MessageSearchResult } from '@/types/chat';
import type { SearchFilters } from '@/hooks/useMessageSearch';

const MESSAGES_MOBILE_QUERY = '(max-width: 767px)';

const isMessagesMobileViewport = () =>
  typeof window !== 'undefined' && window.matchMedia(MESSAGES_MOBILE_QUERY).matches;

export default function MessagePageContent() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const chatSlugFromPath = parseChatSlugFromPathname(pathname);
  const isAuthenticated = useAuthStore(state => state.isAuthenticated);
  const currentUser = useAuthStore(state => state.user);
  const currentUserId = currentUser?.id ? Number(currentUser.id) : 0;
  const activeProject = useProjectStore((s) => s.activeProject);
  const hasProjectStoreHydrated = useProjectStore((s) => s.hasHydrated);
  const [selectedProjectId, setSelectedProjectId] = useState<number | string | null>(null);
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
  const [isCreateChannelDialogOpen, setIsCreateChannelDialogOpen] = useState(false);
  const [isConversationDrawerOpen, setIsConversationDrawerOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearchOpen, setIsSearchOpen] = useState(false);
  const [searchInitialFilters, setSearchInitialFilters] = useState<Partial<SearchFilters> | undefined>();
  const [searchFilterSignal, setSearchFilterSignal] = useState(0);
  const [detailsSignal, setDetailsSignal] = useState<{ chatId: number; seq: number } | null>(null);
  const [isSavedOpen, setIsSavedOpen] = useState(false);
  const [isBrowseOpen, setIsBrowseOpen] = useState(false);
  const [isCommandPaletteOpen, setIsCommandPaletteOpen] = useState(false);

  const activeProjectKey = useMemo(
    () => preferredProjectKey(activeProject),
    [activeProject?.slug, activeProject?.id],
  );

  // Chat store state
  const currentChatId = useChatStore(state => state.currentChatId);
  const setCurrentChat = useChatStore(state => state.setCurrentChat);
  const chatsByProject = useChatStore(state => state.chatsByProject);
  // Get chats for the selected project only (independent from widget)
  const chats = useMemo(
    () => chatsForProjectKey(chatsByProject, selectedProjectId, activeProject),
    [chatsByProject, selectedProjectId, activeProject],
  );

  const { roleByUserId } = useProjectMemberRoles(selectedProjectId);
  const { members: projectMembers, isLoading: isLoadingMembers } = useProjectMembers(selectedProjectId);
  
  // Fetch chats for selected project
  const { fetchChats, createNewChat, isLoading } = useChatData({
    projectId: selectedProjectId || undefined,
    autoFetch: false,
  });
  
  // Real-time updates are handled by useNotificationSSE (mounted in ChatWidget).
  // When a chat SSE event arrives, chatStore.lastChatActivity is bumped and the
  // widget's fetchChats runs.  Here we only need to fetch on project change.
  const lastChatActivity = useChatStore(state => state.lastChatActivity);
  const hasFetchedRef = useRef<string | null>(null);
  const legacyMigrateRef = useRef<number | null>(null);

  const resolveChatSlug = useCallback(
    (chatId: number): string | undefined => {
      const fromList = chats.find((c) => c.id === chatId)?.slug;
      if (fromList) return fromList;
      return Object.values(chatsByProject)
        .flat()
        .find((c) => c.id === chatId)?.slug;
    },
    [chats, chatsByProject],
  );

  const navigateMessages = useCallback(
    (
      next: {
        chatSlug?: string | null;
        messageId?: number | null;
        threadMessageId?: number | null;
        jumpId?: string | null;
        replace?: boolean;
      },
    ) => {
      const href = buildUrl(buildMessagesPath(next.chatSlug, {
        messageId: next.messageId,
        threadMessageId: next.threadMessageId,
        jumpId: next.jumpId,
      }));
      if (next.replace) router.replace(href, { scroll: false });
      else router.push(href);
    },
    [router],
  );

  // Global Cmd/Ctrl-K → open conversation switcher
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        // Don't steal from Tiptap composer
        if ((e.target as HTMLElement)?.closest?.('.ProseMirror')) return;
        e.preventDefault();
        setIsCommandPaletteOpen(true);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  // Re-fetch the chat list when the project changes or an SSE chat event arrives.
  useEffect(() => {
    if (isAuthenticated && selectedProjectId) {
      const fetchKey = `${selectedProjectId}-${lastChatActivity}`;
      if (hasFetchedRef.current !== fetchKey) {
        hasFetchedRef.current = fetchKey;
        fetchChats();
      }
    }
  }, [isAuthenticated, selectedProjectId, lastChatActivity, fetchChats]);

  useEffect(() => {
    if (
      activeProjectKey != null &&
      !projectKeysMatch(activeProjectKey, selectedProjectId, activeProject)
    ) {
      setSelectedProjectId(activeProjectKey);
    }
  }, [activeProjectKey, activeProject, selectedProjectId]);

  const syncSelectedProject = useCallback(
    (raw: number | string | null | undefined) => {
      const next = normalizeProjectKey(raw, activeProject);
      if (next != null && !projectKeysMatch(next, selectedProjectId, activeProject)) {
        setSelectedProjectId(next);
      }
      return next;
    },
    [activeProject, selectedProjectId],
  );

  // Open chat from /messages/<chatSlug> path.
  useEffect(() => {
    if (!chatSlugFromPath) return;

    const fromStore = Object.values(chatsByProject)
      .flat()
      .find((c) => c.slug === chatSlugFromPath);

    if (fromStore) {
      syncSelectedProject(fromStore.project_id ?? fromStore.project);
      if (fromStore.id !== currentChatId) {
        setCurrentChat(fromStore.id);
      }
      return;
    }

    let cancelled = false;
    void getChat(chatSlugFromPath)
      .then(async (chat) => {
        if (cancelled) return;
        useChatStore.getState().addChat(chat);
        const projectKey = syncSelectedProject(chat.project_id ?? chat.project);
        if (projectKey) {
          await activateProjectForNavigation(projectKey);
        }
        setCurrentChat(chat.id);
      })
      .catch(() => {
        if (!cancelled) navigateMessages({ chatSlug: null, replace: true });
      });

    return () => {
      cancelled = true;
    };
  }, [
    chatSlugFromPath,
    chatsByProject,
    currentChatId,
    navigateMessages,
    syncSelectedProject,
    setCurrentChat,
  ]);

  // Migrate legacy ?chatId= deep links to /messages/<slug>.
  useEffect(() => {
    const legacyChatId = getLegacyChatIdFromQuery(searchParams);
    if (!legacyChatId || legacyMigrateRef.current === legacyChatId) return;
    legacyMigrateRef.current = legacyChatId;

    const messageIdRaw = searchParams.get('messageId');
    const threadMessageIdRaw = searchParams.get('threadMessageId');
    const jumpId = searchParams.get('jumpId');
    const messageId = messageIdRaw ? Number(messageIdRaw) : null;
    const threadMessageId = threadMessageIdRaw ? Number(threadMessageIdRaw) : null;

    void (async () => {
      let slug = resolveChatSlug(legacyChatId);
      if (!slug) {
        try {
          slug = await resolveLegacyChatSlug(legacyChatId);
        } catch {
          legacyMigrateRef.current = null;
          return;
        }
      }
      setCurrentChat(legacyChatId);
      navigateMessages({
        chatSlug: slug,
        messageId: Number.isFinite(messageId ?? NaN) ? messageId : null,
        threadMessageId: Number.isFinite(threadMessageId ?? NaN) ? threadMessageId : null,
        jumpId,
        replace: true,
      });
    })();
  }, [navigateMessages, resolveChatSlug, searchParams, setCurrentChat]);

  // Get current chat from store
  const currentChat = chats.find(chat => chat.id === currentChatId);
  
  // Filter chats by search query
  const isSearchingConversations = searchQuery.trim().length > 0;
  const filteredChats = chats.filter(chat => {
    if (!searchQuery.trim()) return true;
    
    const query = searchQuery.toLowerCase();
    
    // Search by chat name
    if (chat.name?.toLowerCase().includes(query)) return true;
    
    // Search by participant names
    const participantMatch = chat.participants.some(p => 
      p.user.username?.toLowerCase().includes(query) ||
      p.user.email?.toLowerCase().includes(query)
    );
    if (participantMatch) return true;
    
    // Search by last message content
    if (chat.last_message?.content.toLowerCase().includes(query)) return true;
    
    return false;
  });

  const hasProjectCandidate = Boolean(activeProject?.id);
  const projectSelectionLoading =
    selectedProjectId === null && (!hasProjectStoreHydrated || hasProjectCandidate);
  
  const handleSelectChat = (chatId: number) => {
    setIsSearchOpen(false);
    setIsSavedOpen(false);
    // Clicking the already-open chat closes it and returns to the list
    if (chatId === currentChatId) {
      setCurrentChat(null);
      navigateMessages({ chatSlug: null, messageId: null, replace: true });
      return;
    }
    setCurrentChat(chatId);
    navigateMessages({
      chatSlug: resolveChatSlug(chatId) ?? null,
      messageId: null,
      replace: true,
    });
  };
  
  const handleBackToList = () => {
    if (isMessagesMobileViewport()) {
      setIsConversationDrawerOpen(true);
    }
  };
  
  const handleCreateChat = () => {
    setIsCreateDialogOpen(true);
  };

  const handleCreateChannel = () => {
    setIsCreateChannelDialogOpen(true);
  };
  
  const handleChatCreated = (chatId: number, chatSlug?: string) => {
    setIsCreateDialogOpen(false);
    setCurrentChat(chatId);
    navigateMessages({
      chatSlug: chatSlug ?? resolveChatSlug(chatId) ?? null,
      messageId: null,
      replace: true,
    });
    fetchChats();
  };

  const handleChannelCreated = (chatId: number, chatSlug?: string) => {
    setIsCreateChannelDialogOpen(false);
    setCurrentChat(chatId);
    navigateMessages({
      chatSlug: chatSlug ?? resolveChatSlug(chatId) ?? null,
      messageId: null,
      replace: true,
    });
    fetchChats();
  };

  const handleStartDM = useCallback(async (targetUserId: number) => {
    if (!selectedProjectId) return;

    // Check if a DM thread already exists with this user
    const existingChat = chats.find(
      (c) =>
        c.type === 'private' &&
        c.participants?.some((p) => p.user.id === targetUserId)
    );

    if (existingChat) {
      setCurrentChat(existingChat.id);
      navigateMessages({
        chatSlug: existingChat.slug,
        messageId: null,
        replace: true,
      });
      return;
    }

    try {
      const newChat = await createNewChat({
        type: 'private',
        project_id: activeProject?.id ?? selectedProjectId,
        participant_ids: [targetUserId],
      });
      setCurrentChat(newChat.id);
      navigateMessages({
        chatSlug: newChat.slug,
        messageId: null,
        replace: true,
      });
    } catch {
      // createNewChat already shows a toast on error
    }
  }, [activeProject?.id, selectedProjectId, chats, createNewChat, setCurrentChat, navigateMessages]);

  // When the user clicks a search result: navigate to that chat + message
  const handleSelectSearchResult = useCallback(
    (result: MessageSearchResult) => {
      setIsSearchOpen(false);
      // Switch project if the result is from a different one
      if (result.project_id) {
        syncSelectedProject(result.project_id);
      }
      setCurrentChat(result.chat_id);
      navigateMessages({
        chatSlug: resolveChatSlug(result.chat_id) ?? null,
        messageId: result.id,
        replace: true,
      });
    },
    [resolveChatSlug, syncSelectedProject, setCurrentChat, navigateMessages]
  );

  const handleSearchInChat = useCallback((chatId: number) => {
    setSearchInitialFilters({ inChat: chatId });
    setSearchFilterSignal((n) => n + 1);
    setIsSearchOpen(true);
  }, []);

  const handleOpenChannelDetails = useCallback((chatId: number) => {
    setIsSearchOpen(false);
    setIsSavedOpen(false);
    setCurrentChat(chatId);
    navigateMessages({
      chatSlug: resolveChatSlug(chatId) ?? null,
      messageId: null,
      replace: true,
    });
    setDetailsSignal((prev) => ({ chatId, seq: (prev?.seq ?? 0) + 1 }));
  }, [navigateMessages, resolveChatSlug, setCurrentChat]);

  const handleSavedJump = useCallback(
    async (
      msgId: number,
      chatId: number,
      parentMsgId?: number | null,
      savedProjectId?: number | string | null,
    ) => {
      setIsSavedOpen(false);

      let targetProjectId = savedProjectId || null;

      let targetChat = targetProjectId
        ? chatsByProject[targetProjectId]?.find((chat) => Number(chat.id) === Number(chatId))
        : undefined;

      if (!targetChat) {
        targetChat = Object.values(chatsByProject)
          .flat()
          .find((chat) => Number(chat.id) === Number(chatId));
      }

      if (!targetChat) {
        try {
          const slug = await resolveLegacyChatSlug(chatId);
          targetChat = await getChat(slug);
          useChatStore.getState().addChat(targetChat);
        } catch {
          // Let ChatWindow's target resolution show the normal not-found state.
        }
      }

      const rawProjectId = targetChat?.project_id ?? targetChat?.project ?? targetProjectId ?? selectedProjectId;
      const projectKey = syncSelectedProject(rawProjectId);
      if (projectKey) {
        targetProjectId = projectKey;
        await activateProjectForNavigation(projectKey);
      }

      let chatSlug = targetChat?.slug;
      if (!chatSlug) {
        try {
          chatSlug = await resolveLegacyChatSlug(chatId);
        } catch {
          chatSlug = undefined;
        }
      }

      setCurrentChat(chatId);
      navigateMessages({
        chatSlug: chatSlug ?? null,
        messageId: parentMsgId ?? msgId,
        threadMessageId: parentMsgId ? msgId : null,
        jumpId: `saved:${chatId}:${parentMsgId ?? msgId}:${msgId}:${Date.now()}`,
        replace: false,
      });
    },
    [chatsByProject, navigateMessages, syncSelectedProject, setCurrentChat],
  );

  // Sidebar chat-list filter input (mobile drawer only).
  const renderSearchInput = (testId: string) => (
    <div className="relative w-full">
      <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
      <input
        type="text"
        placeholder="Search conversations…"
        value={searchQuery}
        onChange={(e) => setSearchQuery(e.target.value)}
        className="w-full rounded-md border border-gray-200 py-1.5 pl-10 pr-4 text-sm focus:border-[#3CCED7] focus:outline-none focus:ring-2 focus:ring-[#3CCED7]/30"
        data-testid={testId}
        aria-label="Search conversations"
      />
    </div>
  );

  return (
    <div className="flex-1 flex flex-col bg-white min-h-0">
      {/* Header */}
      <div
        className="flex items-center gap-3 border-b border-gray-200 bg-white px-3 py-2 sm:px-6 sm:py-3"
        data-testid="messages-header"
      >
        <div className="flex min-w-0 flex-1 items-center gap-2 md:flex-none">
          <MessageSquare className="h-5 w-5 shrink-0 text-[#3CCED7]" />
          <h1 className="shrink-0 text-lg font-semibold text-gray-900">Messages</h1>
          {activeProject?.name && (
            <span className="min-w-0 truncate text-sm text-gray-400 sm:ml-2">· {activeProject.name}</span>
          )}
        </div>
        <button
          type="button"
          onClick={() => setIsConversationDrawerOpen(true)}
          className="inline-flex h-9 shrink-0 items-center gap-2 rounded-md border border-gray-200 px-3 text-sm font-medium text-gray-700 transition hover:bg-gray-50 md:hidden"
          aria-label="Open conversations"
          title="Open conversations"
        >
          <PanelLeftOpen className="h-4 w-4" />
          <span className="hidden min-[380px]:inline">Chats</span>
        </button>
        <div className="ml-auto hidden items-center gap-1 md:flex">
          {selectedProjectId && (
            <div className="relative group/browse">
              <button
                type="button"
                onClick={() => setIsBrowseOpen(true)}
                className="rounded-md p-2 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600"
                aria-label="Browse channels"
              >
                <Hash className="h-4 w-4" />
              </button>
              <div className="pointer-events-none absolute right-0 top-full mt-1.5 z-50 whitespace-nowrap rounded-md bg-white px-2 py-1 text-xs text-gray-700 shadow-md ring-1 ring-gray-200 opacity-0 group-hover/browse:opacity-100 transition-opacity">
                Browse channels
              </div>
            </div>
          )}
          <div className="relative group/search">
            <button
              type="button"
              onClick={() => {
                setIsSearchOpen(true);
                setIsSavedOpen(false);
              }}
              className="rounded-md p-2 text-gray-400 transition hover:bg-gray-100 hover:text-gray-600"
              data-testid="messages-search"
              aria-label="Search messages"
            >
              <Search className="h-4 w-4" />
            </button>
            <div className="pointer-events-none absolute right-0 top-full mt-1.5 z-50 whitespace-nowrap rounded-md bg-white px-2 py-1 text-xs text-gray-700 shadow-md ring-1 ring-gray-200 opacity-0 group-hover/search:opacity-100 transition-opacity">
              Search messages
            </div>
          </div>
          <div className="relative group/saved">
            <button
              type="button"
              onClick={() => {
                setIsSavedOpen((v) => !v);
                setIsSearchOpen(false);
              }}
              className={[
                'rounded-md p-2 transition',
                isSavedOpen
                  ? 'bg-teal-50 text-teal-700'
                  : 'text-gray-400 hover:bg-gray-100 hover:text-gray-600',
              ].join(' ')}
              data-testid="messages-saved"
              aria-label="Saved messages"
              aria-pressed={isSavedOpen}
            >
              <Bookmark className="h-4 w-4" />
            </button>
            <div className="pointer-events-none absolute right-0 top-full mt-1.5 z-50 whitespace-nowrap rounded-md bg-white px-2 py-1 text-xs text-gray-700 shadow-md ring-1 ring-gray-200 opacity-0 group-hover/saved:opacity-100 transition-opacity">
              Saved messages
            </div>
          </div>
        </div>
      </div>

      <SlackMessagesLayout
        selectedProjectId={selectedProjectId}
        chats={filteredChats}
        currentChatId={currentChatId}
        onSelectChat={handleSelectChat}
        onCreateChat={handleCreateChat}
        onCreateChannel={handleCreateChannel}
        roleByUserId={roleByUserId}
        isLoadingChats={isLoading}
        projectMembers={projectMembers}
        isLoadingMembers={isLoadingMembers}
        onStartDM={handleStartDM}
        mobileSidebarOpen={isConversationDrawerOpen}
        onMobileSidebarOpenChange={setIsConversationDrawerOpen}
        mobileSidebarHeader={renderSearchInput('messages-mobile-search')}
        isSearchActive={isSearchingConversations}
        onSearchInChat={handleSearchInChat}
        onOpenChannelDetails={handleOpenChannelDetails}
        chatListEmptyState={
          selectedProjectId ? (
            <div className="p-6 text-sm text-gray-500">No chats yet</div>
          ) : projectSelectionLoading ? null : (
            <div className="flex items-center justify-center p-6 text-center">
              <div>
                <MessageSquare className="w-12 h-12 text-gray-300 mx-auto mb-3" />
                <p className="text-gray-500 text-sm">Select a project to view chats</p>
              </div>
            </div>
          )
        }
        chatPanel={
          isSearchOpen ? (
            <div className="h-full">
              <SearchPanel
                projectId={selectedProjectId}
                chats={chats}
                onSelectResult={handleSelectSearchResult}
                onClose={() => setIsSearchOpen(false)}
                initialFilters={searchInitialFilters}
                filterSignal={searchFilterSignal}
              />
            </div>
          ) : isSavedOpen ? (
            <div className="h-full">
              <SavedItemsPanel
                onClose={() => setIsSavedOpen(false)}
                onJumpToMessage={handleSavedJump}
              />
            </div>
          ) : projectSelectionLoading ? (
            <div className="flex-1" />
          ) : !selectedProjectId ? (
            <div className="flex-1 flex items-center justify-center p-6 text-center">
              <div>
                <MessageSquare className="w-16 h-16 text-gray-200 mx-auto mb-4" />
                <h3 className="text-lg font-medium text-gray-700 mb-2">
                  Select a project to start
                </h3>
                <p className="text-gray-500 text-sm max-w-sm">
                  Select a project from the workspace navigation to view and manage team conversations.
                </p>
              </div>
            </div>
          ) : !currentChat ? (
            <div className="flex-1 flex items-center justify-center p-6 text-center">
              <div>
                <MessageSquare className="w-16 h-16 text-gray-200 mx-auto mb-4" />
                <h3 className="text-lg font-medium text-gray-700 mb-2">
                  Select a conversation
                </h3>
                <p className="text-gray-500 text-sm max-w-sm">
                  Choose a chat from the list or start a new conversation with your team members.
                </p>
                <button
                  type="button"
                  onClick={() => setIsConversationDrawerOpen(true)}
                  className="mt-4 inline-flex items-center gap-2 rounded-md bg-[#3CCED7] px-3 py-2 text-sm font-medium text-white transition hover:bg-[#2AB5BD] md:hidden"
                >
                  <PanelLeftOpen className="h-4 w-4" />
                  Browse conversations
                </button>
              </div>
            </div>
          ) : (
            <div className="h-full" data-testid="messages-chat-window">
              <ChatWindow
                chat={currentChat}
                onBack={handleBackToList}
                roleByUserId={roleByUserId}
                routeChatSlug={chatSlugFromPath}
                hideBackOnDesktop
                openDetailsSignal={detailsSignal ?? undefined}
              />
            </div>
          )
        }
      />
      
      {/* Create Chat Dialog */}
      {selectedProjectId && (
        <>
          <CreateChatDialog
            isOpen={isCreateDialogOpen}
            onClose={() => setIsCreateDialogOpen(false)}
            projectId={String(selectedProjectId)}
            onChatCreated={handleChatCreated}
          />
          <CreateChatDialog
            isOpen={isCreateChannelDialogOpen}
            onClose={() => setIsCreateChannelDialogOpen(false)}
            projectId={String(selectedProjectId)}
            onChatCreated={handleChannelCreated}
            variant="channel"
          />
        </>
      )}

      {/* Browse channels dialog */}
      {isBrowseOpen && selectedProjectId && (
        <BrowseChannelsDialog
          projectId={selectedProjectId}
          currentUserId={currentUserId}
          onClose={() => setIsBrowseOpen(false)}
          onJoinedChannel={(chatSlug) => {
            setIsBrowseOpen(false);
            getChat(chatSlug)
              .then((joined) => useChatStore.getState().addChat(joined))
              .catch(() => {});
          }}
        />
      )}

      {/* Cmd/Ctrl-K conversation switcher */}
      <ChatCommandPalette
        isOpen={isCommandPaletteOpen}
        onOpenChange={setIsCommandPaletteOpen}
        projectId={selectedProjectId}
        currentChatId={currentChatId}
        onSelectChat={handleSelectChat}
      />
    </div>
  );
}
