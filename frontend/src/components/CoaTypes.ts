/**
 * Type definitions for CK3 coat-of-arms structures (ck3_chronicler-7ao).
 *
 * Mirrors the JSON shape CK3 emits at
 * `coat_of_arms.coat_of_arms_manager_database[<id>]` — the same
 * structure resolve_character_coa returns from the Python side and
 * /api/campaigns/{name}/characters/{ck3_id}/coa serves to the frontend.
 *
 * Permissive types (everything optional except where required by
 * structure) since CK3 omits fields it doesn't need on a given CoA.
 */

export interface CoaInstance {
  /** [x, y] both in 0-1 normalized coords */
  position?: readonly [number, number];
  /** [sx, sy] scale; default [1, 1] */
  scale?: readonly [number, number];
  /** [dx, dy] offset; default [0, 0] */
  offset?: readonly [number, number];
  /** rotation in degrees, applied around the emblem's centre. CK3
   * uses positive=counter-clockwise (e.g. -40 tilts a ram-horn 40° CW).
   * Omitted on most emblems; present on custom CoAs that rotate
   * charges (Ljosvetningar's tilted ram horn). */
  rotation?: number;
}

export interface CoaColoredEmblem {
  /** filename like "ce_leopard_passant_guardant.dds" — strip suffix to get PNG asset */
  texture: string;
  /** named colors from the palette ("white", "black", "red", ...) */
  color1?: string;
  color2?: string;
  color3?: string;
  /** which color channels of the texture each color slot tints */
  mask?: readonly number[];
  /** Single placement, OR an array when the same texture is repeated at
   * multiple positions — e.g. the Barcelona Senyera repeats ce_block_02
   * at six x-positions to form six vertical red bars. CK3 source uses
   * Paradox's duplicate-key syntax (`instance={...} instance={...}`)
   * which rakaly preserves as a JSON array under `--duplicate-keys=group`. */
  instance?: CoaInstance | readonly CoaInstance[];
}

export interface CoaDefinition {
  /** filename like "pattern_solid.dds" */
  pattern?: string;
  /** named colors from the palette */
  color1?: string;
  color2?: string;
  color3?: string;
  /** nested sub-shield (recursive) */
  sub?: CoaDefinition;
  /** plural form when multiple sub-shields exist on one CoA */
  subs?: readonly CoaDefinition[];
  /** charge texture overlaid on the field. Some CK3 saves emit this as
   * an array of charges under the singular field name when a node
   * carries multiple emblems (rakaly's duplicate-key grouping); the
   * renderer normalises both shapes via normalizeEmblems. */
  colored_emblem?: CoaColoredEmblem | readonly CoaColoredEmblem[];
  /** plural form when multiple charges exist */
  colored_emblems?: readonly CoaColoredEmblem[];
  /** placement on the parent (only meaningful when this is a sub-shield) */
  instance?: CoaInstance;
}

/** Palette as served by /api/heraldry/assets/palette.json. Values are RGB 0-255. */
export type Palette = Record<string, readonly [number, number, number]>;
