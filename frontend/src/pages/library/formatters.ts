// LibraryPage formatters — byline, in-game span, and blurb truncation.
// Extracted from LibraryPage.tsx by audit M-F3 / ck3_chronicler-27ov.58
// so the card and page shell stay readable.

import type { CampaignResponse } from '../../api/types';

export function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return s.slice(0, max - 1).trimEnd() + '…';
}

export function formatByline(c: CampaignResponse): string | null {
  if (!c.current_player_name) return null;
  let head = c.current_player_name;
  if (c.current_player_nickname) {
    head = `${head}, called ${c.current_player_nickname}`;
  }
  let suffix: string | null = null;
  if (c.current_house_name) {
    suffix = `House ${c.current_house_name}`;
  } else if (c.founding_dynasty_name) {
    suffix = `of the ${c.founding_dynasty_name}`;
  }
  return suffix ? `${head} · ${suffix}` : head;
}

export function formatInGameSpan(c: CampaignResponse): string | null {
  const bm = formatYearMonth(c.bookmark_date);
  const cur = formatYearMonth(c.current_in_game_date);
  if (bm && cur) return `${bm} → ${cur}`;
  if (bm) return `since ${bm}`;
  if (cur) return `as of ${cur}`;
  return null;
}

function formatYearMonth(d: string | null): string | null {
  if (!d) return null;
  const parts = d.split('.');
  if (parts.length < 2) return d;
  return `${parts[0]}.${parts[1]}`;
}
