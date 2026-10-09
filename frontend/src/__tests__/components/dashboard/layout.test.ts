import { placeWidget } from '@/components/dashboard/builder/layout';

describe('dashboard placement', () => {
  const cards = [
    { id: 'a', x: 0, y: 0, w: 6, h: 4 },
    { id: 'b', x: 6, y: 0, w: 6, h: 4 },
  ];

  it('places a moved card below another card when its target is occupied', () => {
    expect(placeWidget(cards, { ...cards[1], x: 0 }, 12, 20)).toEqual([
      cards[0], { id: 'b', x: 0, y: 4, w: 6, h: 4 },
    ]);
  });

  it('keeps a resized card within the grid and away from another card', () => {
    expect(placeWidget(cards, { ...cards[0], w: 8 }, 12, 20)).toEqual([
      { id: 'a', x: 0, y: 4, w: 8, h: 4 }, cards[1],
    ]);
  });

  it('keeps the layout when the grid has no free row', () => {
    expect(placeWidget(cards, { id: 'c', x: 0, y: 0, w: 12, h: 4 }, 12, 4)).toEqual(cards);
  });
});
