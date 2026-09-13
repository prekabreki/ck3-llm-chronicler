// Self-hosted font imports via @fontsource/* packages.
// Each weight/style is a separate import that registers @font-face for it.
// Imported once at app boot via main.tsx; tree-shaken to only the weights
// the design actually uses (verified against design/styles.css).

// EB Garamond — body text (400/500/600 + italic 400/500/600)
import '@fontsource/eb-garamond/400.css';
import '@fontsource/eb-garamond/500.css';
import '@fontsource/eb-garamond/600.css';
import '@fontsource/eb-garamond/400-italic.css';
import '@fontsource/eb-garamond/500-italic.css';
import '@fontsource/eb-garamond/600-italic.css';

// IM Fell English SC — smallcaps display (single weight)
import '@fontsource/im-fell-english-sc/400.css';

// IM Fell DW Pica — italic asides, memories, glosses
import '@fontsource/im-fell-dw-pica/400.css';
import '@fontsource/im-fell-dw-pica/400-italic.css';

// Cormorant Unicase — drop caps + monumental titles
import '@fontsource/cormorant-unicase/500.css';
import '@fontsource/cormorant-unicase/600.css';
import '@fontsource/cormorant-unicase/700.css';

// Cormorant SC — secondary smallcaps (nav, settings)
import '@fontsource/cormorant-sc/500.css';
import '@fontsource/cormorant-sc/600.css';
import '@fontsource/cormorant-sc/700.css';

// JetBrains Mono — paths, IDs, raw events
import '@fontsource/jetbrains-mono/400.css';
import '@fontsource/jetbrains-mono/500.css';
import '@fontsource/jetbrains-mono/600.css';

// Inter — chrome face (vysp). Nav, buttons, smallcaps labels.
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import '@fontsource/inter/800.css';

// Spectral — dense UI body face (vysp). Sturdier than Garamond at 14-16px.
import '@fontsource/spectral/400.css';
import '@fontsource/spectral/500.css';
import '@fontsource/spectral/600.css';
import '@fontsource/spectral/400-italic.css';
