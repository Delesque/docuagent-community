/** Pre-baked terminal node borders.
 *
 *  So the border is rasterized once into a bitmap and applied as a background image.
 *  Zoom only touches `transform`, never layout size, so a baked bitmap stays valid for
 *  the whole gesture and the compositor scales it nearly free. High magnification
 *  softens the bitmap, but by then focus mode has taken over and draws its own frame.
 */

export const BORDER_VARIANTS = 6;

interface BakeKey {
  width: number;
  height: number;
  variant: number;
  color: string;
  thickness: number;
  dpr: number;
}

const cache = new Map<string, string>();

function keyOf(key: BakeKey): string {
  return [key.width, key.height, key.variant, key.color, key.thickness, key.dpr].join("|");
}

/** Stable per-node variant so a node's edge never changes between renders. */
export function variantFor(id: string): number {
  let hash = 2166136261;
  for (let index = 0; index < id.length; index += 1) {
    hash ^= id.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return Math.abs(hash) % BORDER_VARIANTS;
}

/** Draw a crisp terminal rectangle, used for both the outer and inner border. */
function crispRect(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  inset: number,
): void {
  context.strokeRect(
    inset,
    inset,
    Math.max(0, width - inset * 2),
    Math.max(0, height - inset * 2),
  );
}

export interface BakeOptions {
  width: number;
  height: number;
  variant: number;
  color?: string;
  thickness?: number;
}

/** Returns a data URL for use as `background-image`. Cached by geometry and variant,
 *  so a graph of same-sized nodes bakes at most BORDER_VARIANTS times. */
export function bakeBorder(options: BakeOptions): string {
  const dpr = typeof window === "undefined" ? 1 : Math.min(2, window.devicePixelRatio || 1);
  const color = options.color ?? "rgba(108, 255, 168, 0.42)";
  const thickness = options.thickness ?? 1.5;
  const key = keyOf({
    width: options.width,
    height: options.height,
    variant: options.variant,
    color,
    thickness,
    dpr,
  });
  const hit = cache.get(key);
  if (hit) return hit;

  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(options.width * dpr);
  canvas.height = Math.ceil(options.height * dpr);
  const context = canvas.getContext("2d");
  if (!context) return "";
  context.scale(dpr, dpr);

  context.strokeStyle = color;
  context.lineWidth = thickness;
  context.lineJoin = "miter";
  crispRect(context, options.width, options.height, thickness);
  context.stroke();

  // A faint inner echo reads as a terminal double-border.
  context.strokeStyle = color.replace(/[\d.]+\)$/, "0.14)");
  context.lineWidth = thickness * 0.8;
  crispRect(context, options.width, options.height, thickness + 3);
  context.stroke();

  const url = canvas.toDataURL("image/png");
  cache.set(key, url);
  return url;
}

export function clearBorderCache(): void {
  cache.clear();
}

export function borderCacheSize(): number {
  return cache.size;
}
