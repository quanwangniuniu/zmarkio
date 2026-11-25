'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type {
  ChangeEvent,
  ClipboardEvent,
  CompositionEvent,
  KeyboardEvent,
} from 'react';
import {
  FIELD_MESSAGES,
  charCount,
  clip,
  lengthState,
} from '@/components/decisions/decisionValidation';

interface Options {
  value: string;
  onChange: (next: string) => void;
  /** Counted in code points like the backend, not UTF-16 (so no maxLength). */
  max: number;
  counterFrom: number;
  singleLine?: boolean;
  maxHeight?: number;
}

const toSingleLine = (text: string) => text.replace(/\r\n|\r|\n/g, ' ');

// While an IME is composing, Enter confirms a candidate. 229 covers older browsers.
export const isComposingKey = (e: KeyboardEvent) =>
  e.nativeEvent.isComposing || e.keyCode === 229;

export function useLimitedText({ value, onChange, max, counterFrom, singleLine, maxHeight }: Options) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [pastedCut, setPastedCut] = useState(false);
  // Firefox fires an input event after compositionend, before the parent
  // re-renders; compare against this so it can't undo the clip.
  const latestRef = useRef(value);
  const composingRef = useRef(false);
  const compositionStartRef = useRef<{ value: string; start: number; end: number } | null>(null);
  const pendingCaretRef = useRef<number | null>(null);

  const normalize = (text: string) => (singleLine ? toSingleLine(text) : text);
  const emit = (next: string) => {
    latestRef.current = next;
    onChange(next);
  };

  const grow = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    const full = el.scrollHeight;
    el.style.height = `${maxHeight ? Math.min(full, maxHeight) : full}px`;
    el.style.overflowY = maxHeight && full > maxHeight ? 'auto' : 'hidden';
  }, [maxHeight]);

  useLayoutEffect(() => {
    latestRef.current = value;
    grow();
    const el = ref.current;
    if (el && pendingCaretRef.current != null) {
      el.setSelectionRange(pendingCaretRef.current, pendingCaretRef.current);
      pendingCaretRef.current = null;
    }
  }, [value, grow]);

  useEffect(() => {
    window.addEventListener('resize', grow);
    return () => window.removeEventListener('resize', grow);
  }, [grow]);

  const insertClipped = (base: string, start: number, end: number, text: string) => {
    const before = base.slice(0, start);
    const after = base.slice(end);
    const room = Math.max(0, max - charCount(before) - charCount(after));
    const clipped = clip(text, room);
    pendingCaretRef.current = before.length + clipped.text.length;
    emit(before + clipped.text + after);
    return clipped.cut;
  };

  const onChangeHandler = (e: ChangeEvent<HTMLTextAreaElement>) => {
    const next = normalize(e.target.value);
    setPastedCut(false);
    if (composingRef.current || (e.nativeEvent as InputEvent).isComposing) {
      emit(next); // don't clip mid-composition; done in compositionend
      return;
    }
    const prev = latestRef.current;
    if (charCount(next) > max && charCount(next) > charCount(prev)) return;
    emit(next);
  };

  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    e.preventDefault();
    const el = e.currentTarget;
    const start = el.selectionStart ?? el.value.length;
    const end = el.selectionEnd ?? start;
    setPastedCut(insertClipped(el.value, start, end, normalize(e.clipboardData.getData('text'))));
  };

  const onCompositionStart = (e: CompositionEvent<HTMLTextAreaElement>) => {
    composingRef.current = true;
    const el = e.currentTarget;
    compositionStartRef.current = {
      value: el.value,
      start: el.selectionStart ?? el.value.length,
      end: el.selectionEnd ?? el.value.length,
    };
  };

  const onCompositionEnd = (e: CompositionEvent<HTMLTextAreaElement>) => {
    composingRef.current = false;
    const startState = compositionStartRef.current;
    compositionStartRef.current = null;
    const finalValue = normalize(e.currentTarget.value);
    if (!startState || charCount(finalValue) <= max) {
      emit(finalValue);
      return;
    }
    const { value: base, start, end } = startState;
    const composed = finalValue.slice(start, finalValue.length - (base.length - end));
    insertClipped(base, start, end, composed);
  };

  const length = lengthState(value, max, counterFrom);
  const message = pastedCut
    ? FIELD_MESSAGES.pastedCut(max)
    : length.atLimit
      ? FIELD_MESSAGES.limitReached
      : null;

  return {
    textareaProps: {
      ref,
      value,
      rows: 1,
      onChange: onChangeHandler,
      onPaste,
      onCompositionStart,
      onCompositionEnd,
    },
    length,
    message,
    clearNotice: () => setPastedCut(false),
  };
}
