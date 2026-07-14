import { create } from 'zustand';
import type { WorkItem } from '../types';

interface AppState {
  activeTab: string;
  workDir: string | null;
  works: WorkItem[];
  isLoading: boolean;
  isTranslating: boolean;
  progressPercent: number;
  progressStage: string;
  progressMessage: string;

  setActiveTab: (tab: string) => void;
  setWorkDir: (dir: string | null) => void;
  setWorks: (works: WorkItem[]) => void;
  setLoading: (loading: boolean) => void;
  setTranslating: (translating: boolean) => void;
  setProgress: (stage: string, percent: number, message: string) => void;
}

export const useAppStore = create<AppState>((set) => ({
  activeTab: 'workspace',
  workDir: null,
  works: [],
  isLoading: false,
  isTranslating: false,
  progressPercent: 0,
  progressStage: '',
  progressMessage: '',

  setActiveTab: (tab) => set({ activeTab: tab }),
  setWorkDir: (dir) => set({ workDir: dir, works: [] }),
  setWorks: (works) => set({ works }),
  setLoading: (loading) => set({ isLoading: loading }),
  setTranslating: (translating) => set({ isTranslating: translating }),
  setProgress: (stage, percent, message) =>
    set({ progressStage: stage, progressPercent: percent, progressMessage: message }),
}));