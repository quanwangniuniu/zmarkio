'use client';

import {
  autocompletion,
  closeBrackets,
  closeBracketsKeymap,
  type Completion,
  type CompletionContext,
  type CompletionResult,
} from '@codemirror/autocomplete';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { EditorState } from '@codemirror/state';
import { EditorView, keymap, placeholder as cmPlaceholder } from '@codemirror/view';
import { useEffect, useRef } from 'react';
import { cn } from '@/lib/utils';
import type { KPIMetric } from '@/types/report';

/** Engine functions worth completing here. SUM/AVERAGE/COUNT/VLOOKUP take cell
 * ranges, which a KPI formula has no way to express. */
const FORMULA_FUNCTIONS = [
  'IF',
  'ROUND',
  'ABS',
  'MIN',
  'MAX',
  'FLOOR',
  'CEILING',
  'AND',
  'OR',
  'NOT',
];

interface FormulaEditorProps {
  value: string;
  onChange: (value: string) => void;
  metrics: KPIMetric[];
  /** Draws the error ring; the message itself is rendered by the caller. */
  invalid?: boolean;
  onSubmit?: () => void;
  testId?: string;
}

export default function FormulaEditor({
  value,
  onChange,
  metrics,
  invalid = false,
  onSubmit,
  testId = 'kpi-formula-editor',
}: FormulaEditorProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const viewRef = useRef<EditorView | null>(null);

  // The editor is created once, so callbacks and metrics are read through refs
  // to keep the latest values without tearing down CodeMirror on every render.
  const onChangeRef = useRef(onChange);
  const onSubmitRef = useRef(onSubmit);
  const metricsRef = useRef(metrics);
  const valueRef = useRef(value);

  useEffect(() => {
    onChangeRef.current = onChange;
    onSubmitRef.current = onSubmit;
    metricsRef.current = metrics;
  }, [onChange, onSubmit, metrics]);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const complete = (context: CompletionContext): CompletionResult | null => {
      const word = context.matchBefore(/[A-Za-z_][\w]*/);
      if (!word || (word.from === word.to && !context.explicit)) return null;

      const options: Completion[] = [
        ...metricsRef.current.map((metric) => ({
          label: metric.key,
          type: 'variable',
          detail: metric.label,
        })),
        ...FORMULA_FUNCTIONS.map((name) => ({
          label: name,
          type: 'function',
          apply: `${name}(`,
        })),
      ];
      return { from: word.from, options };
    };

    const view = new EditorView({
      parent: host,
      state: EditorState.create({
        doc: valueRef.current,
        extensions: [
          history(),
          closeBrackets(),
          autocompletion({ override: [complete] }),
          cmPlaceholder('revenue / spend'),
          EditorView.lineWrapping,
          // CodeMirror's editing surface is a contenteditable, so browsers and
          // writing assistants spellcheck it and underline metric names.
          EditorView.contentAttributes.of({
            // The <label> cannot point at a contenteditable CodeMirror owns.
            'aria-label': 'Formula',
            spellcheck: 'false',
            autocorrect: 'off',
            autocapitalize: 'off',
            'data-gramm': 'false',
            'data-gramm_editor': 'false',
            'data-enable-grammarly': 'false',
          }),
          // Enter submits rather than inserting a newline; listed before
          // defaultKeymap so it wins.
          keymap.of([
            {
              key: 'Enter',
              run: () => {
                onSubmitRef.current?.();
                return true;
              },
            },
          ]),
          keymap.of([...closeBracketsKeymap, ...defaultKeymap, ...historyKeymap]),
          // A formula is one line; a pasted multi-line value is rejected whole
          // rather than silently flattened.
          EditorState.transactionFilter.of((tr) =>
            tr.newDoc.lines > 1 ? [] : tr
          ),
          EditorView.updateListener.of((update) => {
            if (!update.docChanged) return;
            const next = update.state.doc.toString();
            valueRef.current = next;
            onChangeRef.current(next);
          }),
          EditorView.theme({
            '&': { fontSize: '13px', backgroundColor: 'transparent' },
            '&.cm-focused': { outline: 'none' },
            '.cm-content': {
              padding: '8px 10px',
              fontFamily:
                'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
              minHeight: '38px',
            },
            '.cm-line': { padding: '0' },
          }),
        ],
      }),
    });

    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
  }, []);

  // Accept programmatic changes (e.g. opening the dialog on an existing KPI).
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current === value) return;
    valueRef.current = value;
    view.dispatch({
      changes: { from: 0, to: current.length, insert: value },
    });
  }, [value]);

  return (
    <div
      ref={hostRef}
      data-testid={testId}
      className={cn(
        'rounded-lg border bg-white transition-colors focus-within:ring-2',
        invalid
          ? 'border-red-300 focus-within:border-red-400 focus-within:ring-red-100'
          : 'border-gray-200 focus-within:border-[#3CCED7] focus-within:ring-[#3CCED7]/20'
      )}
    />
  );
}
