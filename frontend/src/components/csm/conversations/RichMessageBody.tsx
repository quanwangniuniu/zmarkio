'use client';

import React, { useEffect } from 'react';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';

// Read-only rich text renderer using Tiptap
export function RichMessageBody({ body, isAgent }: { body: object; isAgent: boolean }) {
  const editor = useEditor({
    extensions: [StarterKit],
    content: body,
    editable: false,
    immediatelyRender: false,
    editorProps: {
      attributes: {
        class: isAgent ? 'csm-rich-dark text-sm' : 'csm-rich-light text-sm text-gray-900',
      },
    },
  });

  useEffect(() => {
    editor?.commands.setContent(body);
  }, [editor, body]);

  return <EditorContent editor={editor} />;
}
