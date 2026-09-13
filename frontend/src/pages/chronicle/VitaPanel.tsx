// Audit F-27: Vita panel — biography prose + regenerate button. Renders
// pending state for living tracked chars (gcn) and missing-bio state
// for dead ones. Extracted from ChroniclePage.

import { RegenerateBiographyButton } from '../../components/RegenerateBiographyButton';
import type { BiographyResponse } from '../../api/types';
import { Fleuron } from './Ornaments';

interface VitaPanelProps {
  bio: BiographyResponse | null;
  loading: boolean;
  isAlive: boolean;
  campaignName: string;
  ck3Id: number;
  firstName: string | null;
}

export function VitaPanel({
  bio,
  loading,
  isAlive,
  campaignName,
  ck3Id,
  firstName,
}: VitaPanelProps): React.JSX.Element {
  if (loading) {
    return (
      <p className="italic-fell chronicle-page__pending">
        The vita is being inscribed…
      </p>
    );
  }
  if (!bio) {
    // ck3_chronicler-gcn: distinguish "alive — no biography yet by design"
    // from "dead — biography failed or hasn't generated yet". Without this
    // split a living character's empty Vita tab read as a broken page.
    if (isAlive) {
      return (
        <div className="chronicle-vita__pending-block">
          <p className="italic-fell chronicle-page__pending">
            The chronicler keeps the vita closed while the subject still lives.
            When their tale ends, this page will be inscribed in full.
          </p>
          <RegenerateBiographyButton
            campaignName={campaignName}
            ck3Id={ck3Id}
            characterName={firstName}
            hasExistingBiography={false}
          />
        </div>
      );
    }
    return (
      <div className="chronicle-vita__pending-block">
        <p className="italic-fell chronicle-page__pending">
          No vita has yet been written for this soul. The chronicler's hand
          moves slowly — biographies are set down upon a tracked character's
          death.
        </p>
        <RegenerateBiographyButton
          campaignName={campaignName}
          ck3Id={ck3Id}
          characterName={firstName}
          hasExistingBiography={false}
          variant="primary"
        />
      </div>
    );
  }
  const paragraphs = bio.body
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0);

  return (
    <div className="chronicle-vita">
      {paragraphs.map((p, i) => (
        <p
          key={i}
          className={i === 0 ? 'dropcap chronicle-vita__para' : 'chronicle-vita__para'}
        >
          {p}
        </p>
      ))}
      <Fleuron />
      <p className="italic-fell chronicle-vita__amen">
        Hic finit vita · qui requiescat in pace.
      </p>
      <div className="chronicle-vita__regenerate">
        <RegenerateBiographyButton
          campaignName={campaignName}
          ck3Id={ck3Id}
          characterName={firstName}
          hasExistingBiography
        />
      </div>
    </div>
  );
}
