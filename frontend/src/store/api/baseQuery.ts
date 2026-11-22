import { AxiosError, type AxiosResponse } from 'axios';
import { fakeBaseQuery } from '@reduxjs/toolkit/query';
import { getApiErrorDetail } from '@/lib/api/errorMessage';

export type QueryError = { status?: number; data?: unknown };

export const apiBaseQuery = fakeBaseQuery<QueryError>();

export const fromApi = async <T>(request: () => Promise<T>) => {
  try {
    return { data: await request() };
  } catch (error) {
    const response = (error as AxiosError).response;
    return { error: { status: response?.status, data: response?.data } };
  }
};

export const getQueryErrorDetail = (error: unknown, fallback: string) => {
  const { status, data } = (error ?? {}) as QueryError;
  if (status === undefined) return getApiErrorDetail(error, fallback);
  return getApiErrorDetail(new AxiosError(undefined, undefined, undefined, undefined, { status, data } as AxiosResponse), fallback);
};
