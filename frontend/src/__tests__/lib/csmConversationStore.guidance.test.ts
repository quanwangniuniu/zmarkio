import { useCsmConversationStore } from '@/lib/csmConversationStore';

describe('csmConversationStore — guidance', () => {
  beforeEach(() => {
    useCsmConversationStore.setState({ guidanceVersionByGroup: {}, pendingComposerInsert: null });
  });

  it('bumps the version of each updated group', () => {
    const { bumpGuidance } = useCsmConversationStore.getState();
    bumpGuidance([1, 2]);
    bumpGuidance([2]);
    expect(useCsmConversationStore.getState().guidanceVersionByGroup).toEqual({ 1: 1, 2: 2 });
  });

  it('gives every insert request a new nonce and consumes only the matching one', () => {
    const store = useCsmConversationStore.getState();
    store.requestComposerInsert(5, 'first');
    const first = useCsmConversationStore.getState().pendingComposerInsert!;
    store.requestComposerInsert(5, 'first');
    const second = useCsmConversationStore.getState().pendingComposerInsert!;
    expect(second.nonce).not.toBe(first.nonce);

    // A stale consumer must not clear a newer request.
    store.consumeComposerInsert(first.nonce);
    expect(useCsmConversationStore.getState().pendingComposerInsert).toEqual(second);

    store.consumeComposerInsert(second.nonce);
    expect(useCsmConversationStore.getState().pendingComposerInsert).toBeNull();
  });
});
