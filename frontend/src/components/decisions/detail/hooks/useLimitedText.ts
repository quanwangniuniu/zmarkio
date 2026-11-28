'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type {
  ChangeEvent,
  ClipboardEvent,
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
  const elementRef = useRef<HTMLTextAreaElement | null>(null);
  const [pastedCut, setPastedCut] = useState(false);
  // The value prop can lag a render behind fast input; compare against this.
  const latestRef = useRef(value);
  const pendingCaretRef = useRef<number | null>(null);

  const normalize = (text: string) => (singleLine ? toSingleLine(text) : text);
  const emit = (next: string) => {
    latestRef.current = next;
    onChange(next);
  };

  const grow = useCallback(() => {
    const el = elementRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const full = el.scrollHeight;
    el.style.height = `${maxHeight ? Math.min(full, maxHeight) : full}px`;
    el.style.overflowY = maxHeight && full > maxHeight ? 'auto' : 'hidden';
  }, [maxHeight]);

  // Measure on mount too: the textarea may appear after the value is set (e.g. on Edit).
  const ref = useCallback(
    (el: HTMLTextAreaElement | null) => {
      elementRef.current = el;
      if (el) grow();
    },
    [grow],
  );

  useLayoutEffect(() => {
    latestRef.current = value;
    grow();
    const el = elementRef.current;
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
    },
    elementRef,
    length,
    message,
    clearNotice: () => setPastedCut(false),
  };
}
