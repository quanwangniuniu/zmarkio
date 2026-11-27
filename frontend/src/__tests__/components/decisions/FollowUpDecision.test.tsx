import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import DecisionsGraphSection from '@/components/decisions/DecisionsGraphSection';
import { DecisionAPI } from '@/lib/api/decisionApi';
import toast from 'react-hot-toast';

const mockUpdateMapUrl = jest.fn();
const mockUpdated = jest.fn();
jest.mock('@/lib/api/decisionApi', () => ({ DecisionAPI: { createDraft: jest.fn(), patchDraft: jest.fn() } }));
jest.mock('react-hot-toast', () => ({ __esModule: true, default: { success: jest.fn(), error: jest.fn() } }));
jest.mock('next/dynamic', () => ({
  __esModule: true,
  default: () => require('@/components/decisions/DecisionTreeFlow').default,
}));
jest.mock('@/components/decisions/hooks/useDecisionMapUrlState', () => ({
  useDecisionMapUrlState: () => ({ timelineMode: 'tree', fullscreen: true, fullscreenSelectedId: 112, updateMapUrl: mockUpdateMapUrl }),
}));
jest.mock('@/components/decisions/hooks/useDecisionGraphLinks', () => ({
  useDecisionGraphLinks: () => ({ edges: [], linkingEnabled: false, linkingDisabled: true }),
}));
jest.mock('@/components/decisions/DecisionTree', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/decisions/DecisionTreeNavigator', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/decisions/DecisionGraphMiniMap', () => ({ __esModule: true, default: () => null }));
jest.mock('@/components/decisions/DecisionFullscreenPanel', () => ({
  __esModule: true, default: ({ children }: any) => <div>{children}</div>,
}));
// Stand in for the existing editor to detect unmounts or lost unsaved input.
jest.mock('@/components/decisions/DecisionTreeDetailPanel', () => ({
  __esModule: true, default: () => <textarea aria-label="Unsaved decision content" defaultValue="" />,
}));
jest.mock('@/components/decisions/decisionGraphLayout', () => ({
  layoutDecisionGraphNodes: (nodes: any[]) => nodes.map((node) => ({ ...node, position: { x: 0, y: 0 } })),
  layoutDecisionGraphNodesAsync: async (nodes: any[]) => nodes.map((node) => ({ ...node, position: { x: 0, y: 0 } })),
}));
jest.mock('reactflow', () => {
  const React = require('react');
  const useGraphState = (initial: any[]) => {
    const [value, setValue] = React.useState(initial);
    return [value, setValue, React.useCallback(() => {}, [])];
  };
  return {
    __esModule: true,
    default: ({ nodes, onNodeClick }: any) => <div>{nodes.map((node: any) => (
      <button key={node.id} onClick={(event) => onNodeClick(event, node)}>Select {node.data.label}</button>
    ))}</div>,
    useNodesState: useGraphState, useEdgesState: useGraphState,
    Background: () => null, Controls: () => null, Handle: () => null,
    BackgroundVariant: { Dots: 'dots' }, ConnectionLineType: { SmoothStep: 'smoothstep' },
    MarkerType: { ArrowClosed: 'arrowclosed' }, Position: { Top: 'top', Bottom: 'bottom' },
  };
});

const parent = {
  id: 112, slug: 'parent', title: 'Parent decision', status: 'DRAFT' as const,
  projectId: 10, projectSeq: 12, topic: 'pricing', createdAt: '2026-11-01T00:00:00Z',
  updatedAt: '2026-11-01T00:00:00Z',
};

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
});

it.each([false, true])('preserves selection/content on 400 and retries (change parent: %s)', async (changeParent) => {
  const create = DecisionAPI.createDraft as jest.Mock;
  create.mockRejectedValueOnce({ response: { status: 400, data: {
    parentDecisionIds: ['Decision 112 does not exist in this project.'],
  } } }).mockResolvedValueOnce({ id: 113, slug: 'child' });
  render(<DecisionsGraphSection graph={{ nodes: [parent, { ...parent, id: 114, slug: 'other', title: 'Other parent' }], edges: [] }} projectId={10}
    canEdit canCreate onEditDecision={jest.fn()} onDecisionUpdated={mockUpdated} />);
  fireEvent.change(screen.getByLabelText('Unsaved decision content'), { target: { value: 'Keep this reasoning' } });
  fireEvent.click(await screen.findByRole('button', { name: 'Select Parent decision' }));
  mockUpdateMapUrl.mockClear();
  fireEvent.click(screen.getByRole('button', { name: 'Graph tools' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add follow-up' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Add follow-up' }));

  expect(await screen.findByRole('alert')).toHaveTextContent('The selected parent decision is no longer available.');
  expect(screen.getByRole('alert')).not.toHaveTextContent('112');
  expect(create).toHaveBeenCalledWith(10, { topic: 'pricing', parentDecisionIds: [112] });
  expect(DecisionAPI.patchDraft).not.toHaveBeenCalled();
  expect(mockUpdateMapUrl).not.toHaveBeenCalled();
  expect(mockUpdated).not.toHaveBeenCalled();
  expect(toast.success).not.toHaveBeenCalled();
  expect(screen.getByLabelText('Unsaved decision content')).toHaveValue('Keep this reasoning');
  expect(screen.getByRole('button', { name: 'Add follow-up' })).toBeEnabled();

  if (changeParent) {
    fireEvent.click(screen.getByRole('button', { name: 'Select Other parent' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  }
  fireEvent.click(screen.getByRole('button', { name: 'Add follow-up' }));
  await waitFor(() => expect(toast.success).toHaveBeenCalledWith('Follow-up decision added'));
  expect(create).toHaveBeenCalledTimes(2);
  expect(create).toHaveBeenLastCalledWith(10, { topic: 'pricing', parentDecisionIds: [changeParent ? 114 : 112] });
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(mockUpdateMapUrl).toHaveBeenCalledWith({ fullscreen: true, decisionId: 'child' });
});

it.each([
  [{ response: { status: 400, data: { detail: 'Invalid topic.' } } }, 'Invalid topic.'],
  [{ response: { status: 403, data: { detail: 'Permission denied.' } } }, 'Permission denied.'],
  [{ response: { status: 400, data: { parentDecisionIds: [], detail: 'Invalid topic.' } } }, 'Invalid topic.'],
  [new Error('Network Error'), 'Network Error'],
  [{ response: { status: 500 } }, 'Failed to create child decision'],
])('preserves unrelated API error handling (%s)', async (error, expectedMessage) => {
  (DecisionAPI.createDraft as jest.Mock).mockRejectedValueOnce(error);
  render(<DecisionsGraphSection graph={{ nodes: [parent], edges: [] }} projectId={10}
    canEdit canCreate onEditDecision={jest.fn()} onDecisionUpdated={mockUpdated} />);
  fireEvent.click(await screen.findByRole('button', { name: 'Select Parent decision' }));
  fireEvent.click(screen.getByRole('button', { name: 'Graph tools' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Add follow-up' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Add follow-up' }));

  expect(await screen.findByRole('alert')).toHaveTextContent(expectedMessage as string);
  expect(screen.getByRole('button', { name: 'Add follow-up' })).toBeEnabled();
  expect(toast.error).not.toHaveBeenCalled();
  expect(toast.success).not.toHaveBeenCalled();
});
