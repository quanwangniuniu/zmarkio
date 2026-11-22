import { createApi } from '@reduxjs/toolkit/query/react';
import { apiBaseQuery } from './baseQuery';

export const baseApi = createApi({
  reducerPath: 'api',
  baseQuery: apiBaseQuery,
  endpoints: () => ({}),
});
