import { AxiosError, type AxiosRequestConfig, type AxiosResponse } from 'axios';
import type { BaseQueryFn } from '@reduxjs/toolkit/query';
import api from '@/lib/api';
import { getApiErrorDetail } from '@/lib/api/errorMessage';

export type QueryError = { status?: number; data?: unknown };

type QueryArgs = Pick<AxiosRequestConfig, 'url' | 'method' | 'data' | 'params'>;

export const axiosBaseQuery = (): BaseQueryFn<QueryArgs, unknown, QueryError> => async (args) => {
  try {
    const response = await api.request(args);
    return { data: response.data };
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
