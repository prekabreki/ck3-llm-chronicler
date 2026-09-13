// ck3_chronicler-zx2l: tests for the gender-aware title-text
// transform. Render-path tests cover the icon-fallback behaviour; the
// transform tests are the pure formatHolderTitle table.

import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { HeldTitlesLines, PrimaryTitleLine } from './PrimaryTitleLine';
import { formatHolderTitle } from './titleFormatting';
import type { PrimaryTitleSummary } from '../api/types';

describe('formatHolderTitle', () => {
  const cases: {
    name: string;
    primary: PrimaryTitleSummary;
    female: boolean | null;
    want: string | null;
  }[] = [
    {
      name: 'kingdom male',
      primary: { key: 'k_england', name: 'Kingdom of England', tier: 'kingdom' },
      female: false,
      want: 'King of England',
    },
    {
      name: 'kingdom female',
      primary: { key: 'k_england', name: 'Kingdom of England', tier: 'kingdom' },
      female: true,
      want: 'Queen of England',
    },
    {
      name: 'kingdom unknown gender defaults masculine',
      primary: { key: 'k_england', name: 'Kingdom of England', tier: 'kingdom' },
      female: null,
      want: 'King of England',
    },
    {
      name: 'empire',
      primary: { key: 'e_byzantium', name: 'Empire of Byzantium', tier: 'empire' },
      female: false,
      want: 'Emperor of Byzantium',
    },
    {
      name: 'duchy female',
      primary: { key: 'd_kent', name: 'Duchy of Kent', tier: 'duchy' },
      female: true,
      want: 'Duchess of Kent',
    },
    {
      name: 'county',
      primary: { key: 'c_thomond', name: 'County of Thomond', tier: 'county' },
      female: false,
      want: 'Count of Thomond',
    },
    {
      name: 'barony female',
      primary: { key: 'b_villedieu', name: 'Barony of Villedieu', tier: 'barony' },
      female: true,
      want: 'Baroness of Villedieu',
    },
    {
      name: 'name absent → derive from engine key',
      primary: { key: 'k_west_francia', name: null, tier: 'kingdom' },
      female: false,
      want: 'King of West Francia',
    },
    {
      name: 'name does not match tier prefix → keep raw name as place',
      primary: { key: 'd_special', name: 'Mar of Kerman', tier: 'duchy' },
      female: false,
      want: 'Duke of Mar of Kerman',
    },
    {
      name: 'tier=other (non-tiered title) → fall back to raw name',
      primary: { key: 'x_court_chaplain', name: 'Court Chaplain', tier: 'other' },
      female: false,
      want: 'Court Chaplain',
    },
    {
      name: 'tier=other with no name → null',
      primary: { key: 'x_unknown', name: null, tier: 'other' },
      female: false,
      want: null,
    },
  ];

  for (const c of cases) {
    it(c.name, () => {
      expect(formatHolderTitle(c.primary, c.female)).toBe(c.want);
    });
  }
});

describe('<PrimaryTitleLine>', () => {
  it('renders nothing when primary_title is null', () => {
    const { container } = render(
      <PrimaryTitleLine primaryTitle={null} female={false} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it('renders the title text and the tier crown image', () => {
    const { getByText, container } = render(
      <PrimaryTitleLine
        primaryTitle={{
          key: 'k_england',
          name: 'Kingdom of England',
          tier: 'kingdom',
        }}
        female={false}
      />,
    );
    expect(getByText('King of England')).toBeInTheDocument();
    const img = container.querySelector('img');
    expect(img).not.toBeNull();
    expect(img?.getAttribute('src')).toBe(
      '/api/heraldry/assets/title_icons/kingdom.png',
    );
  });

  it('skips the crown for tier=other (no canonical icon)', () => {
    const { container } = render(
      <PrimaryTitleLine
        primaryTitle={{
          key: 'x_court_chaplain',
          name: 'Court Chaplain',
          tier: 'other',
        }}
        female={false}
      />,
    );
    expect(container.querySelector('img')).toBeNull();
  });
});

describe('<HeldTitlesLines> (ck3_chronicler-9ngy)', () => {
  const scotland: PrimaryTitleSummary = {
    key: 'k_scotland',
    name: 'Kingdom of Scotland',
    tier: 'kingdom',
  };
  const ireland: PrimaryTitleSummary = {
    key: 'k_ireland',
    name: 'Kingdom of Ireland',
    tier: 'kingdom',
  };
  const kent: PrimaryTitleSummary = {
    key: 'd_kent',
    name: 'Duchy of Kent',
    tier: 'duchy',
  };

  it('renders every held title, not just the primary', () => {
    const { getByText } = render(
      <HeldTitlesLines
        heldTitles={[scotland, ireland, kent]}
        primaryTitle={scotland}
        female={false}
      />,
    );
    expect(getByText('King of Scotland')).toBeInTheDocument();
    expect(getByText('King of Ireland')).toBeInTheDocument();
    expect(getByText('Duke of Kent')).toBeInTheDocument();
  });

  it('falls back to primaryTitle when heldTitles is empty/undefined', () => {
    const { getByText, queryByText } = render(
      <HeldTitlesLines
        heldTitles={undefined}
        primaryTitle={scotland}
        female={false}
      />,
    );
    expect(getByText('King of Scotland')).toBeInTheDocument();
    expect(queryByText('King of Ireland')).toBeNull();
  });

  it('renders nothing when there are no titles at all', () => {
    const { container } = render(
      <HeldTitlesLines heldTitles={[]} primaryTitle={null} female={false} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
