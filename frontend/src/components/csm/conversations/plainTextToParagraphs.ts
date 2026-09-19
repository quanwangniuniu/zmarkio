export interface ParagraphNode {
  type: 'paragraph';
  content?: { type: 'text'; text: string }[];
}

/**
 * Convert plain text into Tiptap paragraph nodes, one per line.
 *
 * Passing a raw string to ``insertContent`` makes Tiptap parse it as HTML,
 * which collapses newlines and treats ``<...>`` as markup. Building the nodes
 * keeps the text verbatim. Empty lines become empty paragraphs (text nodes
 * may not be empty).
 */
export function plainTextToParagraphs(text: string): ParagraphNode[] {
  return text
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((line) =>
      line ? { type: 'paragraph', content: [{ type: 'text', text: line }] } : { type: 'paragraph' }
    );
}
