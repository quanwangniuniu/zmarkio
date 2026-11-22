import { combineSlices, configureStore } from '@reduxjs/toolkit';
import { baseApi } from './api/baseApi';

export const rootReducer = combineSlices(baseApi);

export const makeStore = () =>
  configureStore({
    reducer: rootReducer,
    middleware: (getDefaultMiddleware) => getDefaultMiddleware().concat(baseApi.middleware),
  });

export type AppStore = ReturnType<typeof makeStore>;
export type RootState = ReturnType<typeof rootReducer>;
export type AppDispatch = AppStore['dispatch'];
