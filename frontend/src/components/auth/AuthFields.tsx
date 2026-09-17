import React from 'react';
import { FormInput } from '../form';

type AuthFieldConfig = {
  label: string;
  type: string;
  name: string;
  value: string;
  onChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  error?: string;
  description?: React.ReactNode;
  required?: boolean;
  placeholder?: string;
};

type AuthFieldsProps = {
  fields: AuthFieldConfig[];
  footer?: React.ReactNode;
  footerAlign?: 'start' | 'center' | 'end';
  footerClassName?: string;
};

const footerAlignmentClasses = {
  start: 'flex items-center justify-start',
  center: 'text-center',
  end: 'flex items-center justify-end',
};

export default function AuthFields({
  fields,
  footer,
  footerAlign = 'center',
  footerClassName = '',
}: AuthFieldsProps) {
  return (
    <>
      {fields.map((field) => (
        <FormInput
          key={field.name}
          label={field.label}
          type={field.type}
          name={field.name}
          value={field.value}
          onChange={field.onChange}
          error={field.error}
          description={field.description}
          required={field.required}
          placeholder={field.placeholder}
        />
      ))}
      {footer ? (
        <div className={`${footerAlignmentClasses[footerAlign]} ${footerClassName}`}>
          {footer}
        </div>
      ) : null}
    </>
  );
}
