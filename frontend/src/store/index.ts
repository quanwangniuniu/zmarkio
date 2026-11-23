import { configureStore } from '@reduxjs/toolkit';
import orgCustomizationReducer from './modules/orgCustomization/slice';

export const makeStore = () =>
  configureStore({
    reducer: {
      orgCustomization: orgCustomizationReducer,
    },
  });

export type AppStore = ReturnType<typeof makeStore>;
export type RootState = ReturnType<AppStore['getState']>;
export type AppDispatch = AppStore['dispatch'];
