'use client';

import React, { useState, useCallback, useEffect, useRef } from 'react';
import { Calendar, X, Loader2 } from 'lucide-react';

export interface InlineDateControllerProps {
  value: string | null | undefined;
  onSave: (value: string | null) => Promise<void> | void;
  validate?: (value: string | null) => string | null;
  className?: string;
  renderTrigger?: (value: string | null, formatted: string) => React.ReactNode;
  placeholder?: string;
  minDate?: string; // For validation (e.g., must be after start_date)
  maxDate?: string; // For validation (e.g., must be before end_date)
}

const formatDate = (dateString: string | null | undefined): string => {
  if (!dateString) return '';
  const date = new Date(dateString);
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
};

const formatDateForInput = (dateString: string | null | undefined): string => {
  if (!dateString) return '';
  const date = new Date(dateString);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
};

const normalizeDate = (dateString: string | null | undefined): string | null => {
  if (!dateString || dateString.trim() === '') return null;
  return dateString.split('T')[0] || null;
};

const NO_PENDING_SAVE = Symbol('no-pending-date-save');
type PendingSave = string | null | typeof NO_PENDING_SAVE;

function InlineDateController({
  value: initialValue,
  onSave,
  validate,
  className = '',
  renderTrigger,
  placeholder = 'Not set',
  minDate,
  maxDate,
}: InlineDateControllerProps) {
  const [isEditing, setIsEditing] = useState(false);
  const [dateValue, setDateValue] = useState<string>(formatDateForInput(initialValue));
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const originalValueRef = useRef<string | null | undefined>(initialValue);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  // Duplicate events are de-duplicated, while a genuinely newer value is kept
  // and drained after the in-flight request completes.
  const savingRef = useRef(false);
  const pendingSaveRef = useRef<PendingSave>(NO_PENDING_SAVE);

  // Sync value when initialValue changes externally (only when not editing)
  useEffect(() => {
    if (!isEditing && initialValue !== originalValueRef.current) {
      setDateValue(formatDateForInput(initialValue));
      originalValueRef.current = initialValue;
    }
  }, [initialValue, isEditing]);

  // Focus input when entering edit mode
  useEffect(() => {
    if (isEditing && inputRef.current) {
      inputRef.current.focus();
      inputRef.current.showPicker?.();
    }
  }, [isEditing]);

  const drainSaveQueue = useCallback(async () => {
    if (savingRef.current) return;
    savingRef.current = true;
    setIsLoading(true);
    setError(null);

    try {
      while (pendingSaveRef.current !== NO_PENDING_SAVE) {
        const valueToSave = pendingSaveRef.current;
        pendingSaveRef.current = NO_PENDING_SAVE;

        if (normalizeDate(valueToSave) === normalizeDate(originalValueRef.current)) {
          continue;
        }

        await onSave(valueToSave);
        originalValueRef.current = valueToSave;
      }
      setIsEditing(false);
    } catch (err) {
      pendingSaveRef.current = NO_PENDING_SAVE;
      setError(err instanceof Error ? err.message : 'Save failed');
    } finally {
      savingRef.current = false;
      setIsLoading(false);
    }
  }, [onSave]);

  const queueSave = useCallback((newValue: string | null) => {
    // Validate
    if (validate) {
      const validationError = validate(newValue);
      if (validationError) {
        setError(validationError);
        return;
      }
    }

    // Additional validation: check minDate
    if (newValue && minDate && newValue < minDate) {
      setError('Date must be after minimum date');
      return;
    }

    // Additional validation: check maxDate
    if (newValue && maxDate && newValue > maxDate) {
      setError('Date must be before maximum date');
      return;
    }
    
    const normalizedNew = normalizeDate(newValue);
    const normalizedOriginal = normalizeDate(originalValueRef.current);
    
    if (normalizedNew === normalizedOriginal && !savingRef.current) {
      setIsEditing(false);
      return;
    }

    pendingSaveRef.current = newValue;
    void drainSaveQueue();
  }, [drainSaveQueue, maxDate, minDate, validate]);

  const handleSave = useCallback(() => {
    queueSave(dateValue.trim() || null);
  }, [dateValue, queueSave]);

  // Close on outside click
  useEffect(() => {
    if (!isEditing) return;

    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        handleSave();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [isEditing, handleSave]);

  const handleCancel = useCallback(() => {
    setDateValue(formatDateForInput(originalValueRef.current));
    setIsEditing(false);
    setError(null);
  }, []);

  const handleClear = useCallback(() => {
    setDateValue('');
    
    queueSave(null);
  }, [queueSave]);

  const startEdit = useCallback(() => {
    originalValueRef.current = initialValue;
    setDateValue(formatDateForInput(initialValue));
    setIsEditing(true);
    setError(null);
  }, [initialValue]);

  if (isEditing) {
    const formatted = formatDate(dateValue || null);
    return (
      <div ref={containerRef} className={`inline-flex items-center gap-2 ${className}`}>
        <div className="relative">
          <input
            ref={inputRef}
            type="date"
            value={dateValue}
            min={minDate}
            max={maxDate}
            onChange={(e) => {
              // Only track the value here — don't save. The native picker also
              // changes the value while the user moves between months (it carries
              // the selected day along), and typing a year passes through 0002,
              // 0020, 0202. Enter, blur or a click outside commits, the same as
              // the inline text editors.
              setDateValue(e.target.value);
            }}
            onBlur={handleSave}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.preventDefault();
                handleCancel();
              } else if (e.key === 'Enter') {
                e.preventDefault();
                handleSave();
              }
            }}
            className="px-2 py-1 border border-blue-500 rounded text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          {dateValue && (
            <button
              type="button"
              onClick={handleClear}
              className="absolute right-1 top-1/2 -translate-y-1/2 p-0.5 hover:bg-gray-200 rounded"
              title="Clear date"
            >
              <X className="h-3 w-3 text-gray-400" />
            </button>
          )}
        </div>
        {isLoading && <Loader2 className="h-4 w-4 animate-spin text-gray-400" />}
        {error && <span className="text-xs text-red-600">{error}</span>}
      </div>
    );
  }

  const formatted = formatDate(initialValue);
  const displayValue = formatted || placeholder;
  const triggerContent = renderTrigger
    ? renderTrigger(initialValue || null, formatted)
    : (
        <span className={!initialValue ? 'text-gray-400 italic' : ''}>{displayValue}</span>
      );

  return (
    <div
      onClick={startEdit}
      className={`cursor-pointer hover:text-[#0E8A96] transition-colors inline-flex items-center gap-1 ${className}`}
      title="Click to edit"
    >
      <Calendar className="h-4 w-4 text-gray-400" />
      {triggerContent}
    </div>
  );
}

export default InlineDateController;

