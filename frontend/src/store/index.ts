import { configureStore } from '@reduxjs/toolkit';
import { useDispatch, useSelector } from 'react-redux';
import { orgCustomizationApi } from './orgCustomizationApi';

export const makeStore = () =>
  configureStore({
    reducer: { [orgCustomizationApi.reducerPath]: orgCustomizationApi.reducer },
    middleware: (getDefaultMiddleware) => getDefaultMiddleware().concat(orgCustomizationApi.middleware),
  });

export type AppStore = ReturnType<typeof makeStore>;
export type RootState = ReturnType<AppStore['getState']>;
export type AppDispatch = AppStore['dispatch'];

export const useAppDispatch = useDispatch.withTypes<AppDispatch>();
export const useAppSelector = useSelector.withTypes<RootState>();
