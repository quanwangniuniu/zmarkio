import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import '@testing-library/jest-dom';
import { ConversationComposer } from '@/components/csm/conversations/ConversationComposer';
import CsmConversationAPI, { QuickReplyTemplateAPI } from '@/lib/api/csmConversationApi';
import { useCsmConversationStore } from '@/lib/csmConversationStore';
import type { QuickReplyTemplate } from '@/types/csmConversation';

jest.mock('@/lib/api/csmConversationApi', () => ({
  __esModule: true,
  default: { sendMessage: jest.fn() },
  QuickReplyTemplateAPI: { list: jest.fn() },
}));

// The composer only orchestrates the Tiptap editor (reading text/JSON out of it
// and calling its commands) — it doesn't need a real ProseMirror instance to
// verify that behaviour, so the editor itself is faked here.
let mockEditor: {
  getText: jest.Mock;
  getJSON: jest.Mock;
  isEmpty: boolean;
  isActive: jest.Mock;
  commands: {
    clearContent: jest.Mock;
    setContent: jest.Mock;
    insertContent: jest.Mock;
    focus: jest.Mock;
  };
  chain: jest.Mock;
  on: jest.Mock;
  off: jest.Mock;
};

jest.mock('@tiptap/react', () => ({
  useEditor: jest.fn(() => mockEditor),
  EditorContent: () => null,
}));

const mockedList = QuickReplyTemplateAPI.list as jest.Mock;
const mockedSendMessage = CsmConversationAPI.sendMessage as jest.Mock;

function makeTemplate(overrides: Partial<QuickReplyTemplate>): QuickReplyTemplate {
  return {
    id: 1,
    slug: 'greeting',
    organisation: 5,
    team: null,
    title: 'Greeting',
    content: 'Hello there, how can I help?',
    rich_body: null,
    tags: ['greeting'],
    is_active: true,
    created_by: 1,
    created_by_name: 'Agent',
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

describe('ConversationComposer — quick reply template insertion', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    interface Chainable {
      focus: jest.Mock<Chainable, []>;
      toggleBold: jest.Mock<Chainable, []>;
      toggleItalic: jest.Mock<Chainable, []>;
      toggleBulletList: jest.Mock<Chainable, []>;
      toggleOrderedList: jest.Mock<Chainable, []>;
      run: jest.Mock;
    }
    const chainable: Chainable = {
      focus: jest.fn(() => chainable),
      toggleBold: jest.fn(() => chainable),
      toggleItalic: jest.fn(() => chainable),
      toggleBulletList: jest.fn(() => chainable),
      toggleOrderedList: jest.fn(() => chainable),
      run: jest.fn(),
    };
    mockEditor = {
      getText: jest.fn(() => ''),
      getJSON: jest.fn(() => ({ type: 'doc', content: [] })),
      isEmpty: true,
      isActive: jest.fn(() => false),
      commands: {
        clearContent: jest.fn(),
        setContent: jest.fn(),
        insertContent: jest.fn(),
        focus: jest.fn(),
      },
      chain: jest.fn(() => chainable),
      on: jest.fn(),
      off: jest.fn(),
    };
  });

  const openTemplatePicker = async () => {
    render(<ConversationComposer conversationId={42} organisationId={5} />);
    fireEvent.click(screen.getByTitle('Insert quick reply template'));
    await screen.findByText('Greeting');
  };

  test('toolbar reflects the formatting at the current cursor position', async () => {
    mockEditor.isActive.mockImplementation((format: string) => format === 'bold' || format === 'bulletList');

    render(<ConversationComposer conversationId={42} organisationId={5} />);

    expect(await screen.findByRole('button', { name: 'Bold' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Italic' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.getByRole('button', { name: 'Bullet list' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'Ordered list' })).toHaveAttribute('aria-pressed', 'false');
    expect(mockEditor.on).toHaveBeenCalledWith('selectionUpdate', expect.any(Function));
    expect(mockEditor.on).toHaveBeenCalledWith('transaction', expect.any(Function));
  });

  test('inserting a plain-text template writes into the editor but does not send the message', async () => {
    mockedList.mockResolvedValue([makeTemplate({ content: 'Hello there', rich_body: null })]);
    await openTemplatePicker();

    fireEvent.click(screen.getByText('Greeting').closest('button')!);

    expect(mockEditor.commands.insertContent).toHaveBeenCalledWith('Hello there');
    expect(mockEditor.commands.setContent).not.toHaveBeenCalled();
    expect(mockedSendMessage).not.toHaveBeenCalled();
    // Single action: the picker closes itself once a template is chosen.
    expect(screen.queryByText('Greeting')).not.toBeInTheDocument();
  });

  test('inserting a rich-text template replaces editor content via setContent, not insertContent', async () => {
    const richBody = { type: 'doc', content: [{ type: 'paragraph' }] };
    mockedList.mockResolvedValue([makeTemplate({ rich_body: richBody })]);
    await openTemplatePicker();

    fireEvent.click(screen.getByText('Greeting').closest('button')!);

    expect(mockEditor.commands.setContent).toHaveBeenCalledWith(richBody);
    expect(mockEditor.commands.insertContent).not.toHaveBeenCalled();
    expect(mockedSendMessage).not.toHaveBeenCalled();
  });

  test('the agent can edit the inserted text before an explicit Send click dispatches it', async () => {
    mockedList.mockResolvedValue([makeTemplate({ content: 'Hello there', rich_body: null })]);
    mockedSendMessage.mockResolvedValue({ id: 999, content: 'Hello there, edited', rich_body: null });
    await openTemplatePicker();

    // After insertion, the editor contains the template text. The component
    // calls editor.getText() inside handleInsertTemplate to update
    // hasReplyContent. Make the mock reflect this.
    mockEditor.commands.insertContent.mockImplementation(() => {
      mockEditor.getText.mockReturnValue('Hello there');
    });

    fireEvent.click(screen.getByText('Greeting').closest('button')!);
    expect(mockedSendMessage).not.toHaveBeenCalled();

    // Simulate the agent having edited the inserted text before sending.
    mockEditor.getText.mockReturnValue('Hello there, edited');

    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => expect(mockedSendMessage).toHaveBeenCalledTimes(1));
    const [conversationId, payload] = mockedSendMessage.mock.calls[0];
    expect(conversationId).toBe(42);
    expect(payload.content).toBe('Hello there, edited');
  });
});

describe('ConversationComposer — in-memory draft preservation', () => {
  // Rebuild a fresh editor mock for each case and clear the shared store so
  // draft state never leaks between tests.
  beforeEach(() => {
    jest.clearAllMocks();
    useCsmConversationStore.setState({ draftsByConversation: {} });
    // jsdom does not implement Object URL lifecycle methods.
    URL.createObjectURL = jest.fn(() => 'blob:mock') as never;
    URL.revokeObjectURL = jest.fn() as never;
    interface Chainable {
      focus: jest.Mock<Chainable, []>;
      toggleBold: jest.Mock<Chainable, []>;
      toggleItalic: jest.Mock<Chainable, []>;
      toggleBulletList: jest.Mock<Chainable, []>;
      toggleOrderedList: jest.Mock<Chainable, []>;
      run: jest.Mock;
    }
    const chainable: Chainable = {
      focus: jest.fn(() => chainable),
      toggleBold: jest.fn(() => chainable),
      toggleItalic: jest.fn(() => chainable),
      toggleBulletList: jest.fn(() => chainable),
      toggleOrderedList: jest.fn(() => chainable),
      run: jest.fn(),
    };
    mockEditor = {
      getText: jest.fn(() => ''),
      getJSON: jest.fn(() => ({ type: 'doc', content: [] })),
      isEmpty: true,
      isActive: jest.fn(() => false),
      commands: {
        clearContent: jest.fn(),
        setContent: jest.fn(),
        insertContent: jest.fn(),
        focus: jest.fn(),
      },
      chain: jest.fn(() => chainable),
      on: jest.fn(),
      off: jest.fn(),
    };
  });

  test('restores a saved rich-text draft into the editor on mount', () => {
    const richBody = { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text: 'draft reply' }] }] };
    useCsmConversationStore.getState().setDraft(42, {
      richBody,
      imageFile: null,
      imagePreviewUrl: null,
    });

    render(<ConversationComposer conversationId={42} organisationId={5} />);

    // Restore calls setContent using the saved rich body.
    expect(mockEditor.commands.setContent).toHaveBeenCalledWith(richBody);
  });

  test('restores a saved image attachment on mount', () => {
    const file = new File(['x'], 'photo.png', { type: 'image/png' });
    // Restore an image with no preview URL so the composer renders the
    // attachment card (file-name fallback) rather than an <img>.
    useCsmConversationStore.getState().setDraft(42, {
      richBody: null,
      imageFile: file,
      imagePreviewUrl: null,
    });

    render(<ConversationComposer conversationId={42} organisationId={5} />);

    // The restored image file name renders in the attachment card.
    expect(screen.getByText('photo.png')).toBeInTheDocument();
  });

  test('snapshots the current draft into the store on unmount', () => {
    const richBody = { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text: 'unsent' }] }] };
    mockEditor.getText.mockReturnValue('unsent');
    mockEditor.getJSON.mockReturnValue(richBody);
    mockEditor.isEmpty = false;

    const { unmount } = render(<ConversationComposer conversationId={42} organisationId={5} />);
    expect(useCsmConversationStore.getState().draftsByConversation[42]).toBeUndefined();

    unmount();

    expect(useCsmConversationStore.getState().draftsByConversation[42]).toEqual({
      richBody,
      imageFile: null,
      imagePreviewUrl: null,
    });
  });

  test('clears the draft from the store after a successful send', async () => {
    mockedSendMessage.mockResolvedValue({ id: 999, content: 'hi', rich_body: null });
    // Pre-seed a draft WITH an image so canSend becomes true via imageFile
    // (restore-on-mount does not flip hasReplyContent, so the editor-text path
    // alone would leave Send disabled).
    const file = new File(['x'], 'photo.png', { type: 'image/png' });
    useCsmConversationStore.getState().setDraft(42, {
      richBody: { type: 'doc' },
      imageFile: file,
      imagePreviewUrl: null,
    });

    render(<ConversationComposer conversationId={42} organisationId={5} />);

    fireEvent.click(screen.getByRole('button', { name: /send/i }));

    await waitFor(() => expect(mockedSendMessage).toHaveBeenCalledTimes(1));
    expect(useCsmConversationStore.getState().draftsByConversation[42]).toBeUndefined();
  });
});

describe('ConversationComposer — insert requested by another panel', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    useCsmConversationStore.setState({ draftsByConversation: {}, pendingComposerInsert: null });
    mockEditor = {
      getText: jest.fn(() => ''),
      getJSON: jest.fn(() => ({ type: 'doc', content: [] })),
      isEmpty: true,
      isActive: jest.fn(() => false),
      commands: {
        clearContent: jest.fn(),
        setContent: jest.fn(),
        insertContent: jest.fn(),
        focus: jest.fn(),
      },
      // Formatting commands are not exercised here.
      chain: jest.fn(),
      on: jest.fn(),
      off: jest.fn(),
    };
  });

  it('appends the text as one paragraph per line and consumes the request', async () => {
    render(<ConversationComposer conversationId={42} organisationId={5} />);
    mockEditor.getText.mockReturnValue('Thanks for waiting.');

    act(() => {
      useCsmConversationStore
        .getState()
        .requestComposerInsert(42, 'Thanks for waiting.\n\nA refund <b>needs</b> approval.');
    });

    await waitFor(() => expect(mockEditor.commands.insertContent).toHaveBeenCalledTimes(1));
    expect(mockEditor.commands.focus).toHaveBeenCalledWith('end');
    expect(mockEditor.commands.insertContent).toHaveBeenCalledWith([
      { type: 'paragraph', content: [{ type: 'text', text: 'Thanks for waiting.' }] },
      { type: 'paragraph' },
      { type: 'paragraph', content: [{ type: 'text', text: 'A refund <b>needs</b> approval.' }] },
    ]);
    expect(useCsmConversationStore.getState().pendingComposerInsert).toBeNull();
  });

  it('ignores requests for a different conversation', async () => {
    render(<ConversationComposer conversationId={42} organisationId={5} />);

    act(() => {
      useCsmConversationStore.getState().requestComposerInsert(99, 'Not for this composer');
    });

    await waitFor(() =>
      expect(useCsmConversationStore.getState().pendingComposerInsert?.conversationId).toBe(99)
    );
    expect(mockEditor.commands.insertContent).not.toHaveBeenCalled();
  });
});
