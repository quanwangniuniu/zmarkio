import { moveWidget, placeWidget } from '@/components/dashboard/builder/layout';

describe('dashboard placement', () => {
  const cards = [
    { id: 'a', x: 0, y: 0, w: 6, h: 4 },
    { id: 'b', x: 6, y: 0, w: 6, h: 4 },
  ];

  it('puts the moved card in its target and pushes the displaced card down', () => {
    expect(placeWidget(cards, { ...cards[1], x: 0 }, 12, 20)).toEqual([
      { ...cards[0], y: 4 }, { id: 'b', x: 0, y: 0, w: 6, h: 4 },
    ]);
  });

  it('keeps the resized card in place and pushes its neighbor down', () => {
    expect(placeWidget(cards, { ...cards[0], w: 8 }, 12, 20)).toEqual([
      { id: 'a', x: 0, y: 0, w: 8, h: 4 }, { ...cards[1], y: 4 },
    ]);
  });

  it('pulls cards directly below a shortened card upward without moving other columns', () => {
    const initial = [
      { id: 'attention', x: 3, y: 0, w: 3, h: 5 },
      { id: 'operations', x: 0, y: 5, w: 4, h: 10 },
      { id: 'decisions', x: 4, y: 5, w: 4, h: 10 },
      { id: 'tasks', x: 8, y: 5, w: 4, h: 10 },
      { id: 'chart', x: 0, y: 15, w: 8, h: 5 },
    ];
    const grown = placeWidget(initial, { ...initial[0], h: 12 }, 12, 40);
    expect(grown.map(({ id, y }) => [id, y])).toEqual([
      ['attention', 0], ['operations', 12], ['decisions', 12], ['tasks', 5], ['chart', 22],
    ]);

    const shortened = placeWidget(grown, { ...grown[0], h: 5 }, 12, 40);
    expect(shortened.map(({ id, y }) => [id, y])).toEqual([
      ['attention', 0], ['operations', 5], ['decisions', 5], ['tasks', 5], ['chart', 15],
    ]);
  });

  it('returns displaced cards when a card is moved down and then back up', () => {
    const initial = [
      { id: 'attention', x: 3, y: 0, w: 3, h: 5 },
      { id: 'operations', x: 0, y: 5, w: 4, h: 10 },
      { id: 'decisions', x: 4, y: 5, w: 4, h: 10 },
      { id: 'tasks', x: 8, y: 5, w: 4, h: 10 },
      { id: 'chart', x: 0, y: 15, w: 8, h: 5 },
    ];
    const movedDown = moveWidget(initial, { ...initial[0], y: 5 }, 12, 40);
    expect(movedDown.map(({ id, y }) => [id, y])).toEqual([
      ['attention', 5], ['operations', 10], ['decisions', 10], ['tasks', 5], ['chart', 20],
    ]);

    const movedBack = moveWidget(movedDown, { ...movedDown[0], y: 0 }, 12, 40);
    expect(movedBack.map(({ id, y }) => [id, y])).toEqual([
      ['attention', 0], ['operations', 5], ['decisions', 5], ['tasks', 5], ['chart', 15],
    ]);
  });

  it('keeps the layout when the grid has no free row', () => {
    expect(placeWidget(cards, { id: 'c', x: 0, y: 0, w: 12, h: 4 }, 12, 4)).toEqual(cards);
  });

  it('rejects non-finite positions before they reach the grid', () => {
    expect(placeWidget(cards, { ...cards[0], w: NaN }, 12, 20)).toEqual(cards);
    expect(moveWidget(cards, { ...cards[0], x: NaN }, 12, 20)).toEqual(cards);
  });

  it('uses the last valid drop position when the requested row overflows the grid', () => {
    const widgets = [
      { id: 'audit', x: 0, y: 0, w: 6, h: 4 },
      { id: 'team', x: 0, y: 4, w: 6, h: 4 },
      { id: 'chart', x: 0, y: 8, w: 6, h: 4 },
    ];
    const next = moveWidget(widgets, { ...widgets[0], y: 5 }, 12, 14);
    expect(next.find((widget) => widget.id === 'audit')?.y).toBe(4);
  });
});
