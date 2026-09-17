'use client';

import { useEffect, useState } from 'react';
import { isAxiosError } from 'axios';
import { authAPI } from '@/lib/api';
import type { PasswordValidationRule } from '@/types/auth';

export type PasswordCheckStatus = {
  inputKey: string;
  ready: boolean;
  fieldErrors?: Partial<Record<'username' | 'email', string>>;
};

type Props = {
  password: string;
  username: string;
  email: string;
  onCheckReadyChange?: (status: PasswordCheckStatus) => void;
};

export default function PasswordRequirements({ password, username, email, onCheckReadyChange }: Props) {
  const inputKey = JSON.stringify([password, username, email]);
  const [retryCount, setRetryCount] = useState(0);
  const [validation, setValidation] = useState<{
    inputKey: string;
    rules: PasswordValidationRule[];
    unavailable?: boolean;
  } | null>(null);
  const current = validation?.inputKey === inputKey;
  const checking = !current;
  const rules = validation?.rules || [];
  const visibleRules = current && !validation?.unavailable
    ? rules.filter(rule => rule.valid === false && rule.id !== 'username' && rule.id !== 'email') : [];

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    onCheckReadyChange?.({ inputKey, ready: false });
    const timer = setTimeout(async () => {
      try {
        const result = await authAPI.validatePassword(
          { password, username, email }, controller.signal,
        );
        if (active) {
          setValidation({ inputKey, rules: result.rules });
          onCheckReadyChange?.({ inputKey, ready: true });
        }
      } catch (error) {
        if (active) {
          const data = isAxiosError(error) && error.response?.status === 400
            ? error.response.data : null;
          const fieldRules: PasswordValidationRule[] = Object.entries({
            password: 'Password', username: 'Username', email: 'Email',
          }).flatMap(([field, label]) => {
            const reasons = data?.[field];
            const messages = Array.isArray(reasons)
              ? reasons.filter((reason): reason is string => typeof reason === 'string') : [];
            return messages.length ? [{
              id: field, help_text: '', valid: false,
              errors: messages.map(message => `${label}: ${message}`),
            }] : [];
          });
          setValidation(previous => ({
            inputKey,
            rules: fieldRules.length ? fieldRules : previous?.rules || [],
            unavailable: !fieldRules.length,
          }));
          onCheckReadyChange?.({
            inputKey,
            ready: Boolean(fieldRules.length),
            fieldErrors: Object.fromEntries(fieldRules
              .filter(rule => rule.id === 'username' || rule.id === 'email')
              .map(rule => [rule.id, `❌ ${rule.errors.join(' ')}`])),
          });
        }
      }
    }, password ? 300 : 0);

    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [password, username, email, inputKey, retryCount, onCheckReadyChange]);

  return (
    <div className="space-y-1 text-sm" aria-live="polite" aria-busy={checking}>
      {!password && (
        <p className="text-gray-500">
          Requirement: Use 8–256 characters, not just numbers; avoid common passwords or similarity to username/email.
        </p>
      )}
      {visibleRules.length > 0 && (
        <ul className="space-y-1">
          {visibleRules.map(rule => (
            <li key={rule.id} className="flex gap-2 text-red-600">
              <span aria-hidden="true" className="shrink-0">❌</span>
              <span>
                <span className="sr-only">Not met: </span>
                {rule.errors.join(' ')}
              </span>
            </li>
          ))}
        </ul>
      )}
      {checking && (password || retryCount > 0) && <p className="text-gray-500">Checking password…</p>}
      {current && validation?.unavailable && (
        <div className="flex items-center gap-2 text-gray-600">
          <p>Unable to check password. Please retry.</p>
          <button
            type="button"
            className="shrink-0 text-blue-600 underline"
            onClick={() => {
              setValidation(null);
              setRetryCount(count => count + 1);
            }}
          >
            Retry
          </button>
        </div>
      )}
      {current && password && !validation?.unavailable && Boolean(validation?.rules.length) &&
        validation?.rules.every(rule => rule.valid === true) && (
          <p className="flex items-center gap-2 text-green-700">
            <span aria-hidden="true" className="shrink-0">✅</span>
            Password is valid.
          </p>
        )}
    </div>
  );
}
