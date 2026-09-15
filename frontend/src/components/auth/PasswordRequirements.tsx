'use client';

import { useEffect, useState } from 'react';
import { authAPI } from '@/lib/api';
import type { PasswordValidationRule } from '@/types/auth';

type Props = { password: string; username: string; email: string };

export default function PasswordRequirements({ password, username, email }: Props) {
  const inputKey = JSON.stringify([password, username, email]);
  const [validation, setValidation] = useState<{
    inputKey: string;
    rules: PasswordValidationRule[];
    unavailable?: boolean;
  } | null>(null);
  const current = validation?.inputKey === inputKey;
  const checking = !current;
  const rules = validation?.rules || [];
  const visibleRules = password && current && !validation?.unavailable
    ? rules.filter(rule => rule.valid === false) : [];

  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    const timer = setTimeout(async () => {
      try {
        const result = await authAPI.validatePassword(
          { password, username, email }, controller.signal,
        );
        if (active) setValidation({ inputKey, rules: result.rules });
      } catch {
        if (active) {
          setValidation(previous => ({
            inputKey, rules: previous?.rules || [], unavailable: true,
          }));
        }
      }
    }, password ? 300 : 0);

    return () => {
      active = false;
      clearTimeout(timer);
      controller.abort();
    };
  }, [password, username, email, inputKey]);

  return (
    <div className="space-y-1 text-sm" aria-live="polite" aria-busy={Boolean(password) && checking}>
      {!password && (
        <p className="text-gray-500">
          Password requirement: Use 8+ characters, not just numbers; avoid common passwords or similarity to username/email.
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
      {password && checking && <p className="text-gray-500">Checking password…</p>}
      {password && current && validation?.unavailable && (
        <p className="text-gray-600">Password check unavailable. You can still submit to check your password.</p>
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
