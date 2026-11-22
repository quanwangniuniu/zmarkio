'use client';

import { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * @param {{
 *   isOpen: boolean,
 *   onClose: () => void,
 *   children: import('react').ReactNode,
 *   disableBackdropClose?: boolean,
 *   ariaLabel?: string,
 * }} props
 */
export default function Modal({ isOpen, onClose, children, disableBackdropClose = false, ariaLabel }) {
  const dialogRef = useRef(null);
  const headingId = useId();
  const [labelledBy, setLabelledBy] = useState(undefined);

  useEffect(() => {
    const handleEscape = (e) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };

    if (isOpen) {
      document.addEventListener('keydown', handleEscape);
      document.body.style.overflow = 'hidden';
    }

    return () => {
      document.removeEventListener('keydown', handleEscape);
      document.body.style.overflow = 'unset';
    };
  }, [isOpen, onClose]);

  useEffect(() => {
    if (!isOpen) return;
    const dialog = dialogRef.current;
    if (!dialog) return;
    const previouslyFocused = document.activeElement;

    const heading = dialog.querySelector('h1, h2, h3, h4');
    if (heading) {
      if (!heading.id) heading.id = headingId;
      setLabelledBy(heading.id);
    }
    if (!dialog.contains(document.activeElement)) {
      dialog.focus();
    }

    const handleTab = (e) => {
      if (e.key !== 'Tab') return;
      const focusable = dialog.querySelectorAll(FOCUSABLE_SELECTOR);
      if (focusable.length === 0) {
        e.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || active === dialog)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', handleTab);
    return () => {
      document.removeEventListener('keydown', handleTab);
      if (previouslyFocused instanceof HTMLElement && document.contains(previouslyFocused)) {
        previouslyFocused.focus();
      }
    };
  }, [isOpen, headingId]);

  if (!isOpen) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto p-4">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black bg-opacity-50 transition-opacity"
        onClick={disableBackdropClose ? undefined : onClose}
      />

      {/* Modal Content */}
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={ariaLabel}
        aria-labelledby={ariaLabel ? undefined : labelledBy}
        tabIndex={-1}
        className="relative z-10 mx-auto outline-none"
      >
        {children}
      </div>
    </div>,
    document.body
  );
}
