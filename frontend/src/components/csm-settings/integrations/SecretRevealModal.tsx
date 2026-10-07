'use client';

import toast from 'react-hot-toast';
import { Copy, KeyRound } from 'lucide-react';
import Modal from '@/components/ui/Modal';
import { BUILDER_PRIMARY_BUTTON_CLASS, SECONDARY_BUTTON_CLASS } from '@/components/csm-settings/constants';

export interface RevealedSecret {
  title: string;
  /** Shown above the values, e.g. how to use them. */
  description: string;
  values: { label: string; value: string; secret?: boolean }[];
}

interface Props {
  revealed: RevealedSecret | null;
  onClose: () => void;
}

async function copy(value: string, label: string) {
  try {
    await navigator.clipboard.writeText(value);
    toast.success(`${label} copied.`);
  } catch {
    toast.error('Could not copy. Select the text and copy it manually.');
  }
}

/** Shows a key or secret exactly once; the server never returns it again. */
export default function SecretRevealModal({ revealed, onClose }: Props) {
  return (
    <Modal isOpen={revealed !== null} onClose={onClose}>
      <div className="mx-4 w-full max-w-xl rounded-xl bg-white shadow-xl" role="document">
        <div className="flex items-center gap-2 border-b border-gray-200 px-6 py-4">
          <KeyRound className="h-5 w-5 text-indigo-600" aria-hidden />
          <h2 className="text-lg font-semibold text-gray-900">{revealed?.title}</h2>
        </div>
        <div className="flex flex-col gap-4 p-6">
          <p className="text-sm text-gray-600">{revealed?.description}</p>
          <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
            Copy it now. For security it is stored hashed or encrypted and will not be shown again.
          </p>
          {revealed?.values.map(({ label, value, secret }) => (
            <div key={label} className="flex flex-col gap-1.5">
              <span className="text-sm font-medium text-gray-700">{label}</span>
              <div className="flex items-center gap-2">
                <code
                  className={`flex-1 break-all rounded-lg border px-3 py-2 font-mono text-xs ${
                    secret ? 'border-indigo-200 bg-indigo-50 text-indigo-900' : 'border-gray-200 bg-gray-50 text-gray-800'
                  }`}
                  data-testid={`revealed-${label}`}
                >
                  {value}
                </code>
                <button
                  type="button"
                  onClick={() => copy(value, label)}
                  aria-label={`Copy ${label}`}
                  className={SECONDARY_BUTTON_CLASS}
                >
                  <Copy className="h-4 w-4" aria-hidden />
                  Copy
                </button>
              </div>
            </div>
          ))}
          <div className="flex justify-end border-t border-gray-200 pt-3">
            <button type="button" onClick={onClose} className={BUILDER_PRIMARY_BUTTON_CLASS}>
              I&apos;ve stored it
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
