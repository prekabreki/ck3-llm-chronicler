// Audit F-27: left-aside panel of the chronicle folio. Shield + name
// bar + seal + vitals table + kin list. Extracted from ChroniclePage.

import { CoaTimeline } from '../../components/CoaTimeline';
import { HeldTitlesLines } from '../../components/PrimaryTitleLine';
import { HeraldryWithFallback } from '../../components/RealHeraldry';
import { useAppStore } from '../../store/appStore';
import type { CoaDefinition, Palette } from '../../components/CoaTypes';
import type {
  CharacterDetail,
  FamilyTreeNode,
  FamilyTreeResponse,
} from '../../api/types';
import { prettifyTag } from '../../util/prettify';
import { Fleuron, SmallcapsLabel } from './Ornaments';

interface AsideProps {
  campaignName: string;
  character: CharacterDetail;
  tree: FamilyTreeResponse | null;
  coa: CoaDefinition | null;
  palette: Palette | null;
}

export function ChronicleAside({
  campaignName,
  character,
  tree,
  coa,
  palette,
}: AsideProps): React.JSX.Element {
  const initial = (character.first_name ?? 'X').charAt(0).toUpperCase();
  const houseLabel = character.house_name
    ? `House ${character.house_name}`
    : character.dynasty_name ?? 'Unknown';
  return (
    <aside className="chronicle-page__aside">
      <div className="chronicle-page__shield">
        {character.house_name && (
          <div className="chronicle-page__shield-eyebrow">
            Arms · {houseLabel} · Current
          </div>
        )}
        <HeraldryWithFallback
          coa={coa}
          palette={palette}
          seed={character.ck3_id}
          size={170}
          ring
          label={`shield-${character.ck3_id}`}
        />
        <CoaTimeline
          campaignName={campaignName}
          ck3Id={character.ck3_id}
          palette={palette}
        />
      </div>

      <div className="chronicle-page__namebar">
        <div className="chronicle-page__name">
          {character.first_name ?? `Character ${character.ck3_id}`}
        </div>
        {character.nickname && (
          <div className="italic-fell chronicle-page__epithet">
            {character.nickname}
          </div>
        )}
        {character.dynasty_name && (
          <div className="chronicle-page__role-line">
            of {character.dynasty_name}
            {!character.death_date && ' · Tracked'}
          </div>
        )}
        <HeldTitlesLines
          heldTitles={character.held_titles}
          primaryTitle={character.primary_title}
          female={character.female}
          variant="chronicle-aside"
        />
      </div>

      <div className="chronicle-page__seal" aria-hidden>
        <span className="chronicle-page__seal-initial">{initial}</span>
      </div>

      <Fleuron />

      <VitalsTable character={character} />

      <Fleuron />

      <KinList tree={tree} />
    </aside>
  );
}

function VitalsTable({
  character: c,
}: {
  character: CharacterDetail;
}): React.JSX.Element {
  const rows: [string, string][] = [
    ['Born', c.birth_date ?? '—'],
    ['Died', c.death_date ?? '—'],
    ['Culture', prettifyTag(c.culture)],
    ['Faith', prettifyTag(c.faith)],
  ];
  // ck3_chronicler-u6cr (2026-05-08): the Character.relevance column was
  // dropped — no production code path ever wrote a non-default value, so
  // the row was just "Relevance: unknown" forever. Real "salience" / "is
  // this character worth highlighting" needs to come from a derived
  // signal (player history, biography count) — see follow-up.
  return (
    <table className="chronicle-vitals">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <td className="smallcaps chronicle-vitals__key">{k}</td>
            <td className="chronicle-vitals__value">{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function KinList({
  tree,
}: {
  tree: FamilyTreeResponse | null;
}): React.JSX.Element {
  const setSelected = useAppStore((s) => s.setSelectedCharacterId);
  const setView = useAppStore((s) => s.setView);

  if (!tree) {
    return (
      <div>
        <SmallcapsLabel>Of his kin</SmallcapsLabel>
        <p className="italic-fell chronicle-kin__empty">
          The graft is not yet drawn.
        </p>
      </div>
    );
  }

  // Pick parents from depth-1 ancestors with mother/father relation.
  const parents = tree.ancestors.filter(
    (a) => a.depth === 1 && (a.relation === 'mother' || a.relation === 'father'),
  );
  const spouse = tree.spouses[0] ?? null;
  const children = tree.descendants.filter((d) => d.depth === 1);

  const onJump = (n: FamilyTreeNode): void => {
    setSelected(n.ck3_id);
    // Stay on chronicle; user wants to read about the relative.
    setView('chronicle');
  };

  return (
    <div>
      <SmallcapsLabel>Of his kin</SmallcapsLabel>
      <div className="chronicle-kin">
        {parents.map((p) => (
          <KinRow
            key={p.ck3_id}
            label={p.relation === 'father' ? 'Father' : 'Mother'}
            node={p}
            onJump={onJump}
          />
        ))}
        {spouse && (
          <KinRow
            label="Spouse"
            node={spouse}
            onJump={onJump}
          />
        )}
        {children.length > 0 && (
          <div className="chronicle-kin__row">
            <span className="italic-fell chronicle-kin__label">Children</span>
            <span className="chronicle-kin__value">
              {children.map((ch, i) => (
                <span key={ch.ck3_id}>
                  <button
                    type="button"
                    className="link chronicle-kin__link"
                    onClick={() => onJump(ch)}
                  >
                    {ch.first_name ?? `#${ch.ck3_id}`}
                  </button>
                  {i < children.length - 1 ? ', ' : ''}
                </span>
              ))}
            </span>
          </div>
        )}
        {parents.length === 0 && !spouse && children.length === 0 && (
          <p className="italic-fell chronicle-kin__empty">
            No kin recorded in the chronicle.
          </p>
        )}
      </div>
    </div>
  );
}

function KinRow({
  label,
  node,
  onJump,
}: {
  label: string;
  node: FamilyTreeNode;
  onJump: (n: FamilyTreeNode) => void;
}): React.JSX.Element {
  return (
    <div className="chronicle-kin__row">
      <span className="italic-fell chronicle-kin__label">{label}</span>
      <button
        type="button"
        className="link chronicle-kin__link"
        onClick={() => onJump(node)}
      >
        {node.first_name ?? `#${node.ck3_id}`}
      </button>
    </div>
  );
}
